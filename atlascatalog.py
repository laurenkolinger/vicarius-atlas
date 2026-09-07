"""Read-only walk of the NAS Archive into TCRMP 3D registry rows.

Module: atlascatalog.py (tcrmp_3d_atlas)
Purpose: list one or more NAS season roots over plain ssh, parse every video
    name with the shared grammar in naming3d, group parts into timepoints,
    and record one registry row per clean timepoint plus one sidecar line
    per source file. Nothing is transferred and nothing on the NAS is
    renamed: the Archive stays read-only end to end.
Inputs: the carousel's nas.yaml (host, user, key, source_roots); --root
    season roots (default: source_roots); catalog_overrides.csv beside the
    registry (columns file_name,site,note; a missing file means none;
    --overrides points elsewhere); --name-variant tokens (demo, 3ddemo) for
    the two seasons whose names carry those tokens in place of 3D.
Outputs (real run only): one registry row per timepoint through
    registry.upsert(rid, fields, actor="catalog", protect_operator=True), so
    nothing an operator hand-edited is clobbered; one sidecar line per file
    through registry.set_source_files; the needs-attention report rewritten
    whole at registry.NEEDS_ATTENTION_CSV. A rerun against an unchanged
    Archive changes nothing. --dry-run computes and prints everything and
    writes nothing, not even the registry data root.

How a listing becomes rows:

1. One `find` over ssh per root (NUL-delimited size and path pairs, Synology
   system folders pruned). A root that cannot be listed is reported plainly
   ("this season's drive is not mounted") and skipped.
2. Companion files (.csv, .txt, .md, .log) are dropped. Every other name is
   parsed; a name that does not parse is a needs-attention line. A site
   override replaces the parsed site before grouping, so two takes of one
   site can become two labelled rows (LBHLBPFIX1 and LBHLBPFIX2).
3. Files are grouped by identity (project, date, site, transect) within one
   directory. A part set split across directories is never merged by guess.
4. Per group: parts plus any whole file is a canonical conflict; one whole
   plain file plus proxy copies is the row alone with each proxy recorded
   in_row=false; more than one whole file otherwise is "resolve by hand";
   anything else is the row with every member.
5. Across every root, groups that still share one readable id after
   overrides are sorted by (date, root, path): the earliest is the row and
   each later take is reported, its files recorded in_row=false.

The sidecar edit_note is built in one place (_edit_note) in one order: the
override note, the second-recording note, the proxy-mirror note, the parts
note, the proxy note, the demo-token note.

CLI: python3 atlascatalog.py [--nas-config <path>] [--root <NAS season
root>]... [--overrides <path>] [--name-variant demo|3ddemo]... [--dry-run]
"""
import argparse
import csv
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atlasingest import NON_VIDEO_EXTENSIONS

sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
import naming3d
import registry

ACTOR = "catalog"
DEFAULT_NAS_CONFIG = "/mnt/rip/vicarius_drive/vicarius/modules/driver/github_repo/config/nas.yaml"
NEEDS_ATTENTION_FILENAME = os.path.basename(registry.NEEDS_ATTENTION_CSV)
REPORT_FIELDS = registry.NEEDS_ATTENTION_COLUMNS
REPORT_FILE_MODE = 0o644
OVERRIDES_FILENAME = "catalog_overrides.csv"
OVERRIDE_FIELDS = ["file_name", "site", "note"]
# A site label must fit naming3d.ID_PATTERN, which the atlas RENAME also enforces.
OVERRIDE_SITE_PATTERN = re.compile(r"^[A-Za-z0-9]+\Z")
# Synology keeps thumbnails under @eaDir and deleted files under #recycle;
# neither may reach the name grammar.
PRUNED_NAMES = ("@*", "#recycle")
FIND_PRINTF = r"%s\0%P\0"
LIST_TIMEOUT_S = 1800
SSH_TIMEOUT_S = 60
SSH_CONNECT_TIMEOUT_S = 10
SSH_TIMEOUT_RC = 124
BYTES_PER_GB = 1e9
SIZE_DECIMALS = 3
NAME_JOINER = ";"
NOTE_JOINER = "; "
DETAIL_JOINER = "; "
MIN_PARTS_FOR_NOTE = 2

UNMOUNTED_REASON = "this season's drive is not mounted"
BAD_NAME_REASON = "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}"
CANONICAL_CONFLICT_REASON = ("canonical file already exists alongside part file(s); "
                              "resolve by hand (see atlasprep.md)")
