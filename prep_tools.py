"""Video prep tools for the TCRMP 3D atlas ingest pipeline.

Before a season's raw video folder reaches `atlasingest.py`, it commonly holds
multi-part exports, `_Proxy` mirrors, and inconsistent casing from whatever
camera or export tool produced them. `plan()` reads a folder and works out,
per output file, whether the file needs to be merged from parts, renamed to
the standard form, is already correct, or conflicts with a canonical file
that already exists. `apply()` carries that plan out: merging with ffmpeg
(stream copy, no re-encode) and renaming with `os.rename`, and logs every
action to `prep_log.csv`. `apply()` never overwrites an existing output file.

A merge deletes its source parts only after the merged file is verified: the
merged duration must match the sum of the parts' durations to within the
larger of half a second and two percent. These are field recordings of a
transect at a point in time and cannot be re-shot, so a mismatch keeps every
part, logs the action as "merge unverified" with both numbers, and leaves the
merged file in place for a person to look at. `--keep-parts` never deletes.

See `atlasprep.md` in this same folder for the operator/agent procedure.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
from naming3d import parse_video_name

LOG_FIELDS = ["timestamp", "action", "inputs", "output", "reason"]


def _ext(filename):
    return filename[filename.rindex("."):] if "." in filename else ""


def group_video_names(names):
    """Bucket `names` by parsed identity and classify each group's members
    into parts vs. a canonical file. Pure: takes a list of filenames (no
    filesystem access), so `plan()` and any other caller -- currently also
    `atlascatalog.py` in this same repo, walking a remote listing instead of
    a local folder -- can reuse exactly the same grouping and part-order
    rules without touching disk.

    A name that does not parse as a TCRMP 3D video name
    (`naming3d.parse_video_name` returns None) is left out of every group.

    Returns one dict per identity group, sorted by (project, date, site,
    transect):
    {"key": (project, date, site, transect),
     "output": standard-form output filename for this group,
     "members": [(name, parsed), ...] in part order (unparted members last),
     "parts": the members carrying a part number,
     "canonical": the (name, parsed) tuple whose name already equals
     `output`, or None}.
    """
    groups = defaultdict(list)
    for name in names:
        parsed = parse_video_name(name)
        if parsed is None:
            continue
        key = (parsed["project"], parsed["date"], parsed["site"], parsed["transect"])
        groups[key].append((name, parsed))

    result = []
    for key, members in sorted(groups.items()):
        members = sorted(members, key=lambda m: (m[1]["part"] is None, m[1]["part"] or 0, m[0]))
        project, date, site, transect = key
        output = f"{project}{date}_3D_{site}_{transect}{_ext(members[0][0])}"
        parts = [m for m in members if m[1]["part"] is not None]
        canonical = next((m for m in members if m[1]["part"] is None and m[0] == output), None)
        result.append({"key": key, "output": output, "members": members,
                        "parts": parts, "canonical": canonical})
    return result


def plan(folder):
    """Read `folder` and return one action dict per output file.

    Each dict is {"action": "merge"|"rename"|"keep"|"conflict", "inputs":
    [...], "output": str, "reason": str}. `inputs` are original filenames as
    they sit in `folder`; `output` is the standard-form filename this action
    produces, still relative to `folder`.

    A "conflict" action means a canonical file already exists on disk under
    the standard name for this timepoint at the same time as leftover part
    files for the same key. Nothing about a conflict is safe to guess: the
    plan flags it and `apply()` leaves every file involved untouched.
    """
    names = [n for n in sorted(os.listdir(folder)) if os.path.isfile(os.path.join(folder, n))]

    actions = []
    for group in group_video_names(names):
        output, members = group["output"], group["members"]
        parts, canonical = group["parts"], group["canonical"]

        if len(parts) >= 2:
            part_inputs = [m[0] for m in parts]
            if canonical is not None:
                actions.append({
                    "action": "conflict",
                    "inputs": part_inputs + [canonical[0]],
                    "output": output,
                    "reason": "canonical file already exists alongside parts; resolve by hand "
                              "(delete the parts if the merge was verified, or move the canonical "
                              "file aside)",
                })
            else:
                actions.append({
                    "action": "merge",
                    "inputs": part_inputs,
                    "output": output,
                    "reason": f"{len(part_inputs)} parts share project/date/site/transect; merging in part order",
                })
            continue

        # Single member for this key: either it is already the standard
        # name, or it needs a rename (case, `_Proxy` suffix, lone `_1` part).
        name, parsed = members[0]
        if name == output:
            actions.append({
                "action": "keep",
                "inputs": [name],
                "output": output,
                "reason": "already in standard form",
            })
        else:
            reason_bits = []
            if parsed["proxy"]:
                reason_bits.append("strips _Proxy suffix")
            base, ext = os.path.splitext(name)
            if parsed["proxy"] and base.lower().endswith("_proxy"):
                base = base[: -len("_proxy")]
            if base + ext != output:
                reason_bits.append("normalizes case/underscores to standard name")
            if not reason_bits:
                reason_bits.append("normalizes to standard name")
            actions.append({
                "action": "rename",
                "inputs": [name],
                "output": output,
                "reason": "; ".join(reason_bits),
            })

    return actions


def _codec_name(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
         "-of", "json", path],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe found no video stream in {path}")
    return streams[0]["codec_name"]


def _duration(path):
    """Duration of `path` in seconds, from the container header."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        check=True, capture_output=True, text=True,
    )
    value = (json.loads(out.stdout).get("format") or {}).get("duration")
    if value in (None, "", "N/A"):
        raise RuntimeError(f"ffprobe reported no duration for {path}")
    return float(value)


