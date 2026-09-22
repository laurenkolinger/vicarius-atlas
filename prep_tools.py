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

Lauren's catalog rulings (the registry's catalog_rulings.csv) reach this tool
through `--rulings <path>`. A file the archive holds under an odd name that
she has ruled `catalog_as` is treated here as if it were named
TCRMP{date}_3D_{site_label}_{transect}[_{part}], so it groups, merges and
renames exactly as a well-named file would, and its prep_log.csv row says
"by ruling of <who> <when>: <note>". A file ruled `leave_out` is left exactly
where it is and listed as ruled out. A ruling applies to a file only when the
file name matches and the last segment of the ruling's NAS folder equals this
folder's own name, which is how the Carousel names a staged season on the
Workbench. Without the flag no ruling is applied and nothing here changes. A
path with no file at it also applies no ruling, the reading registry.rulings()
and the catalog give a missing sidecar, so a registry root without one (a
fresh checkout) still preps a well-named season; an empty or malformed file
stops the run.

See `atlasprep.md` in this same folder for the operator/agent procedure.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
from naming3d import MIN_PART, NAME_VARIANTS, parse_video_name, normalise_variants

LOG_FIELDS = ["timestamp", "action", "inputs", "output", "reason"]
# What a merge in progress is called before it takes the recording's name:
# ".<stem>.merging<ext>", a dot name so a folder walk never reads it as a
# recording, with the real extension last so ffmpeg can still choose a muxer.
MERGE_PARTIAL_SUFFIX = ".merging"
# Same set atlasingest.py uses, kept here so this tool can tell "the folder
# holds no videos" apart from "the folder holds videos this tool cannot read".
NON_VIDEO_EXTENSIONS = {".csv", ".txt", ".md", ".log", ".json", ".xml"}
# Exit code for a folder whose video files none of the given name variants
# could parse. Nonzero so the Carousel's ingesting stage stops that season
# instead of recording a merge that never happened.
EXIT_NOTHING_RECOGNIZED = 2
# Exit code when the plan holds a conflict. A conflict is a merge or a rename
# that did not happen, so the season is not prepped and the Carousel must stop
# rather than run the ingest over a folder that is not ready.
EXIT_CONFLICTS = 3
# Exit code when the --rulings file is empty or malformed (a missing file is
# no rulings: see load_rulings). Nothing is merged or renamed then: a ruling
# half applied is worse than none.
EXIT_RULINGS_UNREADABLE = 4

# Two or more parts are a part set whose numbers must run 1 to N; one part
# alone is the whole recording (the same threshold as atlascatalog.MIN_PARTS_FOR_SET).
MIN_PARTS_FOR_SET = 2
# How many unreadable names to quote in that message before "and N more".
UNREAD_NAMES_SHOWN = 5

# The catalog rulings sidecar as the registry library writes it: its columns and
# its two ruling words (registry.RULING_COLUMNS, RULING_LEAVE_OUT,
# RULING_CATALOG_AS). Written out here so this tool can still read the file with
# the csv module when the registry library cannot be imported (it needs yaml;
# this tool needs only naming3d). tests/test_prep_tools.py pins the three equal.
RULING_COLUMNS = ["nas_path", "file_name", "ruling", "site_label", "transect", "date", "part",
                  "note", "ruled_by", "ruled_at"]
RULING_LEAVE_OUT = "leave_out"
RULING_CATALOG_AS = "catalog_as"
RULING_VALUES = (RULING_LEAVE_OUT, RULING_CATALOG_AS)
# The parse a catalog_as ruling stands in for reports this kind (never one
# naming3d reports), so a reason can tell a ruled file from a well-named one.
RULED_KIND = "ruled"
# The project token every ruled file is filed under: the synthetic name is
# TCRMP{date}_3D_{site_label}_{transect}[_{part}].
RULED_PROJECT = "TCRMP"
# The shapes the registry holds a ruling's placement cells to (its
# RULING_SITE_LABEL_PATTERN, RULING_TRANSECT_PATTERN, RULING_DATE_PATTERN).
RULING_SITE_LABEL = re.compile(r"^[A-Za-z0-9]+\Z")
RULING_TRANSECT = re.compile(r"^T[0-9]+\Z")
RULING_DATE = re.compile(r"^[0-9]{8}\Z")
# The rulings key is "<host>:<absolute path>"; the host has no slash in it.
RULING_NAS_PATH = re.compile(r"^[A-Za-z0-9.\-]+:/")
AST = timezone(timedelta(hours=-4))
RULED_AT_FORMAT = "%Y-%m-%d %H:%M AST"


class RulingsError(ValueError):
    """A rulings file this tool cannot use: a folder, empty, unreadable, or holding a line that is not a ruling."""


