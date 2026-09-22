"""Read-only walk of the NAS Archive into TCRMP 3D registry rows.

Module: atlascatalog.py (tcrmp_3d_atlas)
Purpose: list one or more NAS season roots over plain ssh, parse every video
    name with the shared grammar in naming3d, group parts into timepoints,
    and record one registry row per clean timepoint plus one sidecar line
    per source file. Nothing is transferred and nothing on the NAS is
    renamed: the Archive stays read-only end to end.
Inputs: the carousel's nas.yaml (host, user, key, source_roots, and the two
    paths the catalog never walks: defaults.shelf_root and merged_root);
    --root roots to walk, each a whole Archive volume as source_roots are or
    one season folder (default: source_roots); catalog_overrides.csv beside the
    registry (columns file_name,site,note; a missing file means none;
    --overrides points elsewhere); --name-variant tokens (demo, 3ddemo) for
    the two seasons whose names carry those tokens in place of 3D;
    catalog_rulings.csv beside the registry (registry.RULINGS_CSV: Lauren's
    rulings on the files the catalog cannot place, read through
    load_rulings; --rulings points elsewhere, --no-rulings ignores it).
Outputs (real run only): one registry row per timepoint through
    registry.upsert(rid, fields, actor="catalog", protect_operator=True), so
    nothing an operator hand-edited is clobbered; one sidecar line per file
    through registry.set_source_files; the needs-attention report rewritten
    whole at registry.NEEDS_ATTENTION_CSV. A rerun against an unchanged
    Archive changes nothing. --dry-run computes and prints everything and
    writes nothing, not even the registry data root.

How a listing becomes rows:

1. One `find` over ssh per root (NUL-delimited size and path pairs, Synology
   system folders pruned). The Shelf (defaults.shelf_root, where the Carousel
   deposits processing folders) and the merged root (merged_root, where
   merged recordings are deposited) are never walked, whatever source root
   holds them: the find prunes them on the NAS, catalog() drops anything
   listed under them anyway, and a root at or under one of them is reported
   and skipped. The Shelf sits inside the source root /volume6/Archive8_12TB,
   so a plain walk on 2026-09-14 reported 61,216 deposited frames, scripts
   and byte-code files as bad names. A root that cannot be listed is
   reported plainly ("this season's drive is not mounted") and skipped.
   A processing folder is never read inside: a directory named processing
   or frames, one named {SITE}_{T#}_3D (naming3d.PROCESSING_FOLDER_NAME) or
   one ending _3dprocessing is pruned in the find (directories only) and
   dropped after listing, with a count; a root inside one is reported and
   skipped. Why: on 2026-09-14 an old Voyager 1 project on Archive9
   (/volume2/Archive9_10TB/TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing/
   frames/<id>/) made the dry run report 22 second recordings and would
   have moved 22 rows of 2025 spring onto frame folders.
2. Companion files (.csv, .txt, .md, .log) are dropped. The rulings are
   applied next, on every run: a leave_out file is dropped before parsing
   and counted, never a needs-attention line; a catalog_as file is parsed
   as if named TCRMP{date}_3D_{site_label}_{transect}[_{part}] (a synthetic
   parse, kind "ruled"), so it groups, resolves and becomes a row or a part
   of one exactly like a well-named file, its real name kept in
   original_videos and on its sidecar line with the ruling sentence
   (_ruling_note). A ruling whose file no listed root contained is reported
   once ("ruled file not found"). Every other name is parsed; a name that
   does not parse is a needs-attention line. A site override replaces the
   parsed site before grouping, so two takes of one site can become two
   labelled rows (LBHLBPFIX1 and LBHLBPFIX2); an override on a ruled file
   is reported, not applied, because the ruling places the file.
3. Files are grouped by identity (project, date, site, transect). An
   identity whose files sit in more than one directory is one recording
   when two things hold together: taken as one set the files are a
   complete part set (every member part-numbered, the numbers exactly 1 to
   N with no repeat, no whole file and no proxy among them), and the
   directories are one folder and the folders inside it (the folder of the
   shallowest part holds or contains every other part; _spanning_top).
   Lauren decided the join on 2026-09-14 for Fish Bay transects 1 to 3 of
   2024-02-16, part 1 in the season folder and part 2 in its subfolder.
   The nesting condition keeps the join inside one season folder whatever
   root was walked: the default run walks whole volumes (source_roots
   names /volume2/Archive9_10TB, not a season folder), and a _1 in one
   folder of a volume beside a _2 in an unrelated folder of the same
   volume is never joined. Its row's location is the directory of part 1,
   each sidecar line carries the file's own directory, and every line of
   the row says which folders hold the parts (NOTE_PARTS_IN_FOLDERS), each
   spelled from the top folder's own name ("TCRMP_2024_PBL;
   TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos"), so the note reads the
   same whether a volume or the season folder itself was walked. Any other
   identity that spans directories (a whole file or a proxy among the
   members, a gap, a repeat, folders that are not nested that way) is
   refused ("part set split across directories"). The accepted downside:
   two takes filed in one season folder and a folder inside it whose part
   numbers happen to complete each other are joined as one recording; the
   folders note is what shows that in the ATLAS, the sidecar keeps each
   part's Archive path, and prep's log lists every part it joined, so such
   a join can be found and undone by hand.
4. Per group: parts plus any whole file is a canonical conflict; one whole
   plain file plus proxy copies is the row alone with each proxy recorded
   in_row=false; more than one whole file otherwise is "resolve by hand";
   two or more parts whose numbers do not run 1 to N are "resolve by hand";
   a lone part is the whole recording (Lauren, 2026-09-11) and is the row
   whatever its number, with that number kept on its sidecar line and in a
   lone-part note; anything else is the row with every member.
5. Across every root, groups that still share one readable id after
   overrides are sorted by (date, root, path): the earliest is the row and
   each later take is reported, its files recorded in_row=false.

The sidecar edit_note is built in one place (_edit_note) in one order: the
ruling sentence, the override note, the second-recording note, the
proxy-mirror note, the parts note (or, for a lone part, the lone-part note),
the folders note of a part set that spans directories, the proxy note, the
demo-token note.

CLI: python3 atlascatalog.py [--nas-config <path>] [--root <NAS season
root>]... [--overrides <path>] [--rulings <path> | --no-rulings]
[--name-variant demo|3ddemo]... [--dry-run]
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
from datetime import datetime

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
# The rulings sidecar (registry.RULINGS_CSV): one line per ruled NAS path over
# registry.RULING_COLUMNS. A catalog_as ruling's cells must make a readable
# name; \Z (not $) so a cell ending in a newline never passes.
RULINGS_FILENAME = registry.RULINGS_FILENAME
RULING_TRANSECT_PATTERN = re.compile(r"^T[0-9]+\Z")
RULING_DATE_PATTERN = re.compile(r"^[0-9]{8}\Z")
# The name kind of a file catalogued by ruling: the identity came from the
# ruling, not from the name, so no demo-token note applies.
KIND_RULED = "ruled"
# How the ruling sentence spells ruled_at: the sidecar's ISO stamp converted
# to the wall clock CLAUDE.md asks for in prose.
RULING_STAMP_FORMAT = "%Y-%m-%d %H:%M"
RULING_STAMP_SUFFIX = " AST"
# Synology keeps thumbnails under @eaDir and deleted files under #recycle;
# neither may reach the name grammar.
PRUNED_NAMES = ("@*", "#recycle")
# A processing folder is never read inside (rule 1 of 2026-09-14): the
# Carousel's own "processing" and "frames" folders, a transect's
# {SITE}_{T#}_3D folder (naming3d.PROCESSING_FOLDER_NAME) and the
# pre-2026-09-08 *_3dprocessing name. An old Voyager 1 project on Archive9
# (/volume2/Archive9_10TB/TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing/frames/)
# made the 2026-09-14 dry run report 22 second recordings and would have
# moved 22 rows of 2025 spring onto frame folders. The words and the suffix
# match in either case; the transect folder matches as naming3d spells it.
PROCESSING_FOLDER_WORDS = ("processing", "frames")
PROCESSING_FOLDER_SUFFIX = "_3dprocessing"
# The transect folder as a find -name pattern: wider than the exact rule (a
# shell pattern cannot say "letters and digits only"), so _drop_processing
# applies the exact rule to whatever the NAS still lists.
PROCESSING_FOLDER_GLOB = "*_T[0-9]*_3D"
# find's -path takes a shell pattern: these characters in an excluded path
# are escaped so the pattern matches that path and nothing else.
FIND_PATTERN_ESCAPES = "\\*?["
FIND_PRINTF = r"%s\0%P\0"
# A NAS path may arrive as the registry writes a location ("146.226.147.140:/volume6/...")
# or as ssh spells a target ("user@host:/volume6/..."); the prefix goes before
# any path comparison.
HOST_PREFIX_PATTERN = re.compile(r"^[^/]+:(?=/)")
# The nas.yaml keys naming the paths the catalog never walks: the Shelf, where
# the Carousel deposits processing folders, and the merged root, where merged
# recordings are deposited (ADR 0006). The Shelf sits inside the source root
# /volume6/Archive8_12TB, so a plain walk of that root on 2026-09-14 reported
# every deposited frame, script and byte-code file (61,216 of them) as a bad name.
EXCLUDED_ROOT_KEYS = (("defaults", "shelf_root"), ("merged_root",))
LIST_TIMEOUT_S = 1800
SSH_TIMEOUT_S = 60
SSH_CONNECT_TIMEOUT_S = 10
SSH_TIMEOUT_RC = 124
BYTES_PER_GB = 1e9
SIZE_DECIMALS = 3
NAME_JOINER = ";"
NOTE_JOINER = "; "
DETAIL_JOINER = "; "
# Two or more parts are a part set: their numbers must run 1 to N, and the
# parts note describes them. One part alone is the whole recording (below).
MIN_PARTS_FOR_SET = 2
# A part set spans folders when its members sit in this many directories or
# more. It joins only when complete across them and the directories are one
# folder and the folders inside it (module docstring, step 3). That scope is
# the same whichever root was walked, a whole volume or one season folder,
# so the folders note spells each folder from that top folder's own name and
# reads the same either way.
MIN_FOLDERS_FOR_SPAN = 2

UNMOUNTED_REASON = "this season's drive is not mounted"
EXCLUDED_ROOT_REASON = "root is at or under the Shelf or the merged root, which the catalog never walks"
EXCLUDED_ROOT_DETAIL = "under {covering} (defaults.shelf_root or merged_root in nas.yaml); nothing here was listed"
PROCESSING_ROOT_REASON = "root is inside a processing folder, which the catalog never reads"
# A folder holding this file is a Voyager 1 project (its frames, bundle and
# reports live beside it), never a season folder: the whole subtree is dropped
# after listing. The old project TCRMP_2025_PBL_01 on Archive9 (2026-09-14).
PROJECT_MARKER_FILE = "analysis_params.yaml"
PROCESSING_ROOT_DETAIL = ("{folder} on the way to it is a processing folder (processing, frames, {{SITE}}_{{T#}}_3D "
                          "or *_3dprocessing); nothing here was listed")
RULED_FILE_NOT_FOUND_REASON = "ruled file not found; the ruling names a file the listing does not contain"
RULING_MALFORMED_REASON = "ruling line could not be read and was skipped; fix it in the ATLAS or in the file"
OVERRIDE_ON_RULED_REASON = "override not applied: the file has a ruling, which places it"
BAD_NAME_REASON = "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}"
CANONICAL_CONFLICT_REASON = ("canonical file already exists alongside part file(s); "
                              "resolve by hand (see atlasprep.md)")
# An identity spanning directories that is not a complete part set across
# them (a whole file or a proxy among the members, a gap, a repeat), or is
# complete but sits in folders that are not one folder and the folders
# inside it: the complete nested case joins instead since 2026-09-14
# (_split_items). NOT_NESTED_DETAIL is added to the detail of the second
# shape so the reader knows the numbers were fine and the folders were not.
SPLIT_ACROSS_DIRS_REASON = "part set split across directories; resolve by hand (see atlasprep.md)"
NOT_NESTED_DETAIL = "complete as a part set, but the folders are not one folder and the folders inside it"
SECOND_DATE_REASON = "second recording date for one timepoint; review which take to keep"
PROXY_MIRROR_REASON = "proxy mirror beside its full file; resolve by hand"
MULTI_WHOLE_REASON = "more than one whole file for one timepoint; resolve by hand"
# A set of two or more parts whose numbers do not run 1 to N (a gap or a
# repeat) is not a whole recording: the missing part may be on another drive,
# may be named in a way nothing can read, or may never have been exported, and
# the duration check at merge time cannot see a part that was never there.
# One part alone is different. By Lauren's decision of 2026-09-11 ("if that's
# it then that's it") a lone part is the whole recording, whatever its number:
# it makes the row, its number stays on the sidecar line (the part column)
# and in the note (NOTE_LONE_PART), and prep renames it at pull time. The
# 2026-09-08 refusal of a lone part paused four transects for MRS_T3_2023ann,
# whose only readable file is part1 (its sibling "part2?" never parses).
INCOMPLETE_PARTS_REASON = ("part numbers do not run 1 to N for one timepoint; the recording is "
                           "not whole here, resolve by hand")
OVERRIDE_UNLISTED_REASON = "override names a file that was not listed"
# Every run re-evaluates every override and every ruling, so the previous
# report's lines under these two pseudo-roots are replaced whatever roots
# were walked (an unlisted override fixed since would otherwise stay listed).
REEVALUATED_ROOTS = (OVERRIDES_FILENAME, RULINGS_FILENAME)

NOTE_RULING = "catalogued by ruling of {who}"
NOTE_PARTS = "parts grouped; physical merge at pull time"
NOTE_LONE_PART = "lone part {part}; taken as the whole recording, prep renames it at pull time"
NOTE_PARTS_IN_FOLDERS = "parts in {count} folders: {folders}"
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


def _strip_host(path):
    """`path` without a leading "host:" or "user@host:" prefix, so a location
    the registry writes compares like a bare NAS path."""
    return HOST_PREFIX_PATTERN.sub("", path, count=1)


def _path_segments(path):
    """The names along an absolute NAS path, host prefix stripped and normalised,
    so two paths compare by whole segments: /volume6/Archive8 is not an
    ancestor of /volume6/Archive8_12TB, and a trailing or doubled slash
    changes nothing."""
    return [segment for segment in posixpath.normpath(_strip_host(path)).split("/") if segment]


def _excluded_ancestor(path, excluded):
    """The excluded path that is `path` itself or one of its ancestors, else None.

    Parameters:
        path: an absolute NAS path, with or without a host prefix.
        excluded: absolute NAS paths the catalog never walks.

    Returns:
        The covering path in normalised form ("/volume6/Archive8_12TB/driver_deposits"),
        the first one in `excluded` when several cover `path`; None when none does.
    """
    segments = _path_segments(path)
    for candidate in excluded:
        prefix = _path_segments(candidate)
        if segments[:len(prefix)] == prefix:
            return "/" + "/".join(prefix)
    return None


def _check_excluded(excluded):
    """Normalise the paths the catalog never walks: host prefix stripped,
    absolute, normalised, each once, in the order given.

    Parameters:
        excluded: an iterable of absolute NAS paths, or None for none.

    Returns:
        A tuple of normalised absolute paths; () when nothing is excluded.

    Raises:
        TypeError: when `excluded` is a bare string or holds a non-string.
        ValueError: when a path is blank, holds a ".." segment (a typo in
            nas.yaml must not silently exclude a parent), or is not absolute
            once its host prefix is stripped.
    """
    if excluded is None:
        return ()
    if isinstance(excluded, (str, bytes)):
        raise TypeError(f"excluded must be a list of NAS paths, not the bare string {excluded!r}")
    checked = []
    for path in excluded:
        if not isinstance(path, str):
            raise TypeError(f"an excluded path must be a string, got {type(path).__name__}")
        bare = _strip_host(path.strip())
        if ".." in bare.split("/"):
            raise ValueError(f"an excluded path may not climb with '..': {path!r}")
        if not bare:
            raise ValueError("an excluded path is blank")
        if not bare.startswith("/"):
            raise ValueError(f"an excluded path must be an absolute NAS path, got {path!r}")
        normalised = posixpath.normpath(bare)
        if normalised not in checked:
            checked.append(normalised)
    return tuple(checked)


def _find_pattern(path):
    """`path` as a find -path pattern that matches that path and nothing else."""
    return "".join("\\" + char if char in FIND_PATTERN_ESCAPES else char for char in path)


def _prune_paths(root, excluded):
    """The excluded paths strictly under `root`, spelled as find will print
    them (the root as given, trailing slash dropped, plus the remaining
    segments), so a -path prune term matches the directory find reaches."""
    root_segments = _path_segments(root)
    prefix = root.rstrip("/") or "/"
    paths = []
    for candidate in excluded:
        segments = _path_segments(candidate)
        if len(segments) > len(root_segments) and segments[:len(root_segments)] == root_segments:
            paths.append(posixpath.join(prefix, "/".join(segments[len(root_segments):])))
    return paths


def _add_prune_term(prune, term):
    """Append one find test to the prune group, joined to the previous one with -o."""
    if prune:
        prune.append("-o")
    prune.extend(term)


def _is_processing_folder(name):
    """True when `name` is a folder the catalog never reads inside.

    That is the Carousel's own "processing" and "frames" folders (either
    case), a transect's {SITE}_{T#}_3D folder (naming3d.is_processing_folder_name,
    the exact case the Carousel creates it in), or the pre-2026-09-08
    *_3dprocessing name (either case). Anything else, None and a blank
    name included, is not.

    Example:
        _is_processing_folder("frames") -> True
        _is_processing_folder("MRS_T1_3D") -> True
        _is_processing_folder("TCRMP2024_postbl_3D_DemoVideos") -> False
    """
    if not isinstance(name, str) or not name:
        return False
    lowered = name.lower()
    return (lowered in PROCESSING_FOLDER_WORDS or lowered.endswith(PROCESSING_FOLDER_SUFFIX)
            or naming3d.is_processing_folder_name(name))


def _processing_prune_terms():
    """The find tests that match a processing folder by name, as one group
    limited to directories: -iname for the words and the old suffix (either
    case, as _is_processing_folder reads them) and -name for the transect
    folder glob. A regular file named "frames" is not pruned: it is not a
    folder, and it stays a bad-name line."""
    names = []
    for word in PROCESSING_FOLDER_WORDS:
        _add_prune_term(names, ["-iname", word])
    _add_prune_term(names, ["-iname", "*" + PROCESSING_FOLDER_SUFFIX])
    _add_prune_term(names, ["-name", PROCESSING_FOLDER_GLOB])
    return ["(", "-type", "d", "("] + names + [")", ")"]


PROCESSING_PRUNE_TERMS = _processing_prune_terms()


def _processing_ancestor(path):
    """The first folder along `path` (host prefix stripped) that is a
    processing folder, the path's own last folder included, or None."""
    return next((segment for segment in _path_segments(path) if _is_processing_folder(segment)), None)