# How far the merged duration may sit from the sum of the parts before the
# merge counts as unverified. A stream copy concat is exact to within container
# rounding, so this is generous; it exists so a rounding difference on a long
# recording does not read as a truncated file.
MERGE_TOLERANCE_S = 0.5
MERGE_TOLERANCE_FRACTION = 0.02


def verify_durations(merged_s, part_durations):
    """(ok, expected_s, tolerance_s) for a merged file against its parts.

    The merged duration has to match the sum of the parts to within the larger
    of MERGE_TOLERANCE_S and MERGE_TOLERANCE_FRACTION of that sum. Pure, so the
    rule can be tested without ffmpeg.
    """
    expected = sum(part_durations)
    tolerance = max(MERGE_TOLERANCE_S, MERGE_TOLERANCE_FRACTION * expected)
    return abs(merged_s - expected) <= tolerance, expected, tolerance


def _merge(folder, inputs, output, keep_parts=False):
    """Concat `inputs` into `output`, then delete the parts only if the merge
    verified. Returns a dict describing what happened:
    {"verified": bool, "merged_s": float, "parts_s": float, "tolerance_s":
    float, "deleted": bool}.
    """
    paths = [os.path.join(folder, name) for name in inputs]
    codecs = {name: _codec_name(path) for name, path in zip(inputs, paths)}
    distinct = set(codecs.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{name}={codec}" for name, codec in codecs.items())
        raise RuntimeError(
            f"cannot merge parts with mismatched codecs ({detail}); re-encode to a common "
            f"codec before merging {', '.join(inputs)}"
        )

    part_durations = [_duration(path) for path in paths]

    output_path = os.path.join(folder, output)
    with tempfile.TemporaryDirectory() as tmpdir:
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, "w") as fh:
            for path in paths:
                # Single-quoted with -safe 0. Safe because plan() only admits
                # names matching naming3d.VIDEO_NAME, which is alphanumerics
                # and underscores: no name reaching here can hold a quote to
                # break out with. Loosening that regex means quoting these
                # paths properly first.
                fh.write(f"file '{os.path.abspath(path)}'\n")
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", output_path],
            check=True,
        )

    # ffmpeg's exit code says the command ran, not that the output holds every
    # frame that went in: a corrupt tail in one part can be recovered from with
    # a warning and a short file. The parts are irreplaceable field recordings,
    # so nothing is deleted until the durations agree.
    merged_s = _duration(output_path)
    ok, expected, tolerance = verify_durations(merged_s, part_durations)

    deleted = False
    if ok and not keep_parts:
        for path in paths:
            os.remove(path)
        deleted = True
    return {"verified": ok, "merged_s": round(merged_s, 2), "parts_s": round(expected, 2),
            "tolerance_s": round(tolerance, 2), "deleted": deleted}


