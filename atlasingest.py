"""Deterministic, rerunnable ingest of a prepared video folder into the TCRMP 3D registry.

Run `atlasprep.md`'s procedure first so the folder holds exactly one standard-
named video per site/transect/date (`TCRMP{YYYYMMDD}_3D_{SITE}_{T#}.{ext}`; see
`prep_tools.py`). `ingest_folder()` then, for every file in the folder: skips
anything that does not parse as a TCRMP 3D video name, skips a parsed name that
still carries a part suffix (merge it first), otherwise runs `ffprobe` for size,
duration, container, and codec, derives the readable id and season token from
the name, and creates or updates the matching registry row. Rerunning on an
unchanged folder produces no new events; rerunning after an operator edit
(season token, readable id, process, or notes) leaves that edit alone. Ingest
IS the authority on where a video currently sits, though: every run does a
second, unprotected update of `video_location` / `video_location_verified` to
the folder just ingested, so a rerun after the folder moves picks up the new
location even for an existing row.

Non-video files sitting in the folder (`prep_log.csv`, `atlasprep.md`, stray
`.txt`/`.log` notes) are ignored outright; anything else whose name does not
parse is only reported as a skipped/unparsed file if `ffprobe` confirms it is
actually a video (so junk files with a video-like extension are ignored too,
not listed as noise). A file whose name DOES parse but is corrupted or
truncated (ffprobe fails to open it) is reported skipped with the ffprobe
error and does not stop the rest of the folder from ingesting.

CLI: `python3 atlasingest.py <folder> [--dry-run]`.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
import naming3d
import registry

REVIEW_INSTRUCTION = (
    "Open the atlas (module tile TCRMP 3D atlas) and REVIEW each new row: check the readable id and "
    "season token, edit in place if the survey season is wrong, uncheck process for lit/unlit "
    "duplicates and bad takes."
)

# Extensions that never hold video, so they never need an ffprobe round trip
# to rule out: prep_log.csv, atlasprep.md, and any stray text notes sitting
# in the season folder are ignored before we even try to parse their name.
NON_VIDEO_EXTENSIONS = {".csv", ".txt", ".md", ".log"}


def probe(path):
    """Run ffprobe on `path`, returning {"size_gb", "duration_s", "container", "codec"}."""
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=size,duration,format_name",
         "-show_entries", "stream=codec_name,codec_type",
         "-of", "json", path],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    fmt = data.get("format") or {}
    video_streams = [s for s in (data.get("streams") or []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise RuntimeError(f"ffprobe found no video stream in {path}")
    container = (fmt.get("format_name") or "").split(",")[0]
    codec = video_streams[0].get("codec_name", "")
    size_gb = round(int(fmt.get("size", 0)) / 1e9, 3)
    duration_s = round(float(fmt.get("duration", 0.0)), 1)
    return {"size_gb": size_gb, "duration_s": duration_s, "container": container, "codec": codec}


def _looks_like_video(path):
    """True if `ffprobe` can open `path` and finds at least one video stream."""
    try:
        probe(path)
    except (subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError):
        return False
    return True


def _short_error(exc):
    """One short line describing a probe() failure, for a skip reason."""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        if stderr:
            return stderr.splitlines()[-1][:200]
    return str(exc)[:200]


def ingest_folder(folder, actor="ingest", dry_run=False):
    """Ingest every video in `folder` into the registry. Returns one result dict per
    file: {"file", "readable_id", "status" ("created"|"updated"|"skipped"), "reason"}.
    `dry_run=True` computes and reports everything but never writes to the registry.
    """
    folder = os.path.abspath(folder)
    results = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue

        ext = os.path.splitext(name)[1].lower()
        if ext in NON_VIDEO_EXTENSIONS:
            continue

        parsed = naming3d.parse_video_name(name)
        if parsed is None:
            if not _looks_like_video(path):
                continue
            results.append({"file": name, "readable_id": None, "status": "skipped",
                             "reason": "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}"})
            continue

        rid = naming3d.readable_id(parsed["site"], parsed["transect"], parsed["date"])
        if parsed["part"] is not None:
            results.append({"file": name, "readable_id": rid, "status": "skipped",
                             "reason": "merge parts first (see atlasprep.md)"})
            continue

        try:
            facts = probe(path)
        except (subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError) as exc:
            results.append({"file": name, "readable_id": rid, "status": "skipped",
                             "reason": f"ffprobe failed: {_short_error(exc)}"})
            continue

        existing = registry.get(rid)
        status = "updated" if existing is not None else "created"

        fields = {
            "site": parsed["site"],
            "transect": parsed["transect"],
            "year": parsed["date"][:4],
            "season_token": naming3d.season_token(parsed["date"]),
            "original_videos": name,
            "video_size_gb": facts["size_gb"],
            "video_duration_s": facts["duration_s"],
            "video_format": f"{facts['container']}/{facts['codec']}",
        }
        if existing is None:
            fields["ingested_at"] = registry.now()

        if not dry_run:
            # Identity + ffprobe facts: protect operator-edited cells (season
            # token, readable id, process, notes) if this row already exists.
            registry.upsert(rid, fields, actor, protect_operator=True)
            # Location is not operator-owned: ingest is the authority on
            # where the video currently sits, so this always overwrites it.
            registry.upsert(rid, {"video_location": folder, "video_location_verified": registry.now()},
                             actor, protect_operator=False)

        results.append({"file": name, "readable_id": rid, "status": status, "reason": ""})

    return results


def _print_table(results):
    if not results:
        print("No files found.")
        return
    header = ("file", "readable_id", "status", "reason")
    rows = [header] + [(r["file"], r["readable_id"] or "-", r["status"], r["reason"]) for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(header))))


def main():
    parser = argparse.ArgumentParser(
        description="Deterministic, rerunnable ingest of a prepared video folder into the TCRMP 3D registry.")
    parser.add_argument("folder", help="Prepared season folder of standard-named videos, e.g. 2024_annual")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute and report what would happen without writing to the registry")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN: nothing will be written to the registry.\n")

    results = ingest_folder(args.folder, actor="ingest", dry_run=args.dry_run)
    _print_table(results)
    print()
    print(REVIEW_INSTRUCTION)


if __name__ == "__main__":
    main()
