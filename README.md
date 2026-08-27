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

Open it from the desktop's TCRMP folder (the "3D Atlas" tile) or directly at
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
  shows as an empty cell at a glance.

A header strip carries ten tallies (timepoints, step 1 done/running/not
started/failed, awaiting edit, manual scale, active runs, total processing
and output GB) and a "registry read HH:MM:SS" stamp updated on every
successful poll (rows every 15 s, skipped while the tab is hidden or a field
is mid-edit).

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
   carry it out with `python3 prep_tools.py <folder> --apply`.
2. **Ingest** (`atlasingest.py`) - once a folder holds one standard-named
   video per timepoint, `python3 atlasingest.py <folder> [--dry-run]` runs
   `ffprobe` on each file, derives the readable id and season token from the
   name, and creates or updates the matching registry row (size, duration,
   container/codec, `ingested_at`, `video_location`). Rerunning on an
   unchanged folder writes no new events. Ingest is the authority on where a
   video currently sits: `video_location` is refreshed on every run even for
   an existing row, but identity cells an operator has already hand-corrected
   (season token, readable id, process, notes) are left alone
   (`protect_operator=True`).

After ingest, the script prints a reminder to open the atlas and review each
new row before the timepoint is processed: check the readable id and season
token, correct them in place if the survey season is wrong, and uncheck
`process` for lit/unlit duplicates and bad takes.

## Editing rules

Only the cells `registry.OPERATOR_COLUMNS` names are editable here:
`season_token`, `readable_id`, `process`, `notes`, `video_location`,
`processing_location`, `output_location`. Every other column is written by a
module (`3D_phase_1`, later step 2) or by ingest, and the table shows those
as read-only text.

- `season_token` is a select (`_pbl` / `ann`); `process` is a checkbox;
  `video_location`, `processing_location`, `output_location`, and `notes` are
  inline text fields that save on change or Enter.
- `readable_id` has a RENAME control: type the new id and confirm. Renaming
  moves the registry row, its event history, and its snapshot directory to
  the new id, but does **not** rename anything already written to disk under
  the old id - folders, the psx, frame directories keep their existing
  names. A rename to a malformed id (wrong `{SITE}_{T#}_{year}{token}`
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

## The live panel

An "ACTIVE PROCESSING" panel appears only while at least one row's `stage`
is set and not `done`/`failed`/empty - it disappears on its own the moment
nothing is running. Per active run it shows which module last wrote the row,
the readable id, a pulsing stage chip, and an elapsed clock counted from
`stage_started`. Clicking a run jumps to and highlights its table row; with
more than one run active, a FOLLOW LOG control switches which run's console
is tailed. The tail comes from `/atlas/api/console/<id>` (last 200 lines of
the newest `console/*.log` in the processing folder) and reloads every 5 s
while a run is active.

## The notices

A "Ready for manual editing" block sits above the active panel, one line per
row with `manual_edit_status = awaiting`: the readable id, its processing
location, and a COPY PATH button. This is where step 1's manual gate surfaces
- a timepoint step 1 finished lands here so an operator knows to open it in
Metashape for the straighten/crop/manual-scale pass before step 2 can run.
The block is hidden entirely when nothing is awaiting, and it clears itself
automatically the moment step 2 sets `manual_edit_status = done`.

A row with `scale_status = MANUAL_NEEDED` carries its own badge in the table
(purple) that jumps straight to the scale numbers in the detail drawer, since
a mean scale error over threshold needs a person's judgment, not a rerun.

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

The atlas is a view and an edit surface over a registry three writers share:

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
  Metashape.
- **Step 2** (not yet built) is reserved the `step2_*` columns and will set
  `manual_edit_status = done` once its start-of-step scan confirms the
  manual pass happened, clearing the notice above.
- **A future sync driver** would write the `*_location_verified` timestamps
  and `output_folder`/`output_location`/`sizes_verified` once a driver moves
  folders to/from archive; not built yet.

Every one of those writers goes through `vicarius/_METADATA/3d/registry.py`
(`upsert`, `set_stage`, `capture_snapshot`, `rename_id`), never a direct
`open(..., "w")` on the CSV, so the exclusive lock and the event log stay
trustworthy no matter which writer runs concurrently with the atlas's own
edit API.

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
- Related modules: `3D_phase_1` (writes step 1 progress into this registry);
  the retired `3D_phase1`/`3D_phase2` ledger convention this registry
  replaces.
- Related docs: the platform TCRMP 3D registry contract,
  `vicarius/_METADATA/3d/README.md`; the naming rules it depends on,
  `vicarius/_METADATA/3d/naming3d.py`.
- Data-dictionary descriptors: none. This module edits a shared platform
  registry rather than producing its own catalogued dataset.
