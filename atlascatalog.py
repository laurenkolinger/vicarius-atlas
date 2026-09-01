"""Read-only walk of the NAS Archive into TCRMP 3D registry rows.

`atlascatalog.py` lists, parses, and records; it never transfers. It reads
the same NAS credentials and config the carousel uses
(`driver/github_repo/config/nas.yaml`: host, user, key, `source_roots`) and
works only through plain ssh LISTING commands (a recursive `find`): no file
is ever pulled, and nothing on the NAS is ever renamed. The Archive stays
read-only end to end.

`catalog_roots()` walks each season root recursively (nested folders are
expected weirdness), parses filenames with the shared rules in
`naming3d.parse_video_name`, and groups multi-part recordings into one
timepoint row using `prep_tools.group_video_names` -- the same part-set
grouping `prep_tools.plan()` uses to plan a local merge, extracted so the
rule exists exactly once. Every row's `original_videos` lists every source
file, `;`-joined for a multi-part set. `video_location` is stamped with the
Archive-facing location `<host>:<absolute Archive path>`; `video_size_gb` is
summed from the sizes the ssh listing reports (no ffprobe -- the files are
remote, so duration/format facts are left for the real ingest at pull time).

Every write goes through `registry.upsert(rid, fields, actor="catalog",
protect_operator=True)`, so nothing an operator has already hand-edited is
ever clobbered. A rerun against an unchanged Archive changes nothing.

Anything that does not parse clean -- a bad name, a canonical file sitting
alongside leftover parts, or a part set whose members are split across more
than one directory -- is left out of the registry entirely and reported in
the "needs attention" list instead: printed at the end of every run and
written to `catalog_needs_attention.csv` in the registry data root. A season
root that is not mounted or not listable is reported plainly ("this
season's drive is not mounted") and skipped.

CLI: `python3 atlascatalog.py [--nas-config <path>] [--root <NAS season
root>]... [--dry-run]`. With no `--root`, the season roots come from
`--nas-config`'s `source_roots`. `--dry-run` computes and prints everything
without writing to the registry.
"""
import argparse
import csv
import os
import posixpath
import subprocess
import sys
from collections import defaultdict

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prep_tools
from atlasingest import NON_VIDEO_EXTENSIONS

sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
import naming3d
import registry

ACTOR = "catalog"
DEFAULT_NAS_CONFIG = "/mnt/rip/vicarius_drive/vicarius/modules/driver/github_repo/config/nas.yaml"
NEEDS_ATTENTION_FILENAME = "catalog_needs_attention.csv"
REPORT_FIELDS = ["root", "path", "reason", "detail"]

UNMOUNTED_REASON = "this season's drive is not mounted"
BAD_NAME_REASON = "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}"
CANONICAL_CONFLICT_REASON = ("canonical file already exists alongside part file(s); "
                              "resolve by hand (see atlasprep.md)")
SPLIT_ACROSS_DIRS_REASON = "part set split across directories; resolve by hand (see atlasprep.md)"


