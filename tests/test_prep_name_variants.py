#!/usr/bin/env python3
"""
Module:  tests/test_prep_name_variants.py
Purpose: The merge must recognise the same file names the catalog accepts.
Inputs:  prep_tools.group_video_names and plan().
Outputs: unittest results.

2026-09-08. A Voyager 1 run reconstructed three of the four timepoints it was
asked for and reported itself done. The refused one was a four-part recording,
and frame extraction does not take multi-part rows: parts are supposed to be
merged first, by the Carousel's ingest stage, which runs prep_tools --apply.

That stage DID run, on both turns, and reported ok while merging nothing. The
reason is here: group_video_names calls parse_video_name with no variants, so
a file named with the "demo" or "3ddemo" token parses as nothing and is left
out of every group. The catalog accepts those tokens through --name-variant,
so it records the parts; the merge does not, so it never joins them. Two halves
of one pipeline disagreeing about which names are real, and the merge fails
open on exactly the seasons that need it: 92 of 377 registry rows, of which 85
are genuine 2023 annual and 2024 spring recordings.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prep_tools  # noqa: E402

# Real names, taken from the registry rows this failed on.
STANDARD = ["TCRMP20241122_3D_MRS_T1_1.MOV", "TCRMP20241122_3D_MRS_T1_2.MOV"]
DEMO = ["TCRMP20240422_demo_MRS_T1_1.MP4", "TCRMP20240422_demo_MRS_T1_2.MP4",
        "TCRMP20240422_demo_MRS_T1_3.MP4", "TCRMP20240422_demo_MRS_T1_4.MP4"]
DEMO3D = ["TCRMP20240215_3ddemo_BPT_T1_1.MP4", "TCRMP20240215_3ddemo_BPT_T1_2.MP4"]
PART_WORD = ["TCRMP20231207_demo_MRS_T2_part1.MP4", "TCRMP20231207_demo_MRS_T2_part2.MP4"]


class GroupingAcceptsTheVariantsTheCatalogAccepts(unittest.TestCase):
    def test_standard_names_group_without_variants(self):
        """The behaviour that already worked must keep working."""
        groups = prep_tools.group_video_names(STANDARD)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["parts"]), 2)

    def test_demo_named_parts_are_invisible_without_the_variant(self):
        """This is the bug: silence, not an error."""
        self.assertEqual(prep_tools.group_video_names(DEMO), [])

    def test_demo_named_parts_group_when_the_variant_is_given(self):
        groups = prep_tools.group_video_names(DEMO, variants=("demo",))
        self.assertEqual(len(groups), 1, "the four parts did not group")
        self.assertEqual(len(groups[0]["parts"]), 4)

    def test_the_merged_name_is_always_the_standard_form(self):
        """A merge of variant-named parts produces a canonical 3D name, so the
        result is correct on disk whatever the parts were called."""
        groups = prep_tools.group_video_names(DEMO, variants=("demo",))
        self.assertEqual(groups[0]["output"], "TCRMP20240422_3D_MRS_T1.MP4")

    def test_the_3ddemo_variant_is_accepted_too(self):
        groups = prep_tools.group_video_names(DEMO3D, variants=("3ddemo",))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["output"], "TCRMP20240215_3D_BPT_T1.MP4")

    def test_part_word_numbering_groups(self):
        """Some parts are named part1/part2 rather than _1/_2."""
        groups = prep_tools.group_video_names(PART_WORD, variants=("demo",))
        self.assertEqual(len(groups), 1, "part1/part2 naming did not group")
        self.assertEqual(len(groups[0]["parts"]), 2)

    def test_both_variants_at_once(self):
        groups = prep_tools.group_video_names(DEMO + DEMO3D, variants=("demo", "3ddemo"))
        self.assertEqual(len(groups), 2)

    def test_a_variant_never_pulls_in_an_unrelated_name(self):
        names = DEMO + ["notes.txt", "TCRMP_bad_name.MP4", "prep_log.csv"]
        groups = prep_tools.group_video_names(names, variants=("demo",))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["members"]), 4)

    def test_an_unknown_variant_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            prep_tools.group_video_names(DEMO, variants=("nonsense",))
        self.assertIn("nonsense", str(caught.exception))

    def test_variants_default_to_none_so_nothing_changes_by_accident(self):
        self.assertEqual(prep_tools.group_video_names(DEMO), [])


class PlanCarriesTheVariantsThrough(unittest.TestCase):
    def _folder(self, names):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)   # no scratch folders left in /tmp after a run
        for n in names:
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"\0" * 16)
        return d

    def test_plan_sees_nothing_without_the_variant(self):
        self.assertEqual(prep_tools.plan(self._folder(DEMO)), [])

    def test_plan_proposes_one_merge_with_the_variant(self):
        actions = prep_tools.plan(self._folder(DEMO), variants=("demo",))
        merges = [a for a in actions if a["action"] == "merge"]
        self.assertEqual(len(merges), 1, actions)
        self.assertEqual(len(merges[0]["inputs"]), 4)
        self.assertEqual(merges[0]["output"], "TCRMP20240422_3D_MRS_T1.MP4")

    def test_a_single_variant_named_file_is_renamed_not_merged(self):
        actions = prep_tools.plan(self._folder(["TCRMP20250321_demo_MRS_T1.MOV"]),
                                  variants=("demo",))
        kinds = [a["action"] for a in actions]
        self.assertIn("rename", kinds, actions)


class TheCommandLineTakesTheSameFlagAsTheCatalog(unittest.TestCase):
    def test_help_offers_name_variant(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "prep_tools.py")).read()
        self.assertIn("--name-variant", src,
                      "the merge cannot be told about the names the catalog accepts")

    def test_the_driver_passes_the_variants(self):
        ingest = open("/mnt/rip/vicarius_drive/vicarius/modules/driver/github_repo/"
                      "driver/ingest.py").read()
        self.assertIn("name-variant", ingest,
                      "the Carousel runs the merge without the variants, so it merges nothing")

    def test_help_offers_rulings(self):
        """A ruled file reaches the Workbench under its real odd name; without the
        flag the merge cannot read it and the season stops as unread."""
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "prep_tools.py")).read()
        self.assertIn("--rulings", src, "the merge cannot be told about Lauren's rulings")

    def test_the_driver_passes_the_rulings(self):
        ingest = open("/mnt/rip/vicarius_drive/vicarius/modules/driver/github_repo/"
                      "driver/ingest.py").read()
        self.assertIn("--rulings", ingest,
                      "the Carousel runs the merge without the rulings, so every ruled file is unread")


if __name__ == "__main__":
    unittest.main(verbosity=2)
