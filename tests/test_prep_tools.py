import csv, importlib, os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prep_tools

def tiny(path, codec):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=2",
                    "-frames:v", "2", "-c:v", codec, path], check=True)

class PrepTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        tiny(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_1.MP4"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_2.MP4"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20241018_3D_MRS_T1_Proxy.MKV"), "libx264")
        tiny(os.path.join(self.d, "tcrmp20250414_3d_mrs_t1.avi"), "mjpeg")
        tiny(os.path.join(self.d, "TCRMP20231015_3D_MRS_T1.MOV"), "libx264")
    def tearDown(self): shutil.rmtree(self.d)

    def test_plan(self):
        by_out = {a["output"]: a for a in prep_tools.plan(self.d)}
        self.assertEqual(by_out["TCRMP20240412_3D_MRS_T1.MP4"]["action"], "merge")
        self.assertEqual(by_out["TCRMP20240412_3D_MRS_T1.MP4"]["inputs"], ["TCRMP20240412_3D_MRS_T1_1.MP4", "TCRMP20240412_3D_MRS_T1_2.MP4"])
        self.assertEqual(by_out["TCRMP20241018_3D_MRS_T1.MKV"]["action"], "rename")
        self.assertEqual(by_out["TCRMP20250414_3D_MRS_T1.avi"]["action"], "rename")
        self.assertEqual(by_out["TCRMP20231015_3D_MRS_T1.MOV"]["action"], "keep")

    def test_apply(self):
        prep_tools.apply(self.d, prep_tools.plan(self.d), os.path.join(self.d, "prep_log.csv"))
        names = sorted(os.listdir(self.d))
        self.assertIn("TCRMP20240412_3D_MRS_T1.MP4", names); self.assertNotIn("TCRMP20240412_3D_MRS_T1_1.MP4", names)
        self.assertIn("TCRMP20241018_3D_MRS_T1.MKV", names); self.assertIn("TCRMP20250414_3D_MRS_T1.avi", names)
        rows = list(csv.DictReader(open(os.path.join(self.d, "prep_log.csv"))))
        self.assertEqual(sorted(r["action"] for r in rows), ["merge", "rename", "rename"])


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        tiny(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_1.MP4"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_2.MP4"), "libx264")
        with open(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1.MP4"), "wb") as fh:
            fh.write(b"already-merged-canonical-bytes")
    def tearDown(self): shutil.rmtree(self.d)

    def test_plan_flags_conflict_when_canonical_exists_alongside_parts(self):
        actions = prep_tools.plan(self.d)
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["action"], "conflict")
        self.assertEqual(action["output"], "TCRMP20240412_3D_MRS_T1.MP4")
        self.assertEqual(
            sorted(action["inputs"]),
            sorted([
                "TCRMP20240412_3D_MRS_T1_1.MP4",
                "TCRMP20240412_3D_MRS_T1_2.MP4",
                "TCRMP20240412_3D_MRS_T1.MP4",
            ]),
        )

    def test_apply_leaves_canonical_bytes_unchanged_on_conflict(self):
        canonical_path = os.path.join(self.d, "TCRMP20240412_3D_MRS_T1.MP4")
        before = open(canonical_path, "rb").read()

        actions = prep_tools.plan(self.d)
        prep_tools.apply(self.d, actions, os.path.join(self.d, "prep_log.csv"))

        after = open(canonical_path, "rb").read()
        self.assertEqual(before, after)
        self.assertTrue(os.path.exists(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_1.MP4")))
        self.assertTrue(os.path.exists(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1_2.MP4")))

        rows = list(csv.DictReader(open(os.path.join(self.d, "prep_log.csv"))))
        self.assertEqual([r["action"] for r in rows], ["conflict"])


class OverwriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        tiny(os.path.join(self.d, "TCRMP20241018_3D_MRS_T1_Proxy.MKV"), "libx264")
        with open(os.path.join(self.d, "TCRMP20241018_3D_MRS_T1.MKV"), "wb") as fh:
            fh.write(b"existing-canonical-mkv-bytes")
    def tearDown(self): shutil.rmtree(self.d)

    def test_apply_raises_when_rename_target_already_exists(self):
        # Hand-built action, bypassing plan(), to exercise apply()'s general
        # backstop: it must never overwrite an existing output regardless of
        # how the action list was produced.
        actions = [{
            "action": "rename",
            "inputs": ["TCRMP20241018_3D_MRS_T1_Proxy.MKV"],
            "output": "TCRMP20241018_3D_MRS_T1.MKV",
            "reason": "strips _Proxy suffix",
        }]
        canonical_path = os.path.join(self.d, "TCRMP20241018_3D_MRS_T1.MKV")
        before = open(canonical_path, "rb").read()

        with self.assertRaises(RuntimeError):
            prep_tools.apply(self.d, actions, os.path.join(self.d, "prep_log.csv"))

        after = open(canonical_path, "rb").read()
        self.assertEqual(before, after)
        self.assertTrue(os.path.exists(os.path.join(self.d, "TCRMP20241018_3D_MRS_T1_Proxy.MKV")))


class MergeVerificationTests(unittest.TestCase):
    """A merge deletes its irreplaceable source parts only once the merged
    duration matches the sum of the parts' durations (final review I4)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.parts = ["TCRMP20240412_3D_MRS_T1_1.MP4", "TCRMP20240412_3D_MRS_T1_2.MP4"]
        for name in self.parts:
            tiny(os.path.join(self.d, name), "libx264")
        self.log = os.path.join(self.d, "prep_log.csv")

    def tearDown(self):
        shutil.rmtree(self.d)

    def _rows(self):
        return list(csv.DictReader(open(self.log)))

    def test_verify_durations_rule(self):
        ok, expected, tolerance = prep_tools.verify_durations(2.0, [1.0, 1.0])
        self.assertTrue(ok)
        self.assertEqual(expected, 2.0)
        self.assertEqual(tolerance, 0.5)
        # Half a second is the floor, two percent takes over, and one second is
        # the ceiling. The ceiling was added on 2026-09-08: uncapped, a
        # 2000 second recording tolerated 40 seconds and a five minute transect
        # swim tolerated 5.7, so a real gap in the footage passed as verified
        # and the irreplaceable parts were deleted.
        self.assertFalse(prep_tools.verify_durations(1.4, [1.0, 1.0])[0])
        self.assertEqual(prep_tools.verify_durations(2000.0, [1000.0, 1000.0])[1], 2000.0)
        self.assertEqual(prep_tools.verify_durations(2000.0, [1000.0, 1000.0])[2],
                         prep_tools.MERGE_TOLERANCE_MAX_S)
        self.assertTrue(prep_tools.verify_durations(1999.5, [1000.0, 1000.0])[0])
        self.assertFalse(prep_tools.verify_durations(1990.0, [1000.0, 1000.0])[0])
        self.assertFalse(prep_tools.verify_durations(950.0, [1000.0, 1000.0])[0])
        # A duration of zero anywhere makes the comparison vacuous, not lenient.
        self.assertFalse(prep_tools.verify_durations(0.4, [0.0, 0.0])[0])
        self.assertFalse(prep_tools.verify_durations(0.0, [1.0, 1.0])[0])

    def test_verified_merge_deletes_the_parts(self):
        prep_tools.apply(self.d, prep_tools.plan(self.d), self.log)
        names = os.listdir(self.d)
        self.assertIn("TCRMP20240412_3D_MRS_T1.MP4", names)
        for part in self.parts:
            self.assertNotIn(part, names)
        row = [r for r in self._rows() if r["output"] == "TCRMP20240412_3D_MRS_T1.MP4"][0]
        self.assertEqual(row["action"], "merge")
        self.assertIn("verified", row["reason"])
        self.assertIn("parts deleted", row["reason"])

    def test_unverified_merge_keeps_the_parts_and_says_so(self):
        # Make the probe lie about the merged file: report half the duration
        # the parts add up to, the shape a truncated concat has.
        real_duration = prep_tools._duration
        merged_name = "TCRMP20240412_3D_MRS_T1.MP4"

        def lying_duration(path):
            # The join is probed while it still carries its ".merging" name, so
            # the lie has to cover that name as well as the final one.
            value = real_duration(path)
            base = os.path.basename(path)
            joining = base == merged_name or prep_tools.MERGE_PARTIAL_SUFFIX in base
            return value / 4 if joining else value

        prep_tools._duration = lying_duration
        try:
            prep_tools.apply(self.d, prep_tools.plan(self.d), self.log)
        finally:
            prep_tools._duration = real_duration

        names = os.listdir(self.d)
        self.assertIn(merged_name, names)
        for part in self.parts:
            self.assertIn(part, names, "an unverified merge must keep every part")
        row = [r for r in self._rows() if r["output"] == merged_name][0]
        self.assertEqual(row["action"], "merge unverified")
        self.assertIn("merged", row["reason"])
        self.assertIn("Parts kept", row["reason"])

    def test_keep_parts_never_deletes_even_when_verified(self):
        prep_tools.apply(self.d, prep_tools.plan(self.d), self.log, keep_parts=True)
        names = os.listdir(self.d)
        self.assertIn("TCRMP20240412_3D_MRS_T1.MP4", names)
        for part in self.parts:
            self.assertIn(part, names)
        row = [r for r in self._rows() if r["output"] == "TCRMP20240412_3D_MRS_T1.MP4"][0]
        self.assertEqual(row["action"], "merge")
        self.assertIn("--keep-parts", row["reason"])


# --- rulings ---------------------------------------------------------------
# Lauren's catalog rulings (registry catalog_rulings.csv) reach the Workbench as
# the real odd file names the archive holds. prep must apply the same ruling the
# catalog applied, or the ruled file is unread here and the season stops.

RULED_BY = "LO"
RULED_AT = "2026-09-14T16:26:00-04:00"
RULED_AT_AST = "2026-09-14 16:26 AST"
NAS_HOST = "146.226.147.140"
ANNUAL_03 = "/volume4/Archive6_16TB/TCRMP_2024Annual/encoded/TCRMP_2024Annual_03"
ANNUAL_04 = "/volume4/Archive6_16TB/TCRMP_2024Annual/encoded/TCRMP_2024Annual_04"
ANNUAL_EXTRA = "/volume4/Archive6_16TB/TCRMP_2024Annual/encoded/TCRMP_2024Annual_EXTRA"
PBL_2024 = "/volume2/Archive9_10TB/encoded/TCRMP_2024_PBL"
OFAV = "TCRMP20241112_3D_CST_T5OFAV_Proxy.MOV"
OFAV_NOTE = "OFAV colony clip, the only 2024 annual file for Castle T5"
JKB_TAKE = "TCRMP20241113_3D_JKB_T2_2_Proxy.MOV"
JKB_FALSE_START = "TCRMP20241113_3D_JKB_T2_Proxy.MOV"
CAMERA_RAW = "X016C122_24111246_TCRMP_Proxy.MOV"
GKT_EXTRA = [f"TCRMP20240408_demo_GKT_EXTRA_T1_{n}.MP4" for n in (1, 2, 3)]
CASTLE_ON_DISK = [f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in (1, 2, 3, 4, 6, 7)]
CASTLE_NOTE = ("archive part 5 is missing; archive parts 6 and 7 become parts 5 and 6. to sort: "
               "rendered in its own labelled project at the end of the processing order; decide after the report")


def ruling(folder, file_name, kind, site_label="", transect="", date="", part="", note="",
           ruled_by=RULED_BY, ruled_at=RULED_AT):
    """One rulings line as the registry writes it, keyed by the NAS path of `file_name` in `folder`."""
    return {"nas_path": f"{NAS_HOST}:{folder}/{file_name}", "file_name": file_name, "ruling": kind,
            "site_label": site_label, "transect": transect, "date": date, "part": str(part),
            "note": note, "ruled_by": ruled_by, "ruled_at": ruled_at}


def write_rulings(path, lines, columns=None):
    """Write `lines` as a rulings CSV under `columns` (the registry's by default)."""
    columns = columns or prep_tools.RULING_COLUMNS
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore", restval="")
        writer.writeheader()
        writer.writerows(lines)
    return path


def season_folder(root, name):
    """A Workbench season folder named exactly as the NAS season folder it was pulled from."""
    folder = os.path.join(root, name)
    os.makedirs(folder)
    return folder


def blank_video(folder, name):
    """A file that only needs a name: plan() never opens a file it does not merge."""
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(b"\0" * 16)


class RulingsApplyToTheFolder(unittest.TestCase):
    """A ruled file groups, merges and renames exactly as a well-named file would."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _rows(self, folder):
        with open(os.path.join(folder, "prep_log.csv"), newline="") as fh:
            return list(csv.DictReader(fh))

    def test_a_ruled_whole_file_takes_the_name_the_ruling_gives(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        tiny(os.path.join(folder, OFAV), "libx264")
        rulings = [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE)]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, rulings_by_name=by_name)
        self.assertEqual([(a["action"], a["inputs"], a["output"]) for a in actions],
                         [("rename", [OFAV], "TCRMP20241112_3D_CST_T5.MOV")])
        prep_tools.apply(folder, actions, os.path.join(folder, "prep_log.csv"))
        self.assertEqual(sorted(os.listdir(folder)), ["TCRMP20241112_3D_CST_T5.MOV", "prep_log.csv"])
        row = self._rows(folder)[0]
        self.assertEqual(row["action"], "rename")
        self.assertEqual(row["inputs"], OFAV)
        self.assertTrue(row["reason"].startswith(f"by ruling of {RULED_BY} {RULED_AT_AST}: {OFAV_NOTE}"), row["reason"])

    def test_the_ruled_take_is_the_recording_and_the_false_start_is_left_alone(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_04")
        tiny(os.path.join(folder, JKB_TAKE), "libx264")
        with open(os.path.join(folder, JKB_FALSE_START), "wb") as fh:
            fh.write(b"false-start-bytes")
        rulings = [ruling(ANNUAL_04, JKB_TAKE, "catalog_as", "JKB", "T2", "20241113",
                          note="the 138 GB take is the recording"),
                   ruling(ANNUAL_04, JKB_FALSE_START, "leave_out",
                          note="6.3 GB false start; the 138 GB _2 file is the recording")]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, rulings_by_name=by_name)
        self.assertEqual([(a["action"], a["inputs"], a["output"]) for a in actions],
                         [("rename", [JKB_TAKE], "TCRMP20241113_3D_JKB_T2.MOV")])
        self.assertEqual(prep_tools.ruled_out_names(os.listdir(folder), by_name), [JKB_FALSE_START])
        prep_tools.apply(folder, actions, os.path.join(folder, "prep_log.csv"))
        self.assertIn("TCRMP20241113_3D_JKB_T2.MOV", os.listdir(folder))
        with open(os.path.join(folder, JKB_FALSE_START), "rb") as fh:
            self.assertEqual(fh.read(), b"false-start-bytes", "a leave_out file must not be touched")
        self.assertEqual([r["action"] for r in self._rows(folder)], ["rename"])

    def test_three_ruled_parts_merge_whatever_the_rulings_line_order(self):
        folder = season_folder(self.root, "TCRMP_2024_PBL")
        for name in GKT_EXTRA:
            tiny(os.path.join(folder, name), "libx264")
        # Lines out of order on purpose: the file order of the rulings never matters.
        # (Here ruled part order and name order agree; the test after the Castle
        # one is where they differ.)
        rulings = [ruling(PBL_2024, GKT_EXTRA[i], "catalog_as", "GKTEXTRA", "T1", "20240408", part=i + 1,
                          note="extra recording, joined") for i in (2, 0, 1)]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, variants=("demo",), rulings_by_name=by_name)
        self.assertEqual([(a["action"], a["inputs"], a["output"]) for a in actions],
                         [("merge", GKT_EXTRA, "TCRMP20240408_3D_GKTEXTRA_T1.MP4")])
        prep_tools.apply(folder, actions, os.path.join(folder, "prep_log.csv"))
        names = os.listdir(folder)
        self.assertIn("TCRMP20240408_3D_GKTEXTRA_T1.MP4", names)
        for name in GKT_EXTRA:
            self.assertNotIn(name, names, "a verified merge deletes its parts")
        row = self._rows(folder)[0]
        self.assertEqual(row["action"], "merge")
        self.assertEqual(row["inputs"], ";".join(GKT_EXTRA))
        self.assertIn(f"by ruling of {RULED_BY} {RULED_AT_AST}: extra recording, joined", row["reason"])
        self.assertIn("parts deleted", row["reason"])

    def test_castle_six_parts_are_renumbered_by_ruling_and_merged(self):
        folder = season_folder(self.root, "TCRMP_2024_PBL")
        for name in CASTLE_ON_DISK:
            tiny(os.path.join(folder, name), "libx264")
        # Without the rulings this is the gap the merge refuses (part 5 is not on the archive).
        without = prep_tools.plan(folder, variants=("demo",))
        self.assertEqual([a["action"] for a in without], ["conflict"])
        rulings = [ruling(PBL_2024, name, "catalog_as", "CSTSORT", "T5", "20240311", part=ruled_part,
                          note=CASTLE_NOTE) for name, ruled_part in zip(CASTLE_ON_DISK, (1, 2, 3, 4, 5, 6))]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, variants=("demo",), rulings_by_name=by_name)
        self.assertEqual([(a["action"], a["inputs"], a["output"]) for a in actions],
                         [("merge", CASTLE_ON_DISK, "TCRMP20240311_3D_CSTSORT_T5.MP4")])
        reason = actions[0]["reason"]
        self.assertIn("TCRMP20240311_demo_CST_T5_6.MP4 as part 5", reason)
        self.assertIn("TCRMP20240311_demo_CST_T5_7.MP4 as part 6", reason)
        self.assertTrue(reason.startswith(f"by ruling of {RULED_BY} {RULED_AT_AST}: {CASTLE_NOTE}"), reason)
        prep_tools.apply(folder, actions, os.path.join(folder, "prep_log.csv"))
        self.assertIn("TCRMP20240311_3D_CSTSORT_T5.MP4", os.listdir(folder))
        self.assertEqual([r["action"] for r in self._rows(folder)], ["merge"])

    def test_the_ruled_part_order_wins_over_the_file_name_order(self):
        """A ruling can number the parts against their file names, and the join follows the ruling.

        The Castle and GKT_EXTRA sets above sort the same by name and by ruled
        part, so neither can tell a merge in ruled order from one in name
        order (a merge sorted by name passed both). Here archive part 7 is
        ruled part 1, part 4 is ruled 2 and part 1 is ruled 3, so the plan,
        the concat list ffmpeg is handed, and the log row must all run 7, 4, 1.
        """
        folder = season_folder(self.root, "TCRMP_2024_PBL")
        on_disk = [CASTLE_ON_DISK[i] for i in (0, 3, 5)]  # archive parts 1, 4 and 7
        ruled_order = [on_disk[2], on_disk[1], on_disk[0]]  # 7 as part 1, 4 as part 2, 1 as part 3
        self.assertNotEqual(ruled_order, sorted(ruled_order), "the ruled order must differ from name order or this proves nothing")
        for name in on_disk:
            tiny(os.path.join(folder, name), "libx264")
        rulings = [ruling(PBL_2024, name, "catalog_as", "CSTSORT", "T5", "20240311", part=number, note=CASTLE_NOTE)
                   for number, name in enumerate(ruled_order, start=1)]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        groups = prep_tools.group_video_names(sorted(on_disk), variants=("demo",), rulings_by_name=by_name)
        self.assertEqual([m[0] for m in groups[0]["members"]], ruled_order)
        actions = prep_tools.plan(folder, variants=("demo",), rulings_by_name=by_name)
        self.assertEqual([(a["action"], a["inputs"], a["output"]) for a in actions],
                         [("merge", ruled_order, "TCRMP20240311_3D_CSTSORT_T5.MP4")])
        # The order ffmpeg joins in is the order the concat list is written in.
        listed = []
        real = prep_tools.concat_list_line

        def recording(path):
            listed.append(os.path.basename(path))
            return real(path)

        prep_tools.concat_list_line = recording
        try:
            prep_tools.apply(folder, actions, os.path.join(folder, "prep_log.csv"))
        finally:
            prep_tools.concat_list_line = real
        self.assertEqual(listed, ruled_order)
        self.assertIn("TCRMP20240311_3D_CSTSORT_T5.MP4", os.listdir(folder))
        row = self._rows(folder)[0]
        self.assertEqual(row["action"], "merge")
        self.assertEqual(row["inputs"], ";".join(ruled_order))
        self.assertIn(f"{on_disk[2]} as part 1", row["reason"])
        self.assertIn(f"{on_disk[0]} as part 3", row["reason"])

    def test_a_ruled_out_file_that_parses_nowhere_is_not_unread(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_EXTRA")
        blank_video(folder, CAMERA_RAW)
        rulings = [ruling(ANNUAL_EXTRA, CAMERA_RAW, "leave_out", note="raw camera name, content unknown")]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        self.assertEqual(prep_tools.unread_video_names(folder), [CAMERA_RAW])
        self.assertEqual(prep_tools.unread_video_names(folder, rulings_by_name=by_name), [])
        self.assertEqual(prep_tools.plan(folder, rulings_by_name=by_name), [])
        self.assertEqual(prep_tools.ruled_out_names(os.listdir(folder), by_name), [CAMERA_RAW])

    def test_a_ruling_for_a_file_not_in_the_folder_is_unused(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        tiny(os.path.join(folder, OFAV), "libx264")
        rulings = [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE),
                   ruling(ANNUAL_03, "TCRMP20241112_3D_XXX_T9_odd.MOV", "catalog_as", "XXX", "T9", "20241112")]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, rulings_by_name=by_name)
        self.assertEqual([a["output"] for a in actions], ["TCRMP20241112_3D_CST_T5.MOV"])
        self.assertEqual(prep_tools.ruled_out_names(os.listdir(folder), by_name), [])

    def test_a_ruling_applies_only_when_the_folder_names_match(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_02")
        blank_video(folder, OFAV)
        rulings = [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE)]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        self.assertEqual(by_name, {})
        self.assertEqual(prep_tools.plan(folder, rulings_by_name=by_name), [])
        self.assertEqual(prep_tools.unread_video_names(folder, rulings_by_name=by_name), [OFAV])

    def test_two_rulings_one_name_in_two_same_named_folders_is_refused(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        rulings = [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112"),
                   ruling("/volume9/elsewhere/TCRMP_2024Annual_03", OFAV, "leave_out")]
        with self.assertRaises(prep_tools.RulingsError) as caught:
            prep_tools.rulings_for_folder(rulings, folder)
        self.assertIn(OFAV, str(caught.exception))

    def test_a_ruled_file_beside_a_standard_file_for_the_same_timepoint_is_a_conflict(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, OFAV)
        blank_video(folder, "TCRMP20241112_3D_CST_T5.MOV")
        rulings = [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE)]
        by_name = prep_tools.rulings_for_folder(rulings, folder)
        actions = prep_tools.plan(folder, rulings_by_name=by_name)
        self.assertEqual([a["action"] for a in actions], ["conflict"])
        self.assertEqual(sorted(actions[0]["inputs"]), sorted([OFAV, "TCRMP20241112_3D_CST_T5.MOV"]))
        self.assertIn(f"by ruling of {RULED_BY} {RULED_AT_AST}", actions[0]["reason"])

    def test_without_rulings_nothing_changes(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, OFAV)
        self.assertEqual(prep_tools.plan(folder), [])
        self.assertEqual(prep_tools.unread_video_names(folder), [OFAV])
        self.assertEqual(prep_tools.group_video_names([OFAV]), [])
        self.assertEqual(prep_tools.plan_names([OFAV]), [])

    def test_the_ruled_parse_reads_as_the_standard_name(self):
        line = ruling(PBL_2024, CASTLE_ON_DISK[4], "catalog_as", "cstsort", "t5", "20240311", part=5)
        parsed = prep_tools.ruled_parse(line)
        self.assertEqual((parsed["project"], parsed["date"], parsed["site"], parsed["transect"], parsed["part"]),
                         ("TCRMP", "20240311", "CSTSORT", "T5", 5))
        self.assertEqual(parsed["kind"], prep_tools.RULED_KIND)
        self.assertIsNone(prep_tools.ruled_parse(ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112"))["part"])

    def test_ruled_at_is_shown_as_ast(self):
        self.assertEqual(prep_tools.ruled_at_ast("2026-09-14T16:26:00-04:00"), "2026-09-14 16:26 AST")
        self.assertEqual(prep_tools.ruled_at_ast("2026-09-14T20:26:00+00:00"), "2026-09-14 16:26 AST")
        self.assertEqual(prep_tools.ruled_at_ast("2026-09-14 16:26 AST"), "2026-09-14 16:26 AST")
        self.assertEqual(prep_tools.ruled_at_ast(""), "")

    def test_a_quote_in_a_file_name_cannot_break_the_concat_list(self):
        self.assertEqual(prep_tools.concat_list_line("/x/it's.MP4"), "file '/x/it'\\''s.MP4'\n")
        self.assertEqual(prep_tools.concat_list_line("/x/plain?.MP4"), "file '/x/plain?.MP4'\n")


class TheRulingsFileIsChecked(unittest.TestCase):
    """A rulings file that is missing or malformed is refused with a plain message."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "catalog_rulings.csv")

    def tearDown(self):
        shutil.rmtree(self.root)

    def _refused(self, lines, columns=None):
        write_rulings(self.path, lines, columns)
        with self.assertRaises(prep_tools.RulingsError) as caught:
            prep_tools.load_rulings(self.path)
        return str(caught.exception)

    def test_the_column_list_and_ruling_words_match_the_registry(self):
        sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
        import registry
        self.assertEqual(prep_tools.RULING_COLUMNS, registry.RULING_COLUMNS)
        self.assertEqual(prep_tools.RULING_LEAVE_OUT, registry.RULING_LEAVE_OUT)
        self.assertEqual(prep_tools.RULING_CATALOG_AS, registry.RULING_CATALOG_AS)

    def test_a_good_file_loads(self):
        write_rulings(self.path, [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE),
                                  ruling(ANNUAL_EXTRA, CAMERA_RAW, "leave_out", note="raw camera name")])
        lines = prep_tools.load_rulings(self.path)
        self.assertEqual([l["file_name"] for l in lines], [OFAV, CAMERA_RAW])
        self.assertEqual(sorted(lines[0]), sorted(prep_tools.RULING_COLUMNS))

    def test_a_copy_of_the_real_sidecar_loads(self):
        """Lauren's live rulings, read from a temp copy so the live registry is never touched."""
        real = os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"),
                            "_METADATA", "3d", "catalog_rulings.csv")
        if not os.path.isfile(real):
            self.skipTest("no rulings sidecar on this box")
        shutil.copyfile(real, self.path)
        lines = prep_tools.load_rulings(self.path)
        self.assertTrue(all(l["ruling"] in prep_tools.RULING_VALUES for l in lines))
        self.assertTrue(all(l["file_name"] and l["ruled_by"] for l in lines))

    def test_a_missing_file_means_no_rulings(self):
        """As registry.rulings() and the catalog read it: no file, no rulings, nothing stopped.

        The Carousel names the registry's sidecar on every ingest and the
        harness does not track that file, so a fresh checkout has none.
        Until 2026-09-14 a missing file was refused, which stopped every
        well-named season on such a box with exit 4 and nothing merged.
        """
        self.assertEqual(prep_tools.load_rulings(os.path.join(self.root, "nope.csv")), [])

    def test_an_empty_file_is_refused(self):
        open(self.path, "w").close()
        with self.assertRaises(prep_tools.RulingsError) as caught:
            prep_tools.load_rulings(self.path)
        self.assertIn(self.path, str(caught.exception))

    def test_a_wrong_header_is_refused(self):
        message = self._refused([{"path": "x", "verdict": "leave_out"}], columns=["path", "verdict"])
        self.assertIn("header", message)
        self.assertIn(self.path, message)

    def test_an_unknown_ruling_word_is_refused(self):
        message = self._refused([ruling(ANNUAL_03, OFAV, "maybe")])
        self.assertIn("maybe", message)
        self.assertIn("line 2", message)

    def test_a_catalog_as_without_its_placement_is_refused(self):
        self.assertIn("date", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "")]))
        self.assertIn("transect", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "5", "20241112")]))
        self.assertIn("site_label", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST SORT", "T5", "20241112")]))
        self.assertIn("date", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241399")]))
        self.assertIn("part", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", part="x")]))
        self.assertIn("part", self._refused([ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", part=0)]))

    def test_a_file_name_that_is_not_the_last_segment_of_its_path_is_refused(self):
        line = ruling(ANNUAL_03, OFAV, "leave_out")
        line["file_name"] = "other.MOV"
        self.assertIn("other.MOV", self._refused([line]))
        line = ruling(ANNUAL_03, OFAV, "leave_out")
        line["nas_path"] = ""
        self.assertIn("nas_path", self._refused([line]))

    def test_a_directory_is_refused(self):
        with self.assertRaises(prep_tools.RulingsError):
            prep_tools.load_rulings(self.root)

    def test_hostile_cells_are_carried_as_text_not_run(self):
        note = 'a "quoted" note, with a comma; and a newline\ninside'
        write_rulings(self.path, [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=note)])
        lines = prep_tools.load_rulings(self.path)
        self.assertEqual(lines[0]["note"], note)


class TheRegistryReadsItsOwnRulingsFile(unittest.TestCase):
    """The registry's own sidecar is read through registry.rulings(), under its lock; any other path with the csv module."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.saved_root = os.environ.get("VICARIUS_3D_REGISTRY_ROOT")
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.root
        sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")
        import registry
        self.registry = importlib.reload(registry)

    def tearDown(self):
        if self.saved_root is None:
            os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        else:
            os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.saved_root
        importlib.reload(self.registry)
        shutil.rmtree(self.root)

    def test_the_registry_path_goes_through_the_library(self):
        write_rulings(self.registry.RULINGS_CSV, [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112")])
        calls = []
        real = self.registry.rulings

        def counting():
            calls.append(1)
            return real()

        self.registry.rulings = counting
        try:
            lines = prep_tools.load_rulings(self.registry.RULINGS_CSV)
        finally:
            self.registry.rulings = real
        self.assertEqual(calls, [1])
        self.assertEqual([l["file_name"] for l in lines], [OFAV])

    def test_another_path_is_read_with_the_csv_module(self):
        other = write_rulings(os.path.join(self.root, "elsewhere.csv"),
                              [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112")])
        calls = []
        real = self.registry.rulings
        self.registry.rulings = lambda: calls.append(1) or real()
        try:
            lines = prep_tools.load_rulings(other)
        finally:
            self.registry.rulings = real
        self.assertEqual(calls, [])
        self.assertEqual([l["file_name"] for l in lines], [OFAV])


class TheCommandLineAppliesRulings(unittest.TestCase):
    """prep_tools.py --rulings <path>, run as the Carousel runs it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep_tools.py")

    def tearDown(self):
        shutil.rmtree(self.root)

    def _run(self, *args, env_extra=None):
        env = dict(os.environ)
        env.pop("VICARIUS_3D_REGISTRY_ROOT", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, self.script, *args], capture_output=True, text=True, env=env)

    def test_no_rulings_flag_keeps_the_unread_exit_code(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, OFAV)
        result = self._run(folder)
        self.assertEqual(result.returncode, prep_tools.EXIT_NOTHING_RECOGNIZED, result.stdout + result.stderr)
        self.assertIn(OFAV, os.listdir(folder))

    def test_a_missing_rulings_file_applies_no_ruling_and_the_season_is_still_prepped(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, "TCRMP20241112_3D_CST_T5_Proxy.MOV")
        missing = os.path.join(self.root, "nope.csv")
        result = self._run(folder, "--apply", "--rulings", missing)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no rulings file", result.stdout)
        self.assertIn(missing, result.stdout)
        self.assertEqual(sorted(os.listdir(folder)), ["TCRMP20241112_3D_CST_T5.MOV", "prep_log.csv"],
                         "a missing rulings file must prep a well-named season exactly as no --rulings does")

    def test_a_missing_rulings_file_still_leaves_a_ruled_file_unread(self):
        """Missing means no rulings, never a silent pass: the odd name is reported and the run stops."""
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, OFAV)
        missing = os.path.join(self.root, "nope.csv")
        result = self._run(folder, "--apply", "--rulings", missing)
        self.assertEqual(result.returncode, prep_tools.EXIT_NOTHING_RECOGNIZED, result.stdout + result.stderr)
        self.assertIn("no rulings file", result.stdout)
        self.assertIn(OFAV, result.stdout)
        self.assertEqual(os.listdir(folder), [OFAV])

    def test_a_rulings_path_that_is_a_folder_is_refused(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, "TCRMP20241112_3D_CST_T5_Proxy.MOV")
        result = self._run(folder, "--apply", "--rulings", self.root)
        self.assertEqual(result.returncode, prep_tools.EXIT_RULINGS_UNREADABLE, result.stdout + result.stderr)
        self.assertIn("folder", result.stdout)
        self.assertEqual(os.listdir(folder), ["TCRMP20241112_3D_CST_T5_Proxy.MOV"], "a refused rulings path must stop the run before any rename")

    def test_a_malformed_rulings_file_is_refused(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        blank_video(folder, OFAV)
        bad = write_rulings(os.path.join(self.root, "bad.csv"), [ruling(ANNUAL_03, OFAV, "perhaps")])
        result = self._run(folder, "--rulings", bad)
        self.assertEqual(result.returncode, prep_tools.EXIT_RULINGS_UNREADABLE, result.stdout + result.stderr)
        self.assertIn("perhaps", result.stdout)

    def test_a_folder_holding_only_a_ruled_out_file_exits_zero_and_says_so(self):
        folder = season_folder(self.root, "TCRMP_2024Annual_EXTRA")
        blank_video(folder, CAMERA_RAW)
        path = write_rulings(os.path.join(self.root, "r.csv"),
                             [ruling(ANNUAL_EXTRA, CAMERA_RAW, "leave_out", note="raw camera name, content unknown")])
        result = self._run(folder, "--apply", "--rulings", path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ruled out", result.stdout)
        self.assertIn(CAMERA_RAW, result.stdout)
        self.assertEqual(os.listdir(folder), [CAMERA_RAW])

    def test_apply_through_the_registry_root(self):
        registry_root = os.path.join(self.root, "registry")
        os.makedirs(registry_root)
        path = write_rulings(os.path.join(registry_root, "catalog_rulings.csv"),
                             [ruling(ANNUAL_03, OFAV, "catalog_as", "CST", "T5", "20241112", note=OFAV_NOTE)])
        folder = season_folder(self.root, "TCRMP_2024Annual_03")
        tiny(os.path.join(folder, OFAV), "libx264")
        result = self._run(folder, "--apply", "--rulings", path, env_extra={"VICARIUS_3D_REGISTRY_ROOT": registry_root})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 ruling(s)", result.stdout)
        self.assertEqual(sorted(os.listdir(folder)), ["TCRMP20241112_3D_CST_T5.MOV", "prep_log.csv"])


if __name__ == "__main__":
    unittest.main()
