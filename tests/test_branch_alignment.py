"""Regression tests for the two TJA conversion crashes fixed in tja2fumen.

Both cases used to raise: branches whose mid-measure commands don't line up,
and a 'BALLOON:' field with fewer values than there are balloon notes.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import warnings
from pathlib import Path

# tja2fumen imports itself as a top-level package (same as app/converter.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from tja2fumen.parsers import parse_tja  # noqa: E402
from tja2fumen.converters import (convert_tja_to_fumen,  # noqa: E402
                                  process_commands)


def _parse(tja_text: str):
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "song.tja"
        path.write_text(tja_text, encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return parse_tja(str(path))


def _convert(tja_text: str):
    tja = _parse(tja_text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {name: convert_tja_to_fumen(course)
                for name, course in tja.courses.items()}


def _submeasure_counts(tja_text: str):
    course = _parse(tja_text).courses["Oni"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        processed = process_commands(course.branches, course.bpm)
    return {name: len(branch) for name, branch in processed.items()}


HEADER = "TITLE:test\nBPM:120\nWAVE:test.ogg\nOFFSET:0\n"
HEADER_NO_OFFSET = "TITLE:test\nBPM:120\nWAVE:test.ogg\n"


def _branching_chart(normal: str, expert: str, master: str,
                     balloon: str = "", intro: str = "1111,\n") -> str:
    return (
        f"{HEADER}COURSE:Oni\nLEVEL:1\n{balloon}#START\n"
        f"{intro}"
        "#BRANCHSTART p,100,100\n"
        f"#N\n{normal}"
        f"#E\n{expert}"
        f"#M\n{master}"
        "#BRANCHEND\n"
        "1111,\n"
        "#END\n"
    )


class BranchSplitAlignmentTests(unittest.TestCase):
    def test_mid_measure_command_in_one_branch_only(self) -> None:
        # Only #E has a mid-measure #SCROLL, so that branch used to end up
        # with one more sub-measure than the others.
        chart = _branching_chart(
            normal="1111,\n",
            expert="11\n#SCROLL 2\n11,\n",
            master="1111,\n",
        )
        counts = _submeasure_counts(chart)
        self.assertEqual(len(set(counts.values())), 1, counts)
        self.assertIn("Oni", _convert(chart))

    def test_mid_measure_command_in_measure_without_notes(self) -> None:
        # The #N measure has no notes at all, so it has no subdivisions to
        # split on; it must be padded with rests to match #E's split.
        chart = _branching_chart(
            normal=",\n",
            expert="11\n#SCROLL 2\n11,\n",
            master=",\n",
        )
        self.assertIn("Oni", _convert(chart))

    def test_two_commands_at_same_position(self) -> None:
        # Two commands at one position create two sub-measures, so the other
        # branches need two splits there as well.
        chart = _branching_chart(
            normal="1111,\n",
            expert="11\n#SCROLL 2\n#BPMCHANGE 200\n11,\n",
            master="1111,\n",
        )
        self.assertIn("Oni", _convert(chart))

    def test_aligned_chart_is_left_alone(self) -> None:
        chart = _branching_chart(
            normal="11\n#SCROLL 2\n11,\n",
            expert="11\n#SCROLL 2\n11,\n",
            master="11\n#SCROLL 2\n11,\n",
        )
        counts = _submeasure_counts(chart)
        self.assertEqual(len(set(counts.values())), 1, counts)


class BalloonFieldTests(unittest.TestCase):
    def test_not_enough_balloon_values(self) -> None:
        # The pre-branch balloon note gets duplicated across all 3 branches,
        # and the branches add balloons of their own -- but 'BALLOON:' only
        # lists 2 values for what ends up being 6 balloon notes.
        chart = _branching_chart(
            normal="7008,\n",
            expert="7008,\n",
            master="7008,\n",
            balloon="BALLOON:5,7\n",
            intro="7008,\n",
        )
        self.assertIn("Oni", _convert(chart))

    def test_no_balloon_values_at_all(self) -> None:
        chart = _branching_chart(
            normal="7008,\n",
            expert="7008,\n",
            master="7008,\n",
            intro="7008,\n",
        )
        self.assertIn("Oni", _convert(chart))


class MetadataTests(unittest.TestCase):
    def test_missing_offset_defaults_to_zero(self) -> None:
        # 'OFFSET:' is optional in TJA; other players treat it as 0.
        chart = f"{HEADER_NO_OFFSET}COURSE:Oni\nLEVEL:1\n#START\n1111,\n#END\n"
        courses = _convert(chart)
        self.assertIn("Oni", courses)
        self.assertEqual(_parse(chart).offset, 0.0)

    def test_missing_bpm_is_still_an_error(self) -> None:
        chart = "TITLE:test\nWAVE:test.ogg\nOFFSET:0\nCOURSE:Oni\n#START\n1111,\n#END\n"
        with self.assertRaises(ValueError):
            _parse(chart)


if __name__ == "__main__":
    unittest.main()
