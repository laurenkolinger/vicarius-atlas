# TCRMP 3D Atlas (`tcrmp_3d_atlas`)

A live, editable view of every TCRMP 3D transect timepoint in the platform
registry: one row per site, transect, and season, showing identity, ingest
facts, processing progress, scale, manual-edit and step 2 status, locations,
and sizes, refreshed from the registry on every request.

- Tags: TCRMP, 3D | Version: 2.0.0 | Status: active resource | Owner: Lauren K Olinger
- Repo: `/mnt/rip/vicarius_drive/vicarius/modules/tcrmp_3d_atlas/github_repo`
- Registry it reads and writes: `/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/` (contract: [`../../../_METADATA/3d/README.md`](../../../_METADATA/3d/README.md))

## What it shows

The atlas is a page, not a build. There is no `status.json` to regenerate and
no server to launch: it is a native Flask blueprint inside `vicarius_ui_os`
(`vicarius_ui_os/atlas_views.py`) that reads
`vicarius/_METADATA/3d/registry.py` on every request, so what is on screen is
the registry's current state, not a snapshot from the last time someone
remembered to rebuild it.

Open it from the desktop's TCRMP folder (the "ATLAS" tile) or directly at
`/atlas`. Two views:

- **Table** - one row per transect timepoint, every registry column grouped
  into collapsible sections (video ingest, identity, step 1, scale, manual,
  step 2, locations, params). The process checkbox and the readable id stay
  pinned to the left regardless of which groups are collapsed. Long values
  truncate with a click-to-view popover; status cells are colored and a
  running row's stage cell pulses.
- **Matrix** - one row per site and transect, one column per timepoint
  (year + season token, `_pbl` before `ann`), each cell showing the step
  status, the scale flag, and the processing folder size, so a survey gap
  shows as an empty cell at a glance. A header row above the grid carries
  the Start manual edit button and its sidecar (see below).

A header strip carries the tallies, "need attention" first, then timepoints,
step 1 done/running/not started/failed, awaiting edit, manual scale, active
runs, total processing and output GB, and total video GB (hovering the video
GB figure shows the per-season breakdown, rows and median GB per row, newest
season first), and a "registry read HH:MM:SS" stamp updated on every
successful poll (rows every 15 s, skipped while the tab is hidden or a field
is mid-edit). "Need attention" counts the lines in
`catalog_needs_attention.csv`, the file the catalog (below) rewrites whole on
every run.

## Ingest: getting videos into the registry

Ingest is a two-step, script-run process, not something the atlas page does
for you. It happens before a timepoint ever shows up here.

1. **Prep** ([`atlasprep.md`](atlasprep.md), run through `prep_tools.py`) -
   a raw season folder rarely holds exactly one video per timepoint: a camera
   that stops and restarts mid-dive leaves part files, export tools add a
   `_Proxy` mirror, and file names pick up whatever case and separators the
   exporting software used. `prep_tools.py` reads the folder, plans one
   `merge`/`rename`/`keep` action per output file down to the standard name
   `TCRMP{YYYYMMDD}_3D_{SITE}_{T#}.{ext}`, and (with `--apply`) carries it
   out, merging parts with `ffmpeg`'s concat demuxer and logging every action
   to `prep_log.csv`. Dry run by default: `python3 prep_tools.py <folder>`;
   carry it out with `python3 prep_tools.py <folder> --apply`. A merge deletes
   its source parts only after the merged duration is checked against the sum
   of the parts' durations, to within the larger of half a second and two
   percent: a mismatch keeps every part and logs the row as
   `merge unverified` with both numbers, because these recordings cannot be
   re-shot. `--keep-parts` turns the deletion off entirely.
2. **Ingest** (`atlasingest.py`) - once a folder holds one standard-named
   video per timepoint, `python3 atlasingest.py <folder> [--dry-run]` runs
   `ffprobe` on each file, derives the readable id and season token from the
   name, and creates or updates the matching registry row (size, duration,
   container/codec, `ingested_at`, `video_location`). Rerunning on an
   unchanged folder writes no new events. Ingest is the authority on where a
   video currently sits: `video_location` is refreshed on every run even for
   an existing row, but identity cells an operator has already hand-corrected
   (season token, readable id, process, notes) are left alone
   (`protect_operator=True`). A row whose readable id was corrected in the
   atlas after an earlier ingest is found again by its `original_videos` file
   name rather than by the id recomputed from that name, so re-ingesting the
   folder updates that row instead of resurrecting the old id as a duplicate.

After ingest, the script prints a reminder to open the atlas and review each
new row before the timepoint is processed: check the readable id and season
token, correct them in place if the survey season is wrong, and uncheck
`process` for lit/unlit duplicates and bad takes.

## Catalog: walking the Archive into registry rows before any transfer