def _ext(filename):
    return filename[filename.rindex("."):] if "." in filename else ""


def _stem(filename):
    """The file name without its last extension; a name with no dot is its own stem."""
    return filename.rsplit(".", 1)[0] if "." in filename else filename


# --- rulings -----------------------------------------------------------------

def _registry_library():
    """The shared registry library from the same folder as naming3d, or None when it cannot be imported.

    Returns:
        (module, "") when `import registry` works; (None, reason) otherwise.
        The usual reason is a missing yaml package in the running interpreter.
    """
    try:
        import registry
    except ImportError as exc:
        return None, str(exc)
    return registry, ""


def _check_rulings_header(path):
    """Refuse `path` unless its first line carries every column of RULING_COLUMNS.

    Raises:
        RulingsError: when the file cannot be read, is empty, or its header
            lacks a rulings column (names the path and what was found).
    """
    try:
        with open(path, newline="") as fh:
            header = next(csv.reader(fh), None)
    except OSError as exc:
        raise RulingsError(f"the rulings file {path} could not be read: {exc}") from exc
    except csv.Error as exc:
        raise RulingsError(f"the rulings file {path} could not be parsed as CSV: {exc}") from exc
    if not header:
        raise RulingsError(f"the rulings file {path} is empty: it carries no header")
    missing = [c for c in RULING_COLUMNS if c not in header]
    if missing:
        raise RulingsError(f"the rulings file {path} does not carry the rulings header "
                           f"({', '.join(RULING_COLUMNS)}): {', '.join(missing)} missing; found {header}")


def _read_rulings_csv(path):
    """Every line of the rulings file at `path`, shaped to RULING_COLUMNS, read with the csv module.

    Raises:
        RulingsError: when the file cannot be read or parsed (names the path).
    """
    try:
        with open(path, newline="") as fh:
            return [{c: (row.get(c) or "") for c in RULING_COLUMNS} for row in csv.DictReader(fh)]
    except OSError as exc:
        raise RulingsError(f"the rulings file {path} could not be read: {exc}") from exc
    except csv.Error as exc:
        raise RulingsError(f"the rulings file {path} could not be parsed as CSV: {exc}") from exc


def _is_calendar_date(yyyymmdd):
    """True when the eight digits name a day the calendar has (20241399 does not)."""
    try:
        datetime.strptime(yyyymmdd, "%Y%m%d")
    except ValueError:
        return False
    return True


def _check_ruling(line, path, line_number):
    """`line` when it is a ruling this tool can act on.

    A leave_out needs only its key and file name. A catalog_as also needs a
    site label (letters and digits), a transect (T plus digits), a real date
    (YYYYMMDD) and a part that is blank or a whole number from MIN_PART.

    Raises:
        RulingsError: naming the line and the cell that is wrong.
    """
    where = f"line {line_number} of the rulings file {path}"
    name = line["file_name"]
    if not name or "/" in name:
        raise RulingsError(f"{where}: file_name must be a bare file name, got {name!r}")
    if not RULING_NAS_PATH.match(line["nas_path"]):
        raise RulingsError(f"{where}: nas_path must be <host>:<absolute path>, got {line['nas_path']!r}")
    if line["nas_path"].rsplit("/", 1)[-1] != name:
        raise RulingsError(f"{where}: file_name {name!r} is not the last segment of nas_path {line['nas_path']!r}")
    if line["ruling"] not in RULING_VALUES:
        raise RulingsError(f"{where}: ruling must be {' or '.join(RULING_VALUES)}, got {line['ruling']!r}")
    if line["ruling"] == RULING_LEAVE_OUT:
        return line
    if not RULING_SITE_LABEL.match(line["site_label"]):
        raise RulingsError(f"{where}: site_label must be letters and digits, got {line['site_label']!r}")
    if not RULING_TRANSECT.match(line["transect"]):
        raise RulingsError(f"{where}: transect must be T followed by digits, got {line['transect']!r}")
    if not RULING_DATE.match(line["date"]) or not _is_calendar_date(line["date"]):
        raise RulingsError(f"{where}: date must be a real YYYYMMDD date, got {line['date']!r}")
    part = line["part"].strip()
    if part and not (part.isdecimal() and int(part) >= MIN_PART):
        raise RulingsError(f"{where}: part must be blank or a whole number from {MIN_PART}, got {line['part']!r}")
    return line