SPLIT_ACROSS_DIRS_REASON = "part set split across directories; resolve by hand (see atlasprep.md)"
SECOND_DATE_REASON = "second recording date for one timepoint; review which take to keep"
PROXY_MIRROR_REASON = "proxy mirror beside its full file; resolve by hand"
MULTI_WHOLE_REASON = "more than one whole file for one timepoint; resolve by hand"
OVERRIDE_UNLISTED_REASON = "override names a file that was not listed"

NOTE_PARTS = "parts grouped; physical merge at pull time"
NOTE_PROXY = "proxy suffix dropped at rename"
NOTE_DEMO = "name uses the demo token; catalogued as 3D"
NOTE_SECOND = "second recording on {date}, not counted in the row; see needs-attention"
NOTE_MIRROR = "proxy mirror beside its full file, not counted in the row; see needs-attention"
NOTE_OVERRIDE = "site label {site} applied from {filename}"


class SSH:
    """Minimal ssh command runner. Mirrors the shape `driver/remote.py` uses
    in the carousel repo, but is its own copy -- `atlascatalog.py` lives in a
    different module repo and never imports across module repos. `runner` is
    injectable so tests fake the listing command entirely: no subprocess,
    no network, ever, in a test.
    """

    def __init__(self, host, user, key, runner=None):
        """Remember the target; `runner(argv, timeout)` replaces the real ssh call when given."""
        self.host, self.user, self.key = host, user, key
        self.target = f"{user}@{host}"
        self.runner = runner or self._real_run

    def _real_run(self, argv, timeout=SSH_TIMEOUT_S):
        """Run `argv` on the NAS over ssh; returns (returncode, stdout, stderr)."""
        # Each remote-side argument must be shell-quoted: ssh concatenates
        # argv with spaces and hands the result to the *remote* shell
        # verbatim, so an unquoted find -printf escape like `\0` gets its
        # backslash stripped by that shell before find ever sees it. Mirrors
        # driver/remote.py's SSH.run, which quotes for the same reason.
        cmd = ["ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}",
               "-o", "StrictHostKeyChecking=accept-new", self.target] + [shlex.quote(a) for a in argv]
        try:
            # errors="replace": a remote listing can carry a byte sequence that
            # is not valid UTF-8 (a file named on a differently-encoded
            # volume). Decoding it strictly would raise here and end the
            # whole run; a replacement character instead reaches the name
            # grammar, fails it, and becomes one needs-attention line.
            p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
            return (p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired:
            return (SSH_TIMEOUT_RC, "", f"timeout after {timeout}s")

    def run(self, argv, timeout=SSH_TIMEOUT_S):
        """Run `argv` through the configured runner; returns (returncode, stdout, stderr)."""
        return self.runner(argv, timeout)


def _require_root(root):
    """Return `root` when it is an absolute NAS path.

    Raises:
        TypeError: when it is not a string.
        ValueError: when it is blank or relative.
    """
    if not isinstance(root, str):
        raise TypeError(f"a season root must be a string, got {type(root).__name__}")
    if not root.strip():
        raise ValueError("a season root is blank")
    if not root.startswith("/"):
        raise ValueError(f"a season root must be an absolute NAS path, got {root!r}")
    return root


def _find_argv(root):
    """The remote `find` for one root: Synology system folders pruned, then
    every regular file as NUL-delimited size and root-relative path pairs."""
    prune = []
    for name in PRUNED_NAMES:
        if prune:
            prune.append("-o")
        prune += ["-name", name]
    return (["find", root, "-mindepth", "1", "("] + prune
            + [")", "-prune", "-o", "-type", "f", "-printf", FIND_PRINTF])


def list_root(ssh, root, timeout=LIST_TIMEOUT_S):
    """Recursively list every regular file under `root` on the NAS.

    One `find` over ssh per season root -- the proven shape: NUL-delimited
    size and path-relative-to-root pairs, so a name holding a space, a
    newline or an odd character can never be misread as a field boundary.

    Parameters:
        ssh: an SSH runner.
        root: absolute NAS path of the season root.
        timeout: seconds allowed for the listing.

    Returns:
        A list of (relative_path, size_bytes) tuples using forward slashes
        for a nested relative path, or None if `root` cannot be listed
        (drive not mounted, path missing, connection refused, ...). The
        caller reports that plainly and skips the root rather than raising.

    Raises:
        TypeError, ValueError: when `root` is not an absolute path string.
        ValueError: when the listing carries a size that is not a number
            (a truncated or garbled transfer), naming the root and the path.
    """
    _require_root(root)
    rc, out, _err = ssh.run(_find_argv(root), timeout=timeout)
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
        if not size_str.isdecimal():
            raise ValueError(f"the listing of {root} returned a size that is not a number "
                             f"({size_str!r}) for {relpath!r}; the transfer was garbled")
        entries.append((relpath, int(size_str)))
    return entries


# --- small pure helpers -------------------------------------------------------

def _abs_dir(root, dirpath):
    """Absolute NAS directory for a root-relative directory ("" is the root itself)."""
    return root if not dirpath else posixpath.join(root, dirpath)


def _iso_date(yyyymmdd):
    """"YYYY-MM-DD" from the eight digits a parsed name carries."""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _container(file_name):
    """The lowercase extension without its dot, "" when the name has none."""
    return os.path.splitext(file_name)[1].lower().lstrip(".")


def _item(root, path, reason, detail=""):
    """One needs-attention line over REPORT_FIELDS."""
    return {"root": root, "path": path, "reason": reason, "detail": detail}


def _override_note(site, note):
    """The sidecar sentence for an overridden file: the label, its source file, and the operator's note."""
    text = NOTE_OVERRIDE.format(site=site, filename=OVERRIDES_FILENAME)
    return f"{text}: {note}" if note else text


def _edit_note(override_note, second_iso, parts_count, proxy, mirror=False, kind=naming3d.KIND_3D):
    """The sidecar edit_note, built here and nowhere else, in one order.

    Parameters:
        override_note: the _override_note text, or "" when no override applied.
        second_iso: "YYYY-MM-DD" of this file's take when it is a later
            recording not counted in the row, else None.
        parts_count: how many parts the file's own group holds; the parts
            note appears from MIN_PARTS_FOR_NOTE up.
        proxy: True when the name carries the _Proxy suffix.
        mirror: True when the file is a proxy copy beside its full file.
        kind: the name token seen ("3D", "demo", "3ddemo"); a variant adds NOTE_DEMO.

    Returns:
        The sentences that apply, joined with NOTE_JOINER; "" when none does.

    Raises:
        TypeError: when override_note is not text or parts_count is not an int.
        ValueError: when parts_count is negative or kind is unknown.
    """
    if not isinstance(override_note, str):
        raise TypeError(f"override_note must be text, got {type(override_note).__name__}")
    if isinstance(parts_count, bool) or not isinstance(parts_count, int):
        raise TypeError(f"parts_count must be an int, got {type(parts_count).__name__}")
    if parts_count < 0:
        raise ValueError(f"parts_count must not be negative, got {parts_count}")
    if kind not in (naming3d.KIND_3D,) + naming3d.NAME_VARIANTS:
        raise ValueError(f"unknown name kind {kind!r}")
    bits = []
    if override_note:
        bits.append(override_note)
    if second_iso:
        bits.append(NOTE_SECOND.format(date=second_iso))
    if mirror:
        bits.append(NOTE_MIRROR)
    if parts_count >= MIN_PARTS_FOR_NOTE:
        bits.append(NOTE_PARTS)
    if proxy:
        bits.append(NOTE_PROXY)
    if kind != naming3d.KIND_3D:
        bits.append(NOTE_DEMO)
    return NOTE_JOINER.join(bits)


# --- overrides ------------------------------------------------------------------

def _check_override(path, line_no, file_name, site, note, seen):
    """Validate one override line and return its (file_name, {site, note}) entry.

    Raises:
        ValueError: naming `path` and `line_no` for a blank file name, a site
            that is not letters and digits, or a file name already listed.
    """
    where = f"{path}: line {line_no}"
    if not file_name:
        raise ValueError(f"{where}: file_name is blank")
    if not OVERRIDE_SITE_PATTERN.match(site):
        raise ValueError(f"{where}: site {site!r} must be letters and digits only")
    if file_name in seen:
        raise ValueError(f"{where}: {file_name} was already named at line {seen[file_name]}")
    return file_name, {"site": site.upper(), "note": note}


def load_overrides(path):
    """Read catalog_overrides.csv into a dict file_name -> {"site", "note"}.

    Parameters:
        path: the overrides file. A missing or empty file means no
            overrides. A BOM from a spreadsheet export is tolerated, as are
            blank lines; every site is stored upper-case.

    Returns:
        {} when the file is missing or empty; otherwise one entry per line.

    Raises:
        ValueError: naming the path and the line for a header other than
            OVERRIDE_FIELDS, a line with the wrong number of fields, a blank
            file name, a site that is not letters and digits, or a file name
            given twice.
        OSError: when the file exists and cannot be read (names the path).
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return {}
            if [h.strip() for h in header] != OVERRIDE_FIELDS:
                raise ValueError(f"{path}: line 1 must be the header {','.join(OVERRIDE_FIELDS)}, "
                                 f"got {','.join(header)!r}")
            overrides, seen = {}, {}
            for cells in reader:
                if not cells or all(not c.strip() for c in cells):
                    continue
                if len(cells) != len(OVERRIDE_FIELDS):
                    raise ValueError(f"{path}: line {reader.line_num} has {len(cells)} fields, "
                                     f"expected {len(OVERRIDE_FIELDS)} ({','.join(OVERRIDE_FIELDS)})")
                name, entry = _check_override(path, reader.line_num, *(c.strip() for c in cells), seen)
                overrides[name] = entry
                seen[name] = reader.line_num
    except OSError as exc:
        raise OSError(f"could not read the overrides file at {path}: {exc}") from exc
    except csv.Error as exc:
        raise ValueError(f"{path}: not a readable CSV ({exc})") from exc
    return overrides


def _check_overrides(overrides):
    """Shape a programmatic overrides mapping like load_overrides would.

    Raises:
        TypeError: when it is not a mapping of str to mapping.
        ValueError: for a blank file name or a site that is not letters and digits.
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise TypeError(f"overrides must be a mapping file_name -> {{site, note}}, got {type(overrides).__name__}")
    checked, seen = {}, {}
    for line_no, (name, entry) in enumerate(overrides.items(), start=1):
        if not isinstance(name, str) or not isinstance(entry, Mapping):
            raise TypeError(f"overrides entry {name!r} must map a file name to {{site, note}}")
        site, note = entry.get("site"), entry.get("note") or ""
        if not isinstance(site, str) or not isinstance(note, str):
            raise TypeError(f"overrides entry {name!r}: site and note must be text")
        checked_name, checked_entry = _check_override("overrides", line_no, name.strip(), site.strip(), note, seen)
        checked[checked_name] = checked_entry
        seen[checked_name] = line_no
    return checked


# --- collecting one root --------------------------------------------------------

def _video_files(entries):
    """Bucket a listing by directory with expected companions dropped.

    Expected non-video companions (prep_log.csv, atlasprep.md, stray notes)
    are excluded here, once, before anything else sees the listing:
    naming3d.parse_video_name matches on the stem and ignores the
    extension, so a same-stem companion (TCRMP20240412_3D_MRS_T1.csv beside
    the .MP4) would otherwise parse cleanly and join the video's group.

    Returns:
        dirpath -> [(basename, size, relpath)] in listing order.
    """
    by_dir = defaultdict(list)
    for relpath, size in entries:
        basename = posixpath.basename(relpath)
        if os.path.splitext(basename)[1].lower() in NON_VIDEO_EXTENSIONS:
            continue
        by_dir[posixpath.dirname(relpath)].append((basename, size, relpath))
    return by_dir


def _parse_dir(root, files, overrides, variants):
    """Parse one directory's video files and apply site overrides.

    Returns:
        (members, items, seen, used): members are dicts {name, size, relpath,
        parsed, site, override} for every name that parsed; items are the
        BAD_NAME lines; seen holds every listed name an override names;
        used holds the ones the override was applied to.
    """
    members, items, seen, used = [], [], set(), set()
    for basename, size, relpath in files:
        override = overrides.get(basename)
        if override is not None:
            seen.add(basename)
        parsed = naming3d.parse_video_name(basename, variants)
        if parsed is None:
            items.append(_item(root, posixpath.join(root, relpath), BAD_NAME_REASON))
            continue
        site = parsed["site"]
        if override is not None:
            site = override["site"]
            used.add(basename)
        members.append({"name": basename, "size": size, "relpath": relpath,
                        "parsed": parsed, "site": site, "override": override})
    # Sorted so the report reads the same however the NAS orders its listing.
    items.sort(key=lambda item: item["path"])
    return members, items, seen, used


def _member_key(member):
    """The identity a member groups under: (project, date, effective site, transect)."""
    parsed = member["parsed"]
    return (parsed["project"], parsed["date"], member["site"], parsed["transect"])


def _split_items(root, members_by_dir):
    """Identities whose members sit in more than one directory, with one line each.

    A part set whose members are split across directories is ambiguous and
    never merged by guess.

    Returns:
        (ambiguous_keys, items)
    """
    key_dirs = defaultdict(set)
    for dirpath, members in members_by_dir.items():
        for member in members:
            key_dirs[_member_key(member)].add(dirpath)
    ambiguous = {key for key, dirs in key_dirs.items() if len(dirs) > 1}
    items = []
    for key in sorted(ambiguous):
        dirs = sorted(_abs_dir(root, d) for d in key_dirs[key])
        items.append(_item(root, DETAIL_JOINER.join(dirs), SPLIT_ACROSS_DIRS_REASON,
                           f"{key[2]}_{key[3]} {key[1]} found split across: {', '.join(dirs)}"))
    return ambiguous, items


def _group_members(members):
    """Group one directory's members by identity, in part order.

    Mirrors prep_tools.group_video_names (members sorted by part number with
    whole files last, then by name) over already-parsed members, because an
    override or a name variant changes a file's identity before grouping
    and the local prep tool never sees either.

    Returns:
        [{"key": (project, date, site, transect), "members": [...]}] sorted by key.
    """
    groups = defaultdict(list)
    for member in members:
        groups[_member_key(member)].append(member)
    result = []
    for key, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda m: (m["parsed"]["part"] is None, m["parsed"]["part"] or 0, m["name"]))
        result.append({"key": key, "members": ordered})
    return result