`atlascatalog.py` is a read-only reconnaissance step over the NAS Archive,
separate from ingest above and run before it. It lists, parses, and records;
it never transfers a file. It reads the same NAS credentials and config the
carousel driver module uses (`driver/github_repo/config/nas.yaml`: host,
user, key, `source_roots`) and talks to the NAS only through plain ssh
LISTING commands (a recursive `find`) -- no file is ever pulled down, and
nothing on the NAS is ever renamed. The Archive stays read-only end to end.

Two configured paths are never walked or catalogued, whatever source root
holds them: the Shelf (`defaults.shelf_root` in `nas.yaml`, where the
Carousel deposits processing folders) and the merged root (`merged_root`,
where merged recordings are deposited). The Shelf sits inside the source
root `/volume6/Archive8_12TB`, and on 2026-09-14 a dry run over
`source_roots` reported 61,216 deposited frames, scripts and byte-code
files as names that do not match, beside 46 other findings. Two layers keep
them out: the `find` prunes them on the NAS (one `-path` term per excluded
path under the root being listed, beside the Synology `@eaDir` and
`#recycle` prune), and `catalog()` drops anything still listed under one of
them (the run's counts report it as `excluded after listing`). A `--root`
at or under one of them is never listed: it appears in the needs-attention
review as `root is at or under the Shelf or the merged root, which the
catalog never walks`, naming the covering path. Comparison is by whole path
segment after a `146.226.147.140:` host prefix is stripped, so
`/volume6/Archive8` never excludes `/volume6/Archive8_12TB/x`. A `nas.yaml`
without those keys excludes nothing.

A processing folder is never read inside, whatever root holds it: a
directory named `processing` or `frames` (either case), one named
`{SITE}_{T#}_3D` (the transect processing folder `naming3d` spells, exact
case) or one ending `_3dprocessing` (the pre-2026-09-08 name, either
case). On 2026-09-14 an old Voyager 1 project on Archive9
(`/volume2/Archive9_10TB/TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing/frames/<id>/`)
made the dry run report 22 second recordings and would have moved 22 rows
of 2025 spring onto frame folders: a copy inside `frames/` parses like its
original and its volume sorts first. The same two layers apply: the `find`
prunes those directories on the NAS (one `-type d` group of name terms
beside the Synology prune), and `catalog()` drops anything still listed
under one, counted as `inside processing folders, dropped after listing`.
A `--root` inside one is never listed: it appears in the needs-attention
review as `root is inside a processing folder, which the catalog never
reads`, naming the folder. A regular file named `frames` is not a folder
and stays a name that does not match.

For each root it walks recursively (nested folders are expected
weirdness, not an error), it parses filenames with the same shared rule
ingest uses (`naming3d.parse_video_name`), then groups and classifies the
files itself (`_group_members`, `_classify_group`, below). A part set
whose files sit in more than one folder is one recording when it is
complete across those folders (every file part-numbered, the numbers
exactly 1 to N with no repeat, no whole file and no proxy among them) and
the folders are one folder and the folders inside it; any other shape
that spans folders is never merged by guess. The rule and its accepted
downside are under Classifying, below. The part-set grouping mirrors
`prep_tools`'s own rule (the spellings in `atlasprep.md`: a bare numeric
suffix, a literal `part` word and the short `pt` word) so a local merge and a remote catalog
group parts the same way, but the rule lives in `atlascatalog.py` itself
rather than being called from `prep_tools.py`, because an override, a name
variant or a ruling can change a file's identity before grouping; prep
applies the same name variants (`--name-variant`) and rulings (`--rulings`)
on the Workbench so both sides reach the same identity.