def _find_argv(root, excluded=()):
    """The remote `find` for one root: Synology system folders, processing
    folders and the excluded paths under this root pruned, then every
    regular file as NUL-delimited size and root-relative path pairs.

    Parameters:
        root: the absolute NAS path to walk.
        excluded: paths the catalog never walks (see _check_excluded). Only
            the ones strictly under `root` become prune terms, so the NAS
            itself never lists the Shelf when it sits inside a source root
            (2026-09-14). A root at or under an excluded path is refused by
            list_root before any find is built.

    The prune reads: ( -name @* -o -name #recycle -o ( -type d ( processing
    folder names ) ) [-o -path <excluded>]... ) -prune -o -type f -printf.
    """
    prune = []
    for name in PRUNED_NAMES:
        _add_prune_term(prune, ["-name", name])
    _add_prune_term(prune, PROCESSING_PRUNE_TERMS)
    for path in _prune_paths(root, excluded):
        _add_prune_term(prune, ["-path", _find_pattern(path)])
    return (["find", root, "-mindepth", "1", "("] + prune
            + [")", "-prune", "-o", "-type", "f", "-printf", FIND_PRINTF])


def list_root(ssh, root, timeout=LIST_TIMEOUT_S, excluded=()):
    """Recursively list every regular file under `root` on the NAS.

    One `find` over ssh per season root -- the proven shape: NUL-delimited
    size and path-relative-to-root pairs, so a name holding a space, a
    newline or an odd character can never be misread as a field boundary.

    Parameters:
        ssh: an SSH runner.
        root: absolute NAS path of the season root.
        timeout: seconds allowed for the listing.
        excluded: paths the catalog never walks (the Shelf and the merged
            root, see excluded_roots). The ones under `root` are pruned in
            the find itself; a `root` at or under one of them is refused.

    Returns:
        A list of (relative_path, size_bytes) tuples using forward slashes
        for a nested relative path, or None if `root` cannot be listed
        (drive not mounted, path missing, connection refused, ...). The
        caller reports that plainly and skips the root rather than raising.

    Raises:
        TypeError, ValueError: when `root` is not an absolute path string,
            or `excluded` is malformed (see _check_excluded).
        ValueError: when `root` is at or under an excluded path, naming both.
        ValueError: when `root` is inside a processing folder, naming the folder.
        ValueError: when the listing carries a size that is not a number
            (a truncated or garbled transfer), naming the root and the path.
    """
    _require_root(root)
    excluded = _check_excluded(excluded)
    covering = _excluded_ancestor(root, excluded)
    if covering is not None:
        raise ValueError(f"refusing to list {root}: it is at or under {covering}, "
                         "which the catalog never walks")
    folder = _processing_ancestor(root)
    if folder is not None:
        raise ValueError(f"refusing to list {root}: {folder} on the way to it is a processing folder, "
                         "which the catalog never reads inside")
    rc, out, _err = ssh.run(_find_argv(root, excluded), timeout=timeout)
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