def _candidate(key, root, abs_dir, host, members, mirrors):
    """One clean timepoint before cross-root resolution.

    `members` are the files that make the row; `mirrors` are proxy copies
    recorded beside it with in_row=false.
    """
    project, date, site, transect = key
    return {"key": key, "readable_id": naming3d.readable_id(site, transect, date), "date": date,
            "root": root, "path": abs_dir, "host": host, "members": members, "mirrors": mirrors,
            "parts_count": sum(1 for m in members if m["parsed"]["part"] is not None)}


def _classify_group(group, root, abs_dir, host):
    """Apply the per-group rules (module docstring, step 4) to one identity group.

    Returns:
        (candidate or None, items): the row candidate when the group is
        clean, and the needs-attention lines the group raised.
    """
    members = group["members"]
    whole = [m for m in members if m["parsed"]["part"] is None]
    parts = [m for m in members if m["parsed"]["part"] is not None]
    canonical = [m for m in whole if not m["parsed"]["proxy"]]
    names = DETAIL_JOINER.join(sorted(m["name"] for m in members))
    if parts and whole:
        return None, [_item(root, abs_dir, CANONICAL_CONFLICT_REASON, names)]
    if len(whole) > 1 and len(canonical) == 1:
        mirrors = [m for m in whole if m is not canonical[0]]
        if all(m["parsed"]["proxy"] for m in mirrors):
            items = [_item(root, posixpath.join(abs_dir, m["name"]), PROXY_MIRROR_REASON,
                           f"mirror of {canonical[0]['name']}") for m in mirrors]
            return _candidate(group["key"], root, abs_dir, host, canonical, mirrors), items
    if len(whole) > 1:
        return None, [_item(root, abs_dir, MULTI_WHOLE_REASON, names)]
    return _candidate(group["key"], root, abs_dir, host, members, []), []