Each row records: the identity cells (`site`, `transect`, `year`,
`season_token`) from the parsed name, `original_videos` (every source file
in the set, `;`-joined per the registry's multi-part convention), the
Archive-facing `video_location` as `146.226.147.140:<absolute Archive
path>`, and `video_size_gb` summed from the sizes the ssh listing reports.
There is no ffprobe pass here -- the files are remote -- so duration and
container/codec facts are left blank until the real ingest runs. Every write
goes through `registry.upsert(rid, fields, actor="catalog",
protect_operator=True)`, and unlike ingest's own two-call pattern, the
catalog's single upsert call protects every operator column blanket,
`video_location` included: **once a video's `video_location` cell is
filled, the catalog will never correct it, even after a later parsing fix
moves that video's identity to a different row.** Rerunning against an
unchanged Archive changes nothing (idempotent, same as ingest).

Anything that does not parse or group cleanly is never guessed at: it is
reported instead, in the needs-attention review below, never written as a
row.

A file whose extension marks it as a non-video companion (`.csv`, `.txt`,
`.md`, `.log` -- `prep_log.csv`, `atlasprep.md`, stray notes) is excluded
silently before parsing, the same convention `atlasingest.py` uses: it never
becomes part of a row and never triggers a needs-attention entry, even when
its name shares a stem with a real video sitting next to it.

CLI: `python3 atlascatalog.py [--nas-config <path>] [--root <NAS season
root>]... [--overrides <path>] [--rulings <path> | --no-rulings]
[--name-variant demo|3ddemo]... [--dry-run]`.
With no `--root`, the season roots come from `--nas-config`'s `source_roots`
(default: the carousel's `driver/github_repo/config/nas.yaml`, whose
`defaults.shelf_root` and `merged_root` are the paths never walked; the run
prints them as `never walked:` before listing anything, then `never read
inside: processing folders` and which rulings file it read). `--rulings
<path>` reads the rulings from another file than `catalog_rulings.csv`
beside the registry; `--no-rulings` ignores them (every ruled file is then
read by its name alone). `--dry-run` computes and prints everything without
writing to the registry or the needs-attention report's underlying data.

### Site overrides

`catalog_overrides.csv` sits beside the registry (`file_name,site,note`; a
missing file means no overrides; `--overrides <path>` points elsewhere). It
relabels a file's parsed site before grouping, so two takes filmed at the
same site under different survey names can become two distinct rows: the
2025 spring season's two LBH takes become `LBHLBPFIX1` and `LBHLBPFIX2`
through six lines in that file. A site label must be letters and digits, a
blank file name or a name listed twice is refused (the message names both
lines), and an override naming a file the catalog never saw is reported as a
needs-attention line rather than silently ignored. An override on a file
that also has a ruling (below) is reported and not applied: the ruling
places the file.

### Rulings: the files the catalog cannot place

`catalog_rulings.csv` sits beside the registry (`registry.RULINGS_CSV`;
columns `nas_path, file_name, ruling, site_label, transect, date, part,
note, ruled_by, ruled_at`, one line per NAS path, written by the ATLAS
through `registry.set_ruling` and read by the catalog through
`load_rulings`). It holds Lauren's decisions on the files no name rule can
read: 26 of them on 2026-09-14. The catalog applies them on every run,
matching each listed file by host and path:

- `leave_out`: the file is dropped before parsing, counted as `files ruled
  out`, and never becomes a needs-attention line (the five lobster, boat
  and overhead clips, two raw camera files, a 17 s fragment and a 6.3 GB
  false start).
- `catalog_as`: the file is parsed as if it were named
  `TCRMP{date}_3D_{site_label}_{transect}[_{part}].{ext}`, so it groups,
  resolves and becomes a row, or a part of one, exactly like a well-named
  file: `TCRMP20241112_3D_CST_T5OFAV_Proxy.MOV` ruled `CST T5 20241112`
  is the row `CST_T5_2024ann`; `TCRMP20241113_3D_JKB_T2_2_Proxy.MOV`
  ruled `JKB T2 20241113` is `JKB_T2_2024ann` beside its `leave_out`
  false start; the three `GKT_EXTRA_T1` files ruled parts 1, 2, 3 are one
  row with three parts; the six Castle T5 files of 2024-03-11 (archive
  `_1` to `_4`, `_6`, `_7`) ruled parts 1 to 6 are `CSTSORT_T5_2024_pbl`
  with six parts in ruled order, part 5 of the archive being missing. The
  row's `original_videos` keeps the real file names (the Carousel pulls
  by them), each sidecar line keeps the real name and `nas_path`, its
  `part` is the ruling's, its `proxy` says whether the real name carries
  the suffix, and its `edit_note` opens with `catalogued by ruling of LO
  2026-09-14 16:26 AST: <note>`. A site label such as `CSTSORT`,
  `SPHSORT` or `CRBLIT` names a separate project, "to sort", exactly as
  the `LBHLBPFIX` labels did: its readable id follows the same rule
  (`CRBLIT_T3_2024ann` beside `CRBUNLIT_T3_2024ann`) and its processing
  folder is `{LABEL}_{T#}_3D`. A ruled file's real name is never parsed,
  so a `demo` token or a `?` in it never matters and no name variant is
  needed for it.

A ruling whose file no listed root contained is reported once, under the
first walked root that covers its path, as `ruled file not found; the
ruling names a file the listing does not contain`; a ruling under a root
this run did not walk (another season, a drive that would not mount) is
not reported, because the run learnt nothing about it. A line of the file
that cannot be read (a label with a slash, a date that is not a date, a
wrong header, a path ruled twice, the wrong number of fields) is skipped,
printed as one plain sentence before anything is listed (`skipped ruling
line 3 of <path>: site_label must be letters and digits only, got
'CST/T5'`) and reported as a needs-attention line under
`catalog_rulings.csv`; the other lines apply. The run's counts print
`rulings applied: N; files ruled out: M`.

