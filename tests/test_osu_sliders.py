"""osu! slider conversion: long repeated sliders must stay drum rolls."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# tja2fumen imports itself as a top-level package (same as app/converter.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from app import osu  # noqa: E402


BEAT_MS = 250.0  # 240 BPM


def _events(*hit_objects: str, slider_multiplier: float = 1.4):
    beatmap = "\n".join([
        "osu file format v14",
        "[General]", "Mode: 1",
        "[Difficulty]", f"SliderMultiplier:{slider_multiplier}", "SliderTickRate:1",
        "[TimingPoints]", f"0,{BEAT_MS},4,1,0,100,1,0",
        "[HitObjects]", *hit_objects,
    ]).encode("utf-8")
    parsed = osu.parse_osu(beatmap)
    red = [p for p in parsed.timing_points if p.uninherited and p.beat_length > 0]
    return osu._hit_events(parsed, red)


def _slider(spans: int, pixel_length: float, start: float = 1000.0) -> str:
    return f"43,332,{start:.0f},2,0,L|78:332,{spans},{pixel_length}"


class SliderConversionTests(unittest.TestCase):
    def test_long_roll_written_as_a_repeated_tiny_slider(self) -> None:
        # How charters actually write a drum roll: one short span, repeated.
        # Each span is a fraction of a beat, but the roll lasts ~3.9 beats.
        events = _events(_slider(spans=30, pixel_length=22.75))
        self.assertEqual([event.note_type for event in events], ["Drumroll"])
        self.assertAlmostEqual(events[0].duration, 30 * 22.75 * BEAT_MS / 140.0)

    def test_short_slider_still_becomes_hits(self) -> None:
        # One span, well under two beats: osu!taiko plays this as hits.
        events = _events(_slider(spans=1, pixel_length=70.0))
        self.assertEqual([event.note_type for event in events], ["Don", "Don"])

    def test_two_beat_boundary(self) -> None:
        # 140 px == exactly one beat at SliderMultiplier 1.4.
        self.assertEqual(len(_events(_slider(spans=1, pixel_length=279.0))), 2)
        self.assertEqual([e.note_type for e in _events(_slider(spans=1, pixel_length=281.0))],
                         ["Drumroll"])

    def test_big_roll_keeps_its_finisher_type(self) -> None:
        events = _events(f"43,332,1000,2,4,L|78:332,30,22.75")
        self.assertEqual([event.note_type for event in events], ["DRUMROLL"])

    def test_plain_notes_are_untouched(self) -> None:
        events = _events("100,100,1000,1,0", "100,100,1250,1,8")
        self.assertEqual([event.note_type for event in events], ["Don", "Ka"])


if __name__ == "__main__":
    unittest.main()
