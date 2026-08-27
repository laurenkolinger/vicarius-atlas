#!/usr/bin/env python3
"""
3D status builder  ->  status.json for the toggleable viewer (index.html).

Reads the authoritative per-project tracking ledgers (status_*.csv written by
3D_phase1 / 3D_phase2) from the live workspaces on /mnt/rip/3d and
/mnt/tear/3d. For each model it:

  * derives YEAR + SEASON from the PROJECT FOLDER (the source of truth, e.g.
    TCRMP_2025Annual_03 -> 2025 / Annual, TCRMP_2025_PBL_01 -> 2025 / PBL).
    The date embedded in the model-id is only the dive date and is NOT trusted
    for grouping (it carries typos and spans calendar boundaries).
  * resolves the on-disk location of its raw / encoded / psx / output files by
    globbing the CURRENT project layout (not the path recorded in the ledger,
    which can be stale after a project is reorganized), and cross-references the
    VICAR NAS folder-tree snapshot for archived copies + the physical drive.

Each file group gets one of four defined states:
  on_disk   a working copy exists now under the project dir (rip/tear NVMe)
  archived  no working copy, but the NAS snapshot has a copy on an archive drive
  missing   the step that produces it is marked complete, yet no copy exists
            anywhere we can see  (this is the one that needs attention)
  na        not applicable / not yet produced (that step has not run)

Output: status.json, consumed by index.html.  Re-run after a processing run or
a fresh weekly NAS snapshot:  python3 build_status.py
"""
import csv, glob, json, os, re, sys, time
from collections import defaultdict

REPO = "/mnt/rip/vicarius_drive/vicarius/modules/drive_inventory/github_repo"
TREE = os.path.join(REPO, "data/drive_contents_tree.tsv")
INV  = os.path.join(REPO, "output/inventory.json")
PROJECT_ROOTS = ["/mnt/rip/3d", "/mnt/tear/3d"]
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "status.json")
# Manual/ingested location registry — the truth layer for files we cannot see
# from this host (Dropbox, offline drives, external). Appended to by the
# 3d-locate ingest skill; merged on top of the auto-derived ledger+NAS locations.
LOCATIONS = os.path.join(HERE, "locations.csv")
LOC_HEADERS = ["model_id", "artifact", "path", "store", "status",
               "verified", "source", "notes"]

ID_RE = re.compile(
    r"^(?P<ptype>TCRMP|RBTEST|RBMAPPING|HYDRUS\w*|MISC)"
    r"(?P<date>\d{6,9})_3D(?:video)?_(?P<site>[A-Za-z0-9]+)_"
    r"(?P<rep>T(?:RY|ry)?\d+|RUN\d+|[A-Za-z]+\d*)(?P<rest>.*)$", re.I)
VIDEO_EXT = (".crm", ".mov", ".mp4", ".mkv", ".avi", ".insv")
# representative month per season, for chronological ordering of timepoints
SEASON_MONTH = {"PBL": 3, "Annual": 10, "Other": 6}
# Fixed seasonal columns for the matrix time-series. Edit to widen the history;
# any period present in the data is unioned in automatically and sorted by date.
PERIOD_AXIS = ["2023 Annual", "2024 PBL", "2024 Annual", "2025 PBL", "2025 Annual"]


def period_sort(label):
    y, _, se = label.partition(" ")
    return (int(y) if y.isdigit() else 9999) * 100 + SEASON_MONTH.get(se, 6)


def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes", "y", "complete", "pass")


def norm_id(mid):
    return re.sub(r"_(proxy)$", "", mid.strip(), flags=re.I)


def project_period(ledger_path):
    """Year + season from the project FOLDER (source of truth)."""
    m = re.search(r"(20\d\d)\D*?(Annual|PBL)", ledger_path, re.I)
    if m:
        season = "Annual" if m.group(2).lower() == "annual" else "PBL"
        return m.group(1), season
    return None, None


def filename_date(raw):
    """Dive date from the model-id, only used to flag filename typos."""
    d = raw
    def ok(y, mo, da): return 2015 <= y <= 2030 and 1 <= mo <= 12 and 1 <= da <= 31
    if len(d) == 8:
        if ok(int(d[:4]), int(d[4:6]), int(d[6:8])):
            return d, ""
        if ok(int(d[4:]), int(d[2:4]), int(d[:2])):
            return d, f"filename date '{raw}' is DD-MM-YYYY (non-standard)"
        return d, f"filename date '{raw}' unrecognized"
    return d, f"filename date '{raw}' is not 8 digits"


