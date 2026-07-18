from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app import osu
from tja2fumen.parsers import parse_fumen
from tja2fumen.writers import write_fumen


def beatmap(mode: int = 1) -> bytes:
    objects = "\n".join(
        f"256,192,{1000 + index * 250},1,{8 if index % 2 else 0},0:0:0:0:"
        for index in range(32)
    )
    return f"""osu file format v14

[General]
AudioFilename: song.ogg
Mode: {mode}

[Metadata]
Title:Example
Artist:Artist
Creator:Mapper
Version:Mapper's Oni

[Difficulty]
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
1000,500,4,1,0,100,1,0

[HitObjects]
{objects}
""".encode()


class OsuTests(unittest.TestCase):
    def test_parse_native_taiko_metadata(self) -> None:
        parsed = osu.parse_osu(beatmap())
        self.assertEqual(parsed.mode, 1)
        self.assertEqual(parsed.audio_filename, "song.ogg")
        self.assertEqual(parsed.version, "Mapper's Oni")
        self.assertEqual(len(parsed.hit_objects), 32)

    def test_osz_ignores_standard_charts_and_fills_required_courses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "example.osz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("song.ogg", b"test audio placeholder")
                archive.writestr("standard.osu", beatmap(mode=0))
                archive.writestr("taiko.osu", beatmap(mode=1))
            meta = osu.inspect_osz(archive_path)

        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["title"], "Example")
        self.assertEqual(meta["display_title"], "Example")
        self.assertEqual(
            [course["id"] for course in meta["courses"]],
            ["e", "n", "h", "m"],
        )
        self.assertEqual(
            {course["osu_member"] for course in meta["courses"]},
            {"taiko.osu"},
        )

    def test_osz_prefers_romanized_title_over_title_unicode(self) -> None:
        raw = beatmap().replace(
            b"Title:Example\n",
            "Title:Example\nTitleUnicode:\u4f8b\u306e\u66f2\n".encode(),
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "unicode-title.osz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("song.ogg", b"test audio placeholder")
                archive.writestr("taiko.osu", raw)
            meta = osu.inspect_osz(archive_path)

        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["title"], "Example")
        self.assertEqual(meta["display_title"], "Example")

    def test_explicit_course_name_guides_single_chart(self) -> None:
        chart = osu.OsuChart(
            member="oni.osu",
            audio_member="song.ogg",
            title="Example",
            artist="Artist",
            creator="Mapper",
            version="Taiko Oni",
            osu_stars=3.9,
            level=6,
            course_hint=3,
        )
        selected = osu._select_charts([chart])
        self.assertEqual(
            [(slot, item.member) for slot, item in selected],
            [(3, "oni.osu")],
        )

    def test_more_than_five_charts_selects_five_ordered_slots(self) -> None:
        charts = [
            osu.OsuChart(
                member=f"{level}.osu",
                audio_member="song.ogg",
                title="Example",
                artist="Artist",
                creator="Mapper",
                version=f"Custom {level}",
                osu_stars=level / 1.5,
                level=level,
            )
            for level in range(1, 9)
        ]
        selected = osu._select_charts(charts)
        self.assertEqual(len(selected), 5)
        self.assertEqual([slot for slot, _ in selected], [0, 1, 2, 3, 4])
        self.assertEqual(
            sorted(item.level for _, item in selected),
            [item.level for _, item in selected],
        )

    def test_missing_required_courses_reuse_available_chart(self) -> None:
        oni = osu.OsuChart(
            member="oni.osu",
            audio_member="song.ogg",
            title="Example",
            artist="Artist",
            creator="Mapper",
            version="Oni",
            osu_stars=3.9,
            level=6,
            course_hint=3,
        )
        filled = osu._fill_required_courses([(3, oni)])
        self.assertEqual([slot for slot, _ in filled], [0, 1, 2, 3])
        self.assertTrue(all(chart is oni for _, chart in filled))

    def test_ura_requires_a_fifth_chart_or_explicit_name(self) -> None:
        custom = osu.OsuChart(
            member="custom.osu",
            audio_member="song.ogg",
            title="Example",
            artist="Artist",
            creator="Mapper",
            version="Custom",
            osu_stars=7,
            level=10,
        )
        self.assertEqual(osu._select_charts([custom])[0][0], 3)

        ura = osu.OsuChart(
            **{
                **custom.__dict__,
                "member": "ura.osu",
                "version": "Ura",
                "course_hint": 4,
            }
        )
        self.assertEqual(osu._select_charts([ura])[0][0], 4)

    def test_fumen_preserves_circle_count_and_timing(self) -> None:
        fumen = osu.fumen_from_osu(beatmap(), "Oni", 6)
        notes = [
            note
            for measure in fumen.measures
            for note in measure.branches["normal"].notes
        ]
        self.assertEqual(fumen.header.order, ">")
        self.assertEqual(len(notes), 32)
        # 120 BPM has a 2000 ms 4/4 lead measure. Fumen stores the logical
        # 1000 ms osu! boundary minus that lead; PS3 adds it at playback.
        self.assertEqual(fumen.measures[0].offset_start, -1000)
        self.assertEqual(notes[0].pos, 0)
        effective_first_note = (
            fumen.measures[0].offset_start
            + 4 * 60_000 / fumen.measures[0].bpm
            + notes[0].pos
        )
        self.assertEqual(effective_first_note, 1000)
        self.assertEqual({note.note_type.lower()[:2] for note in notes}, {"do", "ka"})

    def test_fumen_assigns_nonzero_shinuchi_score_to_hit_notes(self) -> None:
        fumen = osu.fumen_from_osu(beatmap(), "Oni", 6)
        notes = [
            note
            for measure in fumen.measures
            for note in measure.branches["normal"].notes
        ]

        self.assertEqual(fumen.score_init, 31_250)
        self.assertEqual(fumen.score_diff, 0)
        self.assertEqual({note.score_init for note in notes}, {31_250})
        self.assertEqual({note.score_diff for note in notes}, {0})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "score.bin"
            write_fumen(str(path), fumen)
            serialized = parse_fumen(str(path))
        serialized_notes = [
            note
            for measure in serialized.measures
            for note in measure.branches["normal"].notes
        ]
        self.assertEqual({note.score_init for note in serialized_notes}, {31_250})
        self.assertEqual({note.score_diff for note in serialized_notes}, {0})

    def test_dense_inherited_timing_points_stay_within_game_limit(self) -> None:
        dense_points = "\n".join(
            f"{1001 + index * 5},{-100 + (index % 50)},4,1,0,100,0,0"
            for index in range(1000)
        )
        raw = beatmap().replace(
            b"1000,500,4,1,0,100,1,0",
            ("1000,500,4,1,0,100,1,0\n" + dense_points).encode(),
        )

        fumen = osu.fumen_from_osu(raw, "Oni", 6)

        self.assertLessEqual(len(fumen.measures), osu.MAX_FUMEN_MEASURES)
        self.assertEqual(fumen.header.b512_b515_number_of_measures, 300)

    def test_long_chart_thins_natural_barlines_to_game_limit(self) -> None:
        objects = "\n".join(
            f"256,192,{1000 + index * 2000},1,0,0:0:0:0:"
            for index in range(302)
        )
        raw = beatmap().split(b"[HitObjects]\n", 1)[0] + (
            "[HitObjects]\n" + objects + "\n"
        ).encode()

        fumen = osu.fumen_from_osu(raw, "Oni", 6)

        self.assertEqual(len(fumen.measures), osu.MAX_FUMEN_MEASURES)
        notes = [
            note
            for measure in fumen.measures
            for note in measure.branches["normal"].notes
        ]
        self.assertEqual(len(notes), 302)
        effective_times = sorted(
            measure.offset_start
            + 4 * 60_000 / measure.bpm
            + note.pos
            for measure in fumen.measures
            for note in measure.branches["normal"].notes
        )
        self.assertEqual(
            effective_times,
            [1000 + index * 2000 for index in range(302)],
        )

    def test_too_many_bpm_sections_are_rejected(self) -> None:
        red_points = "\n".join(
            f"{1000 + index * 10},{500 + index % 2},4,1,0,100,1,0"
            for index in range(302)
        )
        raw = beatmap().replace(
            b"1000,500,4,1,0,100,1,0",
            red_points.encode(),
        )

        with self.assertRaisesRegex(ValueError, "BPM/kiai timing sections"):
            osu.fumen_from_osu(raw, "Oni", 6)


if __name__ == "__main__":
    unittest.main()
