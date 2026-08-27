import csv, importlib, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius") + "/_METADATA/3d")

import atlasingest
import registry


def tiny(path, codec, frames=2):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=2",
         "-frames:v", str(frames), "-c:v", codec, path], check=True,
    )


class AtlasIngestTests(unittest.TestCase):
    def setUp(self):
        self.registry_root = tempfile.mkdtemp()
        os.environ["VICARIUS_3D_REGISTRY_ROOT"] = self.registry_root
        importlib.reload(registry)
        self.r = registry

        self.d = tempfile.mkdtemp()
        tiny(os.path.join(self.d, "TCRMP20231015_3D_MRS_T1.MOV"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20240412_3D_MRS_T1.MP4"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20241018_3D_MRS_T1.MKV"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20250414_3D_MRS_T1.avi"), "mjpeg")
        tiny(os.path.join(self.d, "GOPR0001.MP4"), "libx264")
        tiny(os.path.join(self.d, "TCRMP20240412_3D_BID_T2_1.MP4"), "libx264")

    def tearDown(self):
        shutil.rmtree(self.d)
        shutil.rmtree(self.registry_root)
        os.environ.pop("VICARIUS_3D_REGISTRY_ROOT", None)

    def _events(self):
        with open(self.r.EVENTS_CSV) as fh:
            return list(csv.DictReader(fh))

    def test_creates_four_rows_with_correct_ids(self):
        results = atlasingest.ingest_folder(self.d, actor="test")
        created_ids = sorted(r["readable_id"] for r in results if r["status"] == "created")
        self.assertEqual(created_ids, ["MRS_T1_2023ann", "MRS_T1_2024_pbl", "MRS_T1_2024ann", "MRS_T1_2025_pbl"])
        for rid in created_ids:
            self.assertIsNotNone(self.r.get(rid))

    def test_rows_carry_facts_and_ingested_at(self):
        atlasingest.ingest_folder(self.d, actor="test")
        row = self.r.get("MRS_T1_2023ann")
        self.assertEqual(row["video_format"], "mov/h264")
        self.assertEqual(row["video_duration_s"], "1.0")
        self.assertGreaterEqual(float(row["video_size_gb"]), 0.0)
        self.assertEqual(row["original_videos"], "TCRMP20231015_3D_MRS_T1.MOV")
        self.assertEqual(row["video_location"], self.d)
        self.assertRegex(row["video_location_verified"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertRegex(row["ingested_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_rerun_is_idempotent(self):
        # video_location_verified is a "confirmed at" stamp: ingest is the location
        # authority and refreshes it on every run, so that field alone is allowed to
        # keep logging events even when nothing about the video actually changed.
        atlasingest.ingest_folder(self.d, actor="test")
        before = self._events()
        atlasingest.ingest_folder(self.d, actor="test")
        after = self._events()
        new_events = after[len(before):]
        self.assertTrue(all(e["field"] == "video_location_verified" for e in new_events))

    def test_rerun_updates_only_changed_facts(self):
        atlasingest.ingest_folder(self.d, actor="test")
        path = os.path.join(self.d, "TCRMP20231015_3D_MRS_T1.MOV")
        os.remove(path)
        tiny(path, "libx264", frames=4)  # doubles the duration; site/season/location unchanged

        before = len(self._events())
        atlasingest.ingest_folder(self.d, actor="test")
        new_events = self._events()[before:]
        changed_fields = {e["field"] for e in new_events if e["readable_id"] == "MRS_T1_2023ann"}

        self.assertIn("video_duration_s", changed_fields)
        self.assertTrue(changed_fields.issubset({"video_duration_s", "video_size_gb", "video_location_verified"}))
        row = self.r.get("MRS_T1_2023ann")
        self.assertEqual(row["video_duration_s"], "2.0")

    def test_rerun_from_moved_folder_updates_location_and_keeps_operator_edit(self):
        folder_a = tempfile.mkdtemp()
        tiny(os.path.join(folder_a, "TCRMP20231015_3D_MRS_T1.MOV"), "libx264")

        atlasingest.ingest_folder(folder_a, actor="test")
        self.assertEqual(self.r.get("MRS_T1_2023ann")["video_location"], folder_a)

        self.r.upsert("MRS_T1_2023ann", {"season_token": "_pbl"}, actor="operator")

        folder_b = folder_a + "_moved"
        os.rename(folder_a, folder_b)
        self.addCleanup(lambda: shutil.rmtree(folder_b, ignore_errors=True))

        atlasingest.ingest_folder(folder_b, actor="test")
        row = self.r.get("MRS_T1_2023ann")
        self.assertEqual(row["video_location"], folder_b)
        self.assertEqual(row["season_token"], "_pbl")

    def test_operator_edit_season_token_survives_rerun(self):
        atlasingest.ingest_folder(self.d, actor="test")
        self.r.upsert("MRS_T1_2023ann", {"season_token": "_pbl"}, actor="operator")
        atlasingest.ingest_folder(self.d, actor="test")
        self.assertEqual(self.r.get("MRS_T1_2023ann")["season_token"], "_pbl")

    def test_unparsed_name_skipped(self):
        results = atlasingest.ingest_folder(self.d, actor="test")
        row = next(r for r in results if r["file"] == "GOPR0001.MP4")
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["reason"], "name does not match TCRMP{YYYYMMDD}_3D_{SITE}_{T#}")

    def test_part_suffix_skipped(self):
        results = atlasingest.ingest_folder(self.d, actor="test")
        row = next(r for r in results if r["file"] == "TCRMP20240412_3D_BID_T2_1.MP4")
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["reason"], "merge parts first (see atlasprep.md)")
        self.assertIsNone(self.r.get("BID_T2_2024_pbl"))

    def test_dry_run_writes_nothing(self):
        results = atlasingest.ingest_folder(self.d, actor="test", dry_run=True)
        self.assertTrue(any(r["status"] == "created" for r in results))
        self.assertFalse(os.path.exists(self.r.REGISTRY_CSV))
        self.assertEqual(self.r.load(), [])

    def test_non_video_extension_not_listed(self):
        with open(os.path.join(self.d, "prep_log.csv"), "w") as fh:
            fh.write("timestamp,action,inputs,output,reason\n")
        results = atlasingest.ingest_folder(self.d, actor="test", dry_run=True)
        self.assertFalse(any(r["file"] == "prep_log.csv" for r in results))

    def test_junk_file_with_video_extension_not_listed(self):
        with open(os.path.join(self.d, "readme_notes.MP4"), "wb") as fh:
            fh.write(b"not actually a video")
        results = atlasingest.ingest_folder(self.d, actor="test", dry_run=True)
        self.assertFalse(any(r["file"] == "readme_notes.MP4" for r in results))

    def test_corrupted_video_skipped_without_aborting_folder(self):
        # A name that parses (so it is not filtered out earlier) but points at
        # unreadable bytes must not raise out of ingest_folder for the whole run.
        with open(os.path.join(self.d, "TCRMP20230101_3D_XXX_T9.MP4"), "wb") as fh:
            fh.write(os.urandom(1024))

        results = atlasingest.ingest_folder(self.d, actor="test")

        bad = next(r for r in results if r["file"] == "TCRMP20230101_3D_XXX_T9.MP4")
        self.assertEqual(bad["status"], "skipped")
        self.assertTrue(bad["reason"].startswith("ffprobe failed:"))

        created_ids = sorted(r["readable_id"] for r in results if r["status"] == "created")
        self.assertEqual(created_ids, ["MRS_T1_2023ann", "MRS_T1_2024_pbl", "MRS_T1_2024ann", "MRS_T1_2025_pbl"])


if __name__ == "__main__":
    unittest.main()
