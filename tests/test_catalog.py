from __future__ import annotations

import unittest
from unittest.mock import patch

from app import catalog


class CatalogLibraryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