def load_rulings(path):
    """The rulings at `path`, each a dict over RULING_COLUMNS, every line checked.

    The registry's own sidecar (the path equal to registry.RULINGS_CSV) is read
    through registry.rulings(), under the registry lock, when that library can
    be imported; any other path, or a registry library that cannot be
    imported, is read with the csv module. Either way the header and every
    line are checked here, so a hand-edited line is refused by name rather
    than applied half-read.

    A path with no file at it is no rulings, the reading registry.rulings()
    and the catalog's load_rulings give a missing sidecar. The Carousel names
    the registry's sidecar on every ingest and the harness does not track
    that file, so a fresh checkout has none; refusing it stopped every
    well-named season on such a box with nothing merged (2026-09-14). A ruled
    odd file prepped without its ruling is still reported as unread by
    unread_video_names, so nothing passes in silence.

    Parameters:
        path: the rulings CSV.

    Returns:
        A list of dicts with string values, in file order; [] when no file
        is at `path`.

    Raises:
        RulingsError: the path is a folder, or the file is empty, cannot be
            read, lacks the header, or holds a line that is not a ruling.
    """
    if not isinstance(path, str) or not path:
        raise RulingsError("a rulings path must be a non-empty string")
    if os.path.isdir(path):
        raise RulingsError(f"the rulings path {path} is a folder, not a CSV file")
    if not os.path.exists(path):
        return []
    _check_rulings_header(path)
    registry, _why_not = _registry_library()
    if registry is not None and os.path.realpath(path) == os.path.realpath(registry.RULINGS_CSV):
        try:
            lines = registry.rulings()
        except (OSError, ValueError) as exc:
            raise RulingsError(f"the rulings file {path} could not be read: {exc}") from exc
    else:
        lines = _read_rulings_csv(path)
    return [_check_ruling(line, path, number) for number, line in enumerate(lines, start=2)]


def ruling_folder_name(nas_path):
    """The last segment of the folder a ruling's file sits in.

    Example:
        ruling_folder_name("146.226.147.140:/volume2/x/TCRMP_2024_PBL/a.MP4") -> "TCRMP_2024_PBL"
    """
    path = nas_path.split(":", 1)[1] if RULING_NAS_PATH.match(nas_path) else nas_path
    return os.path.basename(os.path.dirname(path))


def rulings_for_folder(rulings, folder):
    """{file name: ruling} for the rulings that belong to `folder`.

    A ruling belongs to a folder when the last segment of its NAS folder equals
    the folder's own name. The Carousel stages a season on the Workbench under
    the NAS season folder's basename, so that segment is the one part of a
    ruling's key that survives the pull. A ruling whose file is not in the
    folder is simply unused.

    Parameters:
        rulings: lines as load_rulings returns them.
        folder: the season folder being prepped (only its basename matters).

    Returns:
        A dict from bare file name to the ruling line; {} when none belong.

    Raises:
        RulingsError: when two rulings name one file in two different folders
            that share the same last segment, which the folder name alone
            cannot tell apart.
    """
    season = os.path.basename(os.path.normpath(folder))
    by_name = {}
    for line in rulings:
        if ruling_folder_name(line["nas_path"]) != season:
            continue
        name = line["file_name"]
        other = by_name.get(name)
        if other is not None and other["nas_path"] != line["nas_path"]:
            raise RulingsError(f"two rulings name {name} in a folder called {season}: {other['nas_path']} "
                               f"and {line['nas_path']}; the folder name alone cannot tell them apart")
        by_name[name] = line
    return by_name


def ruled_out_names(names, rulings_by_name):
    """The names in `names` that a leave_out ruling covers, sorted."""
    rulings_by_name = rulings_by_name or {}
    return sorted(n for n in names if (rulings_by_name.get(n) or {}).get("ruling") == RULING_LEAVE_OUT)


def ruled_in_names(names, rulings_by_name):
    """The names in `names` that a catalog_as ruling covers, sorted."""
    rulings_by_name = rulings_by_name or {}
    return sorted(n for n in names if (rulings_by_name.get(n) or {}).get("ruling") == RULING_CATALOG_AS)


def ruled_parse(ruling):
    """The parse a catalog_as ruling stands in for: the file as if named TCRMP{date}_3D_{site_label}_{transect}[_{part}].

    Parameters:
        ruling: a catalog_as line as load_rulings returns it.

    Returns:
        The same dict shape as naming3d.parse_video_name, with kind RULED_KIND
        and the ruling itself under "ruling" so a reason can cite it. proxy
        reports what the real name says, since that is a fact about the file.
    """
    part = ruling["part"].strip()
    return {
        "project": RULED_PROJECT,
        "date": ruling["date"],
        "site": ruling["site_label"].upper(),
        "transect": ruling["transect"].upper(),
        "part": int(part) if part else None,
        "proxy": _stem(ruling["file_name"]).lower().endswith("_proxy"),
        "kind": RULED_KIND,
        "ruling": dict(ruling),
    }