def _classify_dir(root, dirpath, members, ambiguous, host):
    """Every group of one directory through _classify_group, skipping split identities."""
    candidates, items = [], []
    for group in _group_members(members):
        if group["key"] in ambiguous:
            continue
        candidate, group_items = _classify_group(group, root, _abs_dir(root, dirpath), host)
        items.extend(group_items)
        if candidate is not None:
            candidates.append(candidate)
    return candidates, items


def _collect_one_root(root, entries, host, overrides, variants=()):
    """Turn one root's listing into row candidates and needs-attention lines. Pure.

    Parameters:
        root: the season root that was listed.
        entries: (relpath, size_bytes) pairs from list_root.
        host: the NAS host, stamped into every location.
        overrides: file_name -> {site, note}, applied right after parsing.
        variants: name variants admitted by naming3d.parse_video_name.

    Returns:
        (candidates, items, seen, used): candidates for _resolve_same_timepoint,
        the needs-attention lines, the override names that were listed, and
        the override names that were applied.
    """
    members_by_dir, items, seen, used = {}, [], set(), set()
    for dirpath, files in sorted(_video_files(entries).items()):
        members, dir_items, dir_seen, dir_used = _parse_dir(root, files, overrides, variants)
        items.extend(dir_items)
        seen |= dir_seen
        used |= dir_used
        if members:
            members_by_dir[dirpath] = members
    ambiguous, split_items = _split_items(root, members_by_dir)
    items.extend(split_items)
    candidates = []
    for dirpath in sorted(members_by_dir):
        dir_candidates, dir_items = _classify_dir(root, dirpath, members_by_dir[dirpath], ambiguous, host)
        candidates.extend(dir_candidates)
        items.extend(dir_items)
    return candidates, items, seen, used