The same rulings reach the Workbench: `prep_tools.py` takes `--rulings
<path>` from the Carousel's ingest step, so a ruled file that arrives under
its real odd name is grouped, merged and renamed by its ruling there too.

### Name variants: seasons whose files never carried the `_3D_` token

Two seasons on the hot NAS use `_demo_` or `_3ddemo_` in place of `_3D_` in
every file name (`_3D_` is otherwise a fixed part of the naming convention,
`TCRMP{YYYYMMDD}_3D_{SITE}_{T#}.{ext}`). `--name-variant demo` and
`--name-variant 3ddemo` (repeatable) tell the catalog to accept those tokens
as `3D`; a file catalogued this way is recorded exactly as it would have
been under the real convention, and its `source_files.csv` line's
`edit_note` says "name uses the demo token; catalogued as 3D" so the choice
stays visible after the fact. Without the flag those files are unparsed
names and go to needs attention instead.

### Classifying what one identity groups to

Every file that parses to the same project, date, site and transect is one
group. A group whose files sit in more than one folder is one recording
when two things hold together (Lauren, 2026-09-14, "fix the read so it can
merge across folders"; no set on the archive had this shape that day, the
Fish Bay 2024-02-16 case that prompted it turned out to be two versions of
each recording, ruled on by hand): taken together the files are a complete
part set (every one part-numbered, the numbers exactly 1 to N with no
repeat, no whole file and no proxy among them), and the folders are one
folder and the folders inside it (the folder of the shallowest part holds
or contains every other part). The second condition keeps the join inside
one season folder however the catalog was run: the default run walks
whole volumes (`source_roots` names `/volume2/Archive9_10TB`, not a season
folder), and a `_1` in `encoded/TCRMP_2024_PBL` beside a `_2` in
`backup/TCRMP_2024_PBL_old` on that volume is refused, as are two sibling
subfolders with no part in the folder above them. Such a row's
`video_location` is the folder of part 1, `original_videos` lists the
parts in number order, each sidecar line's `nas_path` is the file's own
folder, and every sidecar line of the row says `parts in K folders:
<folders>`, the folders in part order and each spelled from the top
folder's own name (`parts in 2 folders: TCRMP_2024_PBL;
TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos`), the same words whether a
volume or the season folder itself was walked, so the ATLAS shows that the
parts were gathered from more than one place. The Carousel copies each
part from its own `nas_path` into one Workbench season folder, so prep
joins them exactly as it joins a set from one folder. Any other group that
spans folders (a whole file or a proxy among the files, a gap, a repeat,
folders that are not nested that way) is never merged by guess and goes to
needs attention naming every folder ("part set split across directories;
resolve by hand"); when the numbers were complete and only the folders
failed, the detail says so ("complete as a part set, but the folders are
not one folder and the folders inside it").

The accepted downside, decided with the rule: two takes of one timepoint
filed in one season folder and a folder inside it whose part numbers
happen to complete each other (a `_1` in the season folder and a `_2` in
its subfolder that are really two recordings) are joined as one recording.
The folders note on every sidecar line is what makes such a join visible,
the sidecar keeps each part's Archive path, and `prep_log.csv` lists every
part a merge joined, so the join can be found and undone by hand. Per
group within one folder:

- parts plus any whole file is a canonical conflict (needs attention: "resolve by hand");
- one whole, non-proxy file plus one or more `_Proxy` mirrors is the row alone,
  with each proxy recorded `in_row=false` and a needs-attention line naming it
  ("proxy mirror beside its full file; resolve by hand");
- two or more whole files with no proxy or part relationship is "more than one
  whole file for one timepoint; resolve by hand";
- two or more parts whose numbers do not run 1 to N (a gap or a repeat) is
  "part numbers do not run 1 to N for one timepoint; the recording is not
  whole here, resolve by hand";
- a lone part, one part-numbered file with nothing else of its recording
  beside it, is the whole recording (Lauren, 2026-09-11): it becomes the row
  whatever its number, and its sidecar line keeps the number in `part` and
  says "lone part N; taken as the whole recording, prep renames it at pull
  time";
- anything else (a clean single file, or a part set in one directory) becomes
  the row with every member.

When the same readable id still results from more than one root after
overrides, the earliest take by date, then root, then path becomes the row;
every later take is reported ("second recording date for one timepoint;
review which take to keep") and its files recorded `in_row=false` rather than
overwriting the row silently.

### The per-file sidecar

Every file behind a row, in or out of it, gets one line in
`source_files.csv` (`readable_id, file_name, nas_path, size_bytes,
filmed_on, container, part, proxy, in_row, edit_note, recorded_at,
recorded_by`): `nas_path` is `<host>:<the file's own absolute dir>/<file
name>` (a part set that spans folders has each line on its own folder, not
the row's), `container` the lowercase extension, `filmed_on` the date parsed
from the name, and `edit_note` the sentences above joined in one fixed order
(the ruling sentence, site override, second recording, proxy mirror, parts
or lone part, the folders of a set that spans folders, proxy, demo token).
The atlas reads
this sidecar for the row's Source videos table in the detail row (below);
`in_row=false` lines still show there, dimmed, so an operator can see what
the catalog set aside without opening a shell.

### Reviewing what the catalog could not place

Anything left out of a row (an unparsed name, a canonical conflict, a
proxy mirror, a second recording, an override naming a file never seen, an
override on a ruled file, a ruling naming a file never seen, a ruling line
that could not be read, a root inside a processing folder, or a season root
that will not mount) is printed at the end of every run and, on a real run,
written to `catalog_needs_attention.csv` (`root, path, reason, detail`)
beside the registry: the lines of every root this run walked are replaced,
the lines of a root it did not walk are kept, and the lines under
`catalog_overrides.csv` and `catalog_rulings.csv` are replaced on every run
because every run re-evaluates every override and every ruling. The atlas
header strip's "need attention" tally is this file's line count, so the
number an operator sees on `/atlas` is a direct read of the same file the
catalog just wrote. Fixing an entry (ruling on the file in the ATLAS,
adding an override, renaming a file, moving a stray part into its set's
directory) and rerunning the catalog is the whole review loop: the catalog
is idempotent, so a rerun against an unchanged Archive writes nothing new,
and a fixed entry simply stops appearing in the next report.

The catalog's rows are deliberately incomplete: no duration, no
container/codec, and a `video_location` that can go stale. The full ingest
-- part merging with duration verification, ffprobe facts, on-disk renaming
to the standard name -- happens later, at pull time on the Workbench, once
the files are local, through the same `prep_tools.py` / `atlasingest.py`
pair documented above.

## Editing rules

Only the cells `registry.OPERATOR_COLUMNS` names are editable here:
`season_token`, `readable_id`, `process`, `notes`, `video_location`,
`processing_location`, `output_location`. Every other column is written by a
module (`3D_phase_1`, later step 2) or by ingest, and the table shows those
as read-only text.

- `season_token` is a select (`_pbl` / `ann`), and changing it is a rename,
  not a cell edit. The token is part of the readable id, and every consumer
  reads it back out of the id (sort order, the processing folder name, the
  psx range, the chunk label), so the API recomputes the id from the row's
  site, transect and year plus the new token and calls `registry.rename_id`.
  The confirm dialog names the new id first. Picking the token the row
  already carries is refused with a 400, a token that yields a malformed id
  with a 400, and a collision with an existing id with a 409.
- `process` is a checkbox; `video_location`, `processing_location`,
  `output_location`, and `notes` are inline text fields that save on change
  or Enter.
- `readable_id` has a RENAME control: type the new id and confirm. Renaming
  moves the registry row, its event history, and its snapshot directory to
  the new id, but does **not** rename anything already written to disk under
  the old id - folders, the psx, frame directories keep their existing
  names. The frames folder in particular keeps its old name, and step 1 looks
  for frames under the new id, so rename that folder by hand before
  reprocessing a renamed timepoint. A rename to a malformed id (wrong `{SITE}_{T#}_{year}{token}`
  shape) is refused with a 400 before it reaches the registry; a rename to an
  id that already exists is refused with a 409.