def parse_identity(mid, ledger_path):
    out = dict(site="?", transect="?", date="", multipart="", proxy=False,
               parsed=False, anomaly="", year="unknown", season="Other")
    yr, se = project_period(ledger_path)
    m = ID_RE.match(mid)
    if m:
        d = m.groupdict()
        out["parsed"] = True
        out["site"] = d["site"].upper()
        out["transect"] = d["rep"].upper()
        out["date"], out["anomaly"] = filename_date(d["date"])
        out["proxy"] = bool(re.search(r"proxy", d["rest"], re.I))
        mp = re.search(r"_(\d+)(?=_|$)", d["rest"])
        if mp:
            out["multipart"] = mp.group(1)
    # year/season: folder first, fall back to filename only if folder silent
    if yr:
        out["year"], out["season"] = yr, se
    elif out["date"][:4].isdigit():
        out["year"] = out["date"][:4]
        mo = int(out["date"][4:6]) if len(out["date"]) >= 6 and out["date"][4:6].isdigit() else 0
        out["season"] = "Annual" if mo in (9, 10, 11, 12) else ("PBL" if mo in (2, 3, 4, 5) else "Other")
        if not out["anomaly"]:
            out["anomaly"] = "year/season inferred from filename (no season in folder name)"
    out["period"] = f"{out['year']} {out['season']}"
    out["period_sort"] = (int(out["year"]) if out["year"].isdigit() else 9999) * 100 \
                         + SEASON_MONTH.get(out["season"], 6)
    return out


def load_inventory(path):
    vol, gen = {}, None
    try:
        d = json.load(open(path)); gen = d.get("generated")
        homes = []
        for x in d.get("drives", []):
            name = (x.get("Volume Name") or "").strip()
            if not name:
                continue
            rec = dict(uid=x.get("UID", "?"), tier=x.get("Tier", ""), health=x.get("Health", ""))
            if name == "homes":
                homes.append(rec["uid"]); vol[name] = dict(rec, uid="homes-RAID")
            else:
                vol[name] = rec
        if homes:
            vol["homes"]["uid"] = "homes-RAID"
    except Exception as e:
        print(f"WARN inventory: {e}", file=sys.stderr)
    return vol, gen


def scan_tree(path, vol_map):
    """norm_id -> {kind -> [drive chips]} for archived copies on NAS drives."""
    arch = defaultdict(lambda: defaultdict(list))
    if not os.path.exists(path):
        print(f"WARN tree missing: {path}", file=sys.stderr); return arch
    with open(path, encoding="utf-8", errors="replace") as fh:
        r = csv.reader(fh, delimiter="\t"); next(r, None)
        for parts in r:
            if len(parts) < 6:
                continue
            _v, _d, typ, _s, _mt, rel = parts[:6]
            share = rel.split("/")[0]
            loc = vol_map.get(share, dict(uid="?", tier="", health=""))
            chip = dict(uid=loc["uid"], tier=loc["tier"], share=share)
            base = rel.rsplit("/", 1)[-1]; stem, ext = os.path.splitext(base)
            if typ == "f" and ext.lower() in VIDEO_EXT and ID_RE.match(base):
                _add(arch[norm_id(stem)]["raw"], chip)
            for pat, kind in ((r"/output/models/([^/]+)/", "output"),
                              (r"/processing/frames/([^/]+)(?:/|$)", "encoded"),
                              (r"/psxraw/([^/]+)\.(?:psx|files)", "psx")):
                mm = re.search(pat, "/" + rel)
                if mm and ID_RE.match(mm.group(1)):
                    _add(arch[norm_id(mm.group(1))][kind], chip)
    return arch


def _add(lst, chip):
    if chip not in lst:
        lst.append(chip)


def host_disk(p):
    return "rip" if p.startswith("/mnt/rip") else ("tear" if p.startswith("/mnt/tear") else "host")


