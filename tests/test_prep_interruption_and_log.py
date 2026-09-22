"""What survives a merge that was stopped, and what the prep log says afterwards.

Both defects here were measured by an audit on 2026-09-08 and both produce a
partial result reported as success, the same shape as the 2026-09-07 run.

  1. `_merge` wrote ffmpeg's output straight to the final name. A signal that
     stops ffmpeg leaves a readable SHORT file sitting at the canonical name.
     The run that was interrupted fails loudly, but the next run sees a
     canonical file beside the parts, calls it a conflict, and exits zero, so
     the Carousel carries on and atlasingest records the stump as the whole
     recording. Frames are then extracted from a fraction of the transect.

  2. The prep log's header was written on file existence alone, and the file is
     created by opening it. A run killed during its first merge leaves
     prep_log.csv at zero bytes; every later run then appends data rows with no
     header, csv.DictReader reads the first data row as the field names, and the
     merged deposit sees no merges at all and reports "nothing to copy". The
     joined recording is then deleted with the staged folder and the only copy
     left is the unjoined parts on the read-only archive.

Real ffmpeg, real files, a real signal. Nothing here touches the NAS.

Run from github_repo:  python3 tests/test_prep_interruption_and_log.py
"""
import csv
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"), "_METADATA", "3d")
for path in (REPO, LIB):
    if path not in sys.path:
        sys.path.insert(0, path)

import prep_tools  # noqa: E402

PART_SECONDS = 4
PARTS = 3
MERGED = "TCRMP20240422_3D_MRS_T1.MP4"


def make_part(path, seconds=PART_SECONDS):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=size=64x64:rate=10:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True, capture_output=True)


def part_names(n=PARTS):
    return [f"TCRMP20240422_demo_MRS_T1_{i}.MP4" for i in range(1, n + 1)]


class Folder:
    """A throwaway season folder holding `n` real one-part videos."""

    def __init__(self, n=PARTS):
        self.path = tempfile.mkdtemp(prefix="prep_interrupt_")
        for name in part_names(n):
            make_part(os.path.join(self.path, name))

    def names(self):
        return sorted(os.listdir(self.path))

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class AnInterruptedMergeLeavesNothingAtTheCanonicalName(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)

    def test_a_killed_merge_leaves_no_canonical_file(self):
        script = (
            "import sys; sys.path[:0] = [%r, %r]\n"
            "import prep_tools\n"
            "prep_tools.apply(%r, prep_tools.plan(%r, ('demo',)), %r)\n"
            % (REPO, LIB, self.folder.path, self.folder.path,
               os.path.join(self.folder.path, "prep_log.csv"))
        )
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Long enough for ffmpeg to be writing, short enough that it cannot finish.
        time.sleep(0.6)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=30)

        visible = [n for n in self.folder.names()
                   if n.lower().endswith(".mp4") and not n.startswith(".")]
        self.assertNotIn(MERGED, visible,
                         "a stopped merge left a short file sitting at the whole recording's name")
        self.assertEqual(sorted(visible), part_names(),
                         "every part must still be there after an interrupted merge")
        # What it does leave is a dot file, which nothing that walks a folder
        # reads as a recording, and which the next merge clears.
        partial = [n for n in self.folder.names() if prep_tools.MERGE_PARTIAL_SUFFIX in n]
        self.assertTrue(all(n.startswith(".") for n in partial), partial)

    def test_a_rerun_clears_the_stale_partial_and_completes(self):
        stale = os.path.join(self.folder.path, f".{os.path.splitext(MERGED)[0]}"
                             f"{prep_tools.MERGE_PARTIAL_SUFFIX}.MP4")
        make_part(stale, seconds=1)
        code, out = _run_cli(self.folder.path, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn(MERGED, self.folder.names())
        self.assertNotIn(os.path.basename(stale), self.folder.names(),
                         "the stale half-merge was left on disk")

    def test_a_short_file_at_the_canonical_name_stops_the_run(self):
        # Simulate what an older interrupted run left: a valid short file under
        # the canonical name, beside its parts.
        make_part(os.path.join(self.folder.path, MERGED), seconds=1)
        code, out = _run_cli(self.folder.path, "--apply")
        self.assertNotEqual(code, 0,
                            "a conflict is a merge that never happened and must stop the season")
        self.assertIn("conflict", out.lower())


class TheMergeIsVerifiedBeforeItReplacesAnything(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)

    def test_a_verified_merge_still_works(self):
        code, out = _run_cli(self.folder.path, "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn(MERGED, self.folder.names())
        merged = os.path.join(self.folder.path, MERGED)
        duration = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", merged],
            check=True, capture_output=True, text=True).stdout.strip())
        self.assertAlmostEqual(duration, PARTS * PART_SECONDS, delta=0.5)
        self.assertEqual([n for n in self.folder.names() if n.lower().endswith(".mp4")], [MERGED],
                         "the parts are deleted only after a verified merge")


