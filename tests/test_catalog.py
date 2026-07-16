from __future__ import annotations

import unittest
from unittest.mock import patch

from app import catalog


class CatalogLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog._LIBRARY_CACHE = None

    def test_library_exposes_tja_and_osu_source(self) -> None:
        categories = [{"id": "root", "title": "All", "song_count": 2}]
        songs = [
            {
                "id": "ese_one",
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
        tja = {"id": "ese_one", "source_path": "a/b"}
        osz = {"id": "osu_two", "source_path": "c/d", "source_type": "osz"}
        self.assertNotEqual(catalog.source_hash(tja), catalog.source_hash(osz))
        self.assertEqual(catalog.source_hash(osz), catalog.source_hash(dict(osz)))
        with patch.object(catalog.osu, "CONVERTER_VERSION", 9999):
            catalog._source_hash_cached.cache_clear()
            bumped = catalog.source_hash(osz)
        catalog._source_hash_cached.cache_clear()
        self.assertNotEqual(bumped, catalog.source_hash(osz))


if __name__ == "__main__":
    unittest.main()