- Every edit posts `{readable_id, field, value, initials}` to
  `/atlas/api/edit`, which writes the cell through `registry.upsert` (or
  `registry.rename_id` for the id itself) and logs one event to
  `events.csv`. The actor recorded is `atlas:<initials>` - initials come from
  the desktop session when the shell provides them, otherwise from a one-time
  browser prompt stored locally.
- A rerunning ingest or module can never silently overwrite a hand-edit:
  every programmatic writer that expects to run repeatedly passes
  `protect_operator=True`, which skips an `OPERATOR_COLUMNS` cell once it
  already holds a non-empty value.
- **DELETE**, beside RENAME, is admin only: it appears next to a row's id
  only in an admin session and is disabled with a lock tooltip while the row
  is processing. Deleting asks for initials in a confirm dialog, then posts
  `{readable_ids, initials}` to `POST /atlas/api/delete`. The row and its
  sidecar lines move to `deleted_rows.csv` and each snapshot folder moves
  under `snapshots/_deleted/`; nothing is removed from disk. Every id is
  checked before any row is touched, so one unknown id or one row that is
  processing right now refuses the whole request unchanged: 423 while the
  atlas is locked, 403 without an admin session, 400 for a malformed body or
  blank initials, 404 for an unknown id, 409 for a running row.