def ruled_at_ast(ruled_at):
    """A ruling's ruled_at stamp as "YYYY-MM-DD HH:MM AST"; the text unchanged when it is not an ISO stamp.

    The registry stamps ISO with the AST offset ("2026-09-14T16:26:00-04:00");
    a stamp in another offset is converted, a naive one is taken as AST.
    """
    try:
        stamp = datetime.fromisoformat(ruled_at)
    except (TypeError, ValueError):
        return ruled_at
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(AST)
    return stamp.strftime(RULED_AT_FORMAT)


def ruling_sentence(ruling):
    """"by ruling of <who> <when>: <note>", the sentence every log row for a ruled file carries."""
    return f"by ruling of {ruling['ruled_by']} {ruled_at_ast(ruling['ruled_at'])}: {ruling['note']}"


def _with_ruling_reason(action, members):
    """`action` with its ruled members' ruling sentence(s) in front of its reason.

    Each distinct sentence appears once, and every ruled part is named with the
    number its ruling gives it, so a renumbering (Castle transect 5's archive
    parts 6 and 7 as parts 5 and 6) is written into the log row. Unchanged
    when no member is ruled.
    """
    ruled = [(name, parsed) for name, parsed in members if parsed["kind"] == RULED_KIND]
    if not ruled:
        return action
    sentences = []
    for _name, parsed in ruled:
        sentence = ruling_sentence(parsed["ruling"])
        if sentence not in sentences:
            sentences.append(sentence)
    parts = [f"{name} as part {parsed['part']}" for name, parsed in ruled if parsed["part"] is not None]
    bits = sentences + ([f"parts by ruling: {', '.join(parts)}"] if parts else []) + [action["reason"]]
    action["reason"] = "; ".join(bits)
    return action


# --- grouping and planning ----------------------------------------------------

def _parse_member(name, variants, rulings_by_name):
    """The parse for one file name: its ruling's synthetic parse when a catalog_as
    ruling covers it, nothing when a leave_out does, else naming3d's own."""
    ruling = rulings_by_name.get(name)
    if ruling is None:
        return parse_video_name(name, variants)
    if ruling["ruling"] == RULING_LEAVE_OUT:
        return None
    return ruled_parse(ruling)


def group_video_names(names, variants=(), rulings_by_name=None):
    """Bucket `names` by parsed identity and classify each group's members
    into parts vs. a canonical file. Pure: takes a list of filenames (no
    filesystem access), so `plan()` and any other caller can reuse exactly
    the same grouping and part-order rules without touching disk.
    (`atlascatalog.py` in this same repo mirrors these rules over a remote
    listing rather than calling this.)

    A name that does not parse as a TCRMP 3D video name
    (`naming3d.parse_video_name` returns None) is left out of every group.

    `variants` admits the same alternative name tokens the catalog admits
    through its own --name-variant flag ("demo", "3ddemo"), and must be given
    for a season whose files carry one. Empty by default, so nothing changes
    for a standard-named folder.

    This parameter exists because its absence was a silent data loss. Until
    2026-09-08 this function parsed with no variants while atlascatalog.py
    parsed with them, so the catalog recorded a season's recordings as parts
    while the merge that was meant to join them saw no groups at all, did
    nothing, and reported success. 92 of 377 registry rows were left holding
    unmerged parts, which frame extraction refuses, and a Voyager 1 run
    reconstructed three of the four timepoints it was asked for and called
    itself done.

    `rulings_by_name` ({file name: ruling}, as rulings_for_folder returns) makes
    a catalog_as file parse as the standard name its ruling gives (see
    ruled_parse) and drops a leave_out file before parsing. None by default,
    so nothing changes for a folder prepped without rulings.

    Returns one dict per identity group, sorted by (project, date, site,
    transect):
    {"key": (project, date, site, transect),
     "output": standard-form output filename for this group,
     "members": [(name, parsed), ...] in part order (unparted members last),
     "parts": the members carrying a part number,
     "canonical": the (name, parsed) tuple whose name already equals
     `output`, or None}.
    """
    # Refuse an unknown token by name rather than silently admitting nothing,
    # which is the failure mode this whole parameter exists to end.
    variants = normalise_variants(variants)
    rulings_by_name = rulings_by_name or {}
    groups = defaultdict(list)
    for name in names:
        parsed = _parse_member(name, variants, rulings_by_name)
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


def folder_file_names(folder):
    """The names of the plain files in `folder`, sorted (folders and control files aside)."""
    return [n for n in sorted(os.listdir(folder)) if os.path.isfile(os.path.join(folder, n))]