# --- resolving across roots -----------------------------------------------------

def _source_record(candidate, member, in_row, second_iso=None, mirror=False):
    """One sidecar line for `member`, as a dict over registry.SOURCE_FILE_INPUT_COLUMNS."""
    parsed, override = member["parsed"], member["override"]
    override_note = _override_note(override["site"], override["note"]) if override else ""
    return {
        "file_name": member["name"],
        "nas_path": f"{candidate['host']}:{candidate['path']}/{member['name']}",
        "size_bytes": member["size"],
        "filmed_on": _iso_date(parsed["date"]),
        "container": _container(member["name"]),
        "part": parsed["part"],
        "proxy": parsed["proxy"],
        "in_row": in_row,
        "edit_note": _edit_note(override_note, second_iso, candidate["parts_count"], parsed["proxy"],
                                mirror=mirror, kind=parsed["kind"]),
    }


def _row_fields(candidate):
    """The registry cells the catalog owns for one row."""
    _project, date, site, transect = candidate["key"]
    members = candidate["members"]
    return {
        "site": site,
        "transect": transect,
        "year": date[:4],
        "season_token": naming3d.season_token(date),
        "original_videos": NAME_JOINER.join(m["name"] for m in members),
        "video_location": f"{candidate['host']}:{candidate['path']}",
        "video_size_gb": round(sum(m["size"] for m in members) / BYTES_PER_GB, SIZE_DECIMALS),
    }