class SSH:
    """Minimal ssh command runner. Mirrors the shape `driver/remote.py` uses
    in the carousel repo, but is its own copy -- `atlascatalog.py` lives in a
    different module repo and never imports across module repos. `runner` is
    injectable so tests fake the listing command entirely: no subprocess,
    no network, ever, in a test.
    """

    def __init__(self, host, user, key, runner=None):
        self.host, self.user, self.key = host, user, key
        self.target = f"{user}@{host}"
        self.runner = runner or self._real_run

    def _real_run(self, argv, timeout=60):
        cmd = ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "StrictHostKeyChecking=accept-new", self.target] + list(argv)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return (p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired:
            return (124, "", f"timeout after {timeout}s")

    def run(self, argv, timeout=60):
        return self.runner(argv, timeout)


def list_root(ssh, root, timeout=1800):
    """Recursively list every regular file under `root` on the NAS.

    One `find` over ssh per season root -- the proven shape: NUL-delimited
    size and path-relative-to-root pairs, so a name holding a space or an
    odd character can never be misread as a field boundary.

    Returns a list of (relative_path, size_bytes) tuples using forward
    slashes for a nested relative path, or None if `root` cannot be listed
    (drive not mounted, path missing, connection refused, ...). The caller
    reports that plainly and skips the root rather than raising.
    """
    rc, out, _err = ssh.run(["find", root, "-mindepth", "1", "-type", "f", "-printf", r"%s\0%P\0"],
                             timeout=timeout)
    if rc != 0:
        return None
    fields = out.split("\0")
    if fields and fields[-1] == "":
        fields = fields[:-1]
    entries = []
    for i in range(0, len(fields) - len(fields) % 2, 2):
        size_str, relpath = fields[i], fields[i + 1]
        if not relpath:
            continue
        entries.append((relpath, int(size_str)))
    return entries


def _abs_dir(root, dirpath):
    return root if not dirpath else posixpath.join(root, dirpath)


def _catalog_one_root(root, entries, archive_host, actor, dry_run):
    """Group `entries` (as returned by `list_root`) within `root` into rows
    and needs-attention items, then write the rows (unless `dry_run`).
    Grouping happens within one directory only -- a part set whose members
    sit in different directories is ambiguous and never merged by guess.
    """
    by_dir = defaultdict(list)  # dirpath -> [(basename, size, relpath)]
    for relpath, size in entries:
        by_dir[posixpath.dirname(relpath)].append((posixpath.basename(relpath), size, relpath))

    needs_attention = []
    rows = []

    # First pass: flag bad names, and record which directories hold at
    # least one member of each parsed identity so a split part set can be
    # told apart from an ordinary single-directory group.
    key_dirs = defaultdict(set)
    for dirpath, members in by_dir.items():
        for basename, _size, relpath in members:
            ext = os.path.splitext(basename)[1].lower()
            if ext in NON_VIDEO_EXTENSIONS:
                continue
            parsed = naming3d.parse_video_name(basename)
            if parsed is None:
                needs_attention.append({"root": root, "path": posixpath.join(root, relpath),
                                         "reason": BAD_NAME_REASON, "detail": ""})
                continue
            key = (parsed["project"], parsed["date"], parsed["site"], parsed["transect"])
            key_dirs[key].add(dirpath)

    ambiguous_keys = {key for key, dirs in key_dirs.items() if len(dirs) > 1}
    for key in sorted(ambiguous_keys):
        dirs = sorted(_abs_dir(root, d) for d in key_dirs[key])
        needs_attention.append({
            "root": root, "path": "; ".join(dirs),
            "reason": SPLIT_ACROSS_DIRS_REASON,
            "detail": f"{key[2]}_{key[3]} {key[1]} found split across: {', '.join(dirs)}",
        })

    # Second pass: group each directory on its own and turn every clean
    # group into one row.
    for dirpath in sorted(by_dir):
        names = [basename for basename, _size, _relpath in by_dir[dirpath]]
        size_by_name = {basename: size for basename, size, _relpath in by_dir[dirpath]}
        abs_dir = _abs_dir(root, dirpath)

        for group in prep_tools.group_video_names(names):
            key = group["key"]
            if key in ambiguous_keys:
                continue  # already reported once, above

            canonical, parts, members = group["canonical"], group["parts"], group["members"]
            if canonical is not None and parts:
                names_involved = sorted(m[0] for m in members)
                needs_attention.append({
                    "root": root, "path": abs_dir,
                    "reason": CANONICAL_CONFLICT_REASON,
                    "detail": "; ".join(names_involved),
                })
                continue

            project, date, site, transect = key
            rid = naming3d.readable_id(site, transect, date)
            original_videos = ";".join(m[0] for m in members)
            size_gb = round(sum(size_by_name[m[0]] for m in members) / 1e9, 3)
            fields = {
                "site": site,
                "transect": transect,
                "year": date[:4],
                "season_token": naming3d.season_token(date),
                "original_videos": original_videos,
                "video_location": f"{archive_host}:{abs_dir}",
                "video_size_gb": size_gb,
            }
            if not dry_run:
                registry.upsert(rid, fields, actor, protect_operator=True)
            rows.append({"readable_id": rid, "root": root, "path": abs_dir, "fields": fields})

    return rows, needs_attention


def catalog_roots(roots, ssh, actor=ACTOR, dry_run=False):
    """Walk every root in `roots` over `ssh` and record one registry row per
    clean timepoint. Returns (rows, needs_attention); `rows` describes every
    row written (or that would be written, under `dry_run`), one dict per
    row: {"readable_id", "root", "path", "fields"}.
    """
    rows, needs_attention = [], []
    for root in roots:
        entries = list_root(ssh, root)
        if entries is None:
            needs_attention.append({"root": root, "path": root, "reason": UNMOUNTED_REASON, "detail": ""})
            continue
        root_rows, root_na = _catalog_one_root(root, entries, ssh.host, actor, dry_run)
        rows.extend(root_rows)
        needs_attention.extend(root_na)
    return rows, needs_attention


def load_nas_config(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def resolve_roots(cli_roots, cfg):
    """CLI `--root` values if any were given, else `source_roots` from the
    NAS config."""
    return list(cli_roots) if cli_roots else list(cfg.get("source_roots") or [])


def write_needs_attention_report(needs_attention):
    """Write `needs_attention` to a fixed filename in the registry data
    root (respects `VICARIUS_3D_REGISTRY_ROOT`), overwriting whatever the
    previous run left there -- the report always reflects the run that just
    finished, not an accumulating log. Returns the path written."""
    os.makedirs(registry.ROOT, exist_ok=True)
    path = os.path.join(registry.ROOT, NEEDS_ATTENTION_FILENAME)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for item in needs_attention:
            writer.writerow({k: item.get(k, "") for k in REPORT_FIELDS})
    return path


def _print_rows(rows):
    if not rows:
        print("No timepoints found.")
        return
    header = ("readable_id", "video_location", "video_size_gb", "original_videos")
    table = [header] + [(r["readable_id"], r["fields"]["video_location"],
                          str(r["fields"]["video_size_gb"]), r["fields"]["original_videos"])
                         for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for i, row in enumerate(table):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(header))))


