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
- Multi-part recordings, in three spellings: a bare numeric suffix
  (`TCRMP20240412_3D_MRS_T1_1.MP4`, `_2.MP4`, `_3.MP4`, ...), a literal
  `part` word before the digits (`TCRMP20240412_3D_MRS_T1_part1.MP4`,
  `_part2.MP4`), and the short `pt` word (`_pt1.MP4`, `_pt2.MP4`, added
  2026-09-08 because the archive uses it). All three are recognized and
  merged in numeric part order, and only when the numbers run 1 to N with
  no gap and no repeat; a set with a gap or a repeat is a `conflict` row.
  A part-numbered file with no other part of its recording beside it is the
  whole recording, by Lauren's decision of 2026-09-11 ("if that's it then
  that's it"): `prep_tools.py` renames it to the standard name whatever its
  number and writes the part number into the `prep_log.csv` reason
  (`lone part N: ...`), so the fact is never lost. Before 2026-09-11 such a
  file was refused, which paused four transects for one Meri Shoal
  part1 whose only sibling has an unreadable name.
- Parts of one recording filed in more than one folder. On the Archive the
  catalog (`atlascatalog.py`) treats a part set whose files sit in more than
  one folder as one recording when the set is complete across those folders
  (every file part-numbered, the numbers exactly 1 to N, no repeat, no whole
  file and no proxy among them) and the folders are one folder and the
  folders inside it (Lauren, 2026-09-14, for the three Fish Bay transects of
  2024-02-16 whose part 1 sits in the season folder and part 2 in its
  subfolder). Two folders that are not nested that way, say two season
  folders on one Archive volume, are never joined even when their numbers
  complete each other: the catalog's default run walks whole volumes, and
  the nesting rule is what keeps a join inside one season folder. The
  Carousel copies each part from its own folder into one Workbench season
  folder, so by the time `prep_tools.py` sees them they sit together and
  are merged exactly as a set from one folder is, and the `prep_log.csv`
  merge row lists every part it joined. The accepted downside: two takes
  filed in one season folder and a folder inside it whose part numbers
  happen to complete each other are joined as one recording. The catalog
  writes `parts in K folders: <folders>` on every sidecar line of such a
  row, each folder spelled from the top folder's own name (`TCRMP_2024_PBL;
  TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos`, the same words however
  the catalog was run), so the ATLAS shows it; read that note before
  trusting a merge whose parts came from more than one folder. Anything
  that spans folders without being a complete set nested that way stays
  refused as a split part set.
- Fully lowercase names, e.g. `tcrmp20250414_3d_mrs_t1.avi`.
- Spaces in place of underscores, or extra whitespace around tokens.
  `parse_video_name` does not accept these; a file named this way is left
  out of the plan entirely, not auto-normalized. Fix the spacing by hand
  (rename to use underscores) before running `prep_tools.py`.
- Mixed containers: the same corpus holds `.MOV`, `.MP4`, `.MKV`, and `.AVI`
  files across different seasons. `prep_tools.py` never changes a container;
  it only merges parts (via stream copy, no re-encode) and normalizes the
  name.

## Rulings: the files the name rule cannot place

Some archive files can never parse: a transect written twice (`_T1_again?`),
a label the name grammar does not know (`_T5OFAV`, `_T3lit`), a raw camera
name, a part set with a gap, a false start beside the real take. Lauren rules
on each one in the ATLAS, and the registry keeps her rulings in
`/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/catalog_rulings.csv` (one line
per NAS path: `nas_path, file_name, ruling, site_label, transect, date,
part, note, ruled_by, ruled_at`). The catalog applies them when it walks the
NAS, and `prep_tools.py --rulings <path>` applies the same rulings when the
files arrive on the Workbench under their real names:

- A file ruled `catalog_as` is prepped as if it were named
  `TCRMP{date}_3D_{site_label}_{transect}[_{part}].{ext}`: it groups, merges
  and renames exactly like a well-named file. The file keeps its real name in
  `prep_log.csv`'s `inputs` column, and the row's reason begins `by ruling of
  <who> <when>: <note>` (the `when` in AST), followed by every ruled part
  with the number its ruling gives it. So the six Castle transect 5 files of
  2024-03-11, on the archive as parts 1, 2, 3, 4, 6 and 7 with part 5
  missing, are ruled parts 1 to 6 of `CSTSORT` and merge into
  `TCRMP20240311_3D_CSTSORT_T5.MP4` in that order; the three
  `GKT_EXTRA_T1` files merge into `TCRMP20240408_3D_GKTEXTRA_T1.MP4`; and
  `TCRMP20241112_3D_CST_T5OFAV_Proxy.MOV` is renamed
  `TCRMP20241112_3D_CST_T5.MOV`. A site label such as `CSTSORT` or `CRBLIT`
  names a separate project ("to sort"), and its processing folder is
  `{LABEL}_{T#}_3D` like any other.
- A file ruled `leave_out` is not read at all: no group, no action, no
  "unread" complaint. It stays exactly where it is and the run lists it as
  `ruled out (left untouched)`.
- A ruling applies to a file only when the file name matches and the last
  segment of the ruling's NAS folder equals the season folder's own name
  (`TCRMP_2024_PBL`, `TCRMP_2024Annual_03`). That is how the Carousel names
  a staged season on the Workbench, so a hand-made folder must be named the
  same way for rulings to apply. A ruling whose file is not in the folder is
  simply unused.
- A rulings path with no file at it applies no ruling, the reading the
  registry library and the catalog give a missing sidecar, and the run says
  so in one line (`no rulings file at <path>: no ruling applied`). So a
  registry checkout without `catalog_rulings.csv` still preps a well-named
  season exactly as before, and a ruled odd file there is reported as unread
  like any other, never passed in silence. A rulings file that is empty,
  lacks the header, or holds a line that is not a ruling, or a path that is a
  folder, stops the run before anything is merged or renamed (exit code 4,
  one plain line naming the file and the line). Without `--rulings` no
  ruling is applied and the tool behaves exactly as before.

The Carousel's ingest stage passes `--rulings` with the registry's own file
on every run. The running Carousel loaded that stage's code when it started,
so a change to it takes effect at the next Carousel restart, at a batch
boundary; the rulings file itself is read fresh by every run.

## The procedure

1. Make one folder per season, named `{YYYY}_annual` or `{YYYY}_pbl`
   (`2024_annual`, `2025_pbl`), and put that season's raw video exports in it.
   A folder that holds ruled files must instead carry the NAS season folder's
   own name (see Rulings, above), or the rulings will not apply to it.
2. Run `python3 prep_tools.py <folder>` with no flags. This is a dry run: it
   prints the plan (one line per output file: `merge`, `rename`, `keep`, or
   `conflict`, the input file(s), the output name, and the reason) and
   changes nothing on disk. A `conflict` row means a canonical file already
   sits on disk alongside leftover parts for the same timepoint, or a part
   set's numbers have a gap or a repeat; resolve it by hand (see Never do)
   before re-running. Add `--rulings
   /mnt/rip/vicarius_drive/vicarius/_METADATA/3d/catalog_rulings.csv` when
   the folder holds files Lauren has ruled on; the run then says how many
   rulings it read and which files they cover.
3. Read the plan. If it looks right, run it again with `--apply` and the
   same flags: `python3 prep_tools.py <folder> --apply`. This carries out every merge and
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
3. Show the plan to Lauren: the merges, the renames (a lone part renamed
   as the whole recording is one of these; its reason names the part
   number), any conflicts (a canonical file already sitting alongside
   leftover parts, or a part set with a gap or a repeat), and anything
   that did not match the standard name pattern at all. Names with spaces
   or unexpected tokens are left out of the plan; list them and ask Lauren
   rather than guessing what they should be renamed to. If she has already
   ruled on them in the ATLAS, run again with `--rulings` pointing at the
   registry's `catalog_rulings.csv` and show her the ruled actions instead.
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
  inventing a name for it. Her answer is a ruling, recorded in the ATLAS and
  applied through `--rulings`, never a hand rename on the Workbench.
