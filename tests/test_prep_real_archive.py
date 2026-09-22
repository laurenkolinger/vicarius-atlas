"""The merge rule, measured against the names that actually exist.

tests/fixtures/real_archive_paths.txt is a full listing of every video file on
the four TCRMP source roots, captured 2026-09-08 AST. These tests read it, build
the identity groups the merge would build, and assert that every group the rule
would act on is one it is actually safe to act on.

The four defects held here were each found in that listing, not invented:

  1. CST_T5 on 20240311 has parts numbered 1, 2, 3, 4, 6, 7. Part 5 is not on
     the archive. The merge would have joined the six it can see, and its
     duration check would have passed, because that check compares the merged
     duration against the sum of the parts it merged, not against the recording
     that was made. A transect would have been reconstructed from a recording
     with a hole in it, and nothing would have said so.

  2. TCRMP20240307_demo_SHR_T5_pt1.MP4 and _pt2.MP4 write the part as "pt",
     which the name pattern did not know, so those two parts parsed as nothing
     and would never have been joined.

  3. TCRMP20231207_demo_MRS_T3_part1.MP4 is on the archive with no parsable
     part 2 (its only sibling is a 214 MB file named "part2?", which the name
     pattern does not read, so it is never pulled). A lone part was refused as "not the whole recording" from 2026-09-08 to
     2026-09-11; Lauren reversed that on 2026-09-11 ("if that's it then that's
     it"): a lone part is the whole recording and is renamed, with the part
     number kept in the log reason.

  4. TCRMP20241113_3D_JKB_T2_2_Proxy.MOV sits beside
     TCRMP20241113_3D_JKB_T2_Proxy.MOV. Both are in one identity group, and the
     group produced exactly one action built from the first member, so the
     other file was dropped from the plan without a word.

Run from github_repo:  python3 tests/test_prep_real_archive.py
"""
import collections
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LIB = os.path.join(os.environ.get("VICARIUS_ROOT", "/mnt/rip/vicarius_drive/vicarius"), "_METADATA", "3d")
for path in (REPO, LIB):
    if path not in sys.path:
        sys.path.insert(0, path)

import naming3d  # noqa: E402
import prep_tools  # noqa: E402

CORPUS = os.path.join(HERE, "fixtures", "real_archive_paths.txt")
ALL_VARIANTS = ("demo", "3ddemo")


def archive_paths():
    """Every real video path in the frozen corpus, comments dropped."""
    with open(CORPUS) as fh:
        return [l.strip() for l in fh if l.strip() and not l.startswith("#")]


def names_in(directory, paths):
    return sorted(os.path.basename(p) for p in paths if os.path.dirname(p) == directory)


def groups_by_directory(paths):
    """{directory: [names]}, the way prep_tools.plan sees one folder at a time."""
    out = collections.defaultdict(list)
    for p in paths:
        out[os.path.dirname(p)].append(os.path.basename(p))
    return out


class TheCorpusIsThere(unittest.TestCase):
    def test_the_corpus_is_the_real_archive(self):
        paths = archive_paths()
        self.assertGreater(len(paths), 600, "the frozen archive listing is too small to be the real one")
        self.assertTrue(all(p.startswith("/volume") for p in paths), "a path is not a NAS path")


class APartNumberGapIsNeverMerged(unittest.TestCase):
    """Defect 1. The archive's own case: CST_T5 20240311, parts 1,2,3,4,6,7."""

    NAMES = [f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in (1, 2, 3, 4, 6, 7)]

    def test_the_real_gap_is_refused(self):
        actions = {a["action"] for a in _plan_names(self.NAMES)}
        self.assertNotIn("merge", actions,
                         "parts 1,2,3,4,6,7 were joined as though nothing were missing")
        self.assertIn("conflict", actions)

    def test_the_reason_names_the_missing_part(self):
        conflict = [a for a in _plan_names(self.NAMES) if a["action"] == "conflict"][0]
        self.assertIn("5", conflict["reason"])
        self.assertEqual(len(conflict["inputs"]), 6, "every part belongs to the conflict")

    def test_a_complete_set_still_merges(self):
        names = [f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in (1, 2, 3, 4, 5, 6, 7)]
        actions = _plan_names(names)
        self.assertEqual([a["action"] for a in actions], ["merge"], actions)
        self.assertEqual(len(actions[0]["inputs"]), 7)

    def test_parts_that_do_not_start_at_one_are_refused(self):
        names = [f"TCRMP20240311_demo_CST_T5_{n}.MP4" for n in (2, 3)]
        self.assertEqual([a["action"] for a in _plan_names(names)], ["conflict"])

    def test_a_duplicate_part_number_is_refused(self):
        # The same part number in two containers: which one is part 2?
        names = ["TCRMP20240311_demo_CST_T5_1.MP4", "TCRMP20240311_demo_CST_T5_2.MP4",
                 "TCRMP20240311_demo_CST_T5_part2.MP4"]
        self.assertEqual([a["action"] for a in _plan_names(names)], ["conflict"])