def _print_needs_attention(needs_attention):
    if not needs_attention:
        print("\nNo items need attention.")
        return
    print(f"\n{len(needs_attention)} item(s) need attention:")
    for item in needs_attention:
        detail = f" ({item['detail']})" if item.get("detail") else ""
        print(f"  [{item['root']}] {item['path']}: {item['reason']}{detail}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only walk of the NAS Archive into TCRMP 3D registry rows. "
                     "Lists over plain ssh only; never pulls a file, never renames anything on the NAS.")
    parser.add_argument("--nas-config", default=DEFAULT_NAS_CONFIG,
                         help="Path to the carousel's nas.yaml (host/user/key/source_roots)")
    parser.add_argument("--root", action="append", default=None,
                         help="NAS season root to walk (repeatable). Default: source_roots from --nas-config")
    parser.add_argument("--dry-run", action="store_true",
                         help="Compute and print what would be written without touching the registry")
    args = parser.parse_args(argv)

    if args.dry_run:
        print("DRY RUN: nothing will be written to the registry.\n")

    cfg = load_nas_config(args.nas_config)
    ssh = SSH(cfg["host"], cfg["user"], cfg["key"])
    roots = resolve_roots(args.root, cfg)

    rows, needs_attention = catalog_roots(roots, ssh, actor=ACTOR, dry_run=args.dry_run)

    _print_rows(rows)
    _print_needs_attention(needs_attention)

    report_path = write_needs_attention_report(needs_attention)
    print(f"\nNeeds-attention report: {report_path}")


if __name__ == "__main__":
    main()