def plan(folder, variants=(), rulings_by_name=None):
    """Read `folder` and return one action dict per output file.

    `variants` is passed straight to group_video_names: a season whose files
    carry the "demo" or "3ddemo" token needs it, or this returns no actions
    and the caller merges nothing (see that function for what that cost).
    `rulings_by_name` is passed the same way (see rulings_for_folder).

    Delegates to plan_names, which is where the rules live and which touches
    no disk.
    """
    return plan_names(folder_file_names(folder), variants, rulings_by_name)


def plan_names(names, variants=(), rulings_by_name=None):
    """One action per identity group over `names`. Pure: no filesystem access.

    Parameters:
        names: the file names in one folder.
        variants: name variants to admit, as group_video_names takes them.
        rulings_by_name: rulings to apply, as group_video_names takes them.

    Returns:
        A list of {"action": "merge"|"rename"|"keep"|"conflict", "inputs":
        [...], "output": str, "reason": str}, sorted by identity. `inputs` are
        original file names; `output` is the standard-form name this action
        produces.

    Exactly one action per identity group, and a group's action always names
    every one of its files in `inputs`. That is the guarantee: a file this rule
    has read can never fail to appear in the plan. Before 2026-09-08 a group
    that was not a two-or-more part merge built its action from its first
    member and dropped the rest without a word, which on the real archive meant
    TCRMP20241113_3D_JKB_T2_Proxy.MOV was invisible next to a stray part of the
    same recording. A leave_out file is not read at all: it has no group and no
    action, and main() lists it as ruled out.

    A "conflict" means the rule will not guess. `apply()` leaves every file of
    a conflicting group exactly where it is.
    """
    return [_action_for_group(g) for g in group_video_names(names, variants, rulings_by_name)]


def _missing_part_numbers(numbers):
    """The part numbers a set is missing to be 1..N, as a sorted list.

    Parameters:
        numbers: the part numbers present, in any order, possibly with repeats.

    Returns:
        [] when `numbers` is exactly 1..N with no repeats; otherwise the
        numbers absent from 1..max, which is what a person needs told.

    Example:
        _missing_part_numbers([1, 2, 3, 4, 6, 7]) -> [5]
    """
    present = sorted(numbers)
    if present == list(range(1, len(present) + 1)):
        return []
    return [n for n in range(1, max(present) + 1) if n not in set(present)]


def _action_for_group(group):
    """The one action for one identity group, its reason citing any ruling among its members."""
    return _with_ruling_reason(_base_action_for_group(group), group["members"])


def _base_action_for_group(group):
    """The one action for one identity group, before any ruling is cited.

    A merge happens only when the group is a complete part set: part numbers
    exactly 1..N, no repeats, and nothing else in the group. A single file,
    part-numbered or not, is the whole recording and is renamed or kept.
    Anything else is a conflict, named and left alone.

    The reason a gap is a conflict rather than a merge: the merge verifies
    itself by comparing the joined duration against the sum of the parts it
    joined, so a recording missing its fifth part of seven joins cleanly,
    verifies, and is reconstructed with a hole in it that nothing reports.
    Meri Shoal is not the only transect this would have happened to:
    Castle transect 5 on 2024-03-11 is on the archive as parts
    1, 2, 3, 4, 6 and 7 (which Lauren's ruling of 2026-09-14 renumbers 1 to 6).
    """
    output, members = group["output"], group["members"]
    parts, canonical = group["parts"], group["canonical"]
    all_names = [m[0] for m in members]

    if len(parts) >= MIN_PARTS_FOR_SET:
        numbers = [m[1]["part"] for m in parts]
        missing = _missing_part_numbers(numbers)
        repeats = sorted({n for n in numbers if numbers.count(n) > 1})
        others = [m[0] for m in members if m[1]["part"] is None]
        if missing or repeats or others:
            return {
                "action": "conflict",
                "inputs": all_names,
                "output": output,
                "reason": _incomplete_reason(numbers, missing, repeats, others, canonical),
            }
        return {
            "action": "merge",
            "inputs": [m[0] for m in parts],
            "output": output,
            "reason": f"{len(parts)} parts numbered 1 to {len(parts)} share project/date/site/"
                      f"transect; merging in part order",
        }

    if len(members) > 1:
        return {
            "action": "conflict",
            "inputs": all_names,
            "output": output,
            "reason": "more than one file claims this timepoint and none of them is a part set; "
                      "resolve by hand (keep the whole recording, move the others aside)",
        }

    name, parsed = members[0]
    if parsed["part"] is not None:
        return _lone_part_action(name, parsed, output)
    if name == output:
        return {
            "action": "keep",
            "inputs": [name],
            "output": output,
            "reason": "already in standard form",
        }
    return _rename_action(name, parsed, output)