def resolve_group(local_paths, kind, afm, expected, advanced):
    """Return {status, items, archive} for one file group.

    expected = the step that produces this artifact is complete (it should
               have existed at some point).
    advanced = a LATER step is complete, so this intermediate may have been
               cleaned up on purpose.
    States: on_disk > archived > purged (gone but model moved on) >
            missing (should be here, isn't) > na (step never ran).
    """
    items, on_disk = [], False
    for p in local_paths:
        ex = os.path.exists(p)
        on_disk = on_disk or ex
        items.append(dict(path=p, exists=ex, disk=host_disk(p), archived=afm.get(kind, [])))
    archive = afm.get(kind, [])
    if on_disk:
        status = "on_disk"
    elif archive:
        status = "archived"
    elif not expected:
        status = "na"
    elif advanced:
        status = "purged"
    else:
        status = "missing"
    if not items and archive:
        items.append(dict(path="(working copy not present — archive only)", exists=False,
                          disk="—", archived=archive))
    return dict(status=status, items=items, archive=[c["uid"] for c in archive])


def loc_entries(local_paths, archive_chips, manual_rows):
    """Concrete, deduped list of every known location for one artifact group:
    local working copies (that exist), NAS archive drives, and manual/ingested
    external locations (Dropbox / offline). Used by the matrix hover tooltip."""
    out, seen = [], set()
    for p in local_paths:
        if p and os.path.exists(p) and ("L", p) not in seen:
            seen.add(("L", p)); out.append(dict(path=p, store=host_disk(p), present=True))
    for c in archive_chips:
        k = ("A", c["uid"])
        if k not in seen:
            seen.add(k); out.append(dict(path=c.get("share", ""), store="archive:" + c["uid"],
                                         tier=c.get("tier", ""), present=True))
    for r in manual_rows:
        k = ("M", r["path"])
        if k not in seen:
            seen.add(k); out.append(dict(path=r["path"], store=r.get("store", "external"),
                                         present=(r.get("status", "present") == "present"),
                                         note=r.get("notes", "")))
    return out


def model_files(row, pdir, mid, steps, afm, manual):
    base = norm_id(mid)
    raw = sorted(glob.glob(os.path.join(pdir, "video_source", base + "*")))
    if not raw:  # fall back to the path the ledger recorded (may be absolute/stale)
        raw = [p.strip() for p in (row.get("Video Source") or "").split(",") if p.strip()][:1]
    frames = [os.path.join(pdir, "processing", "frames", mid)]
    report = [os.path.join(pdir, "processing", "reportsraw", mid + "_step1.pdf")]
    # the per-model Step-1 PSX is the ledger's recorded one; glob is the fallback
    psx = [row["PSX file"].strip()] if (row.get("PSX file") or "").strip() \
        else sorted(glob.glob(os.path.join(pdir, "processing", "psxraw", "*.psx")))
    mdir = os.path.join(pdir, "output", "models", mid)
    out = sorted(glob.glob(os.path.join(mdir, "*.obj"))) \
        + sorted(glob.glob(os.path.join(pdir, "output", "orthomosaics", mid + "*")))

    processed = steps["s2"] or steps["s3"]
    enc = resolve_group(frames, "encoded", afm, expected=steps["s0"], advanced=processed)
    rep = resolve_group(report, "encoded", afm, expected=steps["s1"], advanced=processed)
    encoded = dict(status=_best(enc["status"], rep["status"]),
                   items=enc["items"] + rep["items"],
                   archive=list(dict.fromkeys(enc["archive"] + rep["archive"])))
    files = dict(
        raw=resolve_group(raw, "raw", afm,
                          expected=bool((row.get("Video Source") or "").strip()), advanced=steps["s0"]),
        encoded=encoded,
        psx=resolve_group(psx, "psx", afm, expected=steps["s1"], advanced=processed),
        output=resolve_group(out, "output", afm,
                             expected=steps["s3"] or truthy(row.get("Step 3 model exported")), advanced=False),
    )
    loc = dict(
        raw=loc_entries(raw, afm.get("raw", []), manual.get("raw", [])),
        frames=loc_entries(frames + report, afm.get("encoded", []),
                           manual.get("frames", []) + manual.get("report", [])),
        psx=loc_entries(psx, afm.get("psx", []), manual.get("psx", [])),
        output=loc_entries(out, afm.get("output", []),
                           manual.get("output", []) + manual.get("ortho", [])),
    )
    return files, loc


# best (most-present) wins when merging frames + report into one "encoded" cell
_ORDER = {"on_disk": 0, "archived": 1, "purged": 2, "missing": 3, "na": 4}
def _best(a, b):
    return a if _ORDER[a] <= _ORDER[b] else b