def _edit_note(override_note, second_iso, parts_count, proxy, mirror=False, kind=naming3d.KIND_3D,
               lone_part=None, folders=(), ruling_note=""):
    """The sidecar edit_note, built here and nowhere else, in one order.

    Parameters:
        override_note: the _override_note text, or "" when no override applied.
        second_iso: "YYYY-MM-DD" of this file's take when it is a later
            recording not counted in the row, else None.
        parts_count: how many parts the file's own group holds; the parts
            note appears from MIN_PARTS_FOR_SET up.
        proxy: True when the name carries the _Proxy suffix.
        mirror: True when the file is a proxy copy beside its full file.
        kind: the name token seen ("3D", "demo", "3ddemo"); a variant adds NOTE_DEMO.
        lone_part: the part number when the file is the only part of its
            recording and so the whole recording (Lauren, 2026-09-11), else
            None. The lone-part note takes the parts note's slot.
        folders: the folders a part set spans, in part order (the folder of
            part 1 first) and each spelled from the top folder's own name
            (_folder_label), when the set sits in more than one directory
            (Lauren, 2026-09-14); () or None when it sits in one. The
            folders note follows the parts note.
        ruling_note: the _ruling_note sentence for a file catalogued by
            ruling, or "" for any other file. It comes first: it is the
            reason the file has this identity at all.

    Returns:
        The sentences that apply, joined with NOTE_JOINER; "" when none does.

    Raises:
        TypeError: when override_note or ruling_note is not text, parts_count
            is not an int, lone_part is neither None nor an int, or folders
            is a bare string or holds a non-string.
        ValueError: when parts_count is negative, kind is unknown, lone_part
            is below 1, lone_part is given for a group that is not one part,
            or folders are blank, repeated, fewer than MIN_FOLDERS_FOR_SPAN,
            or given for a group that is not a part set.
    """
    if not isinstance(override_note, str):
        raise TypeError(f"override_note must be text, got {type(override_note).__name__}")
    if not isinstance(ruling_note, str):
        raise TypeError(f"ruling_note must be text, got {type(ruling_note).__name__}")
    if isinstance(parts_count, bool) or not isinstance(parts_count, int):
        raise TypeError(f"parts_count must be an int, got {type(parts_count).__name__}")
    if parts_count < 0:
        raise ValueError(f"parts_count must not be negative, got {parts_count}")
    if kind not in (naming3d.KIND_3D, KIND_RULED) + naming3d.NAME_VARIANTS:
        raise ValueError(f"unknown name kind {kind!r}")
    _check_lone_part(lone_part, parts_count)
    folders = _check_folders(folders, parts_count)
    bits = []
    if ruling_note:
        bits.append(ruling_note)
    if override_note:
        bits.append(override_note)
    if second_iso:
        bits.append(NOTE_SECOND.format(date=second_iso))
    if mirror:
        bits.append(NOTE_MIRROR)
    if parts_count >= MIN_PARTS_FOR_SET:
        bits.append(NOTE_PARTS)
    elif lone_part is not None:
        bits.append(NOTE_LONE_PART.format(part=lone_part))
    if folders:
        bits.append(NOTE_PARTS_IN_FOLDERS.format(count=len(folders), folders=DETAIL_JOINER.join(folders)))
    if proxy:
        bits.append(NOTE_PROXY)
    if kind in naming3d.NAME_VARIANTS:
        bits.append(NOTE_DEMO)
    return NOTE_JOINER.join(bits)


def _check_lone_part(lone_part, parts_count):
    """Refuse a lone_part value that cannot be true of a group with parts_count parts.

    Raises:
        TypeError: when lone_part is neither None nor an int.
        ValueError: when lone_part is below 1, or is given while parts_count
            is not exactly one (a lone part and a part set exclude each other).
    """
    if lone_part is None:
        return
    if isinstance(lone_part, bool) or not isinstance(lone_part, int):
        raise TypeError(f"lone_part must be an int or None, got {type(lone_part).__name__}")
    if lone_part < 1:
        raise ValueError(f"lone_part must be 1 or more, got {lone_part}")
    if parts_count != 1:
        raise ValueError(f"lone_part {lone_part} given for a group of {parts_count} parts; "
                         "a lone part is a group of exactly one part")