def apply(folder, actions, log_path, keep_parts=False):
    """Carry out `actions` (as returned by `plan`) inside `folder`.

    Merges are done with the ffmpeg concat demuxer and `-c copy`; renames use
    `os.rename`. "conflict" actions are logged but never touch a file: no
    merge and no rename runs for them. For "merge" and "rename", if the
    output path already exists on disk, `apply()` raises `RuntimeError`
    naming that path instead of overwriting it -- this is the general
    backstop for any output collision `plan()` did not already flag as a
    conflict. Every non-"keep" action appends one row to `log_path` (created
    with a header if it does not already exist).

    A merge's source parts are deleted only after the merged duration is
    checked against the sum of the parts' durations. When they disagree the
    parts stay on disk and the row is logged with action "merge unverified"
    and both numbers in its reason, so the operator can compare the two files
    themselves. `keep_parts=True` never deletes, verified or not.
    """
    is_new_log = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as log_fh:
        writer = csv.DictWriter(log_fh, fieldnames=LOG_FIELDS)
        if is_new_log:
            writer.writeheader()

        for action in actions:
            kind = action["action"]
            if kind == "keep":
                continue

            output_path = os.path.join(folder, action["output"])
            logged_action, logged_reason = kind, action["reason"]

            if kind == "conflict":
                print(f"conflict: {', '.join(action['inputs'])} -> {action['output']} ({action['reason']})")
            else:
                if os.path.exists(output_path):
                    raise RuntimeError(
                        f"refusing to overwrite existing output {output_path} for action '{kind}'"
                    )
                print(f"{kind}: {', '.join(action['inputs'])} -> {action['output']}")
                if kind == "merge":
                    result = _merge(folder, action["inputs"], action["output"],
                                     keep_parts=keep_parts)
                    numbers = (f"merged {result['merged_s']} s against parts "
                               f"{result['parts_s']} s, tolerance {result['tolerance_s']} s")
                    if not result["verified"]:
                        logged_action = "merge unverified"
                        logged_reason = (
                            f"{action['reason']}; merge unverified: {numbers}. Parts kept; "
                            f"compare the files by hand before deleting anything"
                        )
                        print(f"  merge unverified: {numbers}")
                        print(f"  parts kept: {', '.join(action['inputs'])}")
                    elif not result["deleted"]:
                        logged_reason = f"{action['reason']}; verified ({numbers}); parts kept (--keep-parts)"
                        print(f"  verified: {numbers}; parts kept (--keep-parts)")
                    else:
                        logged_reason = f"{action['reason']}; verified ({numbers}); parts deleted"
                        print(f"  verified: {numbers}; parts deleted")
                elif kind == "rename":
                    src = os.path.join(folder, action["inputs"][0])
                    os.rename(src, output_path)
                else:
                    raise RuntimeError(f"unknown action: {kind}")

            writer.writerow({
                "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "action": logged_action,
                "inputs": ";".join(action["inputs"]),
                "output": action["output"],
                "reason": logged_reason,
            })
            log_fh.flush()


def _print_plan(actions):
    for action in actions:
        inputs = ", ".join(action["inputs"])
        print(f"{action['action']:>8}  {inputs}  ->  {action['output']}  ({action['reason']})")


def main():
    parser = argparse.ArgumentParser(description="Plan and apply video prep (merge parts, normalize names) before atlas ingest.")
    parser.add_argument("folder", help="Season folder of raw videos, e.g. 2024_pbl")
    parser.add_argument("--apply", action="store_true", help="Carry out the plan (default is dry run: print only)")
    parser.add_argument("--log", default=None, help="Log CSV path (default: <folder>/prep_log.csv)")
    parser.add_argument("--keep-parts", action="store_true",
                         help="Never delete a merge's source parts, even when the merged "
                              "duration verifies against them")
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    log_path = args.log or os.path.join(folder, "prep_log.csv")

    actions = plan(folder)
    if not actions:
        print(f"No recognizable TCRMP 3D video names found in {folder}")
        return

    _print_plan(actions)

    conflicts = [a for a in actions if a["action"] == "conflict"]
    if conflicts:
        print(f"{len(conflicts)} conflict(s) found; resolve by hand before re-running (see reasons above).")

    if args.apply:
        apply(folder, actions, log_path, keep_parts=args.keep_parts)
        print(f"Applied {len(actions)} action(s). Log: {log_path}")
        print("Any row logged as 'merge unverified' kept its parts: compare that merged file "
              "against them before deleting anything.")
    else:
        print("Dry run only. Re-run with --apply to carry out this plan.")


if __name__ == "__main__":
    main()