class ThePartFormsTheArchiveUses(unittest.TestCase):
    """Defect 2. Three spellings of the same thing, all real: _1, _part1, _pt1."""

    def test_pt_is_a_part(self):
        parsed = naming3d.parse_video_name("TCRMP20240307_demo_SHR_T5_pt1.MP4", ALL_VARIANTS)
        self.assertIsNotNone(parsed, "the pt part form still does not parse")
        self.assertEqual(parsed["part"], 1)
        self.assertEqual(parsed["site"], "SHR")
        self.assertEqual(parsed["transect"], "T5")

    def test_the_real_pt_pair_merges(self):
        names = ["TCRMP20240307_demo_SHR_T5_pt1.MP4", "TCRMP20240307_demo_SHR_T5_pt2.MP4"]
        actions = _plan_names(names)
        self.assertEqual([a["action"] for a in actions], ["merge"], actions)
        self.assertEqual(actions[0]["output"], "TCRMP20240307_3D_SHR_T5.MP4")

    def test_all_three_spellings_agree(self):
        for name, want in (("TCRMP20240422_demo_MRS_T1_2.MP4", 2),
                           ("TCRMP20231207_demo_MRS_T2_part2.MP4", 2),
                           ("TCRMP20240307_demo_SHR_T5_pt2.MP4", 2)):
            with self.subTest(name):
                self.assertEqual(naming3d.parse_video_name(name, ALL_VARIANTS)["part"], want)

    def test_two_spellings_in_one_group_still_merge(self):
        # "_1" and "_pt2" are parts 1 and 2 of the same recording, whatever the
        # diver typed. The numbers run 1 to 2 with nothing else in the group, so
        # there is nothing to guess and refusing would only make hand work. A
        # spelling difference that DID hide a real ambiguity, the same part
        # number written two ways, is caught by the repeat check instead.
        names = ["TCRMP20240307_demo_SHR_T5_1.MP4", "TCRMP20240307_demo_SHR_T5_pt2.MP4"]
        actions = _plan_names(names)
        self.assertEqual([a["action"] for a in actions], ["merge"], actions)
        self.assertEqual(actions[0]["inputs"],
                         ["TCRMP20240307_demo_SHR_T5_1.MP4", "TCRMP20240307_demo_SHR_T5_pt2.MP4"],
                         "the merge must join them in part order, not name order")


class ALonePartIsTheWholeRecording(unittest.TestCase):
    """Defect 3, reversed 2026-09-11 (Lauren: "if that's it then that's it").
    The archive's own case: MRS_T3 20231207 part1, whose only sibling is the
    unreadable "part2?" file and so never reaches the folder as a part."""

    def test_a_lone_part_is_renamed_to_the_whole_recording(self):
        actions = _plan_names(["TCRMP20231207_demo_MRS_T3_part1.MP4"])
        self.assertEqual([a["action"] for a in actions], ["rename"], actions)
        self.assertEqual(actions[0]["output"], "TCRMP20231207_3D_MRS_T3.MP4")

    def test_the_reason_keeps_the_part_number(self):
        reason = _plan_names(["TCRMP20231207_demo_MRS_T3_part1.MP4"])[0]["reason"]
        self.assertIn("lone part 1", reason)

    def test_a_lone_higher_part_is_still_the_whole_recording_and_says_so(self):
        actions = _plan_names(["TCRMP20231207_demo_MRS_T3_part2.MP4"])
        self.assertEqual([a["action"] for a in actions], ["rename"], actions)
        self.assertIn("lone part 2", actions[0]["reason"])

    def test_a_lone_part_beside_a_whole_file_is_still_a_conflict(self):
        actions = _plan_names(["TCRMP20231207_demo_MRS_T3_part1.MP4", "TCRMP20231207_demo_MRS_T3.MP4"])
        self.assertEqual([a["action"] for a in actions], ["conflict"], actions)

    def test_a_file_with_no_part_number_still_renames(self):
        actions = _plan_names(["TCRMP20231207_demo_MRS_T3.MP4"])
        self.assertEqual([a["action"] for a in actions], ["rename"], actions)