def _check_folders(folders, parts_count):
    """The folders a part set spans, as a tuple; () when none were given.

    Raises:
        TypeError: when folders is a bare string, or holds a non-string.
        ValueError: when a folder name is blank or named twice, fewer than
            MIN_FOLDERS_FOR_SPAN are given, or folders come with a group
            that is not a part set (only a part set spans folders).
    """
    if not folders:
        return ()
    if isinstance(folders, (str, bytes)):
        raise TypeError(f"folders must be a sequence of folder names, not the bare string {folders!r}")
    checked = tuple(folders)
    for folder in checked:
        if not isinstance(folder, str):
            raise TypeError(f"a folder name must be text, got {type(folder).__name__}")
        if not folder.strip():
            raise ValueError("a folder name is blank")
    if len(set(checked)) != len(checked):
        raise ValueError(f"a folder is named twice: {checked}")
    if len(checked) < MIN_FOLDERS_FOR_SPAN:
        raise ValueError(f"a part set spans at least {MIN_FOLDERS_FOR_SPAN} folders, got {len(checked)}")
    if parts_count < MIN_PARTS_FOR_SET:
        raise ValueError(f"folders given for a group of {parts_count} parts; only a part set spans folders")
    return checked


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


# --- rulings --------------------------------------------------------------------

def _ruling_key(host, path):
    """The lookup key of a ruled file: its host and its normalised absolute
    path, so a root spelled with a trailing or doubled slash still matches."""
    return (host, posixpath.normpath(path))


def _has_proxy_suffix(name):
    """True when the stem of `name` ends in _Proxy, as naming3d reads it."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.lower().endswith("_proxy")


def _ruling_cells(line, where):
    """`line`'s cells over registry.RULING_COLUMNS as stripped text.

    A part given as a whole number (registry.set_ruling takes one) reads as
    its text; every other cell must be text, None reading as blank.

    Raises:
        TypeError: when `line` is not a mapping or a cell is not text (names `where` and the cell).
        ValueError: when a cell holds a control character (the line would not stay one line).
    """
    if not isinstance(line, Mapping):
        raise TypeError(f"{where}: a ruling must be a mapping over {','.join(registry.RULING_COLUMNS)}, "
                        f"got {type(line).__name__}")
    cells = {}
    for column in registry.RULING_COLUMNS:
        value = line.get(column)
        value = "" if value is None else value
        if column == "part" and isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise TypeError(f"{where}: {column} must be text, got {type(value).__name__}")
        if registry.CONTROL_CHARACTER_PATTERN.search(value):
            raise ValueError(f"{where}: {column} holds a control character")
        cells[column] = value.strip()
    return cells


def _ruled_identity(cells, where):
    """The identity a catalog_as ruling gives its file: the parse of the
    synthetic name TCRMP{date}_3D_{site_label}_{transect}[_{part}], with
    kind KIND_RULED, so the file groups and classifies exactly like a file
    that carried that name.

    Raises:
        ValueError: with a plain sentence naming the cell: a site_label that
            is not letters and digits, a transect that is not T plus digits,
            a date that is not eight digits or not a real calendar date, a
            part that is not a whole number from naming3d.MIN_PART.
    """
    if not OVERRIDE_SITE_PATTERN.match(cells["site_label"]):
        raise ValueError(f"{where}: site_label must be letters and digits only, got {cells['site_label']!r}")
    if not RULING_TRANSECT_PATTERN.match(cells["transect"]):
        raise ValueError(f"{where}: transect must be T followed by digits, got {cells['transect']!r}")
    if not RULING_DATE_PATTERN.match(cells["date"]):
        raise ValueError(f"{where}: date must be YYYYMMDD, got {cells['date']!r}")
    part = cells["part"]
    if part and (not part.isdecimal() or int(part) < naming3d.MIN_PART):
        raise ValueError(f"{where}: part must be a whole number from {naming3d.MIN_PART} or blank, got {part!r}")
    name = f"TCRMP{cells['date']}_{naming3d.KIND_3D}_{cells['site_label']}_{cells['transect']}"
    if part:
        name += f"_{int(part)}"
    parsed = naming3d.parse_video_name(name)
    if parsed is None:
        # The cells passed every shape check above, so only the calendar can have refused the name.
        raise ValueError(f"{where}: date {cells['date']} is not a real calendar date")
    parsed["kind"] = KIND_RULED
    return parsed


def _check_ruling(line, where):
    """One ruling as the catalog applies it.

    Parameters:
        line: a mapping over registry.RULING_COLUMNS, as registry.rulings()
            returns one or a line of the sidecar reads.
        where: how an error names the line ("line 3", "ruling 2").

    Returns:
        A dict over registry.RULING_COLUMNS with every cell stripped, plus
        "key" (_ruling_key of the nas_path) and "identity": the parsed
        identity of a catalog_as (_ruled_identity), None for a leave_out.

    Raises:
        TypeError: when `line` is not a mapping or a cell is not text.
        ValueError: with a plain sentence naming the cell: a nas_path that
            is not <host>:<absolute path>, a file_name that is not its last
            segment, a ruling that is neither leave_out nor catalog_as, a
            blank ruled_by, a leave_out carrying a placement cell, or a
            catalog_as whose cells make no readable name.
    """
    cells = _ruling_cells(line, where)
    if not registry.RULING_NAS_PATH_PATTERN.match(cells["nas_path"]):
        raise ValueError(f"{where}: nas_path must be <host>:<absolute path> as the catalog writes a location, "
                         f"got {cells['nas_path']!r}")
    host, path = cells["nas_path"].split(":", 1)
    if not cells["file_name"] or "/" in cells["file_name"] or cells["file_name"] != path.rsplit("/", 1)[-1]:
        raise ValueError(f"{where}: file_name {cells['file_name']!r} is not the last segment of "
                         f"nas_path {cells['nas_path']!r}")
    if cells["ruling"] not in registry.RULING_VALUES:
        raise ValueError(f"{where}: ruling must be {' or '.join(registry.RULING_VALUES)}, got {cells['ruling']!r}")
    if not cells["ruled_by"]:
        raise ValueError(f"{where}: ruled_by is blank; a ruling names who made it")
    checked = dict(cells, key=_ruling_key(host, path), identity=None)
    if cells["ruling"] == registry.RULING_LEAVE_OUT:
        given = [column for column in registry.RULING_PLACEMENT_COLUMNS if cells[column]]
        if given:
            verb = "was" if len(given) == 1 else "were"
            raise ValueError(f"{where}: a {registry.RULING_LEAVE_OUT} ruling carries no placement, "
                             f"but {', '.join(given)} {verb} given")
        return checked
    checked["identity"] = _ruled_identity(cells, where)
    return checked


def _check_rulings(rulings):
    """Programmatic rulings as the lookup catalog() applies: key -> checked ruling.

    Parameters:
        rulings: an iterable of mappings over registry.RULING_COLUMNS (what
            registry.rulings() or load_rulings returns), or None for none.

    Raises:
        TypeError: when `rulings` is a bare string, a mapping, or not
            iterable, or a line is not a mapping (names its position).
        ValueError: naming the position and the cell for a malformed line
            (see _check_ruling), or a path ruled twice.
    """
    if rulings is None:
        return {}
    if isinstance(rulings, (str, bytes, Mapping)):
        raise TypeError(f"rulings must be a list of rulings over {','.join(registry.RULING_COLUMNS)}, "
                        f"not {type(rulings).__name__}")
    try:
        items = list(rulings)
    except TypeError as exc:
        raise TypeError(f"rulings must be a list of rulings, got {type(rulings).__name__}") from exc
    lookup = {}
    for position, line in enumerate(items, start=1):
        checked = _check_ruling(line, f"ruling {position}")
        if checked["key"] in lookup:
            raise ValueError(f"ruling {position}: {checked['nas_path']} is ruled twice")
        lookup[checked["key"]] = checked
    return lookup


def _ruling_problem(line_no, detail):
    """The needs-attention line for a rulings-file line that could not be read."""
    return _item(RULINGS_FILENAME, f"line {line_no}", RULING_MALFORMED_REASON, detail)


def _read_ruling_line(cells, line_no, lines, seen):
    """Append one CSV record to `lines` when it is a well-formed ruling.

    Returns:
        None when the record was appended; else the problem line for it
        (the wrong number of fields, a cell the rules refuse, a path
        already ruled on an earlier line), the record skipped.
    """
    where = f"line {line_no}"
    if len(cells) != len(registry.RULING_COLUMNS):
        return _ruling_problem(line_no, f"{where} has {len(cells)} fields, expected {len(registry.RULING_COLUMNS)} "
                                        f"({','.join(registry.RULING_COLUMNS)})")
    try:
        checked = _check_ruling(dict(zip(registry.RULING_COLUMNS, cells)), where)
    except (TypeError, ValueError) as exc:
        return _ruling_problem(line_no, str(exc))
    if checked["key"] in seen:
        return _ruling_problem(line_no, f"{where}: {checked['nas_path']} was already ruled at line {seen[checked['key']]}")
    seen[checked["key"]] = line_no
    lines.append({column: checked[column] for column in registry.RULING_COLUMNS})
    return None


def load_rulings(path):
    """Read a rulings sidecar (registry.RULINGS_CSV, or the file --rulings names).

    Tolerant by design: one hand-edited line must never stop a catalog run.
    A line that cannot be read is skipped and reported; the others apply.

    Parameters:
        path: the CSV to read. A missing or empty file means no rulings. A
            BOM from a spreadsheet export, blank lines and a byte that is
            not UTF-8 (read as the replacement character, as the ssh
            listing is) are tolerated.

    Returns:
        (lines, problems): lines are the well-formed rulings as dicts over
        registry.RULING_COLUMNS with cells stripped, in file order; problems
        are needs-attention lines (root RULINGS_FILENAME, path "line N",
        reason RULING_MALFORMED_REASON, detail the plain sentence) for a
        header other than RULING_COLUMNS (then no line is read) and for
        every line that could not be read. ([], []) when the file is missing
        or empty.

    Raises:
        OSError: when the file exists and cannot be read (names the path).
        ValueError: when the csv module cannot parse it (names the path).
    """
    if not os.path.exists(path):
        return [], []
    lines, problems, seen = [], [], {}
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return [], []
            if [h.strip() for h in header] != registry.RULING_COLUMNS:
                problems.append(_ruling_problem(1, f"line 1 must be the header {','.join(registry.RULING_COLUMNS)}, "
                                                   f"got {','.join(header)!r}; no ruling was read"))
                return [], problems
            for cells in reader:
                if not cells or all(not c.strip() for c in cells):
                    continue
                problem = _read_ruling_line(cells, reader.line_num, lines, seen)
                if problem is not None:
                    problems.append(problem)
    except OSError as exc:
        raise OSError(f"could not read the rulings file at {path}: {exc}") from exc
    except csv.Error as exc:
        raise ValueError(f"{path}: not a readable CSV ({exc})") from exc
    return lines, problems


def _ruling_stamp(text):
    """`ruled_at` as "YYYY-MM-DD HH:MM AST": an ISO stamp (with or without an
    offset; one with an offset is converted to AST) becomes that form, and
    any other text is kept as it is, so a stamp can never block a ruling."""
    try:
        stamp = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(registry.AST)
    return stamp.strftime(RULING_STAMP_FORMAT) + RULING_STAMP_SUFFIX


def _ruling_note(line):
    """The sidecar sentence for a file catalogued by ruling: who ruled, when
    (AST), and the note: "catalogued by ruling of LO 2026-09-14 16:26 AST:
    <note>". A blank stamp or a blank note leaves its part off."""
    text = NOTE_RULING.format(who=line["ruled_by"])
    stamp = _ruling_stamp(line.get("ruled_at") or "")
    if stamp:
        text = f"{text} {stamp}"
    note = (line.get("note") or "").strip()
    return f"{text}: {note}" if note else text


