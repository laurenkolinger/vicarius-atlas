"""Tests for atlascatalog.py: the read-only NAS walk into registry rows.

No NAS or network access anywhere in this file: every test builds a fake ssh
listing runner and injects it into atlascatalog.SSH, and every registry write
goes to a temporary root through VICARIUS_3D_REGISTRY_ROOT plus a reload.

Run: cd /mnt/rip/vicarius_drive/vicarius/modules/tcrmp_3d_atlas/github_repo
     && python3 -m unittest discover -s tests -q
"""
import contextlib, csv, importlib, io, os, shlex, shutil, subprocess, sys, tempfile, threading, time, unittest

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

    def test_needs_attention_catches_ambiguous_part_set_split_across_directories(self):
        root = "/volume4/Archive6_16TB/2024_annual"
        listings = {root: [
            ("dirA/TCRMP20240412_3D_MRS_T1_1.MP4", 1_000_000_000),
            ("dirB/TCRMP20240412_3D_MRS_T1_2.MP4", 1_000_000_000),
        ]}
        ssh = self._ssh(listings)

        rows, needs_attention = atlascatalog.catalog_roots([root], ssh, actor="catalog")

        self.assertEqual(rows, [])
        self.assertEqual(len(needs_attention), 1)
        self.assertIn("split across directories", needs_attention[0]["reason"])
        self.assertEqual(self.r.load(), [], "an ambiguous split part set must not write any row")

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
        self.assertEqual(argv[4:11], ["(", "-name", "@*", "-o", "-name", "#recycle", ")"])
        self.assertEqual(argv[11:], ["-prune", "-o", "-type", "f", "-printf", r"%s\0%P\0"])
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

    def test_each_component_alone(self):
        self.assertEqual(atlascatalog._edit_note("", None, 0, False), "")
        self.assertEqual(atlascatalog._edit_note("", None, 1, False), "")
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
        self.assertEqual([r["readable_id"] for r in run.rows], ["FSB_T1_2024_pbl"])
        self.assertEqual(len(run.needs_attention), 6)

        run = self._catalog({self.ROOT_2024PBL: listing}, variants=("demo", "3ddemo"))
        self.assertEqual(sorted(r["readable_id"] for r in run.rows), ["FLC_T3_2024_pbl", "FLC_T5_2024_pbl", "FSB_T1_2024_pbl"])
        self.assertEqual(len(run.needs_attention), 3)
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
