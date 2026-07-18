from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import catalog
from app.config import settings


class CatalogLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog._LIBRARY_CACHE = None

    def test_library_exposes_tja_and_osu_source(self) -> None:
        categories = [{"id": "Pop", "title": "Pop", "song_count": 2}]
        songs = [
            {
                "id": "tja_one",
                "title": "TJA song",
                "subtitle": "",
                "courses": [],
            },
            {
                "id": "osu_two",
                "title": "osu! song",
                "subtitle": "",
                "source_type": "osz",
                "courses": [],
            },
        ]

        with patch.object(catalog, "categories", return_value=categories), \
             patch.object(catalog, "songs", return_value=songs):
            library = catalog.library()

        self.assertEqual(
            [song["source"] for song in library["songs"]],
            ["tja", "osu"],
        )
        self.assertEqual(
            [song["display_title"] for song in library["songs"]],
            ["TJA song", "osu! song"],
        )
        for song in library["songs"]:
            self.assertEqual(len(song["rev"]), 12)

    def test_rev_tracks_source_hash_and_converter_version(self) -> None:
        tja = {"id": "tja_one", "source_path": "a/b"}
        osz = {"id": "osu_two", "source_path": "c/d", "source_type": "osz"}
        self.assertNotEqual(catalog.source_hash(tja), catalog.source_hash(osz))
        self.assertEqual(catalog.source_hash(osz), catalog.source_hash(dict(osz)))
        with patch.object(catalog.osu, "CONVERTER_VERSION", 9999):
            catalog._source_hash_cached.cache_clear()
            bumped = catalog.source_hash(osz)
        catalog._source_hash_cached.cache_clear()
        self.assertNotEqual(bumped, catalog.source_hash(osz))

    def test_plain_category_names_follow_game_menu_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tja_root = root / "TJA"
            osu_root = root / "OSU"
            (tja_root / "Anime").mkdir(parents=True)
            (tja_root / "Pop").mkdir()
            (osu_root / "Custom").mkdir(parents=True)

            with patch.object(settings, "tja_root", tja_root), patch.object(
                settings, "osu_root", osu_root
            ):
                self.assertEqual(catalog.category_names(), ["Pop", "Anime", "Custom"])

    def test_tja_ids_are_relative_to_the_new_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tja_root = Path(temp) / "TJA"
            song_dir = tja_root / "Anime" / "Example"
            song_dir.mkdir(parents=True)
            tja = song_dir / "example.tja"
            tja.write_text("TITLE:Example\nWAVE:example.ogg\nCOURSE:Oni\nLEVEL:7\n")
            (song_dir / "example.ogg").write_bytes(b"audio")

            with patch.object(settings, "tja_root", tja_root):
                entry = catalog._entry_for_tja(tja, "Anime")

            relative = "Anime/Example/example.tja"
            expected_id = "tja_" + hashlib.sha1(relative.encode()).hexdigest()[:16]
            self.assertIsNotNone(entry)
            self.assertEqual(entry["id"], expected_id)
            self.assertEqual(entry["source_path"], relative)

    def test_osz_files_must_be_direct_children_of_a_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            osu_root = Path(temp) / "OSU"
            direct = osu_root / "Anime" / "song.osz"
            nested = osu_root / "Anime" / "nested" / "song.osz"
            root_file = osu_root / "song.osz"
            nested.parent.mkdir(parents=True)
            direct.touch()
            nested.touch()
            root_file.touch()

            with patch.object(settings, "osu_root", osu_root):
                self.assertEqual(catalog._osu_folder(direct), "Anime")
                self.assertIsNone(catalog._osu_folder(nested))
                self.assertIsNone(catalog._osu_folder(root_file))


if __name__ == "__main__":
    unittest.main()