def _ruling_summary(ruling):
    """A ruling in a few words for a report line: its kind, its file and, for a catalog_as, its placement."""
    text = f"{ruling['ruling']} {ruling['file_name']}"
    if ruling["ruling"] == registry.RULING_CATALOG_AS:
        text += f" as {ruling['site_label']} {ruling['transect']} {ruling['date']}"
        if ruling["part"]:
            text += f" part {ruling['part']}"
    return text


def _unfound_ruling_items(rulings, matched, host, listed_roots):
    """One needs-attention line per ruling on `host` whose file no listed
    root contained, placed under the first listed root covering its path.

    A ruling under no listed root (a season not walked, a drive not mounted)
    is not reported: the run learnt nothing about it. Overlapping roots
    (a volume and a season folder inside it) report a ruling once.
    """
    items = []
    for key, ruling in rulings.items():
        if key in matched or key[0] != host:
            continue
        covering = next((root for root in listed_roots if _excluded_ancestor(key[1], [root]) is not None), None)
        if covering is not None:
            items.append(_item(covering, key[1], RULED_FILE_NOT_FOUND_REASON, _ruling_summary(ruling)))
    return items


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


@dataclass
class _Tally:
    """What parsing matched, for one directory, one root or the whole run.

    Every member is a set, so a file listed through two overlapping roots
    (a volume and a season folder inside it) counts once. overrides_seen:
    override names that were listed; overrides_used: the ones applied.
    rulings_matched: the keys of rulings whose file was listed; ruled_out:
    the keys of leave_out rulings that dropped a file; rulings_applied: the
    keys of catalog_as rulings applied to a file.
    """
    overrides_seen: set = field(default_factory=set)
    overrides_used: set = field(default_factory=set)
    rulings_matched: set = field(default_factory=set)
    ruled_out: set = field(default_factory=set)
    rulings_applied: set = field(default_factory=set)

    def add(self, other):
        """Fold `other`'s names and keys into this tally."""
        self.overrides_seen |= other.overrides_seen
        self.overrides_used |= other.overrides_used
        self.rulings_matched |= other.rulings_matched
        self.ruled_out |= other.ruled_out
        self.rulings_applied |= other.rulings_applied


def _identity(basename, ruling, variants, tally):
    """The parsed identity of one listed file, or None when it makes no member.

    A ruled file goes by its ruling: a leave_out gives None and counts the
    file as ruled out; a catalog_as gives the ruling's identity
    (_ruled_identity) with the real name's _Proxy suffix kept as a fact of
    the file (the sidecar's proxy column and the proxy note describe the
    file on the Archive), and counts the ruling as applied. Any other file
    is parsed by naming3d; None when its name does not parse.
    """
    if ruling is None:
        return naming3d.parse_video_name(basename, variants)
    if ruling["ruling"] == registry.RULING_LEAVE_OUT:
        tally.ruled_out.add(ruling["key"])
        return None
    tally.rulings_applied.add(ruling["key"])
    parsed = dict(ruling["identity"])
    parsed["proxy"] = _has_proxy_suffix(basename)
    return parsed


def _parse_dir(root, files, overrides, variants, host, rulings):
    """Parse one directory's video files, applying rulings and site overrides.

    A ruled file goes by its ruling (_identity); an override on a ruled
    file is reported, not applied, because the ruling places the file. Any
    other name that does not parse is a BAD_NAME line, and an override
    replaces the site of one that does.

    Returns:
        (members, items, tally): members are dicts {name, size, relpath, dir,
        parsed, site, override, ruling} for every file that makes a member,
        dir being the file's own absolute NAS directory without host (a part
        set may span directories, so a sidecar line never borrows the row's);
        items the needs-attention lines; tally what was matched (_Tally).
    """
    members, items, tally = [], [], _Tally()
    for basename, size, relpath in files:
        abs_dir = _abs_dir(root, posixpath.dirname(relpath))
        path = posixpath.join(root, relpath)
        ruling = rulings.get(_ruling_key(host, f"{abs_dir}/{basename}"))
        override = overrides.get(basename)
        if override is not None:
            tally.overrides_seen.add(basename)
        if ruling is not None:
            tally.rulings_matched.add(ruling["key"])
            if override is not None:
                items.append(_item(root, path, OVERRIDE_ON_RULED_REASON, f"site {override['site']} from {OVERRIDES_FILENAME}"))
            override = None
        parsed = _identity(basename, ruling, variants, tally)
        if parsed is None:
            if ruling is None:
                items.append(_item(root, path, BAD_NAME_REASON))
            continue
        site = parsed["site"]
        if override is not None:
            site = override["site"]
            tally.overrides_used.add(basename)
        members.append({"name": basename, "size": size, "relpath": relpath, "dir": abs_dir,
                        "parsed": parsed, "site": site, "override": override, "ruling": ruling})
    # Sorted so the report reads the same however the NAS orders its listing.
    items.sort(key=lambda item: item["path"])
    return members, items, tally


def _member_key(member):
    """The identity a member groups under: (project, date, effective site, transect)."""
    parsed = member["parsed"]
    return (parsed["project"], parsed["date"], member["site"], parsed["transect"])


def _spanning_top(members):
    """The top folder of a part set that spans directories, as path segments.

    That is the directory of the shallowest member when every member's
    directory is that folder or a folder inside it; None when the members
    sit in folders that are not nested that way (two season folders on one
    volume, two sibling subfolders with nothing above them). Comparison is
    by whole segment, so /v/season never covers /v/season_old, and a
    doubled or trailing slash changes nothing.

    Raises:
        ValueError: when `members` is empty; a set that spans folders has
            members by definition, so an empty one is a caller's mistake.

    Example:
        _spanning_top([{"dir": "/v/season/sub"}, {"dir": "/v/season"}]) -> ("v", "season")
        _spanning_top([{"dir": "/v/a"}, {"dir": "/v/b"}]) -> None
    """
    if not members:
        raise ValueError("a part set that spans folders has at least one member; got none")
    dirs = [_path_segments(member["dir"]) for member in members]
    top = min(dirs, key=len)
    if all(segments[:len(top)] == top for segments in dirs):
        return tuple(top)
    return None


