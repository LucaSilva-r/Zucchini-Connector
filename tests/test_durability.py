from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import converter, database
from app.config import settings


class PackageIntegrityTests(unittest.TestCase):
    def test_corrupted_cached_asset_is_not_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            song_root = root / "song_one"
            package = song_root / "package"
            package.mkdir(parents=True)
            asset = package / "audio.nub"
            asset.write_bytes(b"good")
            digest = hashlib.sha1(b"good").hexdigest()
            (song_root / "manifest.json").write_text(json.dumps({
                "assets": [{
                    "name": "audio.nub",
                    "size": 4,
                    "sha1": digest,
                }],
                "courses": [],
            }))

            with patch.object(settings, "convert_root", root):
                converter._asset_matches.cache_clear()
                self.assertIsNotNone(converter.asset("song_one", "audio.nub"))
                asset.write_bytes(b"evil")
                self.assertIsNone(converter.asset("song_one", "audio.nub"))


class DurableJobTests(unittest.TestCase):
    def test_retry_deadline_survives_a_new_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "connector.db"
            with patch.object(settings, "database_path", path):
                database._initialized = False
                database.initialize()
                database.record_job(
                    "song_one",
                    "a" * 40,
                    "retrying",
                    attempt_delta=1,
                    next_retry_at=100,
                )
                self.assertEqual(database.due_jobs(now=99), [])
                self.assertEqual(database.due_jobs(now=100), ["song_one"])
                self.assertEqual(database.job_attempt_count("song_one"), 1)
            database._initialized = False


if __name__ == "__main__":
    unittest.main()