class TheToleranceCannotGrowWithTheRecording(unittest.TestCase):
    def test_a_five_minute_transect_cannot_lose_seconds(self):
        # Four 71.2 s parts, the real shape of MRS_T1_2023ann (284.8 s).
        parts = [71.2] * 4
        ok, expected, tolerance = prep_tools.verify_durations(sum(parts) - 5.7, parts)
        self.assertFalse(ok, f"5.7 s missing passed with a tolerance of {tolerance} s")
        self.assertLessEqual(tolerance, 1.0, "the tolerance grows with the recording")

    def test_duplicated_footage_is_caught_too(self):
        parts = [71.2] * 4
        ok, _, _ = prep_tools.verify_durations(sum(parts) + 5.7, parts)
        self.assertFalse(ok, "5.7 s of duplicated footage passed")

    def test_a_small_honest_difference_still_passes(self):
        parts = [71.2] * 4
        ok, _, _ = prep_tools.verify_durations(sum(parts) + 0.2, parts)
        self.assertTrue(ok, "a container rounding difference must not fail a good merge")

    def test_parts_that_report_no_duration_are_refused(self):
        ok, _, _ = prep_tools.verify_durations(0.4, [0.0, 0.0])
        self.assertFalse(ok, "a group whose parts report zero duration verified against nothing")

    def test_a_zero_duration_merged_file_is_refused(self):
        ok, _, _ = prep_tools.verify_durations(0.0, [10.0, 10.0])
        self.assertFalse(ok)


class ThePrepLogAlwaysHasItsHeader(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.addCleanup(self.folder.cleanup)
        self.log = os.path.join(self.folder.path, "prep_log.csv")

    def test_a_zero_byte_log_gets_its_header_back(self):
        open(self.log, "w").close()
        self.assertEqual(os.path.getsize(self.log), 0)
        code, out = _run_cli(self.folder.path, "--apply")
        self.assertEqual(code, 0, out)
        with open(self.log, newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, prep_tools.LOG_FIELDS,
                             "the log was written without its header, so nothing can read it")
            rows = list(reader)
        self.assertTrue(any(r["action"] == "merge" for r in rows), rows)

    def test_the_header_reaches_disk_before_the_first_merge_finishes(self):
        # The window that produced the zero-byte log: the header sat in a buffer
        # for the whole of the first merge.
        script = (
            "import sys; sys.path[:0] = [%r, %r]\n"
            "import prep_tools\n"
            "prep_tools.apply(%r, prep_tools.plan(%r, ('demo',)), %r)\n"
            % (REPO, LIB, self.folder.path, self.folder.path, self.log)
        )
        proc = subprocess.Popen([sys.executable, "-c", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        size = os.path.getsize(self.log) if os.path.exists(self.log) else 0
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
        self.assertGreater(size, 0,
                           "prep_log.csv was still zero bytes while the first merge ran")

    def test_a_second_run_appends_under_the_same_header(self):
        code, _ = _run_cli(self.folder.path, "--apply")
        self.assertEqual(code, 0)
        _run_cli(self.folder.path, "--apply")
        with open(self.log, newline="") as fh:
            reader = csv.DictReader(fh)
            self.assertEqual(reader.fieldnames, prep_tools.LOG_FIELDS)
            self.assertTrue(all(set(r) <= set(prep_tools.LOG_FIELDS) for r in reader))


def _run_cli(folder, *args):
    """prep_tools.py as the Carousel runs it. Returns (exit code, output)."""
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "prep_tools.py"), folder,
         "--name-variant", "demo", "--name-variant", "3ddemo", *args],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


if __name__ == "__main__":
    unittest.main(verbosity=2)