def _folder_label(member, top):
    """The member's directory spelled from the top folder's own name: the top
    folder is its name, a folder inside it is that name plus the path below
    it ("TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos"). The same words
    whichever root was walked, because `top` comes from the parts themselves
    (_spanning_top), not from the root. "/" only for the absurd top of the
    whole file system, so the note never carries a blank folder name.
    """
    tail = _path_segments(member["dir"])[max(len(top) - 1, 0):]
    return "/".join(tail) if tail else "/"


def _is_complete_part_set(members):
    """True when `members` are one whole recording in parts and nothing else.

    That is at least MIN_PARTS_FOR_SET members, every one part-numbered,
    none carrying the _Proxy suffix, and the numbers exactly 1 to N with no
    repeat. The rule a set that spans directories must meet to become one
    row (Lauren, 2026-09-14); a set in one directory goes through
    _classify_group, whose 1 to N check is the same.
    """
    numbers = [m["parsed"]["part"] for m in members]
    if len(numbers) < MIN_PARTS_FOR_SET or any(n is None for n in numbers):
        return False
    if any(m["parsed"]["proxy"] for m in members):
        return False
    return sorted(numbers) == list(range(1, len(numbers) + 1))


def _spanning_candidate(key, root, host, members, top):
    """The one row a complete, nested part set that spans directories makes.

    The members are put in part order, the row's path is the directory of
    part 1, and the folders are named in part order, each once and each
    from the top folder's name (`top`, from _spanning_top), so the sidecar
    note reads from the folder the recording starts in.
    """
    ordered = sorted(members, key=lambda m: m["parsed"]["part"])
    folders = []
    for member in ordered:
        label = _folder_label(member, top)
        if label not in folders:
            folders.append(label)
    return _candidate(key, root, ordered[0]["dir"], host, ordered, [], folders=folders)


def _split_items(root, members_by_dir, host):
    """Identities whose members sit in more than one directory: one row when
    complete and nested across them, else one refusal line each.

    An identity spanning directories is one recording only when its members
    are a complete part set taken together (_is_complete_part_set) and their
    directories are one folder and the folders inside it (_spanning_top).
    Any other spanning shape (a whole file or a proxy among them, a gap, a
    repeat, folders that are not nested that way) is never merged by guess
    and is refused naming every directory; when only the nesting failed the
    detail says so (NOT_NESTED_DETAIL).

    Returns:
        (spanning_keys, items, candidates): every key found in
        MIN_FOLDERS_FOR_SPAN or more directories, joined or refused, so
        per-directory classification skips them all; the refusal lines; and
        one candidate per complete nested set.
    """
    key_members, key_dirs = defaultdict(list), defaultdict(set)
    for dirpath, members in members_by_dir.items():
        for member in members:
            key_members[_member_key(member)].append(member)
            key_dirs[_member_key(member)].add(dirpath)
    spanning = {key for key, dirs in key_dirs.items() if len(dirs) >= MIN_FOLDERS_FOR_SPAN}
    items, candidates = [], []
    for key in sorted(spanning):
        complete = _is_complete_part_set(key_members[key])
        top = _spanning_top(key_members[key]) if complete else None
        if top is not None:
            candidates.append(_spanning_candidate(key, root, host, key_members[key], top))
            continue
        dirs = sorted(_abs_dir(root, d) for d in key_dirs[key])
        detail = f"{key[2]}_{key[3]} {key[1]} found split across: {', '.join(dirs)}"
        if complete:
            detail += f"; {NOT_NESTED_DETAIL}"
        items.append(_item(root, DETAIL_JOINER.join(dirs), SPLIT_ACROSS_DIRS_REASON, detail))
    return spanning, items, candidates


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


def _candidate(key, root, abs_dir, host, members, mirrors, folders=()):
    """One clean timepoint before cross-root resolution.

    `members` are the files that make the row; `mirrors` are proxy copies
    recorded beside it with in_row=false. `parts_count` is how many members
    carry a part number; `lone_part` is that number when the row is one
    part-numbered file and nothing else (the whole recording by Lauren's
    decision of 2026-09-11), else None. `folders` names the directories a
    part set spans, in part order and each spelled from the top folder's
    own name (_folder_label), and is () for a row whose files sit in one
    directory; `abs_dir` is then the directory of part 1 (Lauren, 2026-09-14).
    """
    project, date, site, transect = key
    part_numbers = [m["parsed"]["part"] for m in members if m["parsed"]["part"] is not None]
    lone_part = part_numbers[0] if len(part_numbers) == 1 and len(members) == 1 else None
    return {"key": key, "readable_id": naming3d.readable_id(site, transect, date), "date": date,
            "root": root, "path": abs_dir, "host": host, "members": members, "mirrors": mirrors,
            "parts_count": len(part_numbers), "lone_part": lone_part, "folders": tuple(folders)}


def _classify_group(group, root, abs_dir, host):
    """Apply the per-group rules (module docstring, step 4) to one identity group.

    The 1 to N check applies to a set of MIN_PARTS_FOR_SET or more parts; a
    lone part is the whole recording whatever its number (Lauren, 2026-09-11).

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
    if len(parts) >= MIN_PARTS_FOR_SET:
        numbers = sorted(m["parsed"]["part"] for m in parts)
        if numbers != list(range(1, len(numbers) + 1)):
            detail = f"{names} (part numbers found: {', '.join(str(n) for n in numbers)})"
            return None, [_item(root, abs_dir, INCOMPLETE_PARTS_REASON, detail)]
    return _candidate(group["key"], root, abs_dir, host, members, []), []


def _classify_dir(root, dirpath, members, spanning, host):
    """Every group of one directory through _classify_group, skipping the
    identities that span directories (_split_items joined or refused those)."""
    candidates, items = [], []
    for group in _group_members(members):
        if group["key"] in spanning:
            continue
        candidate, group_items = _classify_group(group, root, _abs_dir(root, dirpath), host)
        items.extend(group_items)
        if candidate is not None:
            candidates.append(candidate)
    return candidates, items


def _collect_one_root(root, entries, host, overrides, variants=(), rulings=None):
    """Turn one root's listing into row candidates and needs-attention lines. Pure.

    Parameters:
        root: the season root that was listed.
        entries: (relpath, size_bytes) pairs from list_root.
        host: the NAS host, stamped into every location and matched against
            each ruling's.
        overrides: file_name -> {site, note}, applied right after parsing.
        variants: name variants admitted by naming3d.parse_video_name.
        rulings: the lookup _check_rulings returns, or None for none.

    Returns:
        (candidates, items, tally): candidates for _resolve_same_timepoint,
        the needs-attention lines, and what was matched (_Tally).
    """
    members_by_dir, items, tally = {}, [], _Tally()
    for dirpath, files in sorted(_video_files(entries).items()):
        members, dir_items, dir_tally = _parse_dir(root, files, overrides, variants, host, rulings or {})
        items.extend(dir_items)
        tally.add(dir_tally)
        if members:
            members_by_dir[dirpath] = members
    spanning, split_items, candidates = _split_items(root, members_by_dir, host)
    items.extend(split_items)
    for dirpath in sorted(members_by_dir):
        dir_candidates, dir_items = _classify_dir(root, dirpath, members_by_dir[dirpath], spanning, host)
        candidates.extend(dir_candidates)
        items.extend(dir_items)
    return candidates, items, tally


# --- resolving across roots -----------------------------------------------------

def _source_record(candidate, member, in_row, second_iso=None, mirror=False):
    """One sidecar line for `member`, as a dict over registry.SOURCE_FILE_INPUT_COLUMNS.

    nas_path is the file's own directory, not the row's: a part set that
    spans directories has its row at part 1's directory and each part on
    its own path, which is where the Carousel copies it from.
    """
    parsed, override, ruling = member["parsed"], member["override"], member["ruling"]
    override_note = _override_note(override["site"], override["note"]) if override else ""
    ruling_note = _ruling_note(ruling) if ruling else ""
    return {
        "file_name": member["name"],
        "nas_path": f"{candidate['host']}:{member['dir']}/{member['name']}",
        "size_bytes": member["size"],
        "filmed_on": _iso_date(parsed["date"]),
        "container": _container(member["name"]),
        "part": parsed["part"],
        "proxy": parsed["proxy"],
        "in_row": in_row,
        "edit_note": _edit_note(override_note, second_iso, candidate["parts_count"], parsed["proxy"],
                                mirror=mirror, kind=parsed["kind"], lone_part=candidate["lone_part"],
                                folders=candidate["folders"], ruling_note=ruling_note),
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
    excluded_files: listed files dropped for sitting under the Shelf or the
        merged root; 0 when the find pruned them on the NAS, as it normally does.
    processing_files: listed files dropped for sitting inside a processing
        folder; 0 when the find pruned those folders on the NAS, as it
        normally does.
    roots_listed: roots that could be listed.
    overrides_applied: override entries applied to a parsed file.
    ruled_out: files dropped by a leave_out ruling (each once, however many
        overlapping roots listed it).
    rulings_applied: catalog_as rulings applied to a listed file (each once).
    rows_left_alone: readable ids write_rows skipped because their run was live.
    """
    rows: list = field(default_factory=list)
    rows_left_alone: list = field(default_factory=list)
    needs_attention: list = field(default_factory=list)
    listed_files: int = 0
    excluded_files: int = 0
    processing_files: int = 0
    roots_listed: int = 0
    overrides_applied: int = 0
    ruled_out: int = 0
    rulings_applied: int = 0


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


