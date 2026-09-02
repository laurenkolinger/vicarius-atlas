import contextlib, csv, importlib, io, os, shlex, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")

import atlascatalog
import prep_tools
import registry


class AtlasCatalogTests(unittest.TestCase):
    """No NAS or network access anywhere in this file: every test builds a
    fake ssh listing runner and injects it into atlascatalog.SSH."""

    def setUp(self):
        self.registry_root = tempfile.mkdtemp()
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.registry_root
        importlib.reload(registry)
        self.r = registry

    def tearDown(self):
        shutil.rmtree(self.registry_root)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)

    def _ssh(self, listings, host="146.226.147.140"):
        """`listings`: NAS root -> list[(relpath, size_bytes)], or None to
        simulate an unmounted/unlistable root. Fakes the ssh listing command
        runner atlascatalog.SSH is built to accept -- no subprocess call."""
        def runner(argv, timeout=60):
            root = argv[1]  # ["find", root, "-mindepth", "1", "-type", "f", "-printf", ...]
            entries = listings.get(root)
            if entries is None:
                return (1, "", f"find: '{root}': No such file or directory")
            payload = "".join(f"{size}\0{relpath}\0" for relpath, size in entries)
            return (0, payload, "")
        return atlascatalog.SSH(host, "driver_svc", "/fake/driver_svc_key", runner=runner)

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


class PrepToolsGroupingExtractionTests(unittest.TestCase):
    """The pure part-set grouping helper atlascatalog.py imports from
    prep_tools.py, exercised with no filesystem access."""

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
