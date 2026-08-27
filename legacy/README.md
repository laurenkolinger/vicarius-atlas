# Legacy atlas viewer (retired 2026-08-27)

These four files were the first TCRMP 3D atlas: `build_status.py` globbed the
per-project `status_*.csv` tracking ledgers under `/mnt/rip/3d` and
`/mnt/tear/3d` plus the `drive_inventory` outputs and wrote `status.json`;
`index.html` rendered `status.json` as a toggleable Tree / Table / Matrix
view; `serve.sh` served the folder on port 8765 with `python3 -m http.server`
so the browser could fetch `status.json` (`file://` reads are blocked). It
was a read-only, rebuild-by-hand status mirror over the old `3D_phase1` /
`3D_phase2` ledger convention, launched from the desktop through
`POST /api/utils/atlas/launch` in `vicarius_ui_os/utils_views.py`.

The atlas was rebuilt as module version 2.0.0 on the platform TCRMP 3D
registry (`vicarius/_METADATA/3d/registry.py`, one CSV of record instead of
per-project ledgers) with a native, live-refreshing UI: a Flask blueprint
inside `vicarius_ui_os` (`atlas_views.py`) reads and writes the registry on
every request, and the module tile opens that pane directly through the
normal module fragment path instead of launching a detached local server.
See `../README.md` for the current atlas.

Kept here for reference only. Nothing in the current module reads, writes,
imports, or launches any file in this folder.