class NoMemberOfAGroupIsEverDropped(unittest.TestCase):
    """Defect 4. The archive's own case: JKB_T2 20241113, a stray part beside
    the whole recording, where only the first member reached the plan."""

    NAMES = ["TCRMP20241113_3D_JKB_T2_2_Proxy.MOV", "TCRMP20241113_3D_JKB_T2_Proxy.MOV"]

    def test_both_files_are_accounted_for(self):
        actions = _plan_names(self.NAMES)
        touched = {n for a in actions for n in a["inputs"]}
        self.assertEqual(touched, set(self.NAMES),
                         "a file in the folder never appeared in the plan")

    def test_it_is_a_conflict_not_a_guess(self):
        self.assertEqual([a["action"] for a in _plan_names(self.NAMES)], ["conflict"])

    def test_every_group_in_the_whole_archive_accounts_for_every_file(self):
        # The invariant, over every folder of the real archive: whatever the
        # rule decides, no file is silently left out of the plan.
        for directory, names in sorted(groups_by_directory(archive_paths()).items()):
            with self.subTest(directory):
                readable = [n for n in names
                            if naming3d.parse_video_name(n, ALL_VARIANTS) is not None]
                actions = prep_tools.plan_names(sorted(readable), ALL_VARIANTS)
                touched = {n for a in actions for n in a["inputs"]}
                self.assertEqual(touched, set(readable),
                                 f"{sorted(set(readable) - touched)} never reached the plan")


class TheWholeArchivePassesThroughTheRule(unittest.TestCase):
    def test_no_folder_produces_a_merge_that_is_not_a_complete_part_set(self):
        for directory, names in sorted(groups_by_directory(archive_paths()).items()):
            readable = [n for n in names if naming3d.parse_video_name(n, ALL_VARIANTS) is not None]
            for action in prep_tools.plan_names(sorted(readable), ALL_VARIANTS):
                if action["action"] != "merge":
                    continue
                parts = [naming3d.parse_video_name(n, ALL_VARIANTS)["part"] for n in action["inputs"]]
                with self.subTest(f"{directory} -> {action['output']}"):
                    self.assertEqual(sorted(parts), list(range(1, len(parts) + 1)),
                                     f"a merge of {sorted(parts)} is not a complete part set")

    def test_the_known_bad_groups_all_come_out_as_conflicts(self):
        wanted = {
            "/volume2/Archive9_10TB/encoded/TCRMP_2024_PBL": "TCRMP20240311_3D_CST_T5.MP4",
            "/volume4/Archive6_16TB/TCRMP_2024Annual/encoded/TCRMP_2024Annual_04":
                "TCRMP20241113_3D_JKB_T2.MOV",
        }
        by_dir = groups_by_directory(archive_paths())
        for directory, output in wanted.items():
            names = [n for n in by_dir.get(directory, [])
                     if naming3d.parse_video_name(n, ALL_VARIANTS) is not None]
            actions = {a["output"]: a["action"] for a in prep_tools.plan_names(sorted(names), ALL_VARIANTS)}
            with self.subTest(output):
                self.assertEqual(actions.get(output), "conflict",
                                 f"{output} in {directory} came out as {actions.get(output)}")


def _plan_names(names):
    """The plan for a folder holding exactly `names`, without touching a disk."""
    return prep_tools.plan_names(sorted(names), ALL_VARIANTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
