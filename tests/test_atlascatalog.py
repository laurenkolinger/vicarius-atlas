"""Tests for atlascatalog.py: the read-only NAS walk into registry rows.

No NAS or network access anywhere in this file: every test builds a fake ssh
listing runner and injects it into atlascatalog.SSH, and every registry write
goes to a temporary root through VICARIUS_3D_REGISTRY_ROOT plus a reload.

Run: cd /mnt/rip/vicarius_drive/vicarius/modules/tcrmp_3d_atlas/github_repo
     && python3 -m unittest discover -s tests -q
"""
import contextlib, csv, fnmatch, importlib, io, os, posixpath, shlex, shutil, subprocess, sys, tempfile, threading, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")

import atlascatalog
import prep_tools
import registry

GB = 1_000_000_000
HOST = "146.226.147.140"
ROOT_2024 = "/volume4/Archive6_16TB/2024_annual"
ROOT_2025 = "/volume2/Archive9_10TB/2025_pbl"


def _names(spec):
    """Build a fake listing from a compact spec: one "relpath [size_bytes]" per
    line, size defaulting to one GB. Names with spaces use explicit tuples."""
    entries = []
    for line in spec.strip().splitlines():
        parts = line.split()
        if not parts:
            continue
        entries.append((parts[0], int(float(parts[1])) if len(parts) > 1 else GB))
    return entries