def _in_flight(row):
    """True when the Carousel is working on this row: its run holds the processing lock, or its video sits on the Workbench.

    A pulled row's video_location is a bare local path (no NAS host prefix)
    from the pull until the cleaning flip; its original_videos then name the
    files as prep left them, which the archive names would not match. The
    lock alone missed the three sibling timepoints of a running turn on
    2026-09-14 19:02 AST.
    """
    location = str(row.get("video_location") or "").strip()
    return registry.run_is_live(row) or (location.startswith("/") and ":" not in location.split("/", 1)[0])


def write_rows(rows, actor=ACTOR):
    """Record `rows` (as CatalogRun.rows) in the registry and the sidecar.

    Per row, in order: registry.upsert with protect_operator=True (a hand
    correction is never stomped), then registry.set_source_files with the
    row's files. Both are no-ops on an unchanged rerun.

    A row in flight (the Carousel holds its processing lock, or its video
    sits on the Workbench, see _in_flight) is left alone and listed, never
    rewritten:
    the Carousel flips its video cells to the Workbench for the run and
    back afterwards, and a catalog run in between must not pull them out
    from under it (2026-09-14, batch_20260914-165958 was mid-run).

    Returns:
        (rows_written, files_written, rows_left_alone): counts and the
        readable ids left alone because their run is live.

    Raises:
        KeyError, ValueError, TypeError: from the registry library when a row
            or file record is malformed.
    """
    existing = {row["readable_id"]: row for row in registry.load()}
    files_written, written, left_alone = 0, 0, []
    for row in rows:
        current = existing.get(row["readable_id"])
        if current is not None and _in_flight(current):
            left_alone.append(row["readable_id"])
            continue
        registry.upsert(row["readable_id"], row["fields"], actor, protect_operator=True)
        registry.set_source_files(row["readable_id"], row["files"], actor)
        files_written += len(row["files"])
        written += 1
    return written, files_written, left_alone


def _drop_excluded(root, entries, excluded):
    """`entries` of `root` that do not sit under an excluded path, and how many did.

    The second layer of the rule that the Shelf and the merged root are never
    catalogued (2026-09-14): the find already prunes them on the NAS, but a
    listing that arrived another way (a caller with its own entries, a find
    that ignored the prune) still never becomes rows or needs-attention lines.

    Returns:
        (kept_entries, dropped_count)
    """
    if not excluded:
        return entries, 0
    kept = [entry for entry in entries if _excluded_ancestor(posixpath.join(root, entry[0]), excluded) is None]
    return kept, len(entries) - len(kept)


def _drop_processing(entries):
    """`entries` whose relative path passes through no processing folder, and how many did.

    The second layer of rule 1 (the find already prunes those folders on
    the NAS): a listing that arrived another way still never makes a row
    or a needs-attention line out of what sits inside one. Only the
    folders on the way count: a regular file named "frames" is not a folder.
    A folder holding PROJECT_MARKER_FILE (a Voyager 1 project root, which
    the find cannot tell from a season folder) is dropped whole with
    everything under it.

    Returns:
        (kept_entries, dropped_count)
    """
    project_roots = {posixpath.dirname(entry[0]) for entry in entries
                     if posixpath.basename(entry[0]) == PROJECT_MARKER_FILE}
    kept = [entry for entry in entries
            if not any(_is_processing_folder(segment) for segment in posixpath.dirname(entry[0]).split("/"))
            and not _under_any(posixpath.dirname(entry[0]), project_roots)]
    return kept, len(entries) - len(kept)


def _under_any(dirpath, roots):
    """True when `dirpath` is one of `roots` or sits inside one (by whole segment)."""
    return any(dirpath == root or dirpath.startswith(root + "/") for root in roots)


def _listed_entries(run, ssh, root, excluded):
    """List one root for catalog(), keeping `run`'s counts and attention lines current.

    Returns:
        The (relpath, size) entries to collect, everything under an excluded
        path or inside a processing folder dropped; None when the root was
        skipped because it is at or under an excluded path (reported with
        EXCLUDED_ROOT_REASON, the NAS never asked), is inside a processing
        folder (PROCESSING_ROOT_REASON, the NAS never asked) or cannot be
        listed (UNMOUNTED_REASON).
    """
    covering = _excluded_ancestor(root, excluded)
    if covering is not None:
        run.needs_attention.append(_item(root, root, EXCLUDED_ROOT_REASON, EXCLUDED_ROOT_DETAIL.format(covering=covering)))
        return None
    folder = _processing_ancestor(root)
    if folder is not None:
        run.needs_attention.append(_item(root, root, PROCESSING_ROOT_REASON, PROCESSING_ROOT_DETAIL.format(folder=folder)))
        return None
    entries = list_root(ssh, root, excluded=excluded)
    if entries is None:
        run.needs_attention.append(_item(root, root, UNMOUNTED_REASON))
        return None
    run.roots_listed += 1
    run.listed_files += len(entries)
    entries, dropped = _drop_excluded(root, entries, excluded)
    run.excluded_files += dropped
    entries, dropped = _drop_processing(entries)
    run.processing_files += dropped
    return entries


def catalog(roots, ssh, actor=ACTOR, dry_run=False, overrides=None, variants=(), excluded=None, rulings=None):
    """Walk every root over `ssh` and record one registry row per clean timepoint.

    Parameters:
        roots: absolute NAS season roots; repeats are listed once.
        ssh: an SSH runner (its host is stamped into every location and
            matched against each ruling's).
        actor: recorded on every registry write.
        dry_run: compute and return everything, write nothing.
        overrides: file_name -> {site, note} (see load_overrides), or None.
        variants: name variants for naming3d.parse_video_name.
        excluded: absolute NAS paths never walked or catalogued, whatever
            root holds them (the Shelf and the merged root from
            excluded_roots), or None for none. A root at or under one is
            reported and skipped; anything listed under one is dropped.
        rulings: the rulings to apply, an iterable of mappings over
            registry.RULING_COLUMNS (what load_rulings or registry.rulings()
            returns), or None for none. A ruling whose file no listed root
            contained is reported once.

    Returns:
        A CatalogRun.

    Raises:
        TypeError, ValueError: for malformed roots, overrides, variants,
            excluded paths or rulings, before anything is listed.
        ValueError: from list_root when a listing is garbled.
    """
    roots = _check_roots(roots)
    overrides = _check_overrides(overrides)
    variants = naming3d.normalise_variants(variants)
    excluded = _check_excluded(excluded)
    rulings = _check_rulings(rulings)
    run = CatalogRun()
    candidates, tally, listed_roots = [], _Tally(), []
    for root in roots:
        entries = _listed_entries(run, ssh, root, excluded)
        if entries is None:
            continue
        listed_roots.append(root)
        root_candidates, root_items, root_tally = _collect_one_root(root, entries, ssh.host, overrides, variants, rulings)
        candidates.extend(root_candidates)
        run.needs_attention.extend(root_items)
        tally.add(root_tally)
    run.rows, resolve_items = _resolve_same_timepoint(candidates)
    run.needs_attention.extend(resolve_items)
    run.needs_attention.extend(_item(OVERRIDES_FILENAME, name, OVERRIDE_UNLISTED_REASON, f"site {overrides[name]['site']}")
                               for name in sorted(set(overrides) - tally.overrides_seen))
    run.needs_attention.extend(_unfound_ruling_items(rulings, tally.rulings_matched, ssh.host, listed_roots))
    run.overrides_applied = len(tally.overrides_used)
    run.ruled_out, run.rulings_applied = len(tally.ruled_out), len(tally.rulings_applied)
    if not dry_run:
        _written, _files, run.rows_left_alone = write_rows(run.rows, actor)
    return run


def catalog_roots(roots, ssh, actor=ACTOR, dry_run=False, overrides=None, variants=(), excluded=None, rulings=None):
    """catalog() for callers that want the (rows, needs_attention) pair only."""
    run = catalog(roots, ssh, actor=actor, dry_run=dry_run, overrides=overrides, variants=variants,
                  excluded=excluded, rulings=rulings)
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


def _config_path(cfg, keys):
    """The path string at nested `keys` in the NAS config, or None when any key
    along the way is missing, None, or blank.

    Raises:
        TypeError: naming the dotted key when a value on the way is not a
            mapping, or the value at the end is not a string.
    """
    value, walked = cfg, []
    for key in keys:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError(f"{'.'.join(walked)} in the NAS config must be a mapping, got {type(value).__name__}")
        walked.append(key)
        value = value.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{'.'.join(keys)} in the NAS config must be a path string, got {type(value).__name__}")
    return value.strip() or None