def _add_records(files, taken, records):
    """Append `records` to `files` unless the sidecar already holds the file.

    The sidecar keys on file_name, so the same physical file listed through
    two overlapping roots (same nas_path) is dropped silently, and a copy
    under the same name on another path is dropped and named.

    Returns:
        (added, skipped): how many records were appended, and the file names
        skipped for sitting on another path.
    """
    added, skipped = 0, []
    for record in records:
        known = taken.get(record["file_name"])
        if known is None:
            taken[record["file_name"]] = record["nas_path"]
            files.append(record)
            added += 1
        elif known != record["nas_path"]:
            skipped.append(record["file_name"])
    return added, skipped


def _second_take_item(first, take, skipped):
    """The needs-attention line for a later take of a timepoint the row already covers."""
    names = DETAIL_JOINER.join(m["name"] for m in take["members"] + take["mirrors"])
    detail = (f"{first['readable_id']}: row uses {_iso_date(first['date'])} in {first['path']}; "
              f"this take {_iso_date(take['date'])}: {names}")
    if skipped:
        detail += f"; not recorded, same file name already in the sidecar: {DETAIL_JOINER.join(skipped)}"
    return _item(take["root"], take["path"], SECOND_DATE_REASON, detail)


def _resolve_same_timepoint(candidates):
    """Across every root, one row per readable id: the earliest take by
    (date, root, path) is the row; each later take is reported and its
    files recorded in_row=false. A take that is the same physical files
    again (one root nested in another) adds nothing and is not reported. Pure.

    Returns:
        (rows, items): rows as {"readable_id", "root", "path", "fields",
        "files"} sorted by registry.sort_key; items the SECOND_DATE lines.
    """
    by_id = defaultdict(list)
    for candidate in candidates:
        by_id[candidate["readable_id"]].append(candidate)
    rows, items = [], []
    for rid in sorted(by_id, key=registry.sort_key):
        takes = sorted(by_id[rid], key=lambda c: (c["date"], c["root"], c["path"]))
        first, files, taken = takes[0], [], {}
        _add_records(files, taken, [_source_record(first, m, True) for m in first["members"]])
        _add_records(files, taken, [_source_record(first, m, False, mirror=True) for m in first["mirrors"]])
        for take in takes[1:]:
            second_iso = _iso_date(take["date"])
            records = ([_source_record(take, m, False, second_iso) for m in take["members"]]
                       + [_source_record(take, m, False, second_iso, mirror=True) for m in take["mirrors"]])
            added, skipped = _add_records(files, taken, records)
            if added or skipped:
                items.append(_second_take_item(first, take, skipped))
        rows.append({"readable_id": rid, "root": first["root"], "path": first["path"],
                     "fields": _row_fields(first), "files": files})
    return rows, items


# --- the run ----------------------------------------------------------------------

@dataclass
class CatalogRun:
    """What one catalog pass found, and (on a real run) wrote.

    rows: one dict per timepoint {"readable_id", "root", "path", "fields", "files"}.
    needs_attention: one dict per line over REPORT_FIELDS.
    listed_files: regular files the listings returned, companions included.
    roots_listed: roots that could be listed.
    overrides_applied: override entries applied to a parsed file.
    """
    rows: list = field(default_factory=list)
    needs_attention: list = field(default_factory=list)
    listed_files: int = 0
    roots_listed: int = 0
    overrides_applied: int = 0


def _check_roots(roots):
    """Unique, absolute season roots in the order given.

    Raises:
        TypeError: when `roots` is a string or not iterable, or holds a non-string.
        ValueError: when a root is blank or relative.
    """
    if isinstance(roots, (str, bytes)):
        raise TypeError(f"roots must be a list of season roots, not the bare string {roots!r}")
    unique = []
    for root in roots:
        _require_root(root)
        if root not in unique:
            unique.append(root)
    return unique


