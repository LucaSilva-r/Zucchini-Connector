"""Batch conversion of the whole library."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# tja2fumen imports itself as a top-level package (same as app/converter.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from app import converter  # noqa: E402


LIBRARY = {"songs": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}


class ConvertAllTests(unittest.TestCase):
    def test_queues_every_song(self) -> None:
        with patch.object(converter.catalog, "library", return_value=LIBRARY), \
             patch.object(converter, "enqueue_many") as enqueue_many:
            converter.convert_all()
        enqueue_many.assert_called_once_with(["a", "b", "c"])

    def test_broken_songs_are_left_alone_by_default(self) -> None:
        with patch.object(converter.catalog, "library", return_value=LIBRARY), \
             patch.object(converter, "enqueue_many"), \
             patch.object(converter, "prepare") as prepare, \
             patch.object(converter, "status_for", return_value={"status": "failed"}):
            converter.convert_all()
        prepare.assert_not_called()

    def test_broken_songs_are_retried_on_request(self) -> None:
        statuses = {"a": "failed", "b": "ready", "c": "failed"}
        with patch.object(converter.catalog, "library", return_value=LIBRARY), \
             patch.object(converter, "enqueue_many"), \
             patch.object(converter, "prepare") as prepare, \
             patch.object(converter, "status_for",
                          side_effect=lambda song_id: {"status": statuses[song_id]}):
            converter.convert_all(include_failed=True)
        self.assertEqual([call.args[0] for call in prepare.call_args_list], ["a", "c"])
        for call in prepare.call_args_list:
            self.assertTrue(call.kwargs["retry"])


class ReconvertManyTests(unittest.TestCase):
    def test_forces_a_rebuild_of_each_song(self) -> None:
        # retry() rebuilds even a song whose package is current, which is what
        # makes this different from convert_all().
        with patch.object(converter, "retry", return_value={"status": "queued"}) as retry:
            result = converter.reconvert_many(["a", "b"])
        self.assertEqual([call.args[0] for call in retry.call_args_list], ["a", "b"])
        self.assertEqual(result["scheduled"], 2)

    def test_unknown_songs_are_counted_not_raised(self) -> None:
        with patch.object(converter, "retry", return_value={"status": "not_found"}):
            result = converter.reconvert_many(["gone"])
        self.assertEqual((result["scheduled"], result["not_found"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
