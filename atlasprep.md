# atlasprep: preparing a raw video folder before atlas ingest

## Purpose

`atlasingest.py` expects exactly one video file per site, transect, and survey
date. Field exports rarely arrive that way: a camera that stops and restarts
mid-dive produces two or three part files for one timepoint, export tools
write a low-resolution `_Proxy` mirror alongside the full file, and file
names pick up whatever case and separators the exporting software felt like
using that day. `prep_tools.py` reads a season folder, works out which files
belong to the same timepoint, and produces one correctly named file per
timepoint before that folder is handed to ingest. Doing this prep as a
separate, inspectable step means ingest itself can assume the one-file-per-
timepoint rule always holds, and means every merge and rename is written to a
log an operator can check before the originals are gone.

## The standard name

```
TCRMP{YYYYMMDD}_3D_{SITE}_{T#}.{ext}
```

Example: `TCRMP20240412_3D_MRS_T1.MP4`. `YYYYMMDD` is the eight-digit survey
date, `SITE` is the uppercase site code, `T#` is the uppercase transect token
(`T1`, `T2`, `T10`, ...), and `{ext}` is the original file extension carried
through unchanged, including its original case (`.avi` stays `.avi`, `.MOV`
stays `.MOV`). A prepared folder holds exactly one file matching this pattern
per site, transect, and date; no `_Proxy`, no `_1`/`_2`/`_part` suffix, no
duplicate timepoint.

## Known variants seen in the corpus so far

- `_Proxy` and `_PROXY` suffixes (and any other case) marking a low-resolution
  mirror export, e.g. `TCRMP20241018_3D_MRS_T1_Proxy.MKV`.
- Multi-part recordings, in two spellings: a bare numeric suffix
  (`TCRMP20240412_3D_MRS_T1_1.MP4`, `_2.MP4`, `_3.MP4`, ...) and a literal
  `part` word before the digits (`TCRMP20240412_3D_MRS_T1_part1.MP4`,
  `_part2.MP4`). Both spellings are recognized and merged in numeric part
  order.
- Fully lowercase names, e.g. `tcrmp20250414_3d_mrs_t1.avi`.
- Spaces in place of underscores, or extra whitespace around tokens.
  `parse_video_name` does not accept these; a file named this way is left
  out of the plan entirely, not auto-normalized. Fix the spacing by hand
  (rename to use underscores) before running `prep_tools.py`.
- Mixed containers: the same corpus holds `.MOV`, `.MP4`, `.MKV`, and `.AVI`
  files across different seasons. `prep_tools.py` never changes a container;
  it only merges parts (via stream copy, no re-encode) and normalizes the
  name.

## The procedure

1. Make one folder per season, named `{YYYY}_annual` or `{YYYY}_pbl`
   (`2024_annual`, `2025_pbl`), and put that season's raw video exports in it.
2. Run `python3 prep_tools.py <folder>` with no flags. This is a dry run: it
   prints the plan (one line per output file: `merge`, `rename`, `keep`, or
   `conflict`, the input file(s), the output name, and the reason) and
   changes nothing on disk. A `conflict` row means a canonical file already
   sits on disk alongside leftover parts for the same timepoint; resolve it
   by hand (see Never do) before re-running.
3. Read the plan. If it looks right, run it again with `--apply`:
   `python3 prep_tools.py <folder> --apply`. This carries out every merge and
   rename in the plan. Merges use the ffmpeg concat demuxer with `-c copy`
   (no re-encode); if the parts to be merged report different codecs via
   `ffprobe`, `prep_tools.py` raises an error naming the mismatched files
   instead of merging them silently.
4. Open `<folder>/prep_log.csv` and check every merge, rename, and conflict
   row (columns: `timestamp, action, inputs, output, reason`). This is the
   record of what happened to the originals; keep it with the folder. A
   logged `conflict` row means no file was touched for that timepoint. It
   still needs a human decision.
5. Run `ffprobe` on every file left in the folder. All must open cleanly
   (`ffprobe -v error <file>` prints nothing and exits 0). A file that fails
   to open needs attention before ingest, merged or not.
6. Hand the folder to `atlasingest.py`.

## What a Claude agent does when a new video folder is shown

1. Read this file (`atlasprep.md`) before touching anything.
2. Run `python3 prep_tools.py <folder>` (no `--apply`) and read the plan
   output.
3. Show the plan to Lauren: the merges, the renames, any conflicts (a
   canonical file already sitting alongside leftover parts), and anything
   that did not match the standard name pattern at all. Names with spaces
   or unexpected tokens are left out of the plan; list them and ask Lauren
   rather than guessing what they should be renamed to.
4. On her yes, run `python3 prep_tools.py <folder> --apply`, then open
   `prep_log.csv` and confirm every row matches what was shown. Every merge
   row records the merged duration, the summed part durations, and whether
   the parts were deleted.
5. If any row reads `merge unverified`, stop. The parts are still on disk;
   report the two durations to Lauren and let her decide, rather than
   deleting anything.
6. Run ingest (`atlasingest.py`) only after the log is checked and every file
   opens under `ffprobe`.

## Never do

- Never rename individual frames or any file inside a frames/processing
  folder; `prep_tools.py` only touches raw video files at the top level of
  the season folder that match a TCRMP 3D video name.
- Never delete the original part files by hand. `prep_tools.py` deletes them
  itself, as the last step of an `--apply` merge, and only once it has
  verified the merge: it probes the merged file's duration and compares it to
  the sum of the parts' durations, and deletes only when the two agree to
  within the larger of half a second and two percent. A mismatch keeps every
  part, logs the row as `merge unverified` with both numbers, and leaves the
  merged file in place for a person to inspect. `--keep-parts` turns the
  deletion off entirely. These are field recordings of a transect at a point
  in time; they cannot be re-shot.
- Never guess a survey date, site, or transect for a file that does not match
  the standard name pattern or one of the known variants above. A file
  `plan()` cannot parse is left out of the plan; ask Lauren rather than
  inventing a name for it.