def excluded_roots(cfg):
    """The NAS paths the catalog never walks, read from the carousel's nas.yaml.

    They are defaults.shelf_root (the Shelf, where the Carousel deposits
    processing folders) and merged_root (where merged recordings are
    deposited); EXCLUDED_ROOT_KEYS names them. A missing or blank key
    excludes nothing. Why: the Shelf sits inside the source root
    /volume6/Archive8_12TB, so a plain walk of that root on 2026-09-14
    reported 61,216 deposited frames, scripts and byte-code files as bad names.

    Parameters:
        cfg: the mapping load_nas_config returns.

    Returns:
        A list of normalised absolute paths, host prefix stripped, in
        EXCLUDED_ROOT_KEYS order, each once; [] when neither key is set.

    Raises:
        TypeError: when `cfg` is not a mapping, or a key holds something other
            than a path string (the message names the key).
        ValueError: naming the key when its path is not absolute.
    """
    if not isinstance(cfg, Mapping):
        raise TypeError(f"the NAS config must be a mapping, got {type(cfg).__name__}")
    found = []
    for keys in EXCLUDED_ROOT_KEYS:
        path = _config_path(cfg, keys)
        if path is None:
            continue
        try:
            found.extend(_check_excluded([path]))
        except ValueError as exc:
            raise ValueError(f"{'.'.join(keys)} in the NAS config: {exc}") from exc
    return list(_check_excluded(found))


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


def _kept_from_previous_report(path, walked_roots):
    """The previous report's lines for roots this run did not walk.

    Parameters:
        path: registry.NEEDS_ATTENTION_CSV.
        walked_roots: the roots this run listed, whose lines this run
            replaces, together with every line whose root sits under one of
            them (a season folder listed by an earlier per-season run is
            covered by a walk of its volume, 2026-09-14). The lines under
            REEVALUATED_ROOTS (the overrides and rulings files) are replaced
            on every run, because every run re-evaluates every override and
            every ruling.

    Returns:
        A list of dicts over REPORT_FIELDS. Empty when there is no previous
        report, or when it cannot be read: a report that cannot be parsed is
        not a reason to refuse to write the current run's findings.

    A run over one season used to erase every other season's findings, because
    the report was replaced whole. The names it erased are files no rule can
    read, so that report was the only record they exist at all: reading
    catalog_needs_attention.csv on 2026-09-08 showed nine problems where the
    archive holds eighteen (2026-09-08).
    """
    if not os.path.isfile(path):
        return []
    walked = set(walked_roots or ()) | set(REEVALUATED_ROOTS)
    try:
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return []
    covered = tuple(r for r in walked if r.startswith("/"))
    return [{k: (row.get(k) or "") for k in REPORT_FIELDS}
            for row in rows
            if (row.get("root") or "") not in walked
            and _excluded_ancestor(row.get("root") or "", covered) is None]


def write_needs_attention_report(needs_attention, walked_roots=None):
    """Write `needs_attention` to registry.NEEDS_ATTENTION_CSV.

    Parameters:
        needs_attention: this run's lines.
        walked_roots: the roots this run listed. Their previous lines are
            replaced by this run's; a root NOT in this list keeps whatever the
            previous report said about it. None means replace the whole report,
            which is what a caller that walked everything wants.

    Returns:
        The path written.

    Raises:
        TypeError, ValueError: for a malformed line (nothing is written).
        OSError: naming the path when the report cannot be written.

    Written through a private temporary file and os.replace, so a reader never
    sees a half-written report and two writers never mix.
    """
    items = _check_items(needs_attention)
    path = registry.NEEDS_ATTENTION_CSV
    if walked_roots is not None:
        items = _kept_from_previous_report(path, walked_roots) + items
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
    print(f"excluded after listing: {run.excluded_files} files under the Shelf or merged root")
    print(f"inside processing folders, dropped after listing: {run.processing_files} files")
    print(f"overrides applied: {run.overrides_applied}")
    print(f"rulings applied: {run.rulings_applied}; files ruled out: {run.ruled_out}")
    print(f"rows: {len(run.rows)}")
    print(f"source files: {len(files)} ({sum(1 for f in files if f['in_row'])} in row)")
    print(f"needs attention: {len(run.needs_attention)}")


def _parser():
    """The command line: config, roots, overrides, name variants, dry run."""
    parser = argparse.ArgumentParser(
        description="Read-only walk of the NAS Archive into TCRMP 3D registry rows. "
                     "Lists over plain ssh only; never pulls a file, never renames anything on the NAS.")
    parser.add_argument("--nas-config", default=DEFAULT_NAS_CONFIG,
                        help="Path to the carousel's nas.yaml (host, user, key, source_roots; its "
                             "defaults.shelf_root and merged_root are the paths the catalog never walks).")
    parser.add_argument("--root", action="append", default=None,
                        help="NAS root to walk (repeatable): a whole Archive volume, as source_roots are, or "
                             "one season folder. Default: source_roots from --nas-config. A root at or under "
                             "the Shelf or the merged root is reported and skipped.")
    parser.add_argument("--overrides", default=None,
                        help=f"Path to {OVERRIDES_FILENAME} ({','.join(OVERRIDE_FIELDS)}). Default: the file "
                             "beside the registry; a missing file means no overrides.")
    rulings = parser.add_mutually_exclusive_group()
    rulings.add_argument("--rulings", default=None,
                         help=f"Path to {RULINGS_FILENAME} ({','.join(registry.RULING_COLUMNS)}). Default: the "
                              "file beside the registry; a missing file means no rulings. A line that cannot be "
                              "read is skipped and reported; the others apply.")
    rulings.add_argument("--no-rulings", action="store_true",
                         help="Ignore the rulings file: every ruled file is read by its name alone, as before "
                              "any ruling was made.")
    parser.add_argument("--name-variant", action="append", default=None, choices=naming3d.NAME_VARIANTS,
                        help="Accept this token in place of 3D in file names (repeatable). Such files are "
                             "catalogued as 3D and their sidecar line says so.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and print what would be written without touching the registry.")
    return parser


def _rulings_for_run(args):
    """The rulings a command-line run applies, and the problems reading them.

    None with --no-rulings; else the file --rulings names, or
    registry.RULINGS_CSV. The run says which up front, and prints every
    line it could not read as one plain sentence before anything is listed.

    Returns:
        (lines, problems) as load_rulings returns them; ([], []) with
        --no-rulings or when the file is missing.
    """
    if args.no_rulings:
        print("rulings: ignored (--no-rulings)")
        return [], []
    path = args.rulings or registry.RULINGS_CSV
    if not os.path.exists(path):
        print(f"rulings: {path} (no file; none apply)")
        return [], []
    lines, problems = load_rulings(path)
    print(f"rulings: {path} ({len(lines)} ruling{'' if len(lines) == 1 else 's'})")
    for problem in problems:
        print(f"skipped ruling {problem['path']} of {path}: {problem['detail']}")
    return lines, problems


def main(argv=None):
    """Parse the command line, list and resolve (step 1), then write (step 2) unless dry run.

    The Shelf and the merged root named in the NAS config are never walked
    (excluded_roots), processing folders are never read inside, and the
    rulings file is read before anything is listed; the run says so up front.
    """
    args = _parser().parse_args(argv)
    if args.dry_run:
        print("DRY RUN: nothing will be written to the registry.\n")
    cfg = load_nas_config(args.nas_config)
    ssh = SSH(cfg["host"], cfg["user"], cfg["key"])
    roots = resolve_roots(args.root, cfg)
    excluded = excluded_roots(cfg)
    if excluded:
        print(f"never walked: {', '.join(excluded)} (defaults.shelf_root and merged_root in {args.nas_config})")
    print("never read inside: processing folders (processing, frames, {SITE}_{T#}_3D, *_3dprocessing)")
    overrides = load_overrides(args.overrides or os.path.join(registry.ROOT, OVERRIDES_FILENAME))
    rulings, ruling_problems = _rulings_for_run(args)

    run = catalog(roots, ssh, actor=ACTOR, dry_run=True, overrides=overrides, variants=args.name_variant or (),
                  excluded=excluded, rulings=rulings)
    run.needs_attention[:0] = ruling_problems
    _print_rows(run.rows)
    _print_needs_attention(run.needs_attention)
    _print_counts(run)
    print("Step 1 complete")

    if args.dry_run:
        print("\nDRY RUN: needs-attention report not written.")
        return
    rows_written, files_written, left_alone = write_rows(run.rows, ACTOR)
    report_path = write_needs_attention_report(run.needs_attention, walked_roots=roots)
    print(f"\nrows written: {rows_written}; source files written: {files_written}")
    if left_alone:
        print(f"rows left alone, processing right now: {len(left_alone)} ({', '.join(left_alone)})")
    print(f"Needs-attention report: {report_path}")
    print("Step 2 complete")


if __name__ == "__main__":
    main()