def write_rows(rows, actor=ACTOR):
    """Record `rows` (as CatalogRun.rows) in the registry and the sidecar.

    Per row, in order: registry.upsert with protect_operator=True (a hand
    correction is never stomped), then registry.set_source_files with the
    row's files. Both are no-ops on an unchanged rerun.

    Returns:
        (rows_written, files_written) counts.

    Raises:
        KeyError, ValueError, TypeError: from the registry library when a row
            or file record is malformed.
    """
    files_written = 0
    for row in rows:
        registry.upsert(row["readable_id"], row["fields"], actor, protect_operator=True)
        registry.set_source_files(row["readable_id"], row["files"], actor)
        files_written += len(row["files"])
    return len(rows), files_written


def catalog(roots, ssh, actor=ACTOR, dry_run=False, overrides=None, variants=()):
    """Walk every root over `ssh` and record one registry row per clean timepoint.

    Parameters:
        roots: absolute NAS season roots; repeats are listed once.
        ssh: an SSH runner (its host is stamped into every location).
        actor: recorded on every registry write.
        dry_run: compute and return everything, write nothing.
        overrides: file_name -> {site, note} (see load_overrides), or None.
        variants: name variants for naming3d.parse_video_name.

    Returns:
        A CatalogRun.

    Raises:
        TypeError, ValueError: for malformed roots, overrides or variants,
            before anything is listed.
        ValueError: from list_root when a listing is garbled.
    """
    roots = _check_roots(roots)
    overrides = _check_overrides(overrides)
    variants = naming3d.normalise_variants(variants)
    run = CatalogRun()
    candidates, seen, used = [], set(), set()
    for root in roots:
        entries = list_root(ssh, root)
        if entries is None:
            run.needs_attention.append(_item(root, root, UNMOUNTED_REASON))
            continue
        run.roots_listed += 1
        run.listed_files += len(entries)
        root_candidates, root_items, root_seen, root_used = _collect_one_root(root, entries, ssh.host, overrides, variants)
        candidates.extend(root_candidates)
        run.needs_attention.extend(root_items)
        seen |= root_seen
        used |= root_used
    run.rows, resolve_items = _resolve_same_timepoint(candidates)
    run.needs_attention.extend(resolve_items)
    run.needs_attention.extend(_item(OVERRIDES_FILENAME, name, OVERRIDE_UNLISTED_REASON, f"site {overrides[name]['site']}")
                               for name in sorted(set(overrides) - seen))
    run.overrides_applied = len(used)
    if not dry_run:
        write_rows(run.rows, actor)
    return run


def catalog_roots(roots, ssh, actor=ACTOR, dry_run=False, overrides=None, variants=()):
    """catalog() for callers that want the (rows, needs_attention) pair only."""
    run = catalog(roots, ssh, actor=actor, dry_run=dry_run, overrides=overrides, variants=variants)
    return run.rows, run.needs_attention


# --- config, report, printing -------------------------------------------------