def _incomplete_reason(numbers, missing, repeats, others, canonical):
    """The plain sentence for a part set that will not be merged."""
    bits = []
    if missing:
        bits.append(f"part {', '.join(str(n) for n in missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} not in this folder "
                    f"(found {', '.join(str(n) for n in sorted(numbers))})")
    if repeats:
        bits.append(f"part {', '.join(str(n) for n in repeats)} appears more than once")
    if others:
        if canonical is not None:
            bits.append("a file already under the standard name sits beside the parts")
        else:
            bits.append(f"{', '.join(others)} carries no part number but claims the same timepoint")
    return ("; ".join(bits) + ". Nothing was merged: a part set is joined only when its numbers "
            "run 1 to N with nothing else in the group, because the duration check cannot see a "
            "part that was never there. Resolve by hand")


def _lone_part_action(name, parsed, output):
    """The rename for a part-numbered file that is the only file of its recording.

    A lone part is the whole recording (Lauren, 2026-09-11 13:31 AST): when
    nothing else of the recording is in the folder, the part number is a naming
    accident, not proof of a missing piece, and refusing it left Meri
    Shoal transect 3, 2023 annual, unprocessed. The part number goes into the
    log reason so the fact is never lost.
    """
    action = _rename_action(name, parsed, output)
    action["reason"] = (f"lone part {parsed['part']}: no other part of this recording is in this "
                        f"folder, so it is taken as the whole recording; " + action["reason"])
    return action


def _rename_action(name, parsed, output):
    """The rename action for a single file that is not yet in standard form.

    A ruled file's new name comes from its ruling, not from its own tokens, so
    its reason says so and _with_ruling_reason puts the ruling in front of it.
    """
    if parsed["kind"] == RULED_KIND:
        reason_bits = ["takes the name the ruling gives"]
    else:
        reason_bits = []
        if parsed["proxy"]:
            reason_bits.append("strips _Proxy suffix")
        base, ext = os.path.splitext(name)
        if parsed["proxy"] and base.lower().endswith("_proxy"):
            base = base[: -len("_proxy")]
        if base + ext != output:
            reason_bits.append("normalizes case/underscores to standard name")
    return {
        "action": "rename",
        "inputs": [name],
        "output": output,
        "reason": "; ".join(reason_bits) or "normalizes to standard name",
    }


# --- merging -------------------------------------------------------------------

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
# The fractional term is capped, so the tolerance cannot grow with the
# recording. Uncapped, a five minute transect swim allowed 5.7 seconds, about
# 170 frames, to be missing from the join or duplicated in it while the run
# printed "verified" and deleted the parts (2026-09-08).
MERGE_TOLERANCE_MAX_S = 1.0


def verify_durations(merged_s, part_durations):
    """(ok, expected_s, tolerance_s) for a merged file against its parts.

    The merged duration has to match the sum of the parts to within the larger
    of MERGE_TOLERANCE_S and MERGE_TOLERANCE_FRACTION of that sum, and never
    more than MERGE_TOLERANCE_MAX_S however long the recording. Pure, so the
    rule can be tested without ffmpeg.

    A part that reports no duration at all, or a merged file that does, makes
    the comparison vacuous rather than lenient: two parts of 0.0 seconds used to
    "verify" against a 0.4 second output and their originals were deleted. Any
    zero is refused instead.
    """
    expected = sum(part_durations)
    tolerance = min(MERGE_TOLERANCE_MAX_S,
                    max(MERGE_TOLERANCE_S, MERGE_TOLERANCE_FRACTION * expected))
    if merged_s <= 0 or expected <= 0 or any(d <= 0 for d in part_durations):
        return False, expected, tolerance
    return abs(merged_s - expected) <= tolerance, expected, tolerance


