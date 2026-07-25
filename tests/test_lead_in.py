"""Lead-in padding: charts that start immediately get silence prepended."""
from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

# tja2fumen imports itself as a top-level package (same as app/converter.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from app.converter import (_first_note_ms, _lead_in_ms,  # noqa: E402
                           _shift_fumen)
from app.config import settings  # noqa: E402
from tja2fumen.classes import (FumenCourse, FumenMeasure,  # noqa: E402
                               FumenNote)


def _fumen(*notes_at_ms: float, bpm: float = 120.0) -> FumenCourse:
    """One measure per note, each note placed at an absolute ms position."""
    fumen = FumenCourse(header=None)  # type: ignore[arg-type]
    lead = 4 * 60_000.0 / bpm
    for at in notes_at_ms:
        measure = FumenMeasure(bpm=bpm, offset_start=at - lead,
                               offset_end=at - lead + 100.0, duration=100.0)
        measure.branches["normal"].notes.append(FumenNote(note_type="Don"))
        measure.branches["normal"].length = 1
        fumen.measures.append(measure)
    return fumen


class LeadInTests(unittest.TestCase):
    def test_first_note_time_matches_the_tja_offset_convention(self) -> None:
        self.assertAlmostEqual(_first_note_ms(_fumen(427.0)), 427.0)

    def test_early_chart_is_padded_up_to_the_target(self) -> None:
        with patch.object(settings, "min_lead_in_ms", 2000):
            fumen = _fumen(427.0, 1000.0)
            lead = _lead_in_ms([fumen])
            self.assertEqual(lead, 1573)
            _shift_fumen(fumen, lead)
            self.assertAlmostEqual(_first_note_ms(fumen), 2000.0)
            # The rest of the chart moves by exactly the same amount.
            self.assertAlmostEqual(fumen.measures[1].offset_start
                                   - fumen.measures[0].offset_start, 573.0)

    def test_late_chart_is_left_alone(self) -> None:
        with patch.object(settings, "min_lead_in_ms", 2000):
            self.assertEqual(_lead_in_ms([_fumen(6910.0)]), 0)

    def test_all_courses_share_one_lead_in(self) -> None:
        # The audio is shared, so the earliest note of any course decides.
        with patch.object(settings, "min_lead_in_ms", 2000):
            self.assertEqual(_lead_in_ms([_fumen(5000.0), _fumen(500.0)]), 1500)

    def test_default_target_is_in_the_range_the_game_itself_uses(self) -> None:
        # Measured over 4252 official solo charts: earliest first note 1122 ms,
        # p5 1491 ms, median 2216 ms. Padding past the median would feel worse
        # than the official charts, padding under the minimum defeats the fix.
        self.assertGreaterEqual(settings.min_lead_in_ms, 1122)
        self.assertLessEqual(settings.min_lead_in_ms, 2216)

    def test_disabled_and_empty_inputs(self) -> None:
        with patch.object(settings, "min_lead_in_ms", 0):
            self.assertEqual(_lead_in_ms([_fumen(0.0)]), 0)
        with patch.object(settings, "min_lead_in_ms", 2000):
            self.assertEqual(_lead_in_ms([]), 0)
            self.assertEqual(_lead_in_ms([_fumen()]), 0)


class RealChartTests(unittest.TestCase):
    def test_cinderella_tja(self) -> None:
        tja = (settings.tja_root / "Vocaloid" / "Cinderella" / "Cinderella.tja")
        if not tja.is_file():
            self.skipTest("Cinderella.tja is not in the library")
        from tja2fumen.parsers import parse_tja
        from tja2fumen.converters import convert_tja_to_fumen
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            courses = parse_tja(str(tja)).courses
            fumens = [convert_tja_to_fumen(course)
                      for course in courses.values()]
        # 'OFFSET:-0.427' -> the first note lands 427 ms into the audio.
        self.assertAlmostEqual(min(_first_note_ms(f) for f in fumens),
                               427.0, delta=1.0)
        with patch.object(settings, "min_lead_in_ms", 2000):
            self.assertEqual(_lead_in_ms(fumens), 1573)


if __name__ == "__main__":
    unittest.main()