def load_nas_config(path):
    """Read the carousel's nas.yaml.

    Returns:
        The mapping it holds (host, user, key, source_roots, ...).

    Raises:
        FileNotFoundError: naming the path when it is missing.
        ValueError: naming the path when it is not YAML or not a mapping.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"NAS config not found at {path}")
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ValueError(f"NAS config at {path} is not readable YAML: {exc}") from exc
    if not isinstance(cfg, Mapping):
        raise ValueError(f"NAS config at {path} must be a mapping (host, user, key, source_roots)")
    return cfg


def resolve_roots(cli_roots, cfg):
    """CLI `--root` values if any were given, else `source_roots` from the NAS config."""
    return list(cli_roots) if cli_roots else list(cfg.get("source_roots") or [])


def _check_items(needs_attention):
    """Every needs-attention line as a dict over REPORT_FIELDS with a reason.

    Raises:
        TypeError: when a line is not a mapping.
        ValueError: when a line has no reason.
    """
    items = []
    for item in needs_attention:
        if not isinstance(item, Mapping):
            raise TypeError(f"a needs-attention line must be a dict over {REPORT_FIELDS}, got {type(item).__name__}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"a needs-attention line has no reason: {dict(item)!r}")
        items.append({k: item.get(k, "") for k in REPORT_FIELDS})
    return items


def write_needs_attention_report(needs_attention):
    """Write `needs_attention` to registry.NEEDS_ATTENTION_CSV, replacing
    whatever the previous run left there: the report always reflects the run
    that just finished, not an accumulating log. Written through a private
    temporary file and os.replace, so a reader never sees a half-written
    report and two writers never mix.

    Returns:
        The path written.

    Raises:
        TypeError, ValueError: for a malformed line (nothing is written).
        OSError: naming the path when the report cannot be written.
    """
    items = _check_items(needs_attention)
    path = registry.NEEDS_ATTENTION_CSV
    os.makedirs(registry.ROOT, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=NEEDS_ATTENTION_FILENAME + ".", suffix=registry.TMP_SUFFIX, dir=registry.ROOT)
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(items)
        os.chmod(tmp, REPORT_FILE_MODE)
        os.replace(tmp, path)
    except OSError as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise OSError(f"could not write the needs-attention report at {path}: {exc}") from exc
    return path


def _files_cell(files):
    """"3" when every file counts toward the row, "3 (1 in row)" otherwise."""
    in_row = sum(1 for f in files if f["in_row"])
    return str(len(files)) if in_row == len(files) else f"{len(files)} ({in_row} in row)"


def _print_rows(rows):
    """Print the rows as an aligned table."""
    if not rows:
        print("No timepoints found.")
        return
    header = ("readable_id", "video_location", "video_size_gb", "files", "original_videos")
    table = [header] + [(r["readable_id"], r["fields"]["video_location"], str(r["fields"]["video_size_gb"]),
                          _files_cell(r["files"]), r["fields"]["original_videos"]) for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for i, row in enumerate(table):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * widths[j] for j in range(len(header))))


def _print_needs_attention(needs_attention):
    """Print every needs-attention line, or that there is none."""
    if not needs_attention:
        print("\nNo items need attention.")
        return
    print(f"\n{len(needs_attention)} item(s) need attention:")
    for item in needs_attention:
        detail = f" ({item['detail']})" if item.get("detail") else ""
        print(f"  [{item['root']}] {item['path']}: {item['reason']}{detail}")


def _print_counts(run):
    """The input and output counts of the listing step."""
    files = [f for r in run.rows for f in r["files"]]
    print(f"\nlisted {run.listed_files} files across {run.roots_listed} roots")
    print(f"overrides applied: {run.overrides_applied}")
    print(f"rows: {len(run.rows)}")
    print(f"source files: {len(files)} ({sum(1 for f in files if f['in_row'])} in row)")
    print(f"needs attention: {len(run.needs_attention)}")


def _parser():
    """The command line: config, roots, overrides, name variants, dry run."""
    parser = argparse.ArgumentParser(
        description="Read-only walk of the NAS Archive into TCRMP 3D registry rows. "
                     "Lists over plain ssh only; never pulls a file, never renames anything on the NAS.")
    parser.add_argument("--nas-config", default=DEFAULT_NAS_CONFIG,
                        help="Path to the carousel's nas.yaml (host, user, key, source_roots).")
    parser.add_argument("--root", action="append", default=None,
                        help="NAS season root to walk (repeatable). Default: source_roots from --nas-config.")
    parser.add_argument("--overrides", default=None,
                        help=f"Path to {OVERRIDES_FILENAME} ({','.join(OVERRIDE_FIELDS)}). Default: the file "
                             "beside the registry; a missing file means no overrides.")
    parser.add_argument("--name-variant", action="append", default=None, choices=naming3d.NAME_VARIANTS,
                        help="Accept this token in place of 3D in file names (repeatable). Such files are "
                             "catalogued as 3D and their sidecar line says so.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and print what would be written without touching the registry.")
    return parser


def main(argv=None):
    """Parse the command line, list and resolve (step 1), then write (step 2) unless dry run."""
    args = _parser().parse_args(argv)
    if args.dry_run:
        print("DRY RUN: nothing will be written to the registry.\n")
    cfg = load_nas_config(args.nas_config)
    ssh = SSH(cfg["host"], cfg["user"], cfg["key"])
    roots = resolve_roots(args.root, cfg)
    overrides = load_overrides(args.overrides or os.path.join(registry.ROOT, OVERRIDES_FILENAME))

    run = catalog(roots, ssh, actor=ACTOR, dry_run=True, overrides=overrides, variants=args.name_variant or ())
    _print_rows(run.rows)
    _print_needs_attention(run.needs_attention)
    _print_counts(run)
    print("Step 1 complete")

    if args.dry_run:
        print("\nDRY RUN: needs-attention report not written.")
        return
    rows_written, files_written = write_rows(run.rows, ACTOR)
    report_path = write_needs_attention_report(run.needs_attention)
    print(f"\nrows written: {rows_written}; source files written: {files_written}")
    print(f"Needs-attention report: {report_path}")
    print("Step 2 complete")


if __name__ == "__main__":
    main()
