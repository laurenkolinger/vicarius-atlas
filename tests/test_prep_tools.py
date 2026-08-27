import os, sys, subprocess, tempfile, unittest, csv, shutil
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

if __name__ == "__main__": unittest.main()

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
        # Half a second is the floor; two percent takes over on long recordings.
        self.assertFalse(prep_tools.verify_durations(1.4, [1.0, 1.0])[0])
        self.assertTrue(prep_tools.verify_durations(1990.0, [1000.0, 1000.0])[0])
        self.assertFalse(prep_tools.verify_durations(950.0, [1000.0, 1000.0])[0])

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
            value = real_duration(path)
            return value / 4 if os.path.basename(path) == merged_name else value

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