def load_locations(path):
    """Manual/ingested registry -> {norm_id: {artifact: [rows]}}."""
    man = defaultdict(lambda: defaultdict(list))
    if not os.path.exists(path):
        return man
    try:
        for row in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
            mid = (row.get("model_id") or "").strip()
            art = (row.get("artifact") or "").strip().lower()
            if not mid or not art:
                continue
            man[norm_id(mid)][art].append({k: (row.get(k) or "").strip() for k in LOC_HEADERS})
    except Exception as e:
        print(f"WARN locations: {e}", file=sys.stderr)
    return man


def stage_of(row):
    s = dict(s0=truthy(row.get("Step 0 complete")), s1=truthy(row.get("Step 1 complete")),
             s2=truthy(row.get("Step 2 complete")), s3=truthy(row.get("Step 3 complete")))
    has_video = bool((row.get("Video Source") or "").strip())
    if s["s2"] or s["s3"]:
        st = "PROCESSED"
    elif s["s1"]:
        st = "ALIGNED"
    elif s["s0"]:
        st = "FRAMES"
    elif has_video:
        st = "RAW"
    else:
        st = "INITIALIZED"
    return st, s


def discover_ledgers(roots):
    found = set()
    for root in roots:
        for f in glob.glob(os.path.join(root, "**/status_*.csv"), recursive=True):
            low = f.lower()
            if "backup" in low or ".bak" in low or "/.venv/" in low:
                continue
            found.add(f)
    return sorted(found)


def main():
    vol_map, nas_gen = load_inventory(INV)
    arch = scan_tree(TREE, vol_map)
    manual = load_locations(LOCATIONS)
    ledgers = discover_ledgers(PROJECT_ROOTS)
    n_loc = sum(len(v) for arts in manual.values() for v in arts.values())

    tcrmp, other = {}, {}
    summary = defaultdict(int)
    periods = {}  # period -> period_sort, for the matrix axis

    for L in ledgers:
        project = os.path.basename(os.path.dirname(L))
        pdir = os.path.dirname(L)
        is_tcrmp = "TCRMP" in project.upper() or "/TCRMP/" in L.upper()
        try:
            rows = list(csv.DictReader(open(L, encoding="utf-8", errors="replace")))
        except Exception as e:
            print(f"WARN ledger {L}: {e}", file=sys.stderr); continue
        for row in rows:
            mid = (row.get("Model ID") or "").strip()
            if not mid:
                continue
            ident = parse_identity(mid, L)
            stage, steps = stage_of(row)
            afm = arch.get(norm_id(mid), {})
            rec = dict(
                model_id=mid, project=project, stage=stage, steps=steps,
                year=ident["year"], season=ident["season"], site=ident["site"],
                transect=ident["transect"], date=ident["date"], period=ident["period"],
                period_sort=ident["period_sort"], multipart=ident["multipart"],
                proxy=ident["proxy"], anomaly=ident["anomaly"],
                aligned=row.get("Aligned cameras", ""), total_cams=row.get("Total cameras", ""),
                scale=row.get("Scale", ""), scale_err=row.get("Scale Error (m)", ""),
                status_str=(row.get("Status") or "").strip(),
                files=model_files(row, pdir, mid, steps, afm),
            )
            summary[stage] += 1; summary["TOTAL"] += 1
            if is_tcrmp and ident["parsed"]:
                periods[rec["period"]] = rec["period_sort"]
                tk = rec["transect"] + (("_" + rec["multipart"]) if rec["multipart"] else "") \
                     + ("_Proxy" if rec["proxy"] else "")
                (tcrmp.setdefault(rec["year"], {}).setdefault(rec["season"], {})
                      .setdefault(rec["site"], {})[tk]) = rec
            else:
                other.setdefault(project, {})[mid] = rec

    period_axis = sorted(set(PERIOD_AXIS) | set(periods.keys()), key=period_sort)
    payload = dict(generated=time.strftime("%Y-%m-%d %H:%M:%S"), nas_snapshot=nas_gen,
                   ledgers=ledgers, summary=dict(summary), drives=vol_map,
                   period_axis=period_axis, tcrmp=tcrmp, other=other)
    json.dump(payload, open(OUT, "w"), indent=1)
    print(f"models: {summary['TOTAL']}  " +
          "  ".join(f"{k}={summary.get(k,0)}" for k in
                    ("RAW", "FRAMES", "ALIGNED", "PROCESSED", "INITIALIZED")))
    print(f"periods (chron): {period_axis}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