# The 2025 spring season as the drive inventory TSV of 2026-09-02 23:45 AST
# lists it under /volume3/Archive2_12TB/TCRMP_2025_PBL/_encoded: (readable id
# before any override, filming date, directory, bytes). 95 files, four RAW
# folders (24/24/24/23), 10,934,914,000,892 bytes, LBH T1 to T3 twice.
PBL_2025_ROOT = "/volume3/Archive2_12TB/TCRMP_2025_PBL/_encoded"
PBL_2025 = [
    ('BTY_T1_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 155475446904),
    ('BTY_T2_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 151666158384),
    ('BTY_T3_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 132085337720),
    ('MGN_T2_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 152388441416),
    ('MGN_T3_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 137527890456),
    ('MGN_T4_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 136014030312),
    ('SVN_T1_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 120441434772),
    ('SVN_T2_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 131258246712),
    ('SVN_T3_2025_pbl', '20250319', 'TCRMP_2025_PBL_RAW_01', 120758892216),
    ('BIT_T1_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 145169003160),
    ('BIT_T2_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 142896568568),
    ('BIT_T3_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 131032139032),
    ('SHR_T2_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 112268563904),
    ('SHR_T4_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 109008334704),
    ('SHR_T5_2025_pbl', '20250320', 'TCRMP_2025_PBL_RAW_01', 116924545048),
    ('MRS_T1_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 97236222464),
    ('MRS_T2_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 96986579680),
    ('MRS_T3_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 105647528188),
    ('SSJ_T1_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 103359108388),
    ('SSJ_T2_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 101944257976),
    ('SSJ_T3_2025_pbl', '20250321', 'TCRMP_2025_PBL_RAW_01', 109743841488),
    ('BPT_T1_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_01', 122553943312),
    ('BPT_T2_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_01', 150728352864),
    ('BPT_T5_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_01', 142821429160),
    ('BWR_T1_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_02', 143824982920),
    ('BWR_T2_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_02', 145206978328),
    ('BWR_T3_2025_pbl', '20250326', 'TCRMP_2025_PBL_RAW_02', 192325928336),
    ('FLC_T3_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 113655616828),
    ('FLC_T5_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 107121232740),
    ('FLC_T6_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 128885581800),
    ('SCP_T1_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 98963946160),
    ('SCP_T2_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 120657862548),
    ('SCP_T3_2025_pbl', '20250327', 'TCRMP_2025_PBL_RAW_02', 107054606952),
    ('HBE_T1_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 104972729092),
    ('HBE_T2_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 94608601040),
    ('HBE_T3_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 99886524144),
    ('SWT_T1_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 91973232456),
    ('SWT_T3_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 105012469716),
    ('SWT_T6_2025_pbl', '20250328', 'TCRMP_2025_PBL_RAW_02', 95491510848),
    ('CSE_T1_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 116009092744),
    ('CSE_T2_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 131280797272),
    ('CSE_T3_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 112132748976),
    ('GKT_T1_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 106033479156),
    ('GKT_T2_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 104963612756),
    ('GKT_T3_2025_pbl', '20250408', 'TCRMP_2025_PBL_RAW_02', 90591150072),
    ('BIX_T4_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_02', 116278010320),
    ('BIX_T5_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_02', 116923582720),
    ('BIX_T6_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_02', 125398430872),
    ('CST_T1_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_03', 112289278064),
    ('CST_T2_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_03', 97746756720),
    ('CST_T3_2025_pbl', '20250411', 'TCRMP_2025_PBL_RAW_03', 87958382952),
    ('EGR_T1_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 98978700992),
    ('EGR_T2_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 105424995636),
    ('EGR_T3_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 95971328640),
    ('SRD_T1_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 138809942104),
    ('SRD_T2_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 130083793116),
    ('SRD_T3_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 138774123756),
    ('SRW_T1_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 81188482648),
    ('SRW_T2_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 87433703720),
    ('SRW_T3_2025_pbl', '20250412', 'TCRMP_2025_PBL_RAW_03', 91041332768),
    ('LBH_T1_2025_pbl', '20250413', 'TCRMP_2025_PBL_RAW_03', 109449240432),
    ('LBH_T2_2025_pbl', '20250413', 'TCRMP_2025_PBL_RAW_03', 115391685084),
    ('LBH_T3_2025_pbl', '20250413', 'TCRMP_2025_PBL_RAW_03', 101538180696),
    ('KGC_T1_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 112906012736),
    ('KGC_T2_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 127848589280),
    ('KGC_T6_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 154640681528),
    ('MTS_T1_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 102683967516),
    ('MTS_T2_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 95952784016),
    ('MTS_T3_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 116556885032),
    ('SPH_T1_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 130561152056),
    ('SPH_T2_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 105131012056),
    ('SPH_T3_2025_pbl', '20250414', 'TCRMP_2025_PBL_RAW_03', 114677257356),
    ('BID_T1_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 86535311516),
    ('BID_T2_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 98174133248),
    ('BID_T3_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 91742436120),
    ('JKB_T1_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 118798986264),
    ('JKB_T2_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 126762033408),
    ('JKB_T3_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 117729070040),
    ('LBH_T1_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 115455218808),
    ('LBH_T2_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 105452971608),
    ('LBH_T3_2025_pbl', '20250415', 'TCRMP_2025_PBL_RAW_04', 111953709968),
    ('CBD_T1_2025_pbl', '20250416', 'TCRMP_2025_PBL_RAW_04', 86750025040),
    ('CBD_T3_2025_pbl', '20250416', 'TCRMP_2025_PBL_RAW_04', 96686358384),
    ('CBS_T1_2025_pbl', '20250416', 'TCRMP_2025_PBL_RAW_04', 103823910296),
    ('CBS_T3_2025_pbl', '20250416', 'TCRMP_2025_PBL_RAW_04', 113845405200),
    ('CBS_T5_2025_pbl', '20250416', 'TCRMP_2025_PBL_RAW_04', 101318754236),
    ('CKR_T2_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 150184996080),
    ('CKR_T3_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 116994155624),
    ('CKR_T4_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 113739400512),
    ('CRB_T1_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 117343351784),
    ('CRB_T2_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 108407048692),
    ('CRB_T3_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 117486703696),
    ('FSB_T1_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 95933178512),
    ('FSB_T2_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 84266160096),
    ('FSB_T3_2025_pbl', '20250508', 'TCRMP_2025_PBL_RAW_04', 107303407232),
]
PBL_2025_BYTES = 10_934_914_000_892
PBL_2025_OVERRIDES = {
    f"TCRMP20250413_3D_LBH_T{n}_Proxy.MOV": {"site": "LBHLBPFIX1", "note": "RAW_03 take; LBH or LBP unresolved"}
    for n in (1, 2, 3)
}
PBL_2025_OVERRIDES.update({
    f"TCRMP20250415_3D_LBH_T{n}_Proxy.MOV": {"site": "LBHLBPFIX2", "note": "RAW_04 take; LBH or LBP unresolved"}
    for n in (1, 2, 3)
})


def _pbl_listing():
    """The 95 (relpath, bytes) entries the fake find returns for the 2025 root."""
    entries = []
    for rid, date, folder, size in PBL_2025:
        site, transect = rid.split("_")[:2]
        entries.append((f"{folder}/TCRMP{date}_3D_{site}_{transect}_Proxy.MOV", size))
    return entries


class _CatalogCase(unittest.TestCase):
    """Temp registry root plus the fake ssh runner every catalog test uses."""

    def setUp(self):
        self.registry_root = tempfile.mkdtemp()
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.registry_root
        importlib.reload(registry)
        self.r = registry

    def tearDown(self):
        shutil.rmtree(self.registry_root)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)

    def _ssh(self, listings, host=HOST):
        """`listings`: NAS root -> list[(relpath, size_bytes)], or None to
        simulate an unmounted/unlistable root. Fakes the ssh listing command
        runner atlascatalog.SSH is built to accept -- no subprocess call."""
        def runner(argv, timeout=60):
            root = argv[1]  # ["find", root, "-mindepth", "1", ...]
            entries = listings.get(root)
            if entries is None:
                return (1, "", f"find: '{root}': No such file or directory")
            payload = "".join(f"{size}\0{relpath}\0" for relpath, size in entries)
            return (0, payload, "")
        return atlascatalog.SSH(host, "driver_svc", "/fake/driver_svc_key", runner=runner)

    def _catalog(self, listings, roots=None, **kwargs):
        """Run the catalog over `listings` (every root unless `roots` narrows it)."""
        ssh = self._ssh(listings)
        return atlascatalog.catalog(list(roots or listings), ssh, actor="catalog", **kwargs)

    def _sidecar(self, rid=None):
        return self.r.source_files(rid)

    def _write_config(self, root):
        fd, cfg_path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, cfg_path)
        with open(cfg_path, "w") as f:
            f.write(f"host: {HOST}\nuser: driver_svc\nkey: /fake/key\nsource_roots:\n  - {root}\n")
        return cfg_path

    @contextlib.contextmanager
    def _stubbed_ssh(self, listings):
        """Swap atlascatalog.SSH for a subclass whose runner serves `listings`."""
        real_ssh_cls = atlascatalog.SSH
        fake = self._ssh(listings)

        class StubSSH(real_ssh_cls):
            def __init__(self, host, user, key, runner=None):
                super().__init__(host, user, key, runner=fake.runner)

        atlascatalog.SSH = StubSSH
        try:
            yield
        finally:
            atlascatalog.SSH = real_ssh_cls

    def _main(self, argv, listings):
        with self._stubbed_ssh(listings):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                atlascatalog.main(argv)
        return buf.getvalue()


class AtlasCatalogTests(_CatalogCase):
    """The original catalog contract: parse, group, protect, report."""

    # --- SSH real runner argv quoting (regression) ----------------------

    def test_real_run_quotes_remote_argv_so_printf_format_survives_shell_word_splitting(self):
        """Regression test for the zero-rows-against-a-real-NAS bug: `SSH._real_run`
        used to append the remote argv to the ssh command unquoted. ssh
        concatenates that argv with spaces and hands the resulting string to
        the *remote* shell verbatim, so the -printf format's `\\0` escapes
        lost their backslashes there before `find` ever saw them, and the
        listing came back empty. This never touches the network: it patches
        `subprocess.run` to capture the exact command `_real_run` composes,
        then `shlex.split`s the remote-side portion the same way a POSIX
        shell parses whatever ssh hands it -- proving the composed command
        round-trips back to the original argv byte-identical, including the
        backslash-zero, only when each remote arg is shell-quoted."""
        remote_argv = ["find", "/volume4/Archive6_16TB/2024_annual", "-mindepth", "1",
                       "-type", "f", "-printf", r"%s\0%P\0"]
        captured = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        ssh = atlascatalog.SSH("146.226.147.140", "driver_svc", "/fake/driver_svc_key")
        real_subprocess_run = atlascatalog.subprocess.run
        atlascatalog.subprocess.run = fake_subprocess_run
        try:
            ssh._real_run(remote_argv)
        finally:
            atlascatalog.subprocess.run = real_subprocess_run

        cmd = captured["cmd"]
        target_index = cmd.index(ssh.target)
        remote_side = cmd[target_index + 1:]
        # Exactly what ssh concatenates with spaces and hands the remote
        # shell as one command line -- so shlex.split-ing it here reproduces
        # that remote shell's word-splitting/quote-removal pass.
        remote_command_string = " ".join(remote_side)
        parsed = shlex.split(remote_command_string)

        self.assertEqual(parsed, remote_argv,
                          "the composed remote command must round-trip through one shell parse "
                          "back to the exact original argv -- including the -printf format's "
                          "backslash-zero -- or the remote find never receives it intact and the "
                          "ssh listing silently returns zero rows")

    # --- basic parse + row creation -----------------------------------

    def test_single_video_creates_row(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000)]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(needs_attention, [])
        self.assertEqual(len(rows), 1)
        # 20240412 is April: season_token is "_pbl" (month <= 6), same rule
        # test_atlasingest.py's own fixtures rely on for this exact date.
        row = self.r.get("MRS_T1_2024_pbl")
        self.assertIsNotNone(row)
        self.assertEqual(row["site"], "MRS")
        self.assertEqual(row["transect"], "T1")
        self.assertEqual(row["year"], "2024")
        self.assertEqual(row["season_token"], "_pbl")
        self.assertEqual(row["original_videos"], "TCRMP20240412_3D_MRS_T1.MP4")
        self.assertEqual(row["video_size_gb"], "5.0")

    def test_video_location_uses_archive_facing_string_format(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 1_000_000_000)]}
        ssh = self._ssh(listings)

        atlascatalog.catalog_roots([root], ssh, actor="catalog")

        row = self.r.get("MRS_T1_2024_pbl")
        self.assertEqual(row["video_location"], f"146.226.147.140:{root}")

    def test_nested_path_recursed_and_included_in_video_location(self):
        root = "/volume5/Archive7_16TB/2023_annual"
        listings = {root: [("MRS_T1/TCRMP20231015_3D_MRS_T1.MOV", 2_000_000_000)]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(needs_attention, [])
        row = self.r.get("MRS_T1_2023ann")
        self.assertIsNotNone(row)
        self.assertEqual(row["video_location"], f"146.226.147.140:{root}/MRS_T1")

    # --- part-set grouping ----------------------------------------------

    def test_multi_part_bare_numeric_grouped_into_one_row(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [
            ("TCRMP20240412_3D_MRS_T1_1.MP4", 1_000_000_000),
            ("TCRMP20240412_3D_MRS_T1_2.MP4", 1_500_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(needs_attention, [])
        self.assertEqual(len(rows), 1)
        row = self.r.get("MRS_T1_2024_pbl")
        self.assertEqual(row["original_videos"],
                          "TCRMP20240412_3D_MRS_T1_1.MP4;TCRMP20240412_3D_MRS_T1_2.MP4")
        self.assertEqual(row["video_size_gb"], "2.5")

    def test_multi_part_literal_part_word_grouped_in_numeric_order(self):
        root = "/volume2/Archive9_10TB/2025_pbl"
        # Listed out of order on purpose: grouping must sort by part number.
        listings = {root: [
            ("TCRMP20250110_3D_BID_T2_part2.MP4", 1_000_000_000),
            ("TCRMP20250110_3D_BID_T2_part1.MP4", 1_000_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(needs_attention, [])
        row = self.r.get("BID_T2_2025_pbl")
        self.assertIsNotNone(row)
        self.assertEqual(row["original_videos"],
                          "TCRMP20250110_3D_BID_T2_part1.MP4;TCRMP20250110_3D_BID_T2_part2.MP4")

    def test_matching_stem_non_video_companion_excluded_from_row_silently(self):
        # naming3d.parse_video_name matches on the filename stem and ignores
        # the extension, so a same-stem .csv (prep_log.csv-style companion)
        # would parse just as cleanly as the real video. It must never join
        # the group: not in original_videos, not counted in video_size_gb,
        # and -- since it is an expected companion, not a bad name -- no
        # needs-attention entry either.
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [
            ("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000),
            ("TCRMP20240412_3D_MRS_T1.csv", 1_000_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(needs_attention, [])
        self.assertEqual(len(rows), 1)
        row = self.r.get("MRS_T1_2024_pbl")
        self.assertIsNotNone(row)
        self.assertEqual(row["original_videos"], "TCRMP20240412_3D_MRS_T1.MP4")
        self.assertEqual(row["video_size_gb"], "5.0")

    # --- idempotency ------------------------------------------------------

    def test_idempotent_rerun_byte_identical_registry_csv(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000)]}
        ssh = self._ssh(listings)

        atlascatalog.catalog_roots([root], ssh, actor="catalog")
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            first = fh.read()

        atlascatalog.catalog_roots([root], ssh, actor="catalog")
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            second = fh.read()

        self.assertEqual(first, second)

    # --- needs-attention ----------------------------------------------

    def test_needs_attention_catches_bad_name(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("GOPR0001.MP4", 1_000_000_000)]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(rows, [])
        self.assertEqual(len(needs_attention), 1)
        self.assertEqual(needs_attention[0]["reason"], "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}")
        self.assertIn("GOPR0001.MP4", needs_attention[0]["path"])

    def test_needs_attention_catches_an_incomplete_part_set_split_across_directories(self):
        # Until 2026-09-14 any part set spanning directories was refused; a
        # complete one (parts 1 and 2 in two folders) is now one recording
        # (CrossFolderPartsTests). A set that is not complete across the
        # folders, here parts 1 and 3, keeps the refusal.
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [
            ("dirA/TCRMP20240412_3D_MRS_T1_1.MP4", 1_000_000_000),
            ("dirB/TCRMP20240412_3D_MRS_T1_3.MP4", 1_000_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(rows, [])
        self.assertEqual(len(needs_attention), 1)
        self.assertIn("split across directories", needs_attention[0]["reason"])
        self.assertEqual(self.r.load(), [], "an incomplete split part set must not write any row")

    def test_needs_attention_catches_canonical_alongside_leftover_part(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [
            ("TCRMP20240412_3D_MRS_T1.MP4", 3_000_000_000),
            ("TCRMP20240412_3D_MRS_T1_1.MP4", 1_000_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(rows, [])
        self.assertEqual(len(needs_attention), 1)
        self.assertIn("resolve by hand", needs_attention[0]["reason"])
        self.assertEqual(self.r.load(), [], "a canonical-alongside-parts conflict must not write any row")

    def test_needs_attention_report_written_beside_registry_data(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("GOPR0001.MP4", 1_000_000_000)]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")
        path = atlascatalog.write_needs_attention_report(needs_attention)

        self.assertEqual(os.path.dirname(path), self.registry_root)
        self.assertTrue(os.path.exists(path))
        with open(path) as fh:
            content = fh.read()
        self.assertIn("GOPR0001.MP4", content)

    # --- protect_operator ----------------------------------------------

    def test_prefilled_video_location_survives_protect_operator(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000)]}
        ssh = self._ssh(listings)
        self.r.upsert("MRS_T1_2024_pbl", {"video_location": "OPERATOR_SET_PATH"}, actor="operator")

        atlascatalog.catalog_roots([root], ssh, actor="catalog")

        row = self.r.get("MRS_T1_2024_pbl")
        self.assertEqual(row["video_location"], "OPERATOR_SET_PATH")
        # video_size_gb is NOT operator-protected, so it still refreshes.
        self.assertEqual(row["video_size_gb"], "5.0")

    # --- unmounted root ---------------------------------------------------

    def test_unmounted_root_skipped_with_plain_reason(self):
        root = "/volume6/Archive8_12TB/2026_pbl"
        listings = {root: None}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(rows, [])
        self.assertEqual(len(needs_attention), 1)
        self.assertEqual(needs_attention[0]["reason"], "this season's drive is not mounted")
        self.assertEqual(self.r.load(), [])

    # --- dry run -----------------------------------------------------------

    def test_dry_run_writes_nothing_to_registry(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000)]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog", dry_run=True)

        self.assertEqual(len(rows), 1)
        self.assertFalse(os.path.exists(self.r.REGISTRY_CSV))
        self.assertEqual(self.r.load(), [])

    # --- CLI helpers (pure, no network) ------------------------------------

    def test_resolve_roots_defaults_to_config_source_roots(self):
        cfg = {"source_roots": ["/volume4/Archive6_16TB", "/volume2/Archive9_10TB"]}
        self.assertEqual(atlascatalog.resolve_roots(None, cfg), cfg["source_roots"])
        self.assertEqual(atlascatalog.resolve_roots(["/custom/root"], cfg), ["/custom/root"])

    def test_load_nas_config_reads_yaml(self):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w") as f:
            f.write("host: 146.226.147.140\nuser: driver_svc\nkey: /fake/key\n"
                     "source_roots:\n  - /volume4/Archive6_16TB\n")
        cfg = atlascatalog.load_nas_config(path)
        self.assertEqual(cfg["host"], "146.226.147.140")
        self.assertEqual(cfg["source_roots"], ["/volume4/Archive6_16TB"])

    def test_main_cli_dry_run_wires_config_source_roots_without_network(self):
        fd, cfg_path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, cfg_path)
        root = "/volume4/Archive6_16TB/2024_annual"
        with open(cfg_path, "w") as f:
            f.write(f"host: 146.226.147.140\nuser: driver_svc\nkey: /fake/key\n"
                     f"source_roots:\n  - {root}\n")

        listings = {root: [("TCRMP20240412_3D_MRS_T1.MP4", 5_000_000_000)]}

        def fake_runner(argv, timeout=60):
            root_arg = argv[1]
            entries = listings.get(root_arg)
            if entries is None:
                return (1, "", "no such path")
            payload = "".join(f"{size}\0{relpath}\0" for relpath, size in entries)
            return (0, payload, "")

        real_ssh_cls = atlascatalog.SSH

        class StubSSH(real_ssh_cls):
            def __init__(self, host, user, key, runner=None):
                super().__init__(host, user, key, runner=fake_runner)

        atlascatalog.SSH = StubSSH
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                atlascatalog.main(["--nas-config", cfg_path, "--dry-run"])
        finally:
            atlascatalog.SSH = real_ssh_cls

        output = buf.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("MRS_T1_2024_pbl", output)
        self.assertEqual(self.r.load(), [])
        # A dry run must not write the needs-attention report either -- it
        # is beside the registry data, but still registry-adjacent state,
        # and the CLI contract promises --dry-run touches nothing.
        report_path = os.path.join(self.registry_root, atlascatalog.NEEDS_ATTENTION_FILENAME)
        self.assertFalse(os.path.exists(report_path))

    def test_dry_run_creates_no_registry_root_and_writes_no_report_file(self):
        # A registry root that does not exist yet (unlike self.registry_root,
        # which tempfile.mkdtemp() already created in setUp): a dry run must
        # never call os.makedirs on it and must never write the report file
        # into it, even when the run does find something needing attention.
        nested_root = os.path.join(self.registry_root, "not_yet_created")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = nested_root
        importlib.reload(registry)
        self.r = registry
        self.assertFalse(os.path.exists(nested_root), "test setup error: root must not pre-exist")

        fd, cfg_path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, cfg_path)
        root = "/volume4/Archive6_16TB/2024_annual"
        with open(cfg_path, "w") as f:
            f.write(f"host: 146.226.147.140\nuser: driver_svc\nkey: /fake/key\n"
                     f"source_roots:\n  - {root}\n")

        # A bad name, so there IS something the report would otherwise hold.
        listings = {root: [("GOPR0001.MP4", 1_000_000_000)]}

        def fake_runner(argv, timeout=60):
            root_arg = argv[1]
            entries = listings.get(root_arg)
            if entries is None:
                return (1, "", "no such path")
            payload = "".join(f"{size}\0{relpath}\0" for relpath, size in entries)
            return (0, payload, "")

        real_ssh_cls = atlascatalog.SSH

        class StubSSH(real_ssh_cls):
            def __init__(self, host, user, key, runner=None):
                super().__init__(host, user, key, runner=fake_runner)

        atlascatalog.SSH = StubSSH
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                atlascatalog.main(["--nas-config", cfg_path, "--dry-run"])
        finally:
            atlascatalog.SSH = real_ssh_cls

        output = buf.getvalue()
        self.assertIn("GOPR0001.MP4", output, "the needs-attention item must still be printed")
        self.assertFalse(os.path.exists(nested_root),
                          "a dry run must never create the registry data root")
        self.assertFalse(os.path.exists(os.path.join(nested_root, atlascatalog.NEEDS_ATTENTION_FILENAME)))


class ListingTests(_CatalogCase):
    """The remote find command and how its output is read."""

    def test_find_argv_carries_the_synology_prune(self):
        seen = {}

        def runner(argv, timeout=60):
            seen["argv"] = argv
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        self.assertEqual(atlascatalog.list_root(ssh, ROOT_2024), [])
        argv = seen["argv"]
        self.assertEqual(argv[:4], ["find", ROOT_2024, "-mindepth", "1"])
        self.assertEqual(argv[4:10], ["(", "-name", "@*", "-o", "-name", "#recycle"])
        self.assertEqual(argv[4:], ["(", "-name", "@*", "-o", "-name", "#recycle", "-o", *atlascatalog.PROCESSING_PRUNE_TERMS, ")",
                                    "-prune", "-o", "-type", "f", "-printf", r"%s\0%P\0"])
        for name in atlascatalog.PRUNED_NAMES:
            self.assertIn(name, argv)

    def test_real_runner_passes_errors_replace(self):
        captured = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key")
        real = atlascatalog.subprocess.run
        atlascatalog.subprocess.run = fake_subprocess_run
        try:
            ssh._real_run(["find", ROOT_2024])
        finally:
            atlascatalog.subprocess.run = real
        self.assertEqual(captured.get("errors"), "replace")
        self.assertTrue(captured.get("text"))

    def test_malformed_size_in_listing_raises_naming_the_root(self):
        def runner(argv, timeout=60):
            return (0, "notanumber\0TCRMP20240412_3D_MRS_T1.MP4\0", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.list_root(ssh, ROOT_2024)
        self.assertIn(ROOT_2024, str(ctx.exception))
        self.assertIn("notanumber", str(ctx.exception))

    def test_odd_trailing_field_is_ignored_and_names_with_spaces_survive(self):
        def runner(argv, timeout=60):
            return (0, "5\0a dir/TCRMP20240412_3D_MRS_T1.MP4\0trailing", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        self.assertEqual(atlascatalog.list_root(ssh, ROOT_2024), [("a dir/TCRMP20240412_3D_MRS_T1.MP4", 5)])

    def test_root_must_be_an_absolute_path_string(self):
        ssh = self._ssh({})
        for bad in ("", "relative/path", None, 3):
            with self.assertRaises((TypeError, ValueError)):
                atlascatalog.list_root(ssh, bad)

    def test_catalog_refuses_a_bare_string_for_roots(self):
        ssh = self._ssh({})
        with self.assertRaises(TypeError):
            atlascatalog.catalog(ROOT_2024, ssh)
        with self.assertRaises(ValueError):
            atlascatalog.catalog([""], ssh)


class ExclusionTests(_CatalogCase):
    """The Shelf and the merged root are never listed or catalogued.

    On 2026-09-14 the Carousel's Shelf (defaults.shelf_root in nas.yaml,
    /volume6/Archive8_12TB/driver_deposits) sat inside the source root
    /volume6/Archive8_12TB, and a dry run reported 61,216 frames, scripts and
    byte-code files from four deposited processing folders as names that do
    not match, beside 46 real findings. Two layers keep them out: the find
    prunes the excluded paths on the NAS, and catalog() drops whatever is
    listed under one anyway.
    """

    SOURCE = "/volume6/Archive8_12TB"
    SHELF = "/volume6/Archive8_12TB/driver_deposits"
    MERGED = "/volume5/Archive10_20TB"
    # What one deposited processing folder looks like beside a season folder,
    # as the 2026-09-14 dry run listed it: a lock, a project, byte code, a
    # frame that parses as nothing, and a re-encoded video whose name parses
    # and would become a row or a second-take line of its own.
    SHELF_SPEC = """
        TCRMP_2024Annual/TCRMP20240412_3D_MRS_T1.MP4 5000000000
        driver_deposits/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl/FLC_T3_3D/.processing.lock 10
        driver_deposits/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl/FLC_T3_3D/FLC_T3_2023_2025.psx 100
        driver_deposits/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl/FLC_T3_3D/.venv/lib/site.pyc 100
        driver_deposits/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl/FLC_T3_3D/frames/FLC_T3_2025_pbl/TCRMP20250327_3D_FLC_T3_frame_000001.jpg 100
        driver_deposits/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl/MRS_T3_3D/frames/MRS_T3_2023ann/TCRMP20231207_3D_MRS_T3.MP4 100
    """
    SHELF_FILES = 5
    MERGED_SPEC = """
        Archive9/TCRMP20240412_3D_SHR_T1.MP4
        Archive10_20TB/2024_annual/TCRMP20240412_3D_MRS_T2.MP4
    """

    def _pruning_runner(self, listings):
        """A fake find that honours its -path prune terms, as the NAS does."""
        def runner(argv, timeout=60):
            root = argv[1]
            pruned = [argv[i + 1] for i, word in enumerate(argv) if word == "-path"]
            entries = listings.get(root)
            if entries is None:
                return (1, "", f"find: '{root}': No such file or directory")
            kept = [(rel, size) for rel, size in entries
                    if not any(posixpath.join(root, rel) == p or posixpath.join(root, rel).startswith(p + "/")
                               for p in pruned)]
            return (0, "".join(f"{size}\0{rel}\0" for rel, size in kept), "")
        return runner

    def _write_config_with_exclusions(self, roots):
        """A nas.yaml naming `roots` as source_roots plus the Shelf and merged root."""
        fd, cfg_path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, cfg_path)
        with open(cfg_path, "w") as f:
            f.write(f"host: {HOST}\nuser: driver_svc\nkey: /fake/key\nsource_roots:\n")
            f.writelines(f"  - {root}\n" for root in roots)
            f.write(f"merged_root: {self.MERGED}\ndefaults:\n  shelf_root: {self.SHELF}\n")
        return cfg_path

    # --- layer two: catalog() drops what was listed under an excluded path ---

    def test_entries_under_the_shelf_are_dropped_and_never_reach_rows_or_the_report(self):
        run = self._catalog({self.SOURCE: _names(self.SHELF_SPEC)}, excluded=[self.SHELF])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.listed_files, self.SHELF_FILES + 1)
        self.assertEqual(run.excluded_files, self.SHELF_FILES)
        self.assertEqual(run.roots_listed, 1)
        self.assertEqual([f["file_name"] for f in self._sidecar()], ["TCRMP20240412_3D_MRS_T1.MP4"])
        atlascatalog.write_needs_attention_report(run.needs_attention, walked_roots=[self.SOURCE])
        with open(self.r.NEEDS_ATTENTION_CSV) as fh:
            self.assertNotIn("driver_deposits", fh.read())

    def test_a_merged_root_path_under_a_source_root_is_dropped(self):
        run = self._catalog({"/volume5": _names(self.MERGED_SPEC)}, excluded=[self.MERGED])
        self.assertEqual([r["readable_id"] for r in run.rows], ["SHR_T1_2024_pbl"])
        self.assertEqual(run.excluded_files, 1)
        self.assertEqual(run.needs_attention, [])

    def test_nothing_changes_when_no_exclusion_is_configured(self):
        # Without the Shelf exclusion the deposited folders are still never
        # read inside: each is a {SITE}_{T#}_3D processing folder (rule 1 of
        # 2026-09-14), so every one of the five Shelf files is dropped after
        # listing and counted as such, and the re-encoded MRS_T3 video inside
        # frames/ makes no row.
        listings = {self.SOURCE: _names(self.SHELF_SPEC)}
        for excluded in ({}, {"excluded": None}, {"excluded": []}, {"excluded": ()}):
            run = self._catalog(listings, **excluded)
            self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"], excluded)
            self.assertEqual(run.excluded_files, 0, excluded)
            self.assertEqual(run.processing_files, self.SHELF_FILES, excluded)
            self.assertEqual(run.needs_attention, [], excluded)
            self.assertEqual(run.listed_files, self.SHELF_FILES + 1, excluded)
        self.assertNotIn("-path", atlascatalog._find_argv(self.SOURCE))
        self.assertNotIn("-path", atlascatalog._find_argv(self.SOURCE, ()))

    def test_exclusion_is_by_path_segment_not_by_prefix(self):
        listing = _names("""
            TCRMP_2024Annual/TCRMP20240412_3D_MRS_T1.MP4
            driver_deposits_old/TCRMP20240412_3D_MRS_T2.MP4
            driver_deposits/TCRMP20240412_3D_MRS_T3.MP4
        """)
        # /volume6/Archive8 is not an ancestor of /volume6/Archive8_12TB/...
        run = self._catalog({self.SOURCE: listing}, excluded=["/volume6/Archive8"])
        self.assertEqual(len(run.rows), 3)
        self.assertEqual(run.excluded_files, 0)
        self.assertNotIn("-path", atlascatalog._find_argv(self.SOURCE, ["/volume6/Archive8"]))
        # driver_deposits_old is not under driver_deposits; driver_deposits is.
        run = self._catalog({self.SOURCE: listing}, excluded=[self.SHELF])
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["MRS_T1_2024_pbl", "MRS_T2_2024_pbl"])
        self.assertEqual(run.excluded_files, 1)
        self.assertEqual(atlascatalog._excluded_ancestor("/volume6/Archive8_12TB/x", ["/volume6/Archive8"]), None)
        self.assertEqual(atlascatalog._excluded_ancestor(self.SHELF + "/x", [self.SHELF]), self.SHELF)
        self.assertEqual(atlascatalog._excluded_ancestor(self.SHELF, [self.SHELF]), self.SHELF)
        self.assertEqual(atlascatalog._excluded_ancestor(self.SHELF + "//x/", [self.SHELF + "/"]), self.SHELF)

    def test_a_host_prefix_is_stripped_before_comparing(self):
        run = self._catalog({self.SOURCE: _names(self.SHELF_SPEC)}, excluded=[f"{HOST}:{self.SHELF}"])
        self.assertEqual(run.excluded_files, self.SHELF_FILES)
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        cfg = {"merged_root": f"{HOST}:{self.MERGED}/", "defaults": {"shelf_root": f"driver_svc@{HOST}:{self.SHELF}"}}
        self.assertEqual(atlascatalog.excluded_roots(cfg), [self.SHELF, self.MERGED])
        self.assertEqual(atlascatalog._strip_host("/volume6/odd:name/x"), "/volume6/odd:name/x")

    def test_a_hundred_thousand_shelf_entries_are_dropped_in_bounded_time(self):
        entries = [("TCRMP_2024Annual/TCRMP20240412_3D_MRS_T1.MP4", GB)]
        entries += [(f"driver_deposits/deposit_{i % 4}/FLC_T3_3D/frames/frame_{i:06d}.jpg", 100) for i in range(100_000)]
        started = time.monotonic()
        run = self._catalog({self.SOURCE: entries}, dry_run=True, excluded=[self.SHELF, self.MERGED])
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10.0, f"100,000 shelf files took {elapsed:.1f}s")
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.excluded_files, 100_000)
        self.assertEqual(run.needs_attention, [])

    # --- layer one: the find prunes the excluded paths on the NAS ----------

    def test_find_argv_carries_prune_terms_only_for_excluded_paths_under_that_root(self):
        argv = atlascatalog._find_argv(self.SOURCE, [self.SHELF, self.MERGED])
        self.assertEqual(argv[:4], ["find", self.SOURCE, "-mindepth", "1"])
        self.assertEqual(argv[4:], ["(", "-name", "@*", "-o", "-name", "#recycle", "-o", *atlascatalog.PROCESSING_PRUNE_TERMS,
                                    "-o", "-path", self.SHELF, ")", "-prune", "-o", "-type", "f", "-printf", r"%s\0%P\0"])
        self.assertNotIn(self.MERGED, argv)
        other = "/volume4/Archive6_16TB"
        self.assertNotIn("-path", atlascatalog._find_argv(other, [self.SHELF, self.MERGED]))
        # The root itself is never a prune term: a root under an excluded path is refused before any find.
        self.assertNotIn("-path", atlascatalog._find_argv(self.SHELF, [self.SHELF]))
        # A root written with a trailing slash still yields the path find prints.
        self.assertIn(self.SHELF, atlascatalog._find_argv(self.SOURCE + "/", [self.SHELF]))
        # Two excluded paths under one root are both pruned.
        both = atlascatalog._find_argv("/volume6", [self.SHELF, "/volume6/scratch", self.MERGED])
        self.assertEqual(both.count("-path"), 2)
        self.assertIn("/volume6/scratch", both)

    def test_list_root_passes_the_prune_terms_to_the_nas(self):
        seen = {}

        def runner(argv, timeout=60):
            seen["argv"] = argv
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        self.assertEqual(atlascatalog.list_root(ssh, self.SOURCE, excluded=[self.SHELF, self.MERGED]), [])
        self.assertEqual(seen["argv"][seen["argv"].index("-path"):][:2], ["-path", self.SHELF])
        self.assertEqual(atlascatalog.list_root(ssh, self.SOURCE), [])
        self.assertNotIn("-path", seen["argv"])

    def test_find_argv_escapes_glob_characters_in_an_excluded_path(self):
        argv = atlascatalog._find_argv("/v", ["/v/a[1]*?", "/v/back\\slash"])
        self.assertIn(r"/v/a\[1]\*\?", argv)
        self.assertIn("/v/back\\\\slash", argv)
        self.assertNotIn("/v/a[1]*?", argv)

    def test_the_find_prune_alone_keeps_the_shelf_off_the_nas(self):
        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key",
                               runner=self._pruning_runner({self.SOURCE: _names(self.SHELF_SPEC)}))
        run = atlascatalog.catalog([self.SOURCE], ssh, excluded=[self.SHELF, self.MERGED])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.listed_files, 1, "the NAS never listed the Shelf")
        self.assertEqual(run.excluded_files, 0, "layer two had nothing left to drop")
        self.assertEqual(run.needs_attention, [])

    # --- a root at or under an excluded path ---------------------------------

    def test_a_root_at_or_under_an_excluded_path_is_reported_not_listed(self):
        seen = []

        def runner(argv, timeout=60):
            seen.append(argv[1])
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        good = "/volume4/Archive6_16TB"
        bad = [self.SHELF, self.SHELF + "/TCRMP_11sep26_LO_MRS1+FLC3_23ann-25pbl", self.MERGED + "/2025_pbl"]
        run = atlascatalog.catalog(bad + [good], ssh, excluded=[self.SHELF, self.MERGED])
        self.assertEqual(seen, [good], "an excluded root must never reach the NAS")
        self.assertEqual(run.roots_listed, 1)
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.EXCLUDED_ROOT_REASON] * 3)
        self.assertEqual([i["root"] for i in run.needs_attention], bad)
        self.assertEqual([i["path"] for i in run.needs_attention], bad)
        for item, covering in zip(run.needs_attention, (self.SHELF, self.SHELF, self.MERGED)):
            self.assertIn(covering, item["detail"])
        self.assertNotIn(atlascatalog.UNMOUNTED_REASON, [i["reason"] for i in run.needs_attention])
        rows, items = atlascatalog.catalog_roots([self.SHELF], ssh, excluded=[self.SHELF])
        self.assertEqual((rows, [i["reason"] for i in items]), ([], [atlascatalog.EXCLUDED_ROOT_REASON]))

    def test_list_root_refuses_an_excluded_root(self):
        ssh = self._ssh({self.SHELF: _names("TCRMP20240412_3D_MRS_T1.MP4")})
        for root in (self.SHELF, self.SHELF + "/", self.SHELF + "/TCRMP_11sep26_LO"):
            with self.assertRaises(ValueError) as ctx:
                atlascatalog.list_root(ssh, root, excluded=[self.SHELF])
            self.assertIn(self.SHELF, str(ctx.exception))
            self.assertIn(root, str(ctx.exception))
        self.assertEqual(len(atlascatalog.list_root(ssh, self.SHELF)), 1, "no exclusion, no refusal")

    # --- reading nas.yaml -------------------------------------------------------

    def test_excluded_roots_reads_both_keys_and_tolerates_missing_ones(self):
        self.assertEqual(atlascatalog.excluded_roots({}), [])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": {}}), [])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": None}), [])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": {"shelf_root": ""}, "merged_root": None}), [])
        self.assertEqual(atlascatalog.excluded_roots({"merged_root": self.MERGED}), [self.MERGED])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": {"shelf_root": self.SHELF + "/"}}), [self.SHELF])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": {"shelf_root": self.SHELF}, "merged_root": self.MERGED}),
                         [self.SHELF, self.MERGED])
        self.assertEqual(atlascatalog.excluded_roots({"defaults": {"shelf_root": self.SHELF}, "merged_root": self.SHELF}),
                         [self.SHELF])

    def test_excluded_roots_reads_the_carousel_config_as_deployed(self):
        if not os.path.exists(atlascatalog.DEFAULT_NAS_CONFIG):
            self.skipTest(f"no carousel config at {atlascatalog.DEFAULT_NAS_CONFIG}")
        cfg = atlascatalog.load_nas_config(atlascatalog.DEFAULT_NAS_CONFIG)
        excluded = atlascatalog.excluded_roots(cfg)
        self.assertEqual(excluded, [cfg["defaults"]["shelf_root"], cfg["merged_root"]])
        # The reason this rule exists: the Shelf sits inside a source root.
        self.assertIsNotNone(atlascatalog._excluded_ancestor(cfg["defaults"]["shelf_root"], cfg["source_roots"]))

    def test_excluded_roots_refuses_a_bad_value_naming_the_key(self):
        with self.assertRaises(TypeError):
            atlascatalog.excluded_roots(["/x"])
        for bad, key in (({"merged_root": 3}, "merged_root"), ({"defaults": {"shelf_root": ["/x"]}}, "defaults.shelf_root"),
                         ({"defaults": "/x"}, "defaults")):
            with self.assertRaises(TypeError) as ctx:
                atlascatalog.excluded_roots(bad)
            self.assertIn(key, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.excluded_roots({"merged_root": "relative/path"})
        self.assertIn("merged_root", str(ctx.exception))

    def test_catalog_refuses_a_malformed_excluded_parameter_before_listing(self):
        called = []

        def runner(argv, timeout=60):
            called.append(argv)
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        for bad, error in ((self.SHELF, TypeError), ([3], TypeError), (["relative"], ValueError), ([""], ValueError),
                           ([f"{HOST}:relative"], ValueError)):
            with self.assertRaises(error, msg=repr(bad)):
                atlascatalog.catalog([ROOT_2024], ssh, excluded=bad)
        self.assertEqual(called, [])

    # --- the command line ---------------------------------------------------------

    def test_main_dry_run_wires_shelf_root_and_merged_root_from_nas_yaml(self):
        cfg = self._write_config_with_exclusions([self.SOURCE, "/volume5"])
        listings = {self.SOURCE: _names(self.SHELF_SPEC), "/volume5": _names(self.MERGED_SPEC)}
        output = self._main(["--nas-config", cfg, "--dry-run"], listings)
        self.assertIn(f"never walked: {self.SHELF}, {self.MERGED}", output)
        self.assertIn("MRS_T1_2024_pbl", output)
        self.assertIn("SHR_T1_2024_pbl", output)
        self.assertNotIn("MRS_T2_2024_pbl", output, "the merged-root video must not become a row")
        self.assertNotIn("MRS_T3_2023ann", output, "the deposited video must not become a row")
        self.assertNotIn("driver_deposits/", output, "no listed deposit path may be printed")
        for line in ("rows: 2", "needs attention: 0", f"excluded after listing: {self.SHELF_FILES + 1} files",
                     "Step 1 complete", "DRY RUN: needs-attention report not written"):
            self.assertIn(line, output, output)
        self.assertEqual(self.r.load(), [])
        self.assertFalse(os.path.exists(self.r.NEEDS_ATTENTION_CSV))

    def test_main_reports_a_root_under_the_shelf_instead_of_listing_it(self):
        cfg = self._write_config_with_exclusions([self.SOURCE])
        output = self._main(["--nas-config", cfg, "--dry-run", "--root", self.SHELF], {})
        self.assertIn(atlascatalog.EXCLUDED_ROOT_REASON, output)
        self.assertNotIn(atlascatalog.UNMOUNTED_REASON, output, "the NAS was never asked")
        self.assertIn("rows: 0", output)
        self.assertIn("needs attention: 1", output)

    def test_main_real_run_replaces_the_shelf_lines_of_an_earlier_report(self):
        # The report of 2026-09-14 held 61,216 Shelf lines under this root; a
        # real run over the root replaces them with this run's findings.
        atlascatalog.write_needs_attention_report([
            {"root": self.SOURCE, "path": f"{self.SHELF}/old/frame.jpg", "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
            {"root": "/volume2/Archive9_10TB", "path": "/volume2/Archive9_10TB/stays.MOV",
             "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
        ])
        cfg = self._write_config_with_exclusions([self.SOURCE])
        output = self._main(["--nas-config", cfg], {self.SOURCE: _names(self.SHELF_SPEC)})
        self.assertIn("Step 2 complete", output)
        self.assertIn(f"excluded after listing: {self.SHELF_FILES} files", output)
        self.assertEqual([r["path"] for r in self.r.needs_attention()], ["/volume2/Archive9_10TB/stays.MOV"])
        self.assertEqual([r["readable_id"] for r in self.r.load()], ["MRS_T1_2024_pbl"])
        self.assertEqual(len(self._sidecar()), 1)


class EditNoteTests(unittest.TestCase):
    """The one place the sidecar edit_note is built, in one order."""

    def test_order_is_override_second_mirror_parts_proxy_demo(self):
        note = atlascatalog._edit_note("site label X applied from catalog_overrides.csv: why",
                                       "2025-04-15", 2, True, mirror=True, kind="demo")
        self.assertEqual(note, "site label X applied from catalog_overrides.csv: why; "
                               "second recording on 2025-04-15, not counted in the row; see needs-attention; "
                               "proxy mirror beside its full file, not counted in the row; see needs-attention; "
                               "parts grouped; physical merge at pull time; "
                               "proxy suffix dropped at rename; "
                               "name uses the demo token; catalogued as 3D")

    def test_lone_part_note_takes_the_parts_slot(self):
        note = atlascatalog._edit_note("", "2025-04-15", 1, True, kind="demo", lone_part=2)
        self.assertEqual(note, "second recording on 2025-04-15, not counted in the row; see needs-attention; "
                               "lone part 2; taken as the whole recording, prep renames it at pull time; "
                               "proxy suffix dropped at rename; "
                               "name uses the demo token; catalogued as 3D")

    def test_each_component_alone(self):
        self.assertEqual(atlascatalog._edit_note("", None, 0, False), "")
        self.assertEqual(atlascatalog._edit_note("", None, 1, False), "")
        self.assertEqual(atlascatalog._edit_note("", None, 1, False, lone_part=1),
                         atlascatalog.NOTE_LONE_PART.format(part=1))
        self.assertEqual(atlascatalog._edit_note("", None, 1, False, lone_part=7), "lone part 7; taken as the "
                         "whole recording, prep renames it at pull time")
        self.assertEqual(atlascatalog._edit_note("", None, 2, False), atlascatalog.NOTE_PARTS)
        self.assertEqual(atlascatalog._edit_note("", None, 0, True), atlascatalog.NOTE_PROXY)
        self.assertEqual(atlascatalog._edit_note("", None, 0, False, kind="3ddemo"), atlascatalog.NOTE_DEMO)
        self.assertEqual(atlascatalog._edit_note("", "2025-04-15", 0, False),
                         "second recording on 2025-04-15, not counted in the row; see needs-attention")

    def test_bad_arguments_are_refused(self):
        with self.assertRaises(TypeError):
            atlascatalog._edit_note(None, None, "2", False)
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, -1, False)
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 0, False, kind="video")

    def test_bad_lone_part_arguments_are_refused(self):
        with self.assertRaises(TypeError):
            atlascatalog._edit_note("", None, 1, False, lone_part="2")
        with self.assertRaises(TypeError):
            atlascatalog._edit_note("", None, 1, False, lone_part=True)
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 1, False, lone_part=0)
        # A lone part and a part set cannot both be true of one group.
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 2, False, lone_part=1)
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 0, False, lone_part=1)

    def test_folders_note_follows_the_parts_note(self):
        self.assertEqual(atlascatalog._edit_note("", None, 2, False, folders=("TCRMP_2024_PBL", "TCRMP_2024_PBL/sub")),
                         "parts grouped; physical merge at pull time; "
                         "parts in 2 folders: TCRMP_2024_PBL; TCRMP_2024_PBL/sub")
        self.assertEqual(atlascatalog._edit_note("", None, 3, False, folders=["b", "a", "c"]),
                         "parts grouped; physical merge at pull time; parts in 3 folders: b; a; c")

    def test_folders_note_sits_between_the_parts_note_and_the_proxy_note(self):
        note = atlascatalog._edit_note("site label X applied from catalog_overrides.csv", "2024-02-16", 2, True,
                                       kind="demo", folders=("a", "b"))
        self.assertEqual(note, "site label X applied from catalog_overrides.csv; "
                               "second recording on 2024-02-16, not counted in the row; see needs-attention; "
                               "parts grouped; physical merge at pull time; "
                               "parts in 2 folders: a; b; "
                               "proxy suffix dropped at rename; "
                               "name uses the demo token; catalogued as 3D")

    def test_no_folders_means_no_folders_note(self):
        self.assertEqual(atlascatalog._edit_note("", None, 2, False, folders=()), atlascatalog.NOTE_PARTS)
        self.assertEqual(atlascatalog._edit_note("", None, 2, False, folders=None), atlascatalog.NOTE_PARTS)

    def test_bad_folders_arguments_are_refused(self):
        with self.assertRaises(TypeError):
            atlascatalog._edit_note("", None, 2, False, folders="ab")
        with self.assertRaises(TypeError):
            atlascatalog._edit_note("", None, 2, False, folders=("a", 3))
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 2, False, folders=("a",))
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 2, False, folders=("a", ""))
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 2, False, folders=("a", "a"))
        # Folders belong to a part set: a lone part or a whole file has none.
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 1, False, folders=("a", "b"))
        with self.assertRaises(ValueError):
            atlascatalog._edit_note("", None, 0, False, folders=("a", "b"))

    def test_override_note_with_and_without_text(self):
        self.assertEqual(atlascatalog._override_note("LBHLBPFIX1", "why"),
                         "site label LBHLBPFIX1 applied from catalog_overrides.csv: why")
        self.assertEqual(atlascatalog._override_note("LBHLBPFIX1", ""),
                         "site label LBHLBPFIX1 applied from catalog_overrides.csv")


class SidecarTests(_CatalogCase):
    """One sidecar line per source file, through registry.set_source_files."""

    def test_lone_proxy_writes_a_sidecar_row_with_the_rename_note(self):
        self._catalog({ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV 109449240432")})
        files = self._sidecar("LBH_T1_2025_pbl")
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f["file_name"], "TCRMP20250413_3D_LBH_T1_Proxy.MOV")
        self.assertEqual(f["nas_path"], f"{HOST}:{ROOT_2025}/TCRMP20250413_3D_LBH_T1_Proxy.MOV")
        self.assertEqual(f["size_bytes"], "109449240432")
        self.assertEqual(f["filmed_on"], "2025-04-13")
        self.assertEqual(f["container"], "mov")
        self.assertEqual(f["part"], "")
        self.assertEqual(f["proxy"], "true")
        self.assertEqual(f["in_row"], "true")
        self.assertEqual(f["edit_note"], atlascatalog.NOTE_PROXY)
        self.assertEqual(f["recorded_by"], "catalog")
        self.assertTrue(f["recorded_at"].endswith("-04:00"))
        self.assertEqual(self.r.get("LBH_T1_2025_pbl")["video_size_gb"], "109.449")

    def test_multi_part_rows_carry_part_numbers_and_the_merge_note(self):
        self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_2.MP4 1500000000
            TCRMP20240412_3D_MRS_T1_1.MP4 1000000000
        """)})
        files = self._sidecar("MRS_T1_2024_pbl")
        self.assertEqual([(f["file_name"], f["part"], f["edit_note"]) for f in files], [
            ("TCRMP20240412_3D_MRS_T1_1.MP4", "1", atlascatalog.NOTE_PARTS),
            ("TCRMP20240412_3D_MRS_T1_2.MP4", "2", atlascatalog.NOTE_PARTS),
        ])
        self.assertTrue(all(f["in_row"] == "true" for f in files))

    def test_proxy_beside_canonical_counts_only_the_canonical(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MP4 5000000000
            TCRMP20240412_3D_MRS_T1_Proxy.MP4 1000000000
        """)})
        row = self.r.get("MRS_T1_2024_pbl")
        self.assertEqual(row["original_videos"], "TCRMP20240412_3D_MRS_T1.MP4")
        self.assertEqual(row["video_size_gb"], "5.0")
        files = {f["file_name"]: f for f in self._sidecar("MRS_T1_2024_pbl")}
        self.assertEqual(files["TCRMP20240412_3D_MRS_T1.MP4"]["in_row"], "true")
        mirror = files["TCRMP20240412_3D_MRS_T1_Proxy.MP4"]
        self.assertEqual(mirror["in_row"], "false")
        self.assertEqual(mirror["proxy"], "true")
        self.assertIn("proxy mirror beside its full file", mirror["edit_note"])
        self.assertEqual(len(run.needs_attention), 1)
        item = run.needs_attention[0]
        self.assertEqual(item["reason"], atlascatalog.PROXY_MIRROR_REASON)
        self.assertEqual(item["path"], f"{ROOT_2024}/TCRMP20240412_3D_MRS_T1_Proxy.MP4")
        self.assertIn("TCRMP20240412_3D_MRS_T1.MP4", item["detail"])

    def test_container_is_the_lowercase_extension_and_blank_without_one(self):
        self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MKV
            TCRMP20240413_3D_MRS_T2
        """)})
        self.assertEqual(self._sidecar("MRS_T1_2024_pbl")[0]["container"], "mkv")
        self.assertEqual(self._sidecar("MRS_T2_2024_pbl")[0]["container"], "")

    def test_rows_payload_carries_the_files(self):
        run = self._catalog({ROOT_2024: _names("TCRMP20240412_3D_MRS_T1.MP4")}, dry_run=True)
        self.assertEqual(set(run.rows[0]), {"readable_id", "root", "path", "fields", "files"})
        self.assertEqual(set(run.rows[0]["files"][0]), set(registry.SOURCE_FILE_INPUT_COLUMNS))


class ClassificationTests(_CatalogCase):
    """Rules per identity group inside one directory (practice plan D3 b)."""

    def test_mov_beside_mkv_is_more_than_one_whole_file(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MOV
            TCRMP20240412_3D_MRS_T1.MKV
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.MULTI_WHOLE_REASON])
        self.assertEqual(run.needs_attention[0]["detail"],
                         "TCRMP20240412_3D_MRS_T1.MKV; TCRMP20240412_3D_MRS_T1.MOV")
        self.assertEqual(self.r.load(), [])

    def test_two_whole_files_without_a_canonical_are_flagged(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_Proxy.MOV
            TCRMP20240412_3D_MRS_T1_Proxy.MKV
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.MULTI_WHOLE_REASON])

    def test_parts_plus_lone_proxy_is_a_canonical_conflict(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_1.MP4
            TCRMP20240412_3D_MRS_T1_2.MP4
            TCRMP20240412_3D_MRS_T1_Proxy.MP4
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.CANONICAL_CONFLICT_REASON])
        self.assertEqual(self._sidecar(), [])

    def test_a_flagged_group_never_takes_part_in_resolution(self):
        # The earlier take (04-12) is a conflict; the later clean take (04-13)
        # must become the row without a second-recording line.
        run = self._catalog({ROOT_2024: _names("""
            dirA/TCRMP20240412_3D_MRS_T1.MP4
            dirA/TCRMP20240412_3D_MRS_T1_1.MP4
            dirB/TCRMP20240413_3D_MRS_T1.MP4
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.rows[0]["path"], f"{ROOT_2024}/dirB")
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.CANONICAL_CONFLICT_REASON])
        files = self._sidecar("MRS_T1_2024_pbl")
        self.assertEqual([f["file_name"] for f in files], ["TCRMP20240413_3D_MRS_T1.MP4"])

    def test_part_zero_goes_to_needs_attention_instead_of_crashing_the_sidecar(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_0.MP4
            TCRMP20240412_3D_MRS_T1_1.MP4
        """)})
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.BAD_NAME_REASON])
        self.assertTrue(run.needs_attention[0]["path"].endswith("_0.MP4"))
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(self._sidecar("MRS_T1_2024_pbl")[0]["part"], "1")

    def test_two_timepoints_in_one_directory_are_two_rows(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MP4
            TCRMP20240412_3D_MRS_T2.MP4
        """)})
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["MRS_T1_2024_pbl", "MRS_T2_2024_pbl"])
        self.assertEqual(run.needs_attention, [])


class LonePartTests(_CatalogCase):
    """A lone part is the whole recording (Lauren, 2026-09-11 13:31 AST: "if
    that's it then that's it"). The archive's own case is Meri Shoal
    transect 3, 2023 annual, whose only readable file is part1 (its sibling
    "part2?" cannot be parsed and is never pulled); the 2026-09-08 refusal of
    a lone part paused four transects for that one file. A set of two or
    more parts must still run 1 to N, and a part beside a whole file is
    still the canonical conflict."""

    ROOT_2023 = "/volume2/Archive9_10TB/encoded/TCRMP_2023Annual"
    MRS_T3_PART1 = "TCRMP20231207_demo_MRS_T3_part1.MP4"

    def test_a_lone_part_1_is_the_row_with_its_number_and_the_lone_part_note(self):
        run = self._catalog({self.ROOT_2023: _names(f"{self.MRS_T3_PART1} 2273372899")}, variants=("demo",))
        self.assertEqual(run.needs_attention, [])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T3_2023ann"])
        row = self.r.get("MRS_T3_2023ann")
        self.assertEqual(row["original_videos"], self.MRS_T3_PART1)
        self.assertEqual(row["video_size_gb"], "2.273")
        files = self._sidecar("MRS_T3_2023ann")
        self.assertEqual([(f["file_name"], f["part"], f["in_row"]) for f in files],
                         [(self.MRS_T3_PART1, "1", "true")])
        self.assertEqual(files[0]["edit_note"],
                         f"{atlascatalog.NOTE_LONE_PART.format(part=1)}; {atlascatalog.NOTE_DEMO}")

    def test_a_lone_part_2_is_the_row_and_the_note_names_part_2(self):
        run = self._catalog({ROOT_2024: _names("TCRMP20240412_3D_MRS_T1_2.MP4 1500000000")})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(self.r.get("MRS_T1_2024_pbl")["original_videos"], "TCRMP20240412_3D_MRS_T1_2.MP4")
        files = self._sidecar("MRS_T1_2024_pbl")
        self.assertEqual([(f["part"], f["in_row"]) for f in files], [("2", "true")])
        self.assertIn("lone part 2", files[0]["edit_note"])
        self.assertEqual(files[0]["edit_note"], atlascatalog.NOTE_LONE_PART.format(part=2))

    def test_a_lone_pt_spelled_part_is_the_row_too(self):
        run = self._catalog({ROOT_2025: _names("TCRMP20250110_3D_BID_T2_pt3.MP4")})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual([r["readable_id"] for r in run.rows], ["BID_T2_2025_pbl"])
        self.assertEqual(self._sidecar("BID_T2_2025_pbl")[0]["edit_note"], atlascatalog.NOTE_LONE_PART.format(part=3))

    def test_two_parts_with_a_gap_stay_incomplete(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_1.MP4
            TCRMP20240412_3D_MRS_T1_3.MP4
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.INCOMPLETE_PARTS_REASON])
        self.assertIn("part numbers found: 1, 3", run.needs_attention[0]["detail"])
        self.assertEqual(self.r.load(), [])
        self.assertEqual(self._sidecar(), [])

    def test_a_repeated_part_number_stays_incomplete(self):
        # The same number written two ways is two files claiming one slot.
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_1.MP4
            TCRMP20240412_3D_MRS_T1_part1.MP4
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.INCOMPLETE_PARTS_REASON])
        self.assertIn("part numbers found: 1, 1", run.needs_attention[0]["detail"])

    def test_a_set_starting_above_one_stays_incomplete(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_2.MP4
            TCRMP20240412_3D_MRS_T1_3.MP4
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.INCOMPLETE_PARTS_REASON])

    def test_a_lone_part_beside_a_whole_file_stays_the_canonical_conflict(self):
        run = self._catalog({self.ROOT_2023: _names(f"""
            {self.MRS_T3_PART1}
            TCRMP20231207_demo_MRS_T3.MP4
        """)}, variants=("demo",))
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.CANONICAL_CONFLICT_REASON])
        self.assertEqual(self.r.load(), [])
        self.assertEqual(self._sidecar(), [])

    def test_a_lone_part_beside_a_proxy_stays_the_canonical_conflict(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1_2.MP4
            TCRMP20240412_3D_MRS_T1_Proxy.MP4
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.CANONICAL_CONFLICT_REASON])

    def test_a_lone_part_as_a_later_take_keeps_its_note_out_of_the_row(self):
        run = self._catalog({ROOT_2024: _names("""
            dirA/TCRMP20240412_3D_MRS_T1.MP4
            dirB/TCRMP20240413_3D_MRS_T1_2.MP4
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.rows[0]["path"], f"{ROOT_2024}/dirA")
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON])
        later = [f for f in self._sidecar("MRS_T1_2024_pbl") if f["file_name"].endswith("_2.MP4")]
        self.assertEqual([(f["part"], f["in_row"]) for f in later], [("2", "false")])
        self.assertEqual(later[0]["edit_note"],
                         f"{atlascatalog.NOTE_SECOND.format(date='2024-04-13')}; "
                         f"{atlascatalog.NOTE_LONE_PART.format(part=2)}")

    def test_a_lone_part_rerun_is_byte_identical(self):
        listings = {ROOT_2024: _names("TCRMP20240412_3D_MRS_T1_2.MP4")}
        self._catalog(listings)
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            first = fh.read()
        self._catalog(listings)
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            second = fh.read()
        self.assertEqual(first, second)


class CrossFolderPartsTests(_CatalogCase):
    """A recording whose parts sit in more than one folder under one season
    root is one recording (Lauren, 2026-09-14: "build it"). The archive's
    own case is Fish Bay transects 1 to 3 of 2024-02-16, with part 1 in
    /volume2/Archive9_10TB/encoded/TCRMP_2024_PBL and part 2 in its subfolder
    TCRMP2024_postbl_3D_DemoVideos. The set must be complete across the
    folders (every member part-numbered, numbers exactly 1 to N, no repeat,
    no whole file, no proxy) and the folders must be one folder and the
    folders inside it (the folder of the shallowest part holds or contains
    every other part), which keeps the join inside one season folder even
    though the default run walks whole volumes. The row's video_location
    is the folder of part 1; every sidecar line carries its own folder and
    the folders note, each folder spelled from the top folder's own name so
    the note reads the same whichever root was walked. Every other shape
    that spans folders stays refused as before."""

    VOLUME = "/volume2/Archive9_10TB"
    SEASON = "TCRMP_2024_PBL"
    ROOT = f"{VOLUME}/encoded/{SEASON}"
    SUB = "TCRMP2024_postbl_3D_DemoVideos"
    # How the folders note spells the subfolder: from the season folder's name.
    SUB_LABEL = f"{SEASON}/{SUB}"
    PART1 = "TCRMP20240216_3D_FSB_T1_1.MP4"
    PART2 = "TCRMP20240216_3D_FSB_T1_2.MP4"

    def _folders_note(self, *folders):
        return atlascatalog.NOTE_PARTS_IN_FOLDERS.format(count=len(folders),
                                                         folders=atlascatalog.DETAIL_JOINER.join(folders))

    def test_the_fish_bay_shape_is_one_row_in_the_season_folder(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1} 1000000000
            {self.SUB}/{self.PART2} 1500000000
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual([r["readable_id"] for r in run.rows], ["FSB_T1_2024_pbl"])
        self.assertEqual(run.rows[0]["path"], self.ROOT)
        row = self.r.get("FSB_T1_2024_pbl")
        self.assertEqual(row["video_location"], f"{HOST}:{self.ROOT}")
        self.assertEqual(row["original_videos"], f"{self.PART1};{self.PART2}")
        self.assertEqual(row["video_size_gb"], "2.5")
        files = {f["file_name"]: f for f in self._sidecar("FSB_T1_2024_pbl")}
        self.assertEqual(len(files), 2)
        self.assertEqual(files[self.PART1]["nas_path"], f"{HOST}:{self.ROOT}/{self.PART1}")
        self.assertEqual(files[self.PART2]["nas_path"], f"{HOST}:{self.ROOT}/{self.SUB}/{self.PART2}")
        self.assertEqual([(files[n]["part"], files[n]["in_row"]) for n in (self.PART1, self.PART2)],
                         [("1", "true"), ("2", "true")])
        expected = f"{atlascatalog.NOTE_PARTS}; {self._folders_note(self.SEASON, self.SUB_LABEL)}"
        self.assertEqual(files[self.PART1]["edit_note"], expected)
        self.assertEqual(files[self.PART2]["edit_note"], expected)
        self.assertEqual(expected, "parts grouped; physical merge at pull time; "
                                   "parts in 2 folders: TCRMP_2024_PBL; TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos")

    def test_the_reverse_shape_puts_video_location_in_the_subfolder(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.SUB}/{self.PART1}
            {self.PART2}
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.rows[0]["path"], f"{self.ROOT}/{self.SUB}")
        row = self.r.get("FSB_T1_2024_pbl")
        self.assertEqual(row["video_location"], f"{HOST}:{self.ROOT}/{self.SUB}")
        self.assertEqual(row["original_videos"], f"{self.PART1};{self.PART2}")
        files = {f["file_name"]: f for f in self._sidecar("FSB_T1_2024_pbl")}
        self.assertEqual(files[self.PART1]["nas_path"], f"{HOST}:{self.ROOT}/{self.SUB}/{self.PART1}")
        self.assertEqual(files[self.PART2]["nas_path"], f"{HOST}:{self.ROOT}/{self.PART2}")
        # The folders are listed in part order: the folder of part 1 first.
        self.assertTrue(files[self.PART2]["edit_note"].endswith(
            self._folders_note(self.SUB_LABEL, self.SEASON)))

    def test_three_folders_join_in_part_order(self):
        # Part 2 sits in the season folder, parts 1 and 3 in two of its
        # subfolders: one row at part 1's folder, the folders named in part
        # order from the season folder's own name.
        run = self._catalog({ROOT_2024: _names("""
            c/TCRMP20240412_3D_MRS_T1_3.MP4 3000000000
            TCRMP20240412_3D_MRS_T1_2.MP4 2000000000
            b/TCRMP20240412_3D_MRS_T1_1.MP4 1000000000
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.rows[0]["path"], f"{ROOT_2024}/b")
        self.assertEqual([f["nas_path"] for f in run.rows[0]["files"]], [
            f"{HOST}:{ROOT_2024}/b/TCRMP20240412_3D_MRS_T1_1.MP4",
            f"{HOST}:{ROOT_2024}/TCRMP20240412_3D_MRS_T1_2.MP4",
            f"{HOST}:{ROOT_2024}/c/TCRMP20240412_3D_MRS_T1_3.MP4",
        ])
        row = self.r.get("MRS_T1_2024_pbl")
        self.assertEqual(row["video_location"], f"{HOST}:{ROOT_2024}/b")
        self.assertEqual(row["original_videos"], "TCRMP20240412_3D_MRS_T1_1.MP4;"
                                                 "TCRMP20240412_3D_MRS_T1_2.MP4;TCRMP20240412_3D_MRS_T1_3.MP4")
        self.assertEqual(row["video_size_gb"], "6.0")
        notes = {f["edit_note"] for f in self._sidecar("MRS_T1_2024_pbl")}
        self.assertEqual(notes, {f"{atlascatalog.NOTE_PARTS}; "
                                 f"{self._folders_note('2024_annual/b', '2024_annual', '2024_annual/c')}"})

    def test_a_deeper_subfolder_is_named_from_the_season_folder(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/deeper/{self.PART2}
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("FSB_T1_2024_pbl")["video_location"], f"{HOST}:{self.ROOT}")
        notes = {f["edit_note"] for f in self._sidecar("FSB_T1_2024_pbl")}
        self.assertEqual(notes, {f"{atlascatalog.NOTE_PARTS}; "
                                 f"{self._folders_note(self.SEASON, f'{self.SUB_LABEL}/deeper')}"})

    def test_the_row_reads_the_same_walked_from_the_volume_or_the_season_folder(self):
        # The default run walks whole volumes (source_roots names
        # /volume2/Archive9_10TB); a --root names one season folder. The
        # same files must give the same row either way: the same
        # video_location, the same paths, the same note, so nothing the
        # ATLAS shows depends on how the catalog was invoked.
        from_season = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)}, dry_run=True)
        prefix = posixpath.relpath(self.ROOT, self.VOLUME)
        from_volume = self._catalog({self.VOLUME: _names(f"""
            {prefix}/{self.PART1}
            {prefix}/{self.SUB}/{self.PART2}
        """)}, dry_run=True)
        for run in (from_season, from_volume):
            self.assertEqual(run.needs_attention, [])
            self.assertEqual(len(run.rows), 1)
        self.assertEqual(from_volume.rows[0]["fields"], from_season.rows[0]["fields"])
        self.assertEqual(from_volume.rows[0]["files"], from_season.rows[0]["files"])
        self.assertEqual(from_volume.rows[0]["fields"]["video_location"], f"{HOST}:{self.ROOT}")
        self.assertEqual(from_volume.rows[0]["files"][1]["edit_note"],
                         "parts grouped; physical merge at pull time; "
                         "parts in 2 folders: TCRMP_2024_PBL; TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos")

    def test_two_season_folders_on_one_volume_stay_refused(self):
        # The default run walks a whole volume. A _1 in one season folder and
        # a _2 in another folder of that volume complete each other by
        # number, but the folders are not one folder and the folders inside
        # it: refused, never joined by guess.
        run = self._catalog({self.VOLUME: _names(f"""
            encoded/{self.SEASON}/{self.PART1}
            backup/{self.SEASON}_old/{self.PART2}
        """)})
        self._assert_refused(run)
        item = run.needs_attention[0]
        self.assertEqual(item["path"], f"{self.VOLUME}/backup/{self.SEASON}_old; {self.VOLUME}/encoded/{self.SEASON}")
        self.assertIn(atlascatalog.NOT_NESTED_DETAIL, item["detail"])

    def test_sibling_subfolders_with_nothing_above_them_stay_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            a/{self.PART1}
            b/{self.PART2}
        """)})
        self._assert_refused(run)
        self.assertIn(atlascatalog.NOT_NESTED_DETAIL, run.needs_attention[0]["detail"])

    def test_a_folder_whose_name_merely_starts_like_the_season_folder_is_not_inside_it(self):
        run = self._catalog({self.VOLUME: _names(f"""
            encoded/{self.SEASON}/{self.PART1}
            encoded/{self.SEASON}_old/{self.PART2}
        """)})
        self._assert_refused(run)

    def test_an_incomplete_cross_folder_set_is_refused_without_the_nested_clause(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_3.MP4
        """)})
        self._assert_refused(run)
        self.assertNotIn(atlascatalog.NOT_NESTED_DETAIL, run.needs_attention[0]["detail"])

    def test_the_nested_folders_predicate(self):
        def member(directory):
            return {"dir": directory}
        nested = [member("/v/season/sub"), member("/v/season"), member("/v/season/sub/deeper")]
        self.assertEqual(atlascatalog._spanning_top(nested), ("v", "season"))
        self.assertIsNone(atlascatalog._spanning_top([member("/v/a"), member("/v/b")]))
        self.assertIsNone(atlascatalog._spanning_top([member("/v/season"), member("/v/season_old/sub")]),
                          "a name prefix is not a folder")
        self.assertEqual(atlascatalog._spanning_top([member("/v/season/"), member("/v/season//sub")]),
                         ("v", "season"), "slashes are normalised before comparing")
        self.assertEqual(atlascatalog._spanning_top([member("/v/season"), member("/v/season")]), ("v", "season"))
        with self.assertRaises(ValueError):
            atlascatalog._spanning_top([])

    def test_two_parts_in_one_folder_and_the_third_in_another_join(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.PART2}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_3.MP4
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("FSB_T1_2024_pbl")["video_location"], f"{HOST}:{self.ROOT}")
        files = self._sidecar("FSB_T1_2024_pbl")
        self.assertEqual(len(files), 3)
        self.assertEqual({f["edit_note"] for f in files},
                         {f"{atlascatalog.NOTE_PARTS}; {self._folders_note(self.SEASON, self.SUB_LABEL)}"})

    def test_mixed_part_spellings_across_folders_join(self):
        run = self._catalog({self.ROOT: _names(f"""
            TCRMP20240216_3D_FSB_T1_part1.MP4
            {self.SUB}/TCRMP20240216_3D_FSB_T1_pt2.MP4
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("FSB_T1_2024_pbl")["original_videos"],
                         "TCRMP20240216_3D_FSB_T1_part1.MP4;TCRMP20240216_3D_FSB_T1_pt2.MP4")
        self.assertEqual(self.r.get("FSB_T1_2024_pbl")["video_location"], f"{HOST}:{self.ROOT}")

    def _assert_refused(self, run):
        """The split refusal exactly as before: no row, no sidecar line, one line naming both folders."""
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SPLIT_ACROSS_DIRS_REASON])
        self.assertIn("found split across", run.needs_attention[0]["detail"])
        self.assertEqual(self.r.load(), [])
        self.assertEqual(self._sidecar(), [])

    def test_a_whole_file_in_one_folder_beside_parts_in_another_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            TCRMP20240216_3D_FSB_T1.MP4
            {self.SUB}/{self.PART1}
            {self.SUB}/{self.PART2}
        """)})
        self._assert_refused(run)
        item = run.needs_attention[0]
        self.assertEqual(item["path"], f"{self.ROOT}; {self.ROOT}/{self.SUB}")
        self.assertIn(self.SUB, item["detail"])

    def test_a_whole_file_beside_a_lone_part_in_another_folder_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            TCRMP20240216_3D_FSB_T1.MP4
            {self.SUB}/{self.PART1}
        """)})
        self._assert_refused(run)

    def test_a_gapped_cross_folder_set_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_3.MP4
        """)})
        self._assert_refused(run)

    def test_a_cross_folder_set_starting_above_one_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART2}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_3.MP4
        """)})
        self._assert_refused(run)

    def test_the_same_numbers_in_two_folders_stay_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.PART2}
            {self.SUB}/{self.PART1}
            {self.SUB}/{self.PART2}
        """)})
        self._assert_refused(run)

    def test_one_number_written_two_ways_in_two_folders_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_part1.MP4
        """)})
        self._assert_refused(run)

    def test_a_proxy_part_among_the_parts_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/TCRMP20240216_3D_FSB_T1_2_Proxy.MP4
        """)})
        self._assert_refused(run)

    def test_a_proxy_whole_file_beside_parts_in_another_folder_stays_refused(self):
        run = self._catalog({self.ROOT: _names(f"""
            TCRMP20240216_3D_FSB_T1_Proxy.MP4
            {self.SUB}/{self.PART1}
            {self.SUB}/{self.PART2}
        """)})
        self._assert_refused(run)

    def test_a_single_folder_set_is_unchanged(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.SUB}/{self.PART2}
            {self.SUB}/{self.PART1}
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("FSB_T1_2024_pbl")["video_location"], f"{HOST}:{self.ROOT}/{self.SUB}")
        files = self._sidecar("FSB_T1_2024_pbl")
        self.assertEqual([(f["file_name"], f["part"], f["edit_note"]) for f in files], [
            (self.PART1, "1", atlascatalog.NOTE_PARTS),
            (self.PART2, "2", atlascatalog.NOTE_PARTS),
        ])
        self.assertTrue(all(f["nas_path"] == f"{HOST}:{self.ROOT}/{self.SUB}/{f['file_name']}" for f in files))

    def test_the_lone_part_rule_is_unchanged(self):
        # Two lone parts of two different transects in two folders: two rows,
        # each the whole recording with its own lone-part note, no split.
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART2}
            {self.SUB}/TCRMP20240216_3D_FSB_T2_1.MP4
        """)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["FSB_T1_2024_pbl", "FSB_T2_2024_pbl"])
        self.assertEqual(self._sidecar("FSB_T1_2024_pbl")[0]["edit_note"], atlascatalog.NOTE_LONE_PART.format(part=2))
        self.assertEqual(self._sidecar("FSB_T2_2024_pbl")[0]["edit_note"], atlascatalog.NOTE_LONE_PART.format(part=1))

    def test_an_override_on_one_part_takes_it_out_of_the_set_before_joining(self):
        # The override changes part 2's identity before grouping, so nothing
        # spans folders: two lone parts, two rows, no join.
        overrides = {self.PART2: {"site": "FSBFIX", "note": "second take"}}
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)}, overrides=overrides)
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["FSBFIX_T1_2024_pbl", "FSB_T1_2024_pbl"])
        self.assertIn("lone part 1", self._sidecar("FSB_T1_2024_pbl")[0]["edit_note"])

    def test_a_cross_folder_set_as_a_later_take_is_recorded_out_of_the_row(self):
        run = self._catalog({self.ROOT: _names(f"""
            earlier/TCRMP20240215_3D_FSB_T1.MP4
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["FSB_T1_2024_pbl"])
        self.assertEqual(run.rows[0]["path"], f"{self.ROOT}/earlier")
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON])
        self.assertEqual(run.needs_attention[0]["path"], self.ROOT, "the later take is reported at part 1's folder")
        files = {f["file_name"]: f for f in self._sidecar("FSB_T1_2024_pbl")}
        self.assertEqual(len(files), 3)
        later = files[self.PART2]
        self.assertEqual((later["part"], later["in_row"]), ("2", "false"))
        self.assertEqual(later["nas_path"], f"{HOST}:{self.ROOT}/{self.SUB}/{self.PART2}")
        self.assertEqual(later["edit_note"],
                         f"{atlascatalog.NOTE_SECOND.format(date='2024-02-16')}; {atlascatalog.NOTE_PARTS}; "
                         f"{self._folders_note(self.SEASON, self.SUB_LABEL)}")

    def test_a_cross_folder_set_and_a_demo_token(self):
        run = self._catalog({self.ROOT: _names(f"""
            TCRMP20240216_demo_FSB_T1_1.MP4
            {self.SUB}/TCRMP20240216_demo_FSB_T1_2.MP4
        """)}, variants=("demo",))
        self.assertEqual(run.needs_attention, [])
        notes = {f["edit_note"] for f in self._sidecar("FSB_T1_2024_pbl")}
        self.assertEqual(notes, {f"{atlascatalog.NOTE_PARTS}; "
                                 f"{self._folders_note(self.SEASON, self.SUB_LABEL)}; "
                                 f"{atlascatalog.NOTE_DEMO}"})

    def test_a_cross_folder_rerun_is_byte_identical(self):
        listings = {self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)}
        self._catalog(listings)
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            first_rows = fh.read()
        with open(self.r.SOURCE_FILES_CSV, "rb") as fh:
            first_files = fh.read()
        self._catalog(listings)
        with open(self.r.REGISTRY_CSV, "rb") as fh:
            self.assertEqual(fh.read(), first_rows)
        with open(self.r.SOURCE_FILES_CSV, "rb") as fh:
            self.assertEqual(fh.read(), first_files)

    def test_a_dry_run_carries_each_file_on_its_own_path(self):
        run = self._catalog({self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)}, dry_run=True)
        self.assertEqual(run.rows[0]["path"], self.ROOT)
        self.assertEqual([f["nas_path"] for f in run.rows[0]["files"]],
                         [f"{HOST}:{self.ROOT}/{self.PART1}", f"{HOST}:{self.ROOT}/{self.SUB}/{self.PART2}"])
        self.assertEqual(self.r.load(), [])

    def test_a_season_folder_with_three_split_transects_and_one_whole_file(self):
        # A shaped fixture, not the archive: three transects with part 1 in the
        # season folder and part 2 in a subfolder, beside one whole file that
        # is not split (the real Fish Bay 2024-02-16 files are two versions of
        # each recording and were ruled on by hand): four rows, nothing to
        # review, every split row in the season folder with both files on
        # their own paths.
        spec = "\n".join(f"TCRMP20240216_3D_FSB_T{n}_1.MP4\n{self.SUB}/TCRMP20240216_3D_FSB_T{n}_2.MP4"
                         for n in (1, 2, 3)) + "\nTCRMP20240216_3D_FSB_T4.MP4"
        run = self._catalog({self.ROOT: _names(spec)})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(sorted(r["readable_id"] for r in run.rows),
                         ["FSB_T1_2024_pbl", "FSB_T2_2024_pbl", "FSB_T3_2024_pbl", "FSB_T4_2024_pbl"])
        rows = {r["readable_id"]: r for r in self.r.load()}
        self.assertTrue(all(rows[f"FSB_T{n}_2024_pbl"]["video_location"] == f"{HOST}:{self.ROOT}" for n in (1, 2, 3, 4)))
        sidecar = self._sidecar()
        self.assertEqual(len(sidecar), 7)
        self.assertEqual(sum(1 for f in sidecar if f["nas_path"].startswith(f"{HOST}:{self.ROOT}/{self.SUB}/")), 3)
        self.assertEqual(sum(1 for f in sidecar if "parts in 2 folders" in f["edit_note"]), 6)
        self.assertEqual(rows["FSB_T4_2024_pbl"]["original_videos"], "TCRMP20240216_3D_FSB_T4.MP4")

    def test_the_command_line_dry_run_prints_the_joined_row_and_nothing_to_review(self):
        # End to end through main() with the ssh listing faked: the printed
        # table shows the row at the season folder and the run says nothing
        # needs attention; a dry run writes nothing.
        cfg_path = self._write_config(self.ROOT)
        out = self._main(["--nas-config", cfg_path, "--dry-run"], {self.ROOT: _names(f"""
            {self.PART1}
            {self.SUB}/{self.PART2}
        """)})
        self.assertIn("FSB_T1_2024_pbl", out)
        self.assertIn(f"{HOST}:{self.ROOT}  ", out)
        self.assertIn(f"{self.PART1};{self.PART2}", out)
        self.assertIn("No items need attention.", out)
        self.assertIn("rows: 1", out)
        self.assertIn("source files: 2 (2 in row)", out)
        self.assertEqual(self.r.load(), [])

    def test_hostile_folder_names_are_carried_verbatim(self):
        # A folder with a space, one with the note's own joiner inside it,
        # and one with a non-ASCII name: each is a real NAS folder name and
        # reaches the sidecar unchanged, path and note alike.
        folders = ["demo videos", "demo videos/a; b", "demo videos/café"]
        # Explicit tuples: _names splits on whitespace, and a folder name with a space is the point here.
        entries = [(f"{folders[n - 1]}/TCRMP20240216_3D_FSB_T1_{n}.MP4", GB) for n in (1, 2, 3)]
        run = self._catalog({self.ROOT: entries})
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.rows[0]["path"], f"{self.ROOT}/demo videos")
        files = self._sidecar("FSB_T1_2024_pbl")
        self.assertEqual([f["nas_path"] for f in files],
                         [f"{HOST}:{self.ROOT}/{folders[n - 1]}/TCRMP20240216_3D_FSB_T1_{n}.MP4" for n in (1, 2, 3)])
        # No part sits in the season folder itself, so the top folder is
        # "demo videos" (part 1's) and the note is spelled from its name.
        expected = self._folders_note(*folders)
        self.assertTrue(all(f["edit_note"].endswith(expected) for f in files), files[0]["edit_note"])

    def test_many_sets_spanning_folders_are_all_joined(self):
        # 300 transects, each with parts 1 to 3 spread over the season folder
        # and two of its subfolders: every one a row, nothing to review, in
        # well under a minute.
        spec = "\n".join(f"{folder}TCRMP20240216_3D_FSB_T{t}_{n}.MP4"
                         for t in range(1, 301) for n, folder in ((1, "f1/"), (2, ""), (3, "f3/")))
        started = time.monotonic()
        run = self._catalog({self.ROOT: _names(spec)}, dry_run=True)
        self.assertLess(time.monotonic() - started, 60)
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(len(run.rows), 300)
        self.assertTrue(all(r["path"] == f"{self.ROOT}/f1" for r in run.rows), "part 1 sits in f1 for every set")
        self.assertEqual(sum(len(r["files"]) for r in run.rows), 900)

    def test_the_complete_set_predicate(self):
        def member(name):
            return {"name": name, "parsed": atlascatalog.naming3d.parse_video_name(name)}
        complete = [member("TCRMP20240216_3D_FSB_T1_2.MP4"), member("TCRMP20240216_3D_FSB_T1_1.MP4")]
        self.assertTrue(atlascatalog._is_complete_part_set(complete))
        self.assertFalse(atlascatalog._is_complete_part_set([member("TCRMP20240216_3D_FSB_T1_1.MP4")]),
                         "one part is not a set")
        self.assertFalse(atlascatalog._is_complete_part_set(complete + [member("TCRMP20240216_3D_FSB_T1.MP4")]))
        self.assertFalse(atlascatalog._is_complete_part_set(complete + [member("TCRMP20240216_3D_FSB_T1_3_Proxy.MP4")]))
        self.assertFalse(atlascatalog._is_complete_part_set(complete + [member("TCRMP20240216_3D_FSB_T1_pt2.MP4")]))
        self.assertFalse(atlascatalog._is_complete_part_set([]))


class SameTimepointTests(_CatalogCase):
    """Two recording dates for one readable id (practice plan D3 c)."""

    SECOND_NOTE = "second recording on 2025-04-15, not counted in the row; see needs-attention; proxy suffix dropped at rename"

    def test_same_timepoint_twice_across_directories_uses_the_earliest(self):
        run = self._catalog({ROOT_2025: _names("""
            RAW_04/TCRMP20250415_3D_LBH_T1_Proxy.MOV 115455218808
            RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV 109449240432
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["LBH_T1_2025_pbl"])
        row = self.r.get("LBH_T1_2025_pbl")
        self.assertEqual(row["video_location"], f"{HOST}:{ROOT_2025}/RAW_03")
        self.assertEqual(row["original_videos"], "TCRMP20250413_3D_LBH_T1_Proxy.MOV")
        self.assertEqual(row["video_size_gb"], "109.449")
        files = {f["file_name"]: f for f in self._sidecar("LBH_T1_2025_pbl")}
        self.assertEqual(len(files), 2)
        self.assertEqual(files["TCRMP20250413_3D_LBH_T1_Proxy.MOV"]["in_row"], "true")
        later = files["TCRMP20250415_3D_LBH_T1_Proxy.MOV"]
        self.assertEqual(later["in_row"], "false")
        self.assertEqual(later["edit_note"], self.SECOND_NOTE)
        self.assertEqual(later["nas_path"], f"{HOST}:{ROOT_2025}/RAW_04/TCRMP20250415_3D_LBH_T1_Proxy.MOV")
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON])
        item = run.needs_attention[0]
        self.assertEqual(item["path"], f"{ROOT_2025}/RAW_04")
        self.assertIn("2025-04-13", item["detail"])
        self.assertIn("2025-04-15", item["detail"])

    def test_same_timepoint_across_two_roots(self):
        other = "/volume4/Archive6_16TB/extra"
        run = self._catalog({
            other: _names("TCRMP20250415_3D_LBH_T1_Proxy.MOV"),
            ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV"),
        }, roots=[other, ROOT_2025])
        self.assertEqual(len(run.rows), 1)
        self.assertEqual(run.rows[0]["root"], ROOT_2025)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON])
        self.assertEqual(run.needs_attention[0]["root"], other)
        self.assertEqual(len(self._sidecar("LBH_T1_2025_pbl")), 2)

    def test_three_takes_flag_two(self):
        run = self._catalog({ROOT_2025: _names("""
            RAW_05/TCRMP20250417_3D_LBH_T1_Proxy.MOV
            RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV
            RAW_04/TCRMP20250415_3D_LBH_T1_Proxy.MOV
        """)})
        self.assertEqual(len(run.rows), 1)
        self.assertEqual(run.rows[0]["path"], f"{ROOT_2025}/RAW_03")
        self.assertEqual([i["reason"] for i in run.needs_attention],
                         [atlascatalog.SECOND_DATE_REASON, atlascatalog.SECOND_DATE_REASON])
        self.assertEqual([i["path"] for i in run.needs_attention],
                         [f"{ROOT_2025}/RAW_04", f"{ROOT_2025}/RAW_05"])
        files = self._sidecar("LBH_T1_2025_pbl")
        self.assertEqual(sum(1 for f in files if f["in_row"] == "true"), 1)
        self.assertEqual(len(files), 3)

    def test_second_take_with_parts_keeps_its_part_numbers_and_notes(self):
        run = self._catalog({ROOT_2025: _names("""
            RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV
            RAW_04/TCRMP20250415_3D_LBH_T1_1.MOV
            RAW_04/TCRMP20250415_3D_LBH_T1_2.MOV
        """)})
        files = {f["file_name"]: f for f in self._sidecar("LBH_T1_2025_pbl")}
        part = files["TCRMP20250415_3D_LBH_T1_2.MOV"]
        self.assertEqual(part["part"], "2")
        self.assertEqual(part["in_row"], "false")
        self.assertEqual(part["edit_note"],
                         "second recording on 2025-04-15, not counted in the row; see needs-attention; "
                         "parts grouped; physical merge at pull time")

    def test_the_same_file_listed_through_two_overlapping_roots_is_recorded_once(self):
        outer = "/volume3/Archive2_12TB/TCRMP_2025_PBL"
        inner = "/volume3/Archive2_12TB/TCRMP_2025_PBL/_encoded"
        run = self._catalog({
            outer: _names("_encoded/RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV"),
            inner: _names("RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV"),
        }, roots=[outer, inner])
        self.assertEqual(len(run.rows), 1)
        files = self._sidecar("LBH_T1_2025_pbl")
        self.assertEqual(len(files), 1, "one physical file must give one sidecar line")
        self.assertEqual(run.needs_attention, [])

    def test_same_file_name_on_two_paths_is_flagged_and_recorded_once(self):
        # Same name, same date, two roots: not a second date, but the sidecar
        # keys on file_name, so the copy is reported and not written twice.
        other = "/volume4/Archive6_16TB/copy"
        run = self._catalog({
            ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV"),
            other: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV"),
        }, roots=[ROOT_2025, other])
        self.assertEqual(len(run.rows), 1)
        self.assertEqual(len(self._sidecar("LBH_T1_2025_pbl")), 1)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON])
        self.assertIn("same file name", run.needs_attention[0]["detail"])
        self.assertEqual(run.needs_attention[0]["root"], other)


class OverrideTests(_CatalogCase):
    """catalog_overrides.csv: a site label per file, applied before grouping."""

    def _overrides_file(self, text, name="catalog_overrides.csv", encoding="utf-8"):
        path = os.path.join(self.registry_root, name)
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
        return path

    def test_load_overrides_missing_file_means_none(self):
        self.assertEqual(atlascatalog.load_overrides(os.path.join(self.registry_root, "absent.csv")), {})

    def test_load_overrides_empty_file_means_none(self):
        path = self._overrides_file("")
        self.assertEqual(atlascatalog.load_overrides(path), {})

    def test_load_overrides_reads_rows_and_uppercases_the_site(self):
        path = self._overrides_file("file_name,site,note\n"
                                    "TCRMP20250413_3D_LBH_T1_Proxy.MOV,lbhlbpfix1,\"RAW_03, first take\"\n"
                                    "\n"
                                    "TCRMP20250415_3D_LBH_T1_Proxy.MOV,LBHLBPFIX2,\n")
        self.assertEqual(atlascatalog.load_overrides(path), {
            "TCRMP20250413_3D_LBH_T1_Proxy.MOV": {"site": "LBHLBPFIX1", "note": "RAW_03, first take"},
            "TCRMP20250415_3D_LBH_T1_Proxy.MOV": {"site": "LBHLBPFIX2", "note": ""},
        })

    def test_load_overrides_tolerates_a_spreadsheet_bom(self):
        path = self._overrides_file("file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX1,x\n",
                                    encoding="utf-8-sig")
        self.assertEqual(list(atlascatalog.load_overrides(path)), ["TCRMP20250413_3D_LBH_T1_Proxy.MOV"])

    def test_malformed_override_line_raises_with_the_line_number(self):
        path = self._overrides_file("file_name,site,note\n"
                                    "TCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX1,ok\n"
                                    "TCRMP20250415_3D_LBH_T1_Proxy.MOV,LBH LBP,bad site\n")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.load_overrides(path)
        self.assertIn(path, str(ctx.exception))
        self.assertIn("line 3", str(ctx.exception))

    def test_override_line_with_too_few_fields_names_the_line(self):
        path = self._overrides_file("file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV\n")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.load_overrides(path)
        self.assertIn("line 2", str(ctx.exception))

    def test_override_header_must_match(self):
        path = self._overrides_file("name,site\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX1\n")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.load_overrides(path)
        self.assertIn("file_name,site,note", str(ctx.exception))

    def test_duplicate_override_file_name_is_refused(self):
        path = self._overrides_file("file_name,site,note\n"
                                    "TCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX1,a\n"
                                    "TCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX2,b\n")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.load_overrides(path)
        self.assertIn("line 3", str(ctx.exception))
        self.assertIn("line 2", str(ctx.exception))

    def test_blank_file_name_and_hostile_site_are_refused(self):
        for text in ("file_name,site,note\n,LBHLBPFIX1,x\n",
                     "file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,,x\n",
                     "file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBH;rm,x\n",
                     "file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBH_T1,x\n",
                     "file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHé,x\n"):
            path = self._overrides_file(text)
            with self.assertRaises(ValueError, msg=text):
                atlascatalog.load_overrides(path)

    def test_override_relabels_the_site_and_the_sidecar_note_says_so(self):
        overrides = {"TCRMP20250413_3D_LBH_T1_Proxy.MOV": {"site": "lbhlbpfix1", "note": "RAW_03 take"}}
        run = self._catalog({ROOT_2025: _names("""
            RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV 109449240432
            RAW_04/TCRMP20250415_3D_LBH_T1_Proxy.MOV 115455218808
        """)}, overrides=overrides)
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["LBHLBPFIX1_T1_2025_pbl", "LBH_T1_2025_pbl"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.overrides_applied, 1)
        row = self.r.get("LBHLBPFIX1_T1_2025_pbl")
        self.assertEqual(row["site"], "LBHLBPFIX1")
        self.assertEqual(row["transect"], "T1")
        self.assertEqual(row["video_size_gb"], "109.449")
        note = self._sidecar("LBHLBPFIX1_T1_2025_pbl")[0]["edit_note"]
        self.assertTrue(note.startswith("site label LBHLBPFIX1 applied from catalog_overrides.csv: RAW_03 take"), note)
        self.assertTrue(note.endswith(atlascatalog.NOTE_PROXY))
        self.assertEqual(self.r.get("LBH_T1_2025_pbl")["video_size_gb"], "115.455")

    def test_override_naming_an_unlisted_file_goes_to_needs_attention(self):
        overrides = {"TCRMP20250413_3D_LBH_T9_Proxy.MOV": {"site": "LBHLBPFIX1", "note": ""}}
        run = self._catalog({ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV")}, overrides=overrides)
        self.assertEqual(len(run.rows), 1)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.OVERRIDE_UNLISTED_REASON])
        self.assertEqual(run.needs_attention[0]["path"], "TCRMP20250413_3D_LBH_T9_Proxy.MOV")
        self.assertEqual(run.needs_attention[0]["root"], atlascatalog.OVERRIDES_FILENAME)
        self.assertEqual(run.overrides_applied, 0)

    def test_override_on_a_bad_name_is_neither_applied_nor_unlisted(self):
        overrides = {"GOPR0001.MP4": {"site": "MRS", "note": ""}}
        run = self._catalog({ROOT_2025: _names("GOPR0001.MP4")}, overrides=overrides)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.BAD_NAME_REASON])
        self.assertEqual(run.overrides_applied, 0)

    def test_programmatic_overrides_are_validated(self):
        listings = {ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV")}
        for bad in ("x", [("a", "b")], {"f": "LBH"}, {"f": {"site": "L B", "note": ""}}, {"": {"site": "L", "note": ""}}):
            with self.assertRaises((TypeError, ValueError), msg=repr(bad)):
                self._catalog(listings, overrides=bad, dry_run=True)

    def test_cli_overrides_flag_and_default_beside_the_registry(self):
        self._overrides_file("file_name,site,note\nTCRMP20250413_3D_LBH_T1_Proxy.MOV,LBHLBPFIX1,first take\n")
        cfg = self._write_config(ROOT_2025)
        listings = {ROOT_2025: _names("TCRMP20250413_3D_LBH_T1_Proxy.MOV")}
        output = self._main(["--nas-config", cfg, "--dry-run"], listings)
        self.assertIn("LBHLBPFIX1_T1_2025_pbl", output)
        self.assertIn("overrides applied: 1", output)

        elsewhere = self._overrides_file("file_name,site,note\n", name="other.csv")
        output = self._main(["--nas-config", cfg, "--dry-run", "--overrides", elsewhere], listings)
        self.assertIn("LBH_T1_2025_pbl", output)
        self.assertNotIn("LBHLBPFIX1", output)
        self.assertIn("overrides applied: 0", output)


class VariantTests(_CatalogCase):
    """Spec decision 12: --name-variant demo and 3ddemo."""

    DEMO_2023 = """
        TCRMP20231012_demo_BTY_T1.MP4 2100000000
        TCRMP20231013_demo_FSB_T3_part1.MP4 1000000000
        TCRMP20231013_demo_FSB_T3_part2.MP4 1000000000
        TCRMP20231013_demo_FSB_T3_part3.MP4 500000000
        TCRMP20231205_demo_GKT_T2_WRONG.MP4 900000000
        TCRMP20231112_demo_SPH_T1_again?.MP4 900000000
    """
    ROOT_2023 = "/volume2/Archive9_10TB/encoded/TCRMP_2023Annual"
    ROOT_2024PBL = "/volume2/Archive9_10TB/encoded/TCRMP_2024_PBL"

    def test_variant_off_rejects_every_demo_name(self):
        run = self._catalog({self.ROOT_2023: _names(self.DEMO_2023)})
        self.assertEqual(run.rows, [])
        self.assertEqual({i["reason"] for i in run.needs_attention}, {atlascatalog.BAD_NAME_REASON})
        self.assertEqual(len(run.needs_attention), 6)

    def test_variant_demo_catalogues_as_3d_and_notes_it(self):
        run = self._catalog({self.ROOT_2023: _names(self.DEMO_2023)}, variants=("demo",))
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["BTY_T1_2023ann", "FSB_T3_2023ann"])
        # Sorted by path whatever order the NAS listed them in.
        self.assertEqual([i["path"] for i in run.needs_attention],
                         [f"{self.ROOT_2023}/TCRMP20231112_demo_SPH_T1_again?.MP4",
                          f"{self.ROOT_2023}/TCRMP20231205_demo_GKT_T2_WRONG.MP4"])
        row = self.r.get("BTY_T1_2023ann")
        self.assertEqual((row["site"], row["transect"], row["year"], row["season_token"]), ("BTY", "T1", "2023", "ann"))
        self.assertEqual(row["original_videos"], "TCRMP20231012_demo_BTY_T1.MP4")
        self.assertEqual(self._sidecar("BTY_T1_2023ann")[0]["edit_note"], atlascatalog.NOTE_DEMO)
        parts = self._sidecar("FSB_T3_2023ann")
        self.assertEqual([p["part"] for p in parts], ["1", "2", "3"])
        self.assertEqual(parts[0]["edit_note"], f"{atlascatalog.NOTE_PARTS}; {atlascatalog.NOTE_DEMO}")
        self.assertEqual(self.r.get("FSB_T3_2023ann")["video_size_gb"], "2.5")

    def test_3ddemo_part_set_needs_its_own_variant(self):
        listing = _names("""
            TCRMP2024_postbl_3D_DemoVideos/TCRMP20240215_3ddemo_FLC_T5_1.mp4
            TCRMP2024_postbl_3D_DemoVideos/TCRMP20240215_3ddemo_FLC_T5_2.mp4
            TCRMP2024_postbl_3D_DemoVideos/TCRMP20240215_3ddemo_FLC_T3.mp4
            TCRMP20240216_demo_FSB_T1.MP4
            TCRMP20240307_demo_SHR_T5_pt1.MP4
            TCRMP20240311_demo_CST_T1_OR_T2?.MP4
            TCRMP20240408_demo_GKT_EXTRA_T1_2.MP4
        """)
        run = self._catalog({self.ROOT_2024PBL: listing}, variants=("demo",), dry_run=True)
        # SHR_T5 joins the rows because "_pt1" is a part spelling the archive
        # really uses (2026-09-08). Only pt1 is in this listing, so the row it
        # makes names a single part: a lone part is the whole recording
        # (Lauren, 2026-09-11), prep renames it at pull time and step 0
        # extracts from it, with the part number kept on the sidecar line.
        self.assertEqual([r["readable_id"] for r in run.rows],
                         ["FSB_T1_2024_pbl", "SHR_T5_2024_pbl"])
        self.assertEqual(len(run.needs_attention), 5)

        run = self._catalog({self.ROOT_2024PBL: listing}, variants=("demo", "3ddemo"))
        self.assertEqual(sorted(r["readable_id"] for r in run.rows),
                         ["FLC_T3_2024_pbl", "FLC_T5_2024_pbl", "FSB_T1_2024_pbl", "SHR_T5_2024_pbl"])
        self.assertEqual(len(run.needs_attention), 2)
        row = self.r.get("FLC_T5_2024_pbl")
        self.assertEqual(row["original_videos"], "TCRMP20240215_3ddemo_FLC_T5_1.mp4;TCRMP20240215_3ddemo_FLC_T5_2.mp4")
        self.assertEqual(row["video_location"], f"{HOST}:{self.ROOT_2024PBL}/TCRMP2024_postbl_3D_DemoVideos")
        files = self._sidecar("FLC_T5_2024_pbl")
        self.assertEqual([f["container"] for f in files], ["mp4", "mp4"])
        self.assertTrue(all(f["edit_note"].endswith(atlascatalog.NOTE_DEMO) for f in files))

    def test_unknown_variant_is_refused_before_any_listing(self):
        calls = []

        def runner(argv, timeout=60):
            calls.append(argv)
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        with self.assertRaises(ValueError):
            atlascatalog.catalog([self.ROOT_2023], ssh, variants=("Demo",))
        self.assertEqual(calls, [])

    def test_cli_name_variant_is_repeatable_and_checked(self):
        cfg = self._write_config(self.ROOT_2024PBL)
        listing = _names("""
            TCRMP20240215_3ddemo_FLC_T3.mp4
            TCRMP20240216_demo_FSB_T1.MP4
        """)
        output = self._main(["--nas-config", cfg, "--dry-run", "--name-variant", "demo", "--name-variant", "3ddemo"],
                            {self.ROOT_2024PBL: listing})
        self.assertIn("FLC_T3_2024_pbl", output)
        self.assertIn("FSB_T1_2024_pbl", output)
        self.assertIn("rows: 2", output)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                atlascatalog.main(["--nas-config", cfg, "--dry-run", "--name-variant", "video"])


class RunShapeTests(_CatalogCase):
    """Whole-run guarantees: reruns, dry runs, the report, the printed counts."""

    def _bytes(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def test_rerun_leaves_registry_sidecar_and_events_byte_identical(self):
        listings = {ROOT_2025: _names("""
            RAW_03/TCRMP20250413_3D_LBH_T1_Proxy.MOV
            RAW_04/TCRMP20250415_3D_LBH_T1_Proxy.MOV
            RAW_03/TCRMP20250413_3D_MRS_T1_1.MOV
            RAW_03/TCRMP20250413_3D_MRS_T1_2.MOV
        """)}
        self._catalog(listings)
        first = tuple(self._bytes(p) for p in (self.r.REGISTRY_CSV, self.r.SOURCE_FILES_CSV, self.r.EVENTS_CSV))
        time.sleep(1.1)  # a second stamp would differ if anything were rewritten
        self._catalog(listings)
        second = tuple(self._bytes(p) for p in (self.r.REGISTRY_CSV, self.r.SOURCE_FILES_CSV, self.r.EVENTS_CSV))
        self.assertEqual(first, second)

    def test_dry_run_writes_nothing_at_all(self):
        run = self._catalog({ROOT_2025: _names("""
            TCRMP20250413_3D_LBH_T1_Proxy.MOV
            GOPR0001.MP4
        """)}, dry_run=True)
        self.assertEqual(len(run.rows), 1)
        self.assertEqual(len(run.needs_attention), 1)
        self.assertEqual(sorted(os.listdir(self.registry_root)), [])

    def test_hostile_names_round_trip_through_the_report(self):
        hostile = ['TCRMP20250413_3D_LBH_T1"quote.MOV', "comma,name.MOV", "semi;colon.MOV",
                   "new\nline.MOV", "ünïcødé.MOV", " leading space.MOV", "tab\tname.MOV"]
        run = self._catalog({ROOT_2025: [(name, GB) for name in hostile]})
        self.assertEqual(run.rows, [])
        path = atlascatalog.write_needs_attention_report(run.needs_attention)
        self.assertEqual(path, self.r.NEEDS_ATTENTION_CSV)
        back = self.r.needs_attention()
        self.assertEqual([i["path"] for i in back], sorted(f"{ROOT_2025}/{name}" for name in hostile))
        self.assertEqual({i["reason"] for i in back}, {atlascatalog.BAD_NAME_REASON})

    def test_ten_thousand_files_in_bounded_time(self):
        entries = []
        for i in range(10_000):
            site = f"S{i % 300:03d}"
            transect = f"T{(i // 300) % 6 + 1}"
            entries.append((f"d{i % 7}/TCRMP2025{4 + (i // 1800) % 2:02d}{(i % 28) + 1:02d}_3D_{site}_{transect}_Proxy.MOV", GB))
        started = time.monotonic()
        run = self._catalog({ROOT_2025: entries}, dry_run=True)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 10.0, f"10,000 files took {elapsed:.1f}s")
        self.assertGreater(len(run.rows) + len(run.needs_attention), 0)

    def test_report_path_equals_the_registry_constant_with_no_tmp_left(self):
        path = atlascatalog.write_needs_attention_report([
            {"root": ROOT_2025, "path": f"{ROOT_2025}/x", "reason": atlascatalog.BAD_NAME_REASON, "detail": ""}])
        self.assertEqual(path, self.r.NEEDS_ATTENTION_CSV)
        self.assertEqual(atlascatalog.NEEDS_ATTENTION_FILENAME, os.path.basename(self.r.NEEDS_ATTENTION_CSV))
        leftovers = [n for n in os.listdir(self.registry_root) if n.endswith(self.r.TMP_SUFFIX)]
        self.assertEqual(leftovers, [])
        with open(path, newline="") as fh:
            self.assertEqual(next(csv.reader(fh)), self.r.NEEDS_ATTENTION_COLUMNS)
        # An empty run rewrites the file to its header only.
        atlascatalog.write_needs_attention_report([])
        self.assertEqual(self.r.needs_attention(), [])

    def test_a_run_over_one_root_keeps_another_root_s_findings(self):
        # The names in this report are files no rule can read, so the report is
        # the only record that they exist at all. A catalog run over the 2024
        # spring season used to erase every finding from every other season, so
        # reading the report showed nine problems where the archive held
        # eighteen (2026-09-08).
        other_root = "/volume4/Archive6_16TB/TCRMP_2024Annual"
        atlascatalog.write_needs_attention_report([
            {"root": ROOT_2025, "path": f"{ROOT_2025}/odd_one.MP4",
             "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
            {"root": other_root, "path": f"{other_root}/odd_two.MOV",
             "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
        ])
        atlascatalog.write_needs_attention_report(
            [{"root": ROOT_2025, "path": f"{ROOT_2025}/odd_three.MP4",
              "reason": atlascatalog.BAD_NAME_REASON, "detail": ""}],
            walked_roots=[ROOT_2025])
        paths = sorted(r["path"] for r in self.r.needs_attention())
        self.assertEqual(paths, sorted([f"{ROOT_2025}/odd_three.MP4", f"{other_root}/odd_two.MOV"]),
                         "a run over one root erased another root's findings")

    def test_walked_roots_replaces_only_those_roots(self):
        other_root = "/volume4/Archive6_16TB/TCRMP_2024Annual"
        atlascatalog.write_needs_attention_report([
            {"root": ROOT_2025, "path": f"{ROOT_2025}/gone.MP4",
             "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
            {"root": other_root, "path": f"{other_root}/stays.MOV",
             "reason": atlascatalog.BAD_NAME_REASON, "detail": ""},
        ])
        # This root was walked and came back clean: its old line goes.
        atlascatalog.write_needs_attention_report([], walked_roots=[ROOT_2025])
        paths = [r["path"] for r in self.r.needs_attention()]
        self.assertEqual(paths, [f"{other_root}/stays.MOV"])

    def test_report_refuses_a_malformed_item(self):
        with self.assertRaises((TypeError, ValueError)):
            atlascatalog.write_needs_attention_report(["not a dict"])
        with self.assertRaises((TypeError, ValueError)):
            atlascatalog.write_needs_attention_report([{"root": "r", "path": "p", "reason": "", "detail": ""}])

    def test_concurrent_report_writers_leave_one_valid_file_and_no_tmp(self):
        errors = []

        def writer(n):
            items = [{"root": "r", "path": f"p{n}-{i}", "reason": "reason", "detail": "d"} for i in range(200)]
            try:
                for _ in range(5):
                    atlascatalog.write_needs_attention_report(items)
            except Exception as exc:  # noqa: BLE001 - the test reports every failure
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        back = self.r.needs_attention()
        self.assertEqual(len(back), 200)
        self.assertEqual(len({i["path"].split("-")[0] for i in back}), 1, "one writer's payload, whole")
        self.assertEqual([n for n in os.listdir(self.registry_root) if n.endswith(self.r.TMP_SUFFIX)], [])

    def test_concurrent_catalog_runs_share_the_registry_safely(self):
        errors = []
        roots = {f"/volume4/Archive6_16TB/season{n}": _names(f"TCRMP2024101{n}_3D_S{n}_T1.MOV") for n in range(1, 6)}

        def run_one(root):
            try:
                self._catalog({root: roots[root]})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run_one, args=(root,)) for root in roots]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r["readable_id"] for r in self.r.load()), [f"S{n}_T1_2024ann" for n in range(1, 6)])
        self.assertEqual(len(self._sidecar()), 5)

    def test_main_prints_the_counts_and_the_step_lines_on_a_real_run(self):
        cfg = self._write_config(ROOT_2024)
        listings = {ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MP4 5000000000
            TCRMP20240412_3D_MRS_T1_Proxy.MP4 1000000000
            notes.txt 10
        """)}
        output = self._main(["--nas-config", cfg], listings)
        for line in ("listed 3 files across 1 roots", "overrides applied: 0", "rows: 1",
                     "source files: 2 (1 in row)", "needs attention: 1", "Step 1 complete", "Step 2 complete",
                     f"Needs-attention report: {self.r.NEEDS_ATTENTION_CSV}"):
            self.assertIn(line, output, output)
        self.assertNotIn("DRY RUN", output)
        self.assertEqual(len(self.r.load()), 1)
        self.assertEqual(len(self.r.needs_attention()), 1)
        self.assertEqual(len(self._sidecar("MRS_T1_2024_pbl")), 2)

    def test_main_dry_run_prints_counts_but_no_step_2(self):
        cfg = self._write_config(ROOT_2024)
        output = self._main(["--nas-config", cfg, "--dry-run"], {ROOT_2024: _names("TCRMP20240412_3D_MRS_T1.MP4")})
        self.assertIn("rows: 1", output)
        self.assertIn("Step 1 complete", output)
        self.assertNotIn("Step 2 complete", output)
        self.assertIn("DRY RUN: needs-attention report not written", output)

    def test_unmounted_root_still_reported_beside_a_good_one(self):
        bad = "/volume6/Archive8_12TB/2026_pbl"
        run = self._catalog({ROOT_2024: _names("TCRMP20240412_3D_MRS_T1.MP4"), bad: None}, roots=[bad, ROOT_2024])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.UNMOUNTED_REASON])
        self.assertEqual(run.roots_listed, 1)
        self.assertEqual(run.listed_files, 1)

    def test_load_nas_config_errors_name_the_path(self):
        missing = os.path.join(self.registry_root, "absent.yaml")
        with self.assertRaises(FileNotFoundError) as ctx:
            atlascatalog.load_nas_config(missing)
        self.assertIn(missing, str(ctx.exception))
        path = os.path.join(self.registry_root, "list.yaml")
        with open(path, "w") as fh:
            fh.write("- a\n- b\n")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.load_nas_config(path)
        self.assertIn(path, str(ctx.exception))


class RealSeasonShapeTests(_CatalogCase):
    """The 2025 spring season exactly as the drive inventory lists it."""

    def test_fixture_matches_the_inventory_census(self):
        self.assertEqual(len(PBL_2025), 95)
        self.assertEqual(sum(size for _, _, _, size in PBL_2025), PBL_2025_BYTES)
        folders = {}
        for _, _, folder, _ in PBL_2025:
            folders[folder] = folders.get(folder, 0) + 1
        self.assertEqual(folders, {"TCRMP_2025_PBL_RAW_01": 24, "TCRMP_2025_PBL_RAW_02": 24,
                                   "TCRMP_2025_PBL_RAW_03": 24, "TCRMP_2025_PBL_RAW_04": 23})

    def test_real_2025_pbl_shape(self):
        run = self._catalog({PBL_2025_ROOT: _pbl_listing()}, overrides=PBL_2025_OVERRIDES)
        self.assertEqual(len(run.rows), 95)
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.overrides_applied, 6)
        self.assertEqual(run.listed_files, 95)
        rows = self.r.load()
        self.assertEqual(len(rows), 95)
        self.assertTrue(all(r["year"] == "2025" and r["season_token"] == "_pbl" for r in rows))
        by_id = {r["readable_id"]: r for r in rows}
        fix1 = by_id["LBHLBPFIX1_T1_2025_pbl"]
        self.assertEqual(fix1["video_location"], f"{HOST}:{PBL_2025_ROOT}/TCRMP_2025_PBL_RAW_03")
        self.assertEqual(fix1["video_size_gb"], "109.449")
        self.assertEqual(fix1["original_videos"], "TCRMP20250413_3D_LBH_T1_Proxy.MOV")
        fix2 = by_id["LBHLBPFIX2_T1_2025_pbl"]
        self.assertEqual(fix2["video_location"], f"{HOST}:{PBL_2025_ROOT}/TCRMP_2025_PBL_RAW_04")
        self.assertEqual(fix2["video_size_gb"], "115.455")
        self.assertNotIn("LBH_T1_2025_pbl", by_id)
        self.assertEqual(sorted(i for i in by_id if i.startswith("CBD_")), ["CBD_T1_2025_pbl", "CBD_T3_2025_pbl"])
        self.assertEqual(len({r["site"] for r in rows}), 32, "30 ordinary sites, CBD, and the two fix labels")
        sidecar = self._sidecar()
        self.assertEqual(len(sidecar), 95)
        self.assertTrue(all(f["in_row"] == "true" and f["proxy"] == "true" for f in sidecar))
        self.assertEqual(sum(int(f["size_bytes"]) for f in sidecar), PBL_2025_BYTES)
        self.assertTrue(all(f["filmed_on"].startswith("2025-0") for f in sidecar))
        fix_notes = [f["edit_note"] for f in sidecar if f["readable_id"].startswith("LBHLBPFIX")]
        self.assertEqual(len(fix_notes), 6)
        self.assertTrue(all(n.startswith("site label LBHLBPFIX") for n in fix_notes))
        with open(self.r.EVENTS_CSV, newline="") as fh:
            events = list(csv.DictReader(fh))
        self.assertEqual(len(events), 95 * 8 + 95)
        self.assertEqual(sum(1 for e in events if e["field"] == "source_files"), 95)

    def test_real_2025_pbl_shape_without_overrides(self):
        run = self._catalog({PBL_2025_ROOT: _pbl_listing()})
        self.assertEqual(len(run.rows), 92)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.SECOND_DATE_REASON] * 3)
        self.assertTrue(all(i["path"].endswith("TCRMP_2025_PBL_RAW_04") for i in run.needs_attention))
        row = self.r.get("LBH_T1_2025_pbl")
        self.assertEqual(row["video_location"], f"{HOST}:{PBL_2025_ROOT}/TCRMP_2025_PBL_RAW_03")
        self.assertEqual(row["video_size_gb"], "109.449")
        sidecar = self._sidecar()
        self.assertEqual(len(sidecar), 95)
        self.assertEqual(sum(1 for f in sidecar if f["in_row"] == "true"), 92)
        later = [f for f in sidecar if f["in_row"] == "false"]
        self.assertEqual({f["filmed_on"] for f in later}, {"2025-04-15"})
        self.assertTrue(all(f["edit_note"].startswith("second recording on 2025-04-15") for f in later))


class PrepToolsGroupingExtractionTests(unittest.TestCase):
    """The pure part-set grouping helper in prep_tools.py stays as it was:
    the catalog now groups on its own (overrides and name variants change
    a file's identity before grouping), and the local prep keeps this one."""

    def test_group_video_names_classifies_parts_and_canonical(self):
        groups = prep_tools.group_video_names([
            "TCRMP20240412_3D_MRS_T1_1.MP4",
            "TCRMP20240412_3D_MRS_T1_2.MP4",
            "TCRMP20241018_3D_MRS_T1_Proxy.MKV",
            "not_a_video_name.MP4",
        ])
        by_key = {g["key"]: g for g in groups}

        merge_group = by_key[("TCRMP", "20240412", "MRS", "T1")]
        self.assertEqual([m[0] for m in merge_group["parts"]],
                          ["TCRMP20240412_3D_MRS_T1_1.MP4", "TCRMP20240412_3D_MRS_T1_2.MP4"])
        self.assertIsNone(merge_group["canonical"])

        proxy_group = by_key[("TCRMP", "20241018", "MRS", "T1")]
        self.assertEqual(len(proxy_group["members"]), 1)
        self.assertEqual(proxy_group["output"], "TCRMP20241018_3D_MRS_T1.MKV")

        self.assertEqual(len(groups), 2, "the unparsed name must be left out entirely")


if __name__ == "__main__":
    unittest.main()


class ExcludedPathTypoTests(unittest.TestCase):
    def test_a_dot_dot_segment_in_an_excluded_path_is_refused_not_folded(self):
        with self.assertRaises(ValueError) as ctx:
            atlascatalog._check_excluded(["/volume6/Archive8_12TB/driver_deposits/.."])
        self.assertIn("..", str(ctx.exception))


class ProcessingFolderTests(_CatalogCase):
    """Rule: the catalog never reads inside a processing folder.

    On 2026-09-14 an old Voyager 1 project on Archive9,
    /volume2/Archive9_10TB/TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing/frames/<id>/,
    made the dry run report 22 second recordings and would have moved 22
    rows of 2025 spring onto frame folders: a copy inside frames/ parses
    like its original and its volume sorts first. A directory named
    processing or frames, one named {SITE}_{T#}_3D, or one ending
    _3dprocessing is pruned in the find (directories only) and dropped after
    listing, with a count; a root inside one is reported, never listed.
    """

    ARCHIVE9 = "/volume2/Archive9_10TB"
    ARCHIVE2 = "/volume3/Archive2_12TB/TCRMP_2025_PBL/_encoded"
    MRS_T1 = "TCRMP20250321_3D_MRS_T1_Proxy.MOV"
    OLD_PROJECT = "TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing"
    ARCHIVE9_SPEC = f"""
        {OLD_PROJECT}/frames/MRS_T1_2025_pbl/{MRS_T1} 97236222464
        {OLD_PROJECT}/frames/MRS_T1_2025_pbl/TCRMP20250321_3D_MRS_T1_frame_000001.jpg 100
        {OLD_PROJECT}/MRS_T1_2023_2025.psx 100
    """
    ARCHIVE9_FILES = 3

    def _dir_pruning_runner(self, listings):
        """A fake find that honours its -name and -iname terms on directory
        names and its -path terms, as the NAS does."""
        def runner(argv, timeout=60):
            root = argv[1]
            entries = listings.get(root)
            if entries is None:
                return (1, "", f"find: '{root}': No such file or directory")
            names = [(argv[i], argv[i + 1]) for i, word in enumerate(argv) if word in ("-name", "-iname")]
            paths = [argv[i + 1] for i, word in enumerate(argv) if word == "-path"]

            def pruned(rel):
                full = posixpath.join(root, rel)
                if any(full == p or full.startswith(p + "/") for p in paths):
                    return True
                for segment in posixpath.dirname(rel).split("/"):
                    for flag, pattern in names:
                        if flag == "-name" and fnmatch.fnmatchcase(segment, pattern):
                            return True
                        if flag == "-iname" and fnmatch.fnmatchcase(segment.lower(), pattern.lower()):
                            return True
                return False

            kept = [(rel, size) for rel, size in entries if not pruned(rel)]
            return (0, "".join(f"{size}\0{rel}\0" for rel, size in kept), "")
        return runner

    def test_the_archive9_frames_folder_makes_no_row_and_no_second_recording_line(self):
        run = self._catalog({self.ARCHIVE9: _names(self.ARCHIVE9_SPEC),
                             self.ARCHIVE2: _names(f"TCRMP_2025_PBL_RAW_01/{self.MRS_T1} 97236222464")})
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2025_pbl"])
        self.assertEqual(run.rows[0]["path"], f"{self.ARCHIVE2}/TCRMP_2025_PBL_RAW_01")
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.processing_files, self.ARCHIVE9_FILES)
        self.assertEqual(run.listed_files, self.ARCHIVE9_FILES + 1)
        self.assertEqual(run.excluded_files, 0)
        self.assertEqual([f["nas_path"] for f in self._sidecar()],
                         [f"{HOST}:{self.ARCHIVE2}/TCRMP_2025_PBL_RAW_01/{self.MRS_T1}"])

    def test_a_transect_processing_folder_under_a_season_root_is_dropped(self):
        run = self._catalog({ROOT_2024: _names("""
            TCRMP20240412_3D_MRS_T1.MP4
            MRS_T1_3D/frames/MRS_T1_2024_pbl/TCRMP20240412_3D_MRS_T1.MP4
            MRS_T1_3D/MRS_T1_2024_2024.psx 100
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2024_pbl"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.processing_files, 2)
        self.assertEqual(len(self._sidecar()), 1)

    def test_an_old_style_3dprocessing_folder_is_dropped(self):
        run = self._catalog({ROOT_2024: _names("""
            MRS_T1_2023ann_3dprocessing/frames/MRS_T1_2023ann/TCRMP20231207_3D_MRS_T1.MP4
            MRS_T1_2023ann_3DPROCESSING/report.pdf 100
        """)})
        self.assertEqual(run.rows, [])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.processing_files, 2)

    def test_the_processing_folder_predicate(self):
        for name in ("processing", "Processing", "PROCESSING", "frames", "Frames", "MRS_T1_3D", "FLC_T10_3D",
                     "MRS_T1_2023ann_3dprocessing", "x_3DPROCESSING"):
            self.assertTrue(atlascatalog._is_processing_folder(name), name)
        for name in ("", "processing2", "frames.bak", "mrs_t1_3d", "MRS_T1_3D_old", "TCRMP_2024_PBL",
                     "TCRMP2024_postbl_3D_DemoVideos", "encoded", "3dprocessing", "frames\n", None):
            self.assertFalse(atlascatalog._is_processing_folder(name), repr(name))

    def test_find_argv_prunes_processing_folders_as_directories(self):
        argv = atlascatalog._find_argv(ROOT_2024)
        terms = atlascatalog.PROCESSING_PRUNE_TERMS
        self.assertEqual(argv[4:], ["(", "-name", "@*", "-o", "-name", "#recycle", "-o", *terms, ")",
                                    "-prune", "-o", "-type", "f", "-printf", r"%s\0%P\0"])
        self.assertEqual(terms[:3], ["(", "-type", "d"])
        for word in atlascatalog.PROCESSING_FOLDER_WORDS:
            self.assertEqual(terms[terms.index(word) - 1], "-iname")
        self.assertEqual(terms[terms.index("*" + atlascatalog.PROCESSING_FOLDER_SUFFIX) - 1], "-iname")
        self.assertEqual(terms[terms.index(atlascatalog.PROCESSING_FOLDER_GLOB) - 1], "-name")
        # The excluded-path terms follow the processing group inside the same prune.
        with_shelf = atlascatalog._find_argv("/volume6", ["/volume6/driver_deposits"])
        self.assertEqual(with_shelf[4:], ["(", "-name", "@*", "-o", "-name", "#recycle", "-o", *terms,
                                          "-o", "-path", "/volume6/driver_deposits", ")",
                                          "-prune", "-o", "-type", "f", "-printf", r"%s\0%P\0"])

    def test_the_find_prune_alone_keeps_processing_folders_off_the_nas(self):
        listings = {self.ARCHIVE9: _names(self.ARCHIVE9_SPEC + f"""
            TCRMP_2025_PBL/TCRMP_2025_PBL_01/{self.MRS_T1} 97236222464
            TCRMP_2025_PBL/MRS_T1_3D/frames/x/TCRMP20250321_3D_MRS_T1_Proxy.MOV
            TCRMP_2025_PBL/MRS_T1_2025_pbl_3dprocessing/x/TCRMP20250321_3D_MRS_T1_Proxy.MOV
            TCRMP_2025_PBL/Frames/TCRMP20250321_3D_MRS_T1_Proxy.MOV
        """)}
        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=self._dir_pruning_runner(listings))
        run = atlascatalog.catalog([self.ARCHIVE9], ssh)
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2025_pbl"])
        self.assertEqual(run.listed_files, 1, "the NAS never listed the processing folders")
        self.assertEqual(run.processing_files, 0, "layer two had nothing left to drop")
        self.assertEqual(run.needs_attention, [])

    def test_a_root_inside_a_processing_folder_is_reported_not_listed(self):
        seen = []

        def runner(argv, timeout=60):
            seen.append(argv[1])
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        inside = f"{self.ARCHIVE9}/{self.OLD_PROJECT}/frames"
        run = atlascatalog.catalog([inside, ROOT_2024, f"{self.ARCHIVE9}/x/MRS_T1_3D"], ssh)
        self.assertEqual(seen, [ROOT_2024])
        self.assertEqual([(i["root"], i["path"], i["reason"]) for i in run.needs_attention],
                         [(inside, inside, atlascatalog.PROCESSING_ROOT_REASON),
                          (f"{self.ARCHIVE9}/x/MRS_T1_3D",) * 2 + (atlascatalog.PROCESSING_ROOT_REASON,)])
        self.assertIn("processing", run.needs_attention[0]["detail"])
        self.assertIn("MRS_T1_3D", run.needs_attention[1]["detail"])
        self.assertEqual(run.roots_listed, 1)
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.list_root(ssh, inside)
        self.assertIn("frames", str(ctx.exception))
        self.assertEqual(seen, [ROOT_2024], "list_root refused before any find")

    def test_a_file_named_frames_is_a_bad_name_not_a_folder(self):
        run = self._catalog({ROOT_2024: _names("frames 10\nprocessing 10\nMRS_T1_3D 10")})
        self.assertEqual(run.processing_files, 0)
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.BAD_NAME_REASON] * 3)

    def test_hostile_folder_names_around_a_processing_folder(self):
        run = self._catalog({ROOT_2024: _names("""
            odd/frames/TCRMP20240412_3D_MRS_T1.MP4
            a//frames//TCRMP20240412_3D_MRS_T2.MP4
            ./frames/TCRMP20240412_3D_MRS_T3.MP4
            frames.old/TCRMP20240412_3D_MRS_T4.MP4
            processing/TCRMP20240412_3D_MRS_T5.MP4
        """)})
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T4_2024_pbl"])
        self.assertEqual(run.processing_files, 4)
        self.assertEqual(run.needs_attention, [])

    def test_the_count_of_files_inside_processing_folders_is_printed(self):
        cfg = self._write_config(self.ARCHIVE9)
        output = self._main(["--nas-config", cfg, "--dry-run"], {self.ARCHIVE9: _names(self.ARCHIVE9_SPEC)})
        self.assertIn(f"inside processing folders, dropped after listing: {self.ARCHIVE9_FILES} files", output)
        self.assertIn("never read inside: processing folders", output)
        self.assertIn("No timepoints found.", output)
        self.assertIn(f"listed {self.ARCHIVE9_FILES} files across 1 roots", output)

    def test_a_processing_folder_under_the_shelf_counts_as_excluded_not_processing(self):
        shelf = f"{self.ARCHIVE9}/driver_deposits"
        run = self._catalog({self.ARCHIVE9: _names("driver_deposits/x/MRS_T1_3D/frames/y/TCRMP20240412_3D_MRS_T1.MP4")},
                            excluded=[shelf])
        self.assertEqual(run.rows, [])
        self.assertEqual(run.excluded_files, 1)
        self.assertEqual(run.processing_files, 0)


# The 26 rulings Lauren made on 2026-09-14 16:26 AST, as catalog_rulings.csv
# holds them: (volume, path under it, ruling, label, transect, date, part).
RULED_2026_09_14 = [
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2023Annual/TCRMP20231112_demo_SPH_T1_again?.MP4", "catalog_as", "SPHSORT", "T1", "20231112", ""),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2023Annual/TCRMP20231115_demo_KGC_T3_WRONG.MP4", "catalog_as", "KGCSORT", "T3", "20231115", ""),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2023Annual/TCRMP20231205_demo_GKT_T2_WRONG.MP4", "catalog_as", "GKTSORT", "T2", "20231205", ""),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2023Annual/TCRMP20231207_demo_MRS_T3_part2?.MP4", "leave_out", "", "", "", ""),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T1_OR_T2?.MP4", "catalog_as", "CSTSORT", "T1", "20240311", ""),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_1.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "1"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_2.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "2"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_3.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "3"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_4.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "4"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_6.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "5"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240311_demo_CST_T5_7.MP4", "catalog_as", "CSTSORT", "T5", "20240311", "6"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240408_demo_GKT_EXTRA_T1_1.MP4", "catalog_as", "GKTEXTRA", "T1", "20240408", "1"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240408_demo_GKT_EXTRA_T1_2.MP4", "catalog_as", "GKTEXTRA", "T1", "20240408", "2"),
    ("/volume2/Archive9_10TB", "encoded/TCRMP_2024_PBL/TCRMP20240408_demo_GKT_EXTRA_T1_3.MP4", "catalog_as", "GKTEXTRA", "T1", "20240408", "3"),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_02/TCRMP20241024_3D_CRB_T3lit_Proxy.MOV", "catalog_as", "CRBLIT", "T3", "20241024", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_02/TCRMP20241024_3D_CRB_T3unlit_Proxy.MOV", "catalog_as", "CRBUNLIT", "T3", "20241024", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_03/TCRMP20241112_3D_CST_T5OFAV_Proxy.MOV", "catalog_as", "CST", "T5", "20241112", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_04/TCRMP20241113_3D_JKB_T2_2_Proxy.MOV", "catalog_as", "JKB", "T2", "20241113", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_04/TCRMP20241113_3D_JKB_T2_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241029_3D_SSJ_lobster1_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241029_3D_SSJ_lobster2_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241111_3D_BIX_boat1_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241111_3D_BIX_boat2_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241111_3D_BIX_overhead_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/X016C122_24111246_TCRMP_Proxy.MOV", "leave_out", "", "", "", ""),
    ("/volume4/Archive6_16TB", "TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/X016C127_2411126X_TCRMP_Proxy.MOV", "leave_out", "", "", "", ""),
]
RULED_LEAVE_OUT = 9
RULED_CATALOG_AS = 17
RULED_ROWS = ["CRBLIT_T3_2024ann", "CRBUNLIT_T3_2024ann", "CSTSORT_T1_2024_pbl", "CSTSORT_T5_2024_pbl",
              "CST_T5_2024ann", "GKTEXTRA_T1_2024_pbl", "GKTSORT_T2_2023ann", "JKB_T2_2024ann",
              "KGCSORT_T3_2023ann", "SPHSORT_T1_2023ann"]
LIVE_RULINGS_CSV = "/mnt/rip/vicarius_drive/vicarius/_METADATA/3d/catalog_rulings.csv"


class RulingTests(_CatalogCase):
    """catalog_rulings.csv: Lauren's rulings on the files the catalog cannot
    place, applied on every run.

    A leave_out file is dropped before parsing and counted, never a
    needs-attention line. A catalog_as file is parsed as if it were named
    TCRMP{date}_3D_{label}_{T#}[_{part}] and then grouped, resolved and
    recorded exactly like a well-named file: its real name and path on the
    sidecar line and in original_videos, its part from the ruling, and the
    ruling sentence first in its edit_note. A site label such as CSTSORT or
    CRBLIT names a separate project, as the LBHLBPFIX labels did. The 26
    rulings of 2026-09-14 (RULED_2026_09_14) are the shape.
    """

    VOL2 = "/volume2/Archive9_10TB"
    VOL4 = "/volume4/Archive6_16TB"
    ANN_2023 = f"{VOL2}/encoded/TCRMP_2023Annual"
    PBL_2024 = f"{VOL2}/encoded/TCRMP_2024_PBL"
    ANN_2024_03 = f"{VOL4}/TCRMP_2024Annual/encoded/TCRMP_2024Annual_03"
    ANN_2024_04 = f"{VOL4}/TCRMP_2024Annual/encoded/TCRMP_2024Annual_04"
    RULED_AT = "2026-09-14T16:26:00-04:00"
    STAMP = "2026-09-14 16:26 AST"
    OFAV = "TCRMP20241112_3D_CST_T5OFAV_Proxy.MOV"
    OFAV_NOTE = "OFAV colony clip, the only 2024 annual file for Castle T5"
    HEADER = ",".join(registry.RULING_COLUMNS) + "\n"

    def _ruling(self, abs_path, ruling, label="", transect="", date="", part="", note="", who="LO", when=None):
        """One ruling line as registry.rulings() returns it."""
        return {"nas_path": f"{HOST}:{abs_path}", "file_name": posixpath.basename(abs_path), "ruling": ruling,
                "site_label": label, "transect": transect, "date": date, "part": part, "note": note,
                "ruled_by": who, "ruled_at": self.RULED_AT if when is None else when}

    def _ofav_ruling(self, **cells):
        """The CST T5 OFAV ruling, with any cell replaced by name (site_label="CST/T5" for a hostile one)."""
        line = self._ruling(f"{self.ANN_2024_03}/{self.OFAV}", "catalog_as", "CST", "T5", "20241112", note=self.OFAV_NOTE)
        line.update(cells)
        return line

    def _ruled_lines(self):
        """The 26 rulings of 2026-09-14 as lines."""
        return [self._ruling(f"{volume}/{relpath}", ruling, label, transect, date, part, note="to sort")
                for volume, relpath, ruling, label, transect, date, part in RULED_2026_09_14]

    def _ruled_listings(self):
        """The 26 ruled files on their two volumes, each one GB."""
        listings = {self.VOL2: [], self.VOL4: []}
        for volume, relpath, *_ in RULED_2026_09_14:
            listings[volume].append((relpath, GB))
        return listings

    def _rulings_file(self, text, name="rulings.csv"):
        path = os.path.join(self.registry_root, name)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path

    def _sentence(self, note, who="LO", stamp=None):
        stamp = self.STAMP if stamp is None else stamp
        return f"catalogued by ruling of {who} {stamp}: {note}"

    # --- leave_out -----------------------------------------------------------

    def test_a_leave_out_file_is_dropped_before_parsing_and_counted(self):
        lobster = f"{self.VOL4}/TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA/TCRMP20241029_3D_SSJ_lobster1_Proxy.MOV"
        run = self._catalog({self.VOL4: [(lobster[len(self.VOL4) + 1:], GB)]},
                            rulings=[self._ruling(lobster, "leave_out", note="not a transect recording")])
        self.assertEqual(run.rows, [])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.ruled_out, 1)
        self.assertEqual(run.rulings_applied, 0)
        self.assertEqual(self._sidecar(), [])

    def test_the_jkb_pair_keeps_the_ruled_take_and_leaves_the_false_start_out(self):
        false_start, take = "TCRMP20241113_3D_JKB_T2_Proxy.MOV", "TCRMP20241113_3D_JKB_T2_2_Proxy.MOV"
        rulings = [self._ruling(f"{self.ANN_2024_04}/{false_start}", "leave_out", note="6.3 GB false start"),
                   self._ruling(f"{self.ANN_2024_04}/{take}", "catalog_as", "JKB", "T2", "20241113",
                                note="the 138 GB take is the recording")]
        run = self._catalog({self.ANN_2024_04: _names(f"{false_start} 6300000000\n{take} 138000000000")}, rulings=rulings)
        self.assertEqual([r["readable_id"] for r in run.rows], ["JKB_T2_2024ann"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual((run.ruled_out, run.rulings_applied), (1, 1))
        row = self.r.get("JKB_T2_2024ann")
        self.assertEqual(row["original_videos"], take)
        self.assertEqual(row["video_size_gb"], "138.0")
        files = self._sidecar("JKB_T2_2024ann")
        self.assertEqual([(f["file_name"], f["part"], f["in_row"]) for f in files], [(take, "", "true")])
        self.assertEqual(files[0]["edit_note"],
                         f"{self._sentence('the 138 GB take is the recording')}; {atlascatalog.NOTE_PROXY}")

    # --- catalog_as ------------------------------------------------------------

    def test_catalog_as_a_whole_file_makes_the_row_with_the_real_name_and_the_ruling_sentence(self):
        run = self._catalog({self.ANN_2024_03: _names(f"{self.OFAV} 45000000000")}, rulings=[self._ofav_ruling()])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.rulings_applied, 1)
        row = self.r.get("CST_T5_2024ann")
        self.assertEqual((row["site"], row["transect"], row["year"], row["season_token"]), ("CST", "T5", "2024", "ann"))
        self.assertEqual(row["original_videos"], self.OFAV)
        self.assertEqual(row["video_location"], f"{HOST}:{self.ANN_2024_03}")
        self.assertEqual(row["video_size_gb"], "45.0")
        files = self._sidecar("CST_T5_2024ann")
        self.assertEqual(len(files), 1)
        line = files[0]
        self.assertEqual(line["file_name"], self.OFAV)
        self.assertEqual(line["nas_path"], f"{HOST}:{self.ANN_2024_03}/{self.OFAV}")
        self.assertEqual((line["part"], line["proxy"], line["in_row"], line["filmed_on"], line["container"]),
                         ("", "true", "true", "2024-11-12", "mov"))
        self.assertEqual(line["edit_note"], f"{self._sentence(self.OFAV_NOTE)}; {atlascatalog.NOTE_PROXY}")
        self.assertEqual(line["edit_note"], "catalogued by ruling of LO 2026-09-14 16:26 AST: OFAV colony clip, "
                                            "the only 2024 annual file for Castle T5; proxy suffix dropped at rename")

    def test_catalog_as_a_labelled_whole_makes_the_labelled_row_beside_its_sibling(self):
        folder = f"{self.VOL4}/TCRMP_2024Annual/encoded/TCRMP_2024Annual_02"
        lit, unlit = "TCRMP20241024_3D_CRB_T3lit_Proxy.MOV", "TCRMP20241024_3D_CRB_T3unlit_Proxy.MOV"
        rulings = [self._ruling(f"{folder}/{lit}", "catalog_as", "CRBLIT", "T3", "20241024", note="lighting take"),
                   self._ruling(f"{folder}/{unlit}", "catalog_as", "CRBUNLIT", "T3", "20241024", note="lighting take")]
        run = self._catalog({folder: _names(f"{lit}\n{unlit}")}, rulings=rulings)
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["CRBLIT_T3_2024ann", "CRBUNLIT_T3_2024ann"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("CRBLIT_T3_2024ann")["site"], "CRBLIT")
        self.assertEqual(self.r.get("CRBLIT_T3_2024ann")["original_videos"], lit)
        self.assertEqual(self.r.get("CRBUNLIT_T3_2024ann")["original_videos"], unlit)

    def test_a_ruled_part_set_makes_one_row_with_three_parts(self):
        names = [f"TCRMP20240408_demo_GKT_EXTRA_T1_{n}.MP4" for n in (1, 2, 3)]
        rulings = [self._ruling(f"{self.PBL_2024}/{name}", "catalog_as", "GKTEXTRA", "T1", "20240408", str(n),
                                note="extra recording, joined") for n, name in enumerate(names, start=1)]
        # Listed out of order: the row still reads in part order.
        run = self._catalog({self.PBL_2024: _names("\n".join(reversed(names)))}, rulings=rulings)
        self.assertEqual([r["readable_id"] for r in run.rows], ["GKTEXTRA_T1_2024_pbl"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(run.rulings_applied, 3)
        row = self.r.get("GKTEXTRA_T1_2024_pbl")
        self.assertEqual(row["original_videos"], ";".join(names))
        self.assertEqual(row["video_size_gb"], "3.0")
        files = self._sidecar("GKTEXTRA_T1_2024_pbl")
        self.assertEqual([(f["file_name"], f["part"], f["proxy"]) for f in files],
                         [(name, str(n), "false") for n, name in enumerate(names, start=1)])
        for line in files:
            self.assertEqual(line["edit_note"], f"{self._sentence('extra recording, joined')}; {atlascatalog.NOTE_PARTS}")

    def test_the_renumbered_castle_set_makes_six_parts_in_ruled_order(self):
        archive_numbers = (1, 2, 3, 4, 6, 7)
        names = [f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in archive_numbers]
        note = "archive part 5 is missing; archive parts 6 and 7 become parts 5 and 6"
        rulings = [self._ruling(f"{self.PBL_2024}/{name}", "catalog_as", "CSTSORT", "T5", "20240311", str(part), note=note)
                   for part, name in enumerate(names, start=1)]
        run = self._catalog({self.PBL_2024: _names("\n".join(names))}, rulings=rulings)
        self.assertEqual([r["readable_id"] for r in run.rows], ["CSTSORT_T5_2024_pbl"])
        self.assertEqual(run.needs_attention, [])
        row = self.r.get("CSTSORT_T5_2024_pbl")
        self.assertEqual(row["original_videos"], ";".join(names))
        files = self._sidecar("CSTSORT_T5_2024_pbl")
        self.assertEqual([(f["file_name"], f["part"]) for f in files],
                         [(name, str(part)) for part, name in enumerate(names, start=1)])
        self.assertEqual(files[-1]["file_name"], "TCRMP20240311_demo_CST_T5_7.MP4")
        self.assertEqual(files[-1]["part"], "6")
        self.assertTrue(all(f["edit_note"] == f"{self._sentence(note)}; {atlascatalog.NOTE_PARTS}" for f in files))

    def test_a_ruled_lone_part_is_the_row_with_the_lone_part_note(self):
        name = "TCRMP20240311_demo_CST_T5_7.MP4"
        run = self._catalog({self.PBL_2024: _names(name)},
                            rulings=[self._ruling(f"{self.PBL_2024}/{name}", "catalog_as", "CSTSORT", "T5", "20240311", "2")])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CSTSORT_T5_2024_pbl"])
        self.assertEqual(run.needs_attention, [])
        files = self._sidecar("CSTSORT_T5_2024_pbl")
        self.assertEqual([(f["file_name"], f["part"]) for f in files], [(name, "2")])
        self.assertEqual(files[0]["edit_note"],
                         f"catalogued by ruling of LO {self.STAMP}; {atlascatalog.NOTE_LONE_PART.format(part=2)}")

    def test_the_sort_labels_make_their_own_rows_whatever_the_real_name_says(self):
        cases = [(self.ANN_2023, "TCRMP20231112_demo_SPH_T1_again?.MP4", "SPHSORT", "T1", "20231112", "SPHSORT_T1_2023ann"),
                 (self.ANN_2023, "TCRMP20231115_demo_KGC_T3_WRONG.MP4", "KGCSORT", "T3", "20231115", "KGCSORT_T3_2023ann"),
                 (self.ANN_2023, "TCRMP20231205_demo_GKT_T2_WRONG.MP4", "GKTSORT", "T2", "20231205", "GKTSORT_T2_2023ann"),
                 (self.PBL_2024, "TCRMP20240311_demo_CST_T1_OR_T2?.MP4", "CSTSORT", "T1", "20240311", "CSTSORT_T1_2024_pbl")]
        listings = {self.ANN_2023: [], self.PBL_2024: []}
        rulings = []
        for folder, name, label, transect, date, _rid in cases:
            listings[folder].append((name, GB))
            rulings.append(self._ruling(f"{folder}/{name}", "catalog_as", label, transect, date))
        # No name variant: the demo token in the real names never matters to a ruled file.
        run = self._catalog(listings, rulings=rulings)
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), sorted(c[-1] for c in cases))
        self.assertEqual(run.needs_attention, [])
        for folder, name, label, _transect, _date, rid in cases:
            self.assertEqual(self.r.get(rid)["original_videos"], name)
            self.assertEqual(self.r.get(rid)["site"], label)
            note = self._sidecar(rid)[0]["edit_note"]
            self.assertNotIn(atlascatalog.NOTE_DEMO, note)
            self.assertTrue(note.startswith("catalogued by ruling of LO"), note)

    def test_a_ruled_file_groups_with_a_well_named_file_of_the_same_identity(self):
        # Two whole proxies for CST T5 2024-11-12, neither canonical: "more than one whole file", as for any pair.
        plain = "TCRMP20241112_3D_CST_T5_Proxy.MOV"
        run = self._catalog({self.ANN_2024_03: _names(f"{self.OFAV}\n{plain}")}, rulings=[self._ofav_ruling()])
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.MULTI_WHOLE_REASON])
        self.assertIn(self.OFAV, run.needs_attention[0]["detail"])
        self.assertIn(plain, run.needs_attention[0]["detail"])
        # Ruled as part 2 beside a well-named part 1: one complete set.
        part1 = "TCRMP20241112_3D_CST_T5_1.MOV"
        ruling = self._ruling(f"{self.ANN_2024_03}/{self.OFAV}", "catalog_as", "CST", "T5", "20241112", "2")
        run = self._catalog({self.ANN_2024_03: _names(f"{self.OFAV}\n{part1}")}, rulings=[ruling])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(self.r.get("CST_T5_2024ann")["original_videos"], f"{part1};{self.OFAV}")
        self.assertEqual([(f["file_name"], f["part"]) for f in self._sidecar("CST_T5_2024ann")],
                         [(part1, "1"), (self.OFAV, "2")])

    def test_an_override_on_a_ruled_file_is_reported_and_the_ruling_wins(self):
        overrides = {self.OFAV: {"site": "CSTX", "note": "old override"}}
        run = self._catalog({self.ANN_2024_03: _names(self.OFAV)}, rulings=[self._ofav_ruling()], overrides=overrides)
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual([(i["reason"], i["path"]) for i in run.needs_attention],
                         [(atlascatalog.OVERRIDE_ON_RULED_REASON, f"{self.ANN_2024_03}/{self.OFAV}")])
        self.assertEqual(run.overrides_applied, 0)
        self.assertNotIn("catalog_overrides.csv", self._sidecar("CST_T5_2024ann")[0]["edit_note"])

    def test_a_ruled_file_is_matched_by_path_not_by_name(self):
        # The same name in another folder is not the ruled file: it stays a bad name.
        other = f"{self.VOL4}/elsewhere"
        run = self._catalog({self.ANN_2024_03: _names(self.OFAV), other: _names(self.OFAV)}, rulings=[self._ofav_ruling()])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual([(i["reason"], i["path"]) for i in run.needs_attention],
                         [(atlascatalog.BAD_NAME_REASON, f"{other}/{self.OFAV}")])

    def test_a_root_spelled_with_a_trailing_slash_still_matches_the_ruling(self):
        run = self._catalog({self.ANN_2024_03 + "/": _names(self.OFAV)}, rulings=[self._ofav_ruling()])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual(run.needs_attention, [])

    def test_a_ruling_for_another_host_never_matches(self):
        ruling = self._ofav_ruling()
        ruling["nas_path"] = "other.nas:" + f"{self.ANN_2024_03}/{self.OFAV}"
        run = self._catalog({self.ANN_2024_03: _names(self.OFAV)}, rulings=[ruling])
        self.assertEqual(run.rows, [])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.BAD_NAME_REASON])

    # --- a ruling whose file the listing does not contain ---------------------

    def test_a_ruling_whose_file_is_not_listed_is_reported_once_across_overlapping_roots(self):
        missing = f"{self.ANN_2024_03}/TCRMP20241112_3D_CST_T5GONE_Proxy.MOV"
        ruling = self._ruling(missing, "catalog_as", "CST", "T5", "20241112", note="gone")
        listings = {self.VOL4: [(f"TCRMP_2024Annual/encoded/TCRMP_2024Annual_03/{self.OFAV}", GB)],
                    self.ANN_2024_03: _names(self.OFAV)}
        run = self._catalog(listings, roots=[self.VOL4, self.ANN_2024_03], rulings=[ruling, self._ofav_ruling()])
        self.assertEqual([r["readable_id"] for r in run.rows], ["CST_T5_2024ann"])
        self.assertEqual([(i["root"], i["path"], i["reason"]) for i in run.needs_attention],
                         [(self.VOL4, missing, atlascatalog.RULED_FILE_NOT_FOUND_REASON)])
        detail = run.needs_attention[0]["detail"]
        self.assertIn("catalog_as", detail)
        self.assertIn("CST T5 20241112", detail)
        self.assertEqual(run.rulings_applied, 1)

    def test_a_ruling_under_no_walked_root_is_not_reported(self):
        run = self._catalog({self.ANN_2023: _names("TCRMP20231207_3D_MRS_T1.MP4")}, rulings=[self._ofav_ruling()])
        self.assertEqual([r["readable_id"] for r in run.rows], ["MRS_T1_2023ann"])
        self.assertEqual(run.needs_attention, [])

    def test_a_ruling_under_an_unmounted_root_is_not_reported(self):
        run = self._catalog({self.VOL4: None}, rulings=[self._ofav_ruling()])
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.UNMOUNTED_REASON])

    def test_a_ruled_file_inside_a_processing_folder_is_dropped_and_reported_not_found(self):
        inside = f"{self.VOL4}/x/frames/{self.OFAV}"
        run = self._catalog({self.VOL4: _names(f"x/frames/{self.OFAV}")},
                            rulings=[self._ruling(inside, "catalog_as", "CST", "T5", "20241112")])
        self.assertEqual(run.rows, [])
        self.assertEqual(run.processing_files, 1)
        self.assertEqual([(i["path"], i["reason"]) for i in run.needs_attention],
                         [(inside, atlascatalog.RULED_FILE_NOT_FOUND_REASON)])

    def test_a_leave_out_whose_file_is_not_listed_is_reported_too(self):
        gone = f"{self.ANN_2024_04}/gone.MOV"
        run = self._catalog({self.ANN_2024_04: _names("TCRMP20241113_3D_JKB_T1_Proxy.MOV")},
                            rulings=[self._ruling(gone, "leave_out", note="x")])
        self.assertEqual([(i["path"], i["reason"]) for i in run.needs_attention],
                         [(gone, atlascatalog.RULED_FILE_NOT_FOUND_REASON)])
        self.assertIn("leave_out", run.needs_attention[0]["detail"])

    # --- the sentence and the stamp -------------------------------------------

    def test_the_ruling_sentence_forms(self):
        line = {"ruled_by": "LO", "ruled_at": self.RULED_AT, "note": "why"}
        self.assertEqual(atlascatalog._ruling_note(line), "catalogued by ruling of LO 2026-09-14 16:26 AST: why")
        self.assertEqual(atlascatalog._ruling_note({**line, "note": ""}), "catalogued by ruling of LO 2026-09-14 16:26 AST")
        self.assertEqual(atlascatalog._ruling_note({**line, "ruled_at": ""}), "catalogued by ruling of LO: why")
        self.assertEqual(atlascatalog._ruling_note({**line, "ruled_at": "", "note": ""}), "catalogued by ruling of LO")

    def test_the_stamp_reads_in_ast(self):
        self.assertEqual(atlascatalog._ruling_stamp("2026-09-14T16:26:00-04:00"), "2026-09-14 16:26 AST")
        self.assertEqual(atlascatalog._ruling_stamp("2026-09-14T20:26:00+00:00"), "2026-09-14 16:26 AST")
        self.assertEqual(atlascatalog._ruling_stamp("2026-09-14T16:26:00"), "2026-09-14 16:26 AST")
        self.assertEqual(atlascatalog._ruling_stamp("2026-09-14 16:26 AST"), "2026-09-14 16:26 AST")
        self.assertEqual(atlascatalog._ruling_stamp("whenever"), "whenever")
        self.assertEqual(atlascatalog._ruling_stamp(""), "")

    def test_edit_note_puts_the_ruling_sentence_first(self):
        sentence = "catalogued by ruling of LO 2026-09-14 16:26 AST: x"
        note = atlascatalog._edit_note("", "2025-04-15", 2, True, kind=atlascatalog.KIND_RULED, ruling_note=sentence)
        self.assertEqual(note, f"{sentence}; second recording on 2025-04-15, not counted in the row; see needs-attention; "
                               f"{atlascatalog.NOTE_PARTS}; {atlascatalog.NOTE_PROXY}")
        self.assertEqual(atlascatalog._edit_note("", None, 0, False, kind=atlascatalog.KIND_RULED, ruling_note=sentence), sentence)
        with self.assertRaises(TypeError):
            atlascatalog._edit_note("", None, 0, False, ruling_note=3)

    # --- reading the file -------------------------------------------------------

    def test_load_rulings_missing_or_empty_file_means_none(self):
        self.assertEqual(atlascatalog.load_rulings(os.path.join(self.registry_root, "absent.csv")), ([], []))
        self.assertEqual(atlascatalog.load_rulings(self._rulings_file("")), ([], []))
        self.assertEqual(atlascatalog.load_rulings(self._rulings_file(self.HEADER)), ([], []))

    def test_load_rulings_reads_the_registry_s_own_file(self):
        self.r.set_ruling(f"{HOST}:{self.ANN_2024_03}/{self.OFAV}", self.OFAV, "catalog_as", "atlas:LO",
                          site_label="CST", transect="T5", date="20241112", note=self.OFAV_NOTE)
        lines, problems = atlascatalog.load_rulings(self.r.RULINGS_CSV)
        self.assertEqual(problems, [])
        self.assertEqual([(l["file_name"], l["ruling"], l["site_label"], l["ruled_by"]) for l in lines],
                         [(self.OFAV, "catalog_as", "CST", "LO")])
        self.assertEqual(list(lines[0]), registry.RULING_COLUMNS)

    def test_a_malformed_line_in_the_rulings_file_is_reported_not_fatal(self):
        good = f"{HOST}:{self.ANN_2024_03}/{self.OFAV},{self.OFAV},catalog_as,CST,T5,20241112,,ok,LO,{self.RULED_AT}\n"
        bad_label = f"{HOST}:{self.ANN_2024_03}/a.MOV,a.MOV,catalog_as,CST/T5,T5,20241112,,x,LO,{self.RULED_AT}\n"
        bad_date = f"{HOST}:{self.ANN_2024_03}/b.MOV,b.MOV,catalog_as,CST,T5,20241399,,x,LO,{self.RULED_AT}\n"
        path = self._rulings_file(self.HEADER + good + bad_label + bad_date)
        lines, problems = atlascatalog.load_rulings(path)
        self.assertEqual([l["file_name"] for l in lines], [self.OFAV])
        self.assertEqual([(p["root"], p["path"], p["reason"]) for p in problems],
                         [(atlascatalog.RULINGS_FILENAME, "line 3", atlascatalog.RULING_MALFORMED_REASON),
                          (atlascatalog.RULINGS_FILENAME, "line 4", atlascatalog.RULING_MALFORMED_REASON)])
        self.assertIn("site_label", problems[0]["detail"])
        self.assertIn("CST/T5", problems[0]["detail"])
        self.assertIn("date", problems[1]["detail"])
        self.assertIn("20241399", problems[1]["detail"])

    def test_every_malformed_shape_is_a_problem_line_naming_its_field(self):
        base = f"{HOST}:{self.ANN_2024_03}/{self.OFAV},{self.OFAV}"
        shapes = [
            (f"{base},keep,,,,,x,LO,{self.RULED_AT}\n", "ruling"),
            (f"{base},catalog_as,CST,5,20241112,,x,LO,{self.RULED_AT}\n", "transect"),
            (f"{base},catalog_as,CST,T5,2024-11-12,,x,LO,{self.RULED_AT}\n", "date"),
            (f"{base},catalog_as,CST,T5,20241112,0,x,LO,{self.RULED_AT}\n", "part"),
            (f"{base},catalog_as,CST,T5,20241112,two,x,LO,{self.RULED_AT}\n", "part"),
            (f"{base},catalog_as,,T5,20241112,,x,LO,{self.RULED_AT}\n", "site_label"),
            (f"{base},catalog_as,CST,T5,20241112,,x,,{self.RULED_AT}\n", "ruled_by"),
            (f"{base},leave_out,CST,,,,x,LO,{self.RULED_AT}\n", "no placement"),
            (f"{HOST}:{self.ANN_2024_03}/{self.OFAV},other.MOV,leave_out,,,,,x,LO,{self.RULED_AT}\n", "file_name"),
            (f"/no/host/{self.OFAV},{self.OFAV},leave_out,,,,,x,LO,{self.RULED_AT}\n", "nas_path"),
            (f"{base},leave_out,,,,,x,LO,{self.RULED_AT}\n{base},leave_out,,,,,y,LO,{self.RULED_AT}\n", "line 2"),
            (f"{base},catalog_as,CST,T5,20241112,,x,LO\n", "fields"),
        ]
        for text, field in shapes:
            lines, problems = atlascatalog.load_rulings(self._rulings_file(self.HEADER + text))
            self.assertEqual(len(problems), 1, (text, problems))
            self.assertIn(field, problems[0]["detail"], text)
            self.assertLessEqual(len(lines), 1, text)

    def test_a_wrong_header_is_one_problem_and_no_ruling(self):
        lines, problems = atlascatalog.load_rulings(self._rulings_file("path,name\nx,y\n"))
        self.assertEqual(lines, [])
        self.assertEqual([(p["path"], p["reason"]) for p in problems], [("line 1", atlascatalog.RULING_MALFORMED_REASON)])
        self.assertIn(",".join(registry.RULING_COLUMNS), problems[0]["detail"])

    def test_a_hostile_note_and_a_control_character_in_a_cell(self):
        hostile = 'note with "quotes", commas; semicolons and <b>tags</b>'
        line = self._ofav_ruling(note=hostile)
        run = self._catalog({self.ANN_2024_03: _names(self.OFAV)}, rulings=[line])
        self.assertEqual(self._sidecar("CST_T5_2024ann")[0]["edit_note"],
                         f"{self._sentence(hostile)}; {atlascatalog.NOTE_PROXY}")
        text = f'{HOST}:{self.ANN_2024_03}/{self.OFAV},{self.OFAV},catalog_as,CST,T5,20241112,,"a\nb",LO,{self.RULED_AT}\n'
        lines, problems = atlascatalog.load_rulings(self._rulings_file(self.HEADER + text))
        self.assertEqual(lines, [])
        self.assertIn("control character", problems[0]["detail"])

    def test_a_hostile_programmatic_ruling_is_refused_before_any_listing(self):
        calls = []

        def runner(argv, timeout=60):
            calls.append(argv)
            return (0, "", "")

        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=runner)
        for bad in ("x", [1], [{"nas_path": 3}], [self._ofav_ruling(site_label="CST/T5")],
                    [self._ofav_ruling(date="20241399")], [self._ofav_ruling(part="0")],
                    [self._ruling(f"{self.ANN_2024_03}/{self.OFAV}", "leave_out", label="CST")],
                    [self._ofav_ruling(), self._ofav_ruling()], {"nas_path": "x"}):
            with self.assertRaises((TypeError, ValueError), msg=repr(bad)) as ctx:
                atlascatalog.catalog([self.ANN_2024_03], ssh, rulings=bad, dry_run=True)
            self.assertNotIn("Traceback", str(ctx.exception))
        self.assertEqual(calls, [], "nothing was listed")
        with self.assertRaises(ValueError) as ctx:
            atlascatalog.catalog([self.ANN_2024_03], ssh, rulings=[self._ofav_ruling(site_label="CST/T5")], dry_run=True)
        self.assertIn("letters and digits", str(ctx.exception))
        self.assertEqual(sorted(os.listdir(self.registry_root)), [])

    # --- the run as a whole -----------------------------------------------------

    def test_the_full_ruled_shape_of_2026_09_14(self):
        listings = self._ruled_listings()
        listings[self.VOL2].append(("encoded/TCRMP_2023Annual/TCRMP20231207_demo_MRS_T3_part1.MP4", GB))
        listings[self.VOL4].append(("TCRMP_2024Annual/encoded/TCRMP_2024Annual_04/TCRMP20241113_3D_JKB_T1_Proxy.MOV", GB))
        run = self._catalog(listings, rulings=self._ruled_lines(), variants=("demo",))
        self.assertEqual(run.needs_attention, [])
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), sorted(RULED_ROWS + ["JKB_T1_2024ann", "MRS_T3_2023ann"]))
        self.assertEqual((run.ruled_out, run.rulings_applied), (RULED_LEAVE_OUT, RULED_CATALOG_AS))
        self.assertEqual(run.listed_files, len(RULED_2026_09_14) + 2)
        self.assertEqual(len(self._sidecar("CSTSORT_T5_2024_pbl")), 6)
        self.assertEqual(len(self._sidecar("GKTEXTRA_T1_2024_pbl")), 3)
        self.assertEqual(len(self._sidecar()), RULED_CATALOG_AS + 2)
        self.assertEqual(self.r.get("CSTSORT_T5_2024_pbl")["original_videos"],
                         ";".join(f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in (1, 2, 3, 4, 6, 7)))
        self.assertEqual(self.r.get("MRS_T3_2023ann")["original_videos"], "TCRMP20231207_demo_MRS_T3_part1.MP4")
        for row in self.r.load():
            self.assertEqual(row["video_location"].split(":", 1)[0], HOST)
        # Without the demo variant the ruled rows stand unchanged; only the well-named demo file is a bad name.
        run = self._catalog(self._ruled_listings(), rulings=self._ruled_lines())
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), RULED_ROWS)
        self.assertEqual(run.needs_attention, [])

    def test_a_rerun_with_rulings_is_byte_identical(self):
        listings = self._ruled_listings()
        self._catalog(listings, rulings=self._ruled_lines())
        paths = (self.r.REGISTRY_CSV, self.r.SOURCE_FILES_CSV, self.r.EVENTS_CSV)

        def read_all():
            out = []
            for path in paths:
                with open(path, "rb") as fh:
                    out.append(fh.read())
            return out

        first = read_all()
        time.sleep(1.1)
        self._catalog(listings, rulings=self._ruled_lines())
        self.assertEqual(first, read_all())

    def test_the_report_replaces_the_previous_override_and_ruling_lines_every_run(self):
        previous = [{"root": atlascatalog.OVERRIDES_FILENAME, "path": "x", "reason": atlascatalog.OVERRIDE_UNLISTED_REASON, "detail": ""},
                    {"root": atlascatalog.RULINGS_FILENAME, "path": "line 3", "reason": atlascatalog.RULING_MALFORMED_REASON, "detail": ""},
                    {"root": ROOT_2025, "path": f"{ROOT_2025}/GOPR0001.MP4", "reason": atlascatalog.BAD_NAME_REASON, "detail": ""}]
        atlascatalog.write_needs_attention_report(previous)
        atlascatalog.write_needs_attention_report([], walked_roots=[ROOT_2024])
        self.assertEqual([i["root"] for i in self.r.needs_attention()], [ROOT_2025])

    @unittest.skipUnless(os.path.exists(LIVE_RULINGS_CSV), "the live rulings file is not on this machine")
    def test_the_live_rulings_file_reads_with_no_problem(self):
        lines, problems = atlascatalog.load_rulings(LIVE_RULINGS_CSV)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(len(lines), 1)
        self.assertTrue(all(l["ruling"] in registry.RULING_VALUES for l in lines))
        self.assertTrue(all(l["nas_path"].startswith(HOST + ":/") for l in lines))

    # --- the command line ---------------------------------------------------------

    def test_cli_applies_the_registry_s_rulings_by_default(self):
        self.r.set_ruling(f"{HOST}:{self.ANN_2024_03}/{self.OFAV}", self.OFAV, "catalog_as", "atlas:LO",
                          site_label="CST", transect="T5", date="20241112", note=self.OFAV_NOTE)
        cfg = self._write_config(self.ANN_2024_03)
        output = self._main(["--nas-config", cfg, "--dry-run"], {self.ANN_2024_03: _names(self.OFAV)})
        self.assertIn("CST_T5_2024ann", output)
        self.assertIn(f"rulings: {self.r.RULINGS_CSV} (1 ruling)", output)
        self.assertIn("rulings applied: 1", output)
        self.assertIn("files ruled out: 0", output)
        self.assertIn("needs attention: 0", output)

    def test_cli_rulings_flag_points_elsewhere_and_no_rulings_ignores_the_file(self):
        self.r.set_ruling(f"{HOST}:{self.ANN_2024_03}/{self.OFAV}", self.OFAV, "leave_out", "atlas:LO", note="x")
        elsewhere = self._rulings_file(self.HEADER + f"{HOST}:{self.ANN_2024_03}/{self.OFAV},{self.OFAV},catalog_as,"
                                       f"CST,T5,20241112,,\"{self.OFAV_NOTE}\",LO,{self.RULED_AT}\n", name="elsewhere.csv")
        cfg = self._write_config(self.ANN_2024_03)
        listings = {self.ANN_2024_03: _names(self.OFAV)}
        output = self._main(["--nas-config", cfg, "--dry-run", "--rulings", elsewhere], listings)
        self.assertIn("CST_T5_2024ann", output)
        self.assertIn(f"rulings: {elsewhere} (1 ruling)", output)
        output = self._main(["--nas-config", cfg, "--dry-run", "--no-rulings"], listings)
        self.assertNotIn("CST_T5_2024ann", output)
        self.assertIn("rulings: ignored (--no-rulings)", output)
        self.assertIn(atlascatalog.BAD_NAME_REASON, output)
        self.assertIn("needs attention: 1", output)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                atlascatalog._parser().parse_args(["--rulings", elsewhere, "--no-rulings"])

    def test_cli_says_when_the_rulings_file_is_missing(self):
        cfg = self._write_config(self.ANN_2024_03)
        missing = os.path.join(self.registry_root, "absent.csv")
        output = self._main(["--nas-config", cfg, "--dry-run", "--rulings", missing], {self.ANN_2024_03: _names(self.OFAV)})
        self.assertIn(f"rulings: {missing} (no file; none apply)", output)

    def test_cli_prints_a_malformed_ruling_line_before_listing_and_reports_it(self):
        good = f"{HOST}:{self.ANN_2024_03}/{self.OFAV},{self.OFAV},catalog_as,CST,T5,20241112,,ok,LO,{self.RULED_AT}\n"
        bad = f"{HOST}:{self.ANN_2024_03}/a.MOV,a.MOV,catalog_as,CST/T5,T5,20241112,,x,LO,{self.RULED_AT}\n"
        path = self._rulings_file(self.HEADER + good + bad)
        cfg = self._write_config(self.ANN_2024_03)
        output = self._main(["--nas-config", cfg, "--rulings", path], {self.ANN_2024_03: _names(self.OFAV)})
        warning = f"skipped ruling line 3 of {path}: "
        self.assertIn(warning, output)
        self.assertLess(output.index(warning), output.index("readable_id"), "the warning comes before the listing")
        self.assertIn("CST_T5_2024ann", output)
        self.assertIn("Step 2 complete", output)
        report = self.r.needs_attention()
        self.assertEqual([(i["root"], i["path"], i["reason"]) for i in report],
                         [(atlascatalog.RULINGS_FILENAME, "line 3", atlascatalog.RULING_MALFORMED_REASON)])
        self.assertIn("site_label", report[0]["detail"])


class ProcessingFolderRealFindTests(_CatalogCase):
    """The prune expression, proven against a real GNU find on a local temp
    tree (the NAS is never touched: this is /usr/bin/find over a folder
    under the test's own temp root, no ssh)."""

    def _local_find_runner(self, argv, timeout=60):
        """Run the composed find locally, as the NAS would run it over ssh."""
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout)
        return (done.returncode, done.stdout, done.stderr)

    def test_the_prune_expression_works_in_a_real_find(self):
        tree = tempfile.mkdtemp(dir=self.registry_root)
        kept = ["TCRMP_2024_PBL/TCRMP20240412_3D_MRS_T1.MP4",
                "TCRMP_2024_PBL/TCRMP2024_postbl_3D_DemoVideos/TCRMP20240412_3D_MRS_T2.MP4",
                "TCRMP_2024_PBL/frames.old/TCRMP20240412_3D_MRS_T4.MP4",
                "TCRMP_2024_PBL/frames"]
        pruned = ["TCRMP_2025_PBL/TCRMP_2025_PBL_01/processing/frames/x/TCRMP20250321_3D_MRS_T1_Proxy.MOV",
                  "TCRMP_2025_PBL/Frames/TCRMP20250321_3D_MRS_T2_Proxy.MOV",
                  "TCRMP_2024_PBL/MRS_T3_3D/frames/y/TCRMP20240412_3D_MRS_T3.MP4",
                  "TCRMP_2024_PBL/MRS_T3_2023ann_3dprocessing/report.pdf",
                  "TCRMP_2024_PBL/@eaDir/thumb.jpg"]
        for relpath in kept + pruned:
            os.makedirs(os.path.join(tree, os.path.dirname(relpath)), exist_ok=True)
            with open(os.path.join(tree, relpath), "w") as fh:
                fh.write("x")
        ssh = atlascatalog.SSH(HOST, "driver_svc", "/fake/key", runner=self._local_find_runner)
        entries = atlascatalog.list_root(ssh, tree)
        self.assertEqual(sorted(rel for rel, _size in entries), sorted(kept))
        self.assertEqual({size for _rel, size in entries}, {1})
        run = atlascatalog.catalog([tree], ssh, dry_run=True)
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["MRS_T1_2024_pbl", "MRS_T2_2024_pbl", "MRS_T4_2024_pbl"])
        self.assertEqual(run.processing_files, 0, "the NAS-side prune left nothing for layer two")
        # The regular file named frames is a bad name, as it is a file and not a folder.
        self.assertEqual([i["reason"] for i in run.needs_attention], [atlascatalog.BAD_NAME_REASON])
        self.assertTrue(run.needs_attention[0]["path"].endswith("/frames"))


class RulingFileToleranceTests(_CatalogCase):
    """What a rulings file can be and still never stop a run."""

    def test_a_rulings_path_that_is_a_directory_is_a_plain_error(self):
        with self.assertRaises(OSError) as ctx:
            atlascatalog.load_rulings(self.registry_root)
        self.assertIn(self.registry_root, str(ctx.exception))

    def test_a_rulings_file_in_another_encoding_still_reads(self):
        path = os.path.join(self.registry_root, "latin.csv")
        text = (",".join(registry.RULING_COLUMNS) + "\n"
                f"{HOST}:/volume4/x/a.MOV,a.MOV,leave_out,,,,,café note,LO,2026-09-14T16:26:00-04:00\n")
        with open(path, "wb") as fh:
            fh.write(text.encode("latin-1"))
        lines, problems = atlascatalog.load_rulings(path)
        self.assertEqual(problems, [])
        self.assertEqual([l["file_name"] for l in lines], ["a.MOV"])
        self.assertTrue(lines[0]["note"].startswith("caf"))

    def test_a_huge_rulings_file_and_listing_in_bounded_time(self):
        root = "/volume4/Archive6_16TB/TCRMP_2024Annual/encoded"
        count = 5000
        rulings = [{"nas_path": f"{HOST}:{root}/odd_{n}.MOV", "file_name": f"odd_{n}.MOV", "ruling": "catalog_as",
                    "site_label": "CST", "transect": f"T{n}", "date": "20241112", "part": "", "note": "n",
                    "ruled_by": "LO", "ruled_at": "2026-09-14T16:26:00-04:00"} for n in range(1, count + 1)]
        listing = [(f"odd_{n}.MOV", GB) for n in range(1, count + 1)]
        listing += [(f"MRS_T1_3D/frames/x/frame_{n}.jpg", 100) for n in range(count)]
        started = time.monotonic()
        run = self._catalog({root: listing}, rulings=rulings, dry_run=True)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(len(run.rows), count)
        self.assertEqual(run.rulings_applied, count)
        self.assertEqual(run.processing_files, count)
        self.assertEqual(run.needs_attention, [])

    @unittest.skipUnless(os.path.exists(LIVE_RULINGS_CSV), "the live rulings file is not on this machine")
    def test_main_with_the_live_rulings_file_over_the_ruled_shape(self):
        """End to end through the command line with the real rulings file
        (read only) over a fake listing of exactly the files it rules on."""
        lines, _problems = atlascatalog.load_rulings(LIVE_RULINGS_CSV)
        listings = {}
        for line in lines:
            path = line["nas_path"].split(":", 1)[1]
            volume = "/" + path.split("/")[1]
            listings.setdefault(volume, []).append((path[len(volume) + 1:], GB))
        fd, cfg = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        self.addCleanup(os.remove, cfg)
        with open(cfg, "w") as fh:
            fh.write(f"host: {HOST}\nuser: driver_svc\nkey: /fake/key\nsource_roots:\n")
            fh.writelines(f"  - {volume}\n" for volume in sorted(listings))
        output = self._main(["--nas-config", cfg, "--dry-run", "--rulings", LIVE_RULINGS_CSV], listings)
        self.assertIn(f"rulings: {LIVE_RULINGS_CSV} ({len(lines)} rulings)", output)
        leave_out = sum(1 for l in lines if l["ruling"] == registry.RULING_LEAVE_OUT)
        self.assertIn(f"rulings applied: {len(lines) - leave_out}; files ruled out: {leave_out}", output)
        self.assertIn("needs attention: 0", output)
        self.assertNotIn("skipped ruling", output)


class ProjectRootAndLiveRowTests(_CatalogCase):
    """Two guards of 2026-09-14: a Voyager 1 project root is never read, and a live row is never rewritten."""

    def test_a_folder_holding_analysis_params_is_dropped_whole(self):
        entries = [("TCRMP_2025_PBL_01/analysis_params.yaml", 9697),
                   ("TCRMP_2025_PBL_01/video_source", 1584),
                   ("TCRMP_2025_PBL_01/notes/TCRMP20250320_3D_SHR_T5_Proxy.MOV", 100),
                   ("TCRMP_2025_PBL_RAW_01/TCRMP20250321_3D_MRS_T1_Proxy.MOV", 100)]
        kept, dropped = atlascatalog._drop_processing(entries)
        self.assertEqual([e[0] for e in kept], ["TCRMP_2025_PBL_RAW_01/TCRMP20250321_3D_MRS_T1_Proxy.MOV"])
        self.assertEqual(dropped, 3)

    def test_a_row_whose_run_is_live_is_left_alone(self):
        import fcntl
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        self.r.upsert("MRS_T9_2024ann", {"site": "MRS", "transect": "T9", "year": "2024", "season_token": "ann",
                                         "video_location": "h:/archive/old", "processing_location": folder}, actor="seed")
        self.r.upsert("MRS_T8_2024ann", {"site": "MRS", "transect": "T8", "year": "2024", "season_token": "ann",
                                         "video_location": "h:/archive/old"}, actor="seed")
        lock = open(os.path.join(folder, ".processing.lock"), "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # video_size_gb is a catalog-owned cell (video_location is operator-protected on a rerun)
        rows = [{"readable_id": rid, "fields": {"video_size_gb": "9.99"}, "files": []} for rid in ("MRS_T9_2024ann", "MRS_T8_2024ann")]
        written, files, left = atlascatalog.write_rows(rows, "catalog")
        self.assertEqual((written, files, left), (1, 0, ["MRS_T9_2024ann"]))
        rows_now = {r["readable_id"]: r for r in self.r.load()}
        self.assertEqual(rows_now["MRS_T9_2024ann"]["video_size_gb"], "")
        self.assertEqual(rows_now["MRS_T8_2024ann"]["video_size_gb"], "9.99")


    def test_a_row_whose_video_sits_on_the_workbench_is_left_alone(self):
        # The pull flips video_location to a local folder; the three sibling
        # timepoints of a running turn hold no lock yet (2026-09-14 19:02 AST).
        self.r.upsert("MRS_T7_2024ann", {"site": "MRS", "transect": "T7", "year": "2024", "season_token": "ann",
                                         "video_location": "/mnt/rip/driver_staging/batch_x/turn001/TCRMP_2024Annual_05",
                                         "original_videos": "TCRMP20241122_3D_MRS_T7.MOV"}, actor="seed")
        rows = [{"readable_id": "MRS_T7_2024ann", "fields": {"original_videos": "TCRMP20241122_3D_MRS_T7_Proxy.MOV", "video_size_gb": "1.0"}, "files": []}]
        self.assertEqual(atlascatalog.write_rows(rows, "catalog"), (0, 0, ["MRS_T7_2024ann"]))
        row = {r["readable_id"]: r for r in self.r.load()}["MRS_T7_2024ann"]
        self.assertEqual(row["original_videos"], "TCRMP20241122_3D_MRS_T7.MOV")


class PreviousReportCoverageTests(_CatalogCase):
    def test_a_previous_line_under_a_walked_root_is_replaced(self):
        # The 2026-09-04 report was written per season folder; a walk of the
        # whole volume on 2026-09-14 covers those folders and replaces them.
        path = self.r.NEEDS_ATTENTION_CSV
        with open(path, "w", newline="") as fh:
            fh.write("root,path,reason,detail\n")
            fh.write("/vol/enc/TCRMP_2024_PBL,/vol/enc/TCRMP_2024_PBL/x.MP4,name does not match,\n")
            fh.write("/other/enc/TCRMP_2024_PBL,/other/enc/TCRMP_2024_PBL/y.MP4,name does not match,\n")
            fh.write("/volx/enc,/volx/enc/z.MP4,name does not match,\n")
        kept = atlascatalog._kept_from_previous_report(path, ["/vol"])
        self.assertEqual([k["path"] for k in kept], ["/other/enc/TCRMP_2024_PBL/y.MP4", "/volx/enc/z.MP4"])