- **Registry hygiene** (admin only, collapsed below the header tallies) adds
  Delete a season: choose a year and a season token and VICARIUS lists every
  row of that season, hidden rows included, before Delete these rows sends
  the same confirm-and-delete flow as a single-row DELETE, one id at a time
  in the list's order.
- `POST /atlas/api/mark` `{readable_ids, state (awaiting | editing | done),
  initials, manual_edit_done (the psx save time, done only)}` sets
  `manual_edit_status`, actor `manual_edit:<initials>`. This route is not on
  the table: only the Start manual edit module and Voyager tools call it, to
  move a row onto the edit bench, to done at certification, or back to
  awaiting on a cancelled job. The same id-checked-before-any-write and
  423/400/404/409 ladder applies.

### Known property: location cells are read back verbatim

`processing_location`, `output_location` and `video_location` are
operator-editable, and the detail routes read whatever path they hold: the
drawer reads that folder's `analysis_params.yaml` and `status.csv`, tails its
console logs and lists its contents, and `/atlas/api/report` serves a PDF
found under it. So anyone who can reach the atlas can point those routes at
any directory on this machine and read it back through the browser. Nothing
there writes, deletes or moves a file.

That is acceptable for the deployment this module has today: one operator, a
UI bound to this box on port 5090, the module lock-gated, and a person who can
already read their own files from a shell. It stops being acceptable the
moment the UI is reachable by a second person or from another machine. The
fix at that point is a `realpath` containment check against a configured list
of allowed roots, applied in `_live_detail`, `api_console` and `api_report`
in `vicarius_ui_os/atlas_views.py`. `snapshot_dir` has the same shape but is
not operator-editable, so reaching it needs a spreadsheet edit.

## The Now strip

Three collapsed blocks sit above the header tallies, each remembering its
open state per browser and rebuilt from files and the registry on every
load, so the strip survives closing the window, reopening it, and
restarting the desktop:

- **Voyager 1** - every active row (id, stage, elapsed, a FOLLOW LOG
  control) and a console tail of the followed row. Clicking a run jumps to
  and highlights its table row. The tail comes from
  `/atlas/api/console/<id>` (last 200 lines of the newest `console/*.log` in
  the processing folder) and the block reloads every 5 s while a run is
  active, 30 s otherwise. This replaces the earlier "ACTIVE PROCESSING"
  panel; the same data attributes carry it.
- **Manual edit** - the current Start manual edit job, when one is running:
  job id, phase, editor, the rows with their site names, the edit bench
  path, and the last twenty log lines.
- **Space** - one line per place (Workbench, edit bench, each Archive root,
  Shelf, merged root) with free GB and the time it was measured, from
  `GET /atlas/api/now`.

`GET /atlas/api/now` returns `{active, manual_edit, space}` and feeds all
three blocks plus the standalone progress window,
`GET /popout/voyager-progress` (the strip alone, refreshing every 5 s;
`vicarius progress` prints its URL and opens it with `xdg-open` when a
display exists).

## Focus and columns follow the step

A Focus select in the toolbar dims every row (and matrix cell) outside the
chosen set without hiding it, so a filtered row stays reachable and
editable: **All rows**, **Active** (rows in flight plus rows in the manual
edit job), **Manual edit** (rows in the job), **Needs attention** (rows
interrupted, failed, or awaiting an edit). A "Columns follow the step"
toggle, remembered per browser, expands the column group of whatever stage
is active right now and collapses the others (step 1 stages to the step 1
group, awaiting and editing to the manual group, step 2 stages to the step
2 group); clicking a group chip while it is on turns it off so a manual
choice always wins.

## The notices

A "Ready for manual editing" block sits above the Now strip, one line per
row with `manual_edit_status = awaiting`: the readable id, its processing
location, and a COPY PATH button. This is where step 1's manual gate
surfaces - a timepoint step 1 finished appears here so an operator knows a
transect is ready for the edit bench. The block is hidden entirely when
nothing is awaiting, and a row leaves it the moment a Start manual edit job
pulls it down (`manual_edit_status` becomes `editing`) or, failing that,
once certification sets it to `done`.

A row with `scale_status = MANUAL_NEEDED` carries its own badge in the table
(purple) that jumps straight to the scale numbers in the detail drawer, since
a mean scale error over threshold needs a person's judgment, not a rerun.
A row on the edit bench inside a manual edit job carries an "on edit bench"
chip and is greyed in the table (an outline in the matrix): Voyager 1 and
Voyager 2 leave it alone until the job is certified and pushed back to the
Shelf.

## Detail row

Clicking the chevron at the left of a table row opens a detail row beneath
it with six sections, each a two-column key and value block; a section with
nothing recorded shows one line, "No record yet.":

1. **Source videos** - the row's `source_files.csv` lines: file name, filmed
   on, size GB, container, part, in row, note, and the NAS path with a COPY
   PATH button; a file recorded `in_row=false` shows dimmed.