def concat_list_line(path):
    """One line of the ffmpeg concat list for `path`, single-quoted.

    A single quote inside the path is written as '\\'' (close, escaped quote,
    reopen), the concat demuxer's own rule, so a name of any shape is safe.
    A well-formed name holds only letters, digits and underscores, but a
    ruled file keeps its real name, and the archive has names with "?" in them.
    """
    return "file '" + path.replace("'", "'\\''") + "'\n"


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
    # ffmpeg writes here, not to output_path. Stopping ffmpeg (a systemd stop, a
    # Ctrl-C, a reboot) makes it write its trailer and quit, so writing straight
    # to the final name left a readable SHORT file standing as the whole
    # recording: the next run called it a conflict, exited zero, and the stump
    # was ingested as the transect (2026-09-08). A dot name so nothing that
    # walks the folder mistakes it for a recording.
    # The real extension stays last: ffmpeg chooses its muxer from it, and
    # ".MP4.merging" leaves it unable to choose one at all.
    stem, ext = os.path.splitext(output)
    partial_path = os.path.join(folder, f".{stem}{MERGE_PARTIAL_SUFFIX}{ext}")
    # A partial left by a run that was stopped is stale by definition: this run
    # is about to write the same join. Removed rather than appended to, so a
    # folder cannot accumulate half-merges.
    if os.path.exists(partial_path):
        os.remove(partial_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, "w") as fh:
            for path in paths:
                fh.write(concat_list_line(os.path.abspath(path)))
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", partial_path],
            check=True,
        )

    # ffmpeg's exit code says the command ran, not that the output holds every
    # frame that went in: a corrupt tail in one part can be recovered from with
    # a warning and a short file. The parts are irreplaceable field recordings,
    # so nothing is deleted until the durations agree.
    merged_s = _duration(partial_path)
    ok, expected, tolerance = verify_durations(merged_s, part_durations)
    # The join takes the recording's name only once it has been checked against
    # the parts. os.replace is atomic within a folder, so there is no moment at
    # which a half-written file carries that name.
    os.replace(partial_path, output_path)

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
    # On content, not existence: opening in append mode below CREATES the file,
    # so a run killed during its first merge left prep_log.csv at zero bytes and
    # every later run appended data rows under no header at all. csv.DictReader
    # then read the first data row as the field names and the merged deposit saw
    # no merges, reported "nothing to copy", and the joined recording was
    # deleted with the staged folder (2026-09-08).
    is_new_log = not os.path.exists(log_path) or os.path.getsize(log_path) == 0
    with open(log_path, "a", newline="") as log_fh:
        writer = csv.DictWriter(log_fh, fieldnames=LOG_FIELDS)
        if is_new_log:
            writer.writeheader()
            log_fh.flush()  # before the first merge, which can take minutes

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


# --- the command line ------------------------------------------------------------

def _print_plan(actions):
    for action in actions:
        inputs = ", ".join(action["inputs"])
        print(f"{action['action']:>8}  {inputs}  ->  {action['output']}  ({action['reason']})")


def unread_video_names(folder, variants=(), rulings_by_name=None):
    """Names in `folder` that look like video files and that `parse_video_name`
    could not read, even with `variants` admitted.

    Parameters:
        folder: a directory of raw video files.
        variants: name variants to admit, as `plan()` takes them.
        rulings_by_name: rulings that apply here, as `plan()` takes them. A
            ruled file is never unread: a catalog_as file parses through its
            ruling and a leave_out file was ruled out on purpose.

    Returns:
        A sorted list of filenames. Empty means every video file in the folder
        parsed, so an empty plan really does mean there is nothing to do.

    This separates the two reasons `plan()` can come back empty. A staging
    folder holding 69 recordings whose names carry a season's own token is not
    a folder with nothing to do; before 2026-09-08 both cases printed the same
    line and exited zero, and the Carousel recorded a merge that never ran.
    """
    rulings_by_name = rulings_by_name or {}
    unread = []
    for name in folder_file_names(folder):
        if name.startswith("."):
            continue  # a merge in progress, or any other control file
        if _ext(name).lower() in NON_VIDEO_EXTENSIONS or not _ext(name):
            continue
        if name in rulings_by_name:
            continue
        if parse_video_name(name, variants) is None:
            unread.append(name)
    return unread


def _refuse_silent_no_op(folder, variants, rulings_by_name):
    """Print why an empty plan is wrong and exit nonzero, or return if it is right.

    Called only when `plan()` returned no actions. Exits with
    EXIT_NOTHING_RECOGNIZED when the folder holds video files this tool cannot
    read; returns quietly when the folder genuinely holds no video files (a
    folder holding only ruled-out files is one of those).
    """
    unread = unread_video_names(folder, variants, rulings_by_name)
    if not unread:
        return
    shown = ", ".join(unread[:UNREAD_NAMES_SHOWN])
    more = f" and {len(unread) - UNREAD_NAMES_SHOWN} more" if len(unread) > UNREAD_NAMES_SHOWN else ""
    admitted = ", ".join(variants) if variants else "none"
    print(f"{len(unread)} video file(s) in {folder} do not parse as TCRMP 3D video names "
          f"(name variants admitted: {admitted}): {shown}{more}")
    print(f"Nothing was merged or renamed. Re-run with --name-variant "
          f"({'|'.join(NAME_VARIANTS)}) if this season uses one, or fix the names.")
    sys.exit(EXIT_NOTHING_RECOGNIZED)