2. **Origin** - the site's full name with its code as a chip (from
   `site_codes.csv`; blank when the table cannot resolve the code), the
   video location, total GB, ingested at, format and duration when present,
   and the season's camera model and preprocessing (from `seasons.csv`).
3. **Voyager 1** - the run id as a chip linking to the exact parameter
   version's URL, the parameter version, step 0 and step 1 started,
   finished and seconds, frames extracted, the step 1 report PDF link
   (`/atlas/api/report/<id>`), and the console log path.
4. **Manual edit** - job id, editor, pulled at, certified at, the checks
   verdict and summary, edit seconds, and the pre-edit archive path.
5. **Voyager 2** - reserved facts (ortho GB, mesh triangles, ortho and
   export seconds, DEM GB, report), shown once Voyager 2 writes them.
6. **Vigil** - reserved facts (colonies, processed at, by), shown once
   Vigil writes them.

Every value above the row's own registry columns comes from
`row_facts.csv` through `GET /atlas/api/facts/<id>` (or the `facts` key
already carried in the rows payload); nothing here is computed live from a
folder the way the id-click detail drawer below is.

## Matrix and Start manual edit

The matrix view's header row carries the status legend at the left and a
**Start manual edit** button at the right. It enables once at least one row
has `step1_status` complete, `manual_edit_status` awaiting, and no manual
edit job is already running; with a job running it instead reads "Manual
edit in progress" and opens the same panel. Either way the button opens a
sidecar beside the grid (below it at narrow widths) that mounts the Start
manual edit module's own fragment (`GET /manual-edit/fragment`), so bringing
transects down to the edit bench, walking the Metashape checklist, running
the checks and certifying a job all happen without leaving the atlas; see
[the manual_edit module's README](/mnt/rip/vicarius_drive/vicarius/modules/manual_edit/github_repo/README.md)
for what a student does inside it. A cell whose row is on the edit bench
carries a dashed outline in the matrix, matching the table's "on edit
bench" chip. The matrix header and the season token cells also carry the
season's camera model and preprocessing as a tooltip, and the id cell shows
a small `#N` order chip once `processing_order.csv` holds a position for
that row (order is edited only in Voyager tools).

The same matrix renders in a selection mode for other pages:
`window.Atlas.mountMatrix(el, {selectable, eligible, onSelect, rows})`
draws only the grid and legend, lets an operator click, shift-click or drag
a rectangle of cells, normalises it to contiguous transects by contiguous
timepoints, and reports `{ids, transects, timepoints, excluded}` on every
change. The Voyager 1 setup window embeds it this way to pick which
transects a run will reconstruct.

## Query hints

A handful of query-string parameters, honoured once per page load and never
written back to a viewer's saved preferences, help a reviewer or a
screenshot reach a specific state directly: `?admin=1` (render the admin
controls; the server still refuses an admin action without a real admin
session), `?expand=<readable id>` (open that row's detail row), `?hygiene=1`
(open Registry hygiene), `?confirm=<readable id>` (open the delete confirm
for that row), `?now=1` (open every Now strip block), `?focus=active|
manual|attention`, `?follow=<readable id>` (open the Now strip and follow
that row's console), `?sidecar=1` (matrix view with the manual edit sidecar
already open), plus the older `?view=matrix`, `?groups=collapsed:<group>,
<group>`, `?open=<readable id>` and `?hidden=1`.

## Detail drawer

Click a readable id (or a badge) to open `/atlas/api/detail/<id>`. It labels
its own source explicitly rather than guessing what a person is looking at:
**live folder on this machine** (the processing folder still exists - shows
step timings, scale numbers, locations, a console tail, folder contents, and
the full `analysis_params.yaml`), **snapshot taken by the registry** (the
folder is gone, but a `capture_snapshot()` was taken at the end of a step -
same blocks, sourced from `snapshots/<id>/`), or **no folder and no
snapshot** (a registry row with nothing on disk yet, typically right after
ingest and before step 1 has run).

## What other modules write

The atlas is a view and an edit surface over a registry several writers share:

- **Catalog** (`atlascatalog.py`, above) creates a row per timepoint it
  finds on the Archive and keeps `video_location`/`video_size_gb` current,
  identity cells included, all through one `protect_operator=True` upsert.
  It writes nothing else to the row itself -- no ffprobe facts, no
  `ingested_at` -- but it is also the writer of the `source_files.csv`
  sidecar (one line per file, in or out of the row) and of
  `catalog_needs_attention.csv` (rewritten whole on every run).
- **Ingest** (`atlasingest.py`, above) creates rows and keeps
  `video_location`/`video_size_gb`/`video_duration_s`/`video_format`/`ingested_at`
  current.
- **`3D_phase_1`** (the processing module) writes `processing_folder`,
  `processing_location`, `psx_file`, `step`/`stage`/`stage_started`,
  `console_log`, the `step1_*` timing and status columns, the `scale_*`
  columns, the headline counts (`tie_points`, `faces_full`,
  `faces_delivery`, `texture_pages`, `dem_mm_per_pix`), `processing_size_gb`,
  `snapshot_dir`, and `params_summary`, and sets `manual_edit_status =
  awaiting` when step 1 finishes and the folder needs a person's pass in
  Metashape. It also records the Voyager 1 detail row facts (`run_id`,
  `params_version`, step 0 and step 1 timings, `frames_extracted`, the step
  1 report path, both console log paths) to `row_facts.csv` section
  `voyager1` through `registry_client.facts`.
- **Start manual edit** (the `manual_edit` module) sets `manual_edit_status`
  to `editing` when a job pulls a row to the edit bench, and to `done` (with
  `manual_edit_done`, the psx save time) at certification, always through
  `POST /atlas/api/mark`, actor `manual_edit:<initials>`; it also writes the
  Manual edit detail row facts (job id, editor, pulled and certified at, the
  checks verdict and summary, edit seconds, the pre-edit archive path) to
  `row_facts.csv` section `manual_edit`. This supersedes the "Manual editing
  finished" atlas control described in the 2026-08-31 phase 2 draft; step 2
  now verifies the edit rather than flipping the status itself.
- **Voyager tools** (admin only) edits `processing_order.csv` (Save order,
  Lock order in, Unlock) and `seasons.csv` (camera model, preprocessing,
  notes per season) through the registry library, and can write the
  `voyager1` fact `spot_redo` when a completed row is sent back through
  Voyager 1.
- **Voyager 2 and Vigil** (drafted, not yet built) are reserved the
  `step2_*` columns and the `row_facts.csv` sections `voyager2` and `vigil`
  respectively (the virtual column group at the right of the matrix and the
  Voyager 2 and Vigil detail row sections read no record yet until then).
- **A future sync driver** would write the `*_location_verified` timestamps
  and `output_folder`/`output_location`/`sizes_verified` once a driver moves
  folders to/from archive; not built yet.

Every one of those writers goes through `vicarius/_METADATA/3d/registry.py`
(`upsert`, `set_stage`, `set_facts`, `set_processing_order`, `set_season`,
`capture_snapshot`, `rename_id`, `delete_row`), never a direct
`open(..., "w")` on a sidecar file, so the exclusive lock and the event log
stay trustworthy no matter which writer runs concurrently with the atlas's
own edit API.

## The registry contract

Full column table, the operator-edited/never-overwrite rule, sort order, the
`VICARIUS_3D_REGISTRY_ROOT` env override, and the spreadsheet-editing caution
all live in [`../../../_METADATA/3d/README.md`](../../../_METADATA/3d/README.md).
Read that file for the schema; this README covers only how the atlas surfaces
and edits it.

## Legacy viewer (retired)

Before this rewrite, the atlas was a static `status.json` built by hand from
per-project `status_*.csv` ledgers and served on port 8765. Those files are
kept for reference in [`legacy/`](legacy/README.md); nothing in the current
module reads, writes, or launches them.

## Provenance and links

- Repo: `/mnt/rip/vicarius_drive/vicarius/modules/tcrmp_3d_atlas/github_repo`
- Blueprint: `vicarius_ui_os/atlas_views.py`, `templates/atlas.html`,
  `static/atlas.js`, `static/atlas.css` (registered in `vicarius_ui_os/app.py`).
- Related modules: `3D_phase_1` (Voyager 1, writes step 1 progress and the
  `voyager1` facts into this registry); `manual_edit` (Start manual edit,
  mounted in the matrix sidecar, writes `manual_edit_status` and the
  `manual_edit` facts); `voyager_tools` (admin only, edits the processing
  order and the season table, linked from the atlas toolbar); the retired
  `3D_phase1`/`3D_phase2` ledger convention this registry replaces.
- Related docs: the platform TCRMP 3D registry contract,
  `vicarius/_METADATA/3d/README.md`; the naming rules it depends on,
  `vicarius/_METADATA/3d/naming3d.py`; the Voyager program spec,
  `docs/superpowers/specs/2026-09-03-voyager-program-design.md`, sections 3
  and 4.
- Data-dictionary descriptors: none for the atlas itself. This module edits
  a shared platform registry rather than producing its own catalogued
  dataset; the sidecar files it reads and writes (`source_files.csv`,
  `catalog_needs_attention.csv`, `processing_order.csv`, `row_facts.csv`,
  `seasons.csv`) are described by the registry's own descriptors under
  `vicarius/_METADATA/dictionary/datasets/`.