def _rulings_for_run(path, folder):
    """The rulings at `path` that apply to `folder`, as {file name: ruling}; {} with no path.

    A path with no file at it applies no ruling and says so in one line, so
    the Carousel's log tail shows why a ruled file was left unread. Exits
    with EXIT_RULINGS_UNREADABLE, after one plain line, when the path is a
    folder or the file is empty or malformed: nothing is merged or renamed
    then.
    """
    if not path:
        return {}
    if not os.path.exists(path):
        print(f"no rulings file at {path}: no ruling applied (a well-named season needs none; "
              f"a ruled file in this folder is reported as unread below)")
        return {}
    try:
        rulings = load_rulings(path)
        by_name = rulings_for_folder(rulings, folder)
    except RulingsError as exc:
        print(f"Nothing was merged or renamed: {exc}")
        sys.exit(EXIT_RULINGS_UNREADABLE)
    names = folder_file_names(folder)
    ruled_in, ruled_out = ruled_in_names(names, by_name), ruled_out_names(names, by_name)
    print(f"{len(rulings)} ruling(s) read from {path}: {len(ruled_in) + len(ruled_out)} apply to files "
          f"in this folder ({len(ruled_in)} catalogued by ruling, {len(ruled_out)} ruled out)")
    for name in ruled_out:
        print(f"ruled out (left untouched): {name} ({ruling_sentence(by_name[name])})")
    return by_name


def main():
    parser = argparse.ArgumentParser(description="Plan and apply video prep (merge parts, normalize names) before atlas ingest.")
    parser.add_argument("folder", help="Season folder of raw videos, e.g. 2024_pbl")
    parser.add_argument("--apply", action="store_true", help="Carry out the plan (default is dry run: print only)")
    parser.add_argument("--log", default=None, help="Log CSV path (default: <folder>/prep_log.csv)")
    parser.add_argument("--keep-parts", action="store_true",
                         help="Never delete a merge's source parts, even when the merged "
                              "duration verifies against them")
    parser.add_argument("--name-variant", action="append", default=None, choices=NAME_VARIANTS,
                        help="Admit a season's name variant in place of the standard _3D_ token, "
                             "repeatable. The 2023 annual and 2024 spring seasons name every file "
                             "_demo_ or _3ddemo_; without this flag those files are not recognized "
                             "and their parts are never merged. Same flag, same tokens, as "
                             "atlascatalog.py.")
    parser.add_argument("--rulings", default=None, metavar="PATH",
                        help="Lauren's catalog rulings CSV (the registry's catalog_rulings.csv). A file "
                             "ruled catalog_as is prepped as if named TCRMP{date}_3D_{label}_{T#}[_{part}]; "
                             "a file ruled leave_out is left untouched. A ruling applies only to a file "
                             "of that name in a folder named as the ruling's NAS folder. Without this "
                             "flag, or with a path no file is at, no ruling is applied; an empty or "
                             "malformed file stops the run.")
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    log_path = args.log or os.path.join(folder, "prep_log.csv")
    variants = tuple(args.name_variant or ())
    rulings_by_name = _rulings_for_run(args.rulings, folder)

    actions = plan(folder, variants, rulings_by_name)
    if not actions:
        print(f"No recognizable TCRMP 3D video names found in {folder}")
        _refuse_silent_no_op(folder, variants, rulings_by_name)
        return

    _print_plan(actions)

    # Names in this folder the parser could not read, said every time and not
    # only when the plan came back empty. A folder of fifty good files and one
    # unreadable one used to report nothing at all about the one, and the
    # unreadable file is often the missing part of a recording that IS being
    # acted on (2026-09-08).
    unread = unread_video_names(folder, variants, rulings_by_name)
    if unread:
        shown = ", ".join(unread[:UNREAD_NAMES_SHOWN])
        more = f" and {len(unread) - UNREAD_NAMES_SHOWN} more" if len(unread) > UNREAD_NAMES_SHOWN else ""
        print(f"{len(unread)} video file(s) here do not parse as TCRMP 3D video names and were not "
              f"read at all: {shown}{more}. If one of them is a part of a recording above, that "
              f"recording is not whole.")

    conflicts = [a for a in actions if a["action"] == "conflict"]

    if args.apply:
        apply(folder, actions, log_path, keep_parts=args.keep_parts)
        print(f"Applied {len(actions)} action(s). Log: {log_path}")
        print("Any row logged as 'merge unverified' kept its parts: compare that merged file "
              "against them before deleting anything.")
    else:
        print("Dry run only. Re-run with --apply to carry out this plan.")

    # Last, so the actions that WERE safe have already been carried out and
    # logged. A conflict or an unread name means this folder is not prepped, so
    # the season stops here rather than being ingested half ready.
    if conflicts:
        print(f"{len(conflicts)} conflict(s) found; resolve by hand before re-running "
              f"(see the reasons above). Nothing about a conflict was changed on disk.")
        sys.exit(EXIT_CONFLICTS)
    if unread:
        sys.exit(EXIT_NOTHING_RECOGNIZED)


if __name__ == "__main__":
    main()
