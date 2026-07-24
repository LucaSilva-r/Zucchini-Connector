from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CONNECTOR_UPDATES_ROOT", tempfile.mkdtemp())

from app import updates
from app.config import settings


class FakeUpload:
    def __init__(self, name: str, body: bytes) -> None:
        self.filename = name
        self._body = body
        self._read = False

    async def read(self, _size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        return self._body


class UpdateStorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        settings.updates_root = Path(tempfile.mkdtemp())
        settings.updates_root.mkdir(parents=True, exist_ok=True)
        for path in settings.updates_root.iterdir():
            if path.is_file():
                path.unlink()

    async def test_stores_gex_update_by_content_hash(self) -> None:
        header = bytearray(16)
        header[:4] = b"SCE\0"
        header[8:10] = (0x8000).to_bytes(2, "big")
        body = bytes(header) + b"payload"
        item = await updates.store_upload(
            FakeUpload("zucchini-gex.sprx", body), "0.11.0", "gex",
            "Adds remote rollback support",
        )
        self.assertEqual(item["flavor"], "gex")
        self.assertEqual(Path(updates.artifact(item["id"])["path"]).read_bytes(), body)
        history = updates.list_artifacts()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["version"], "0.11.0")
        self.assertEqual(history[0]["note"], "Adds remote rollback support")

    async def test_rejects_wrong_self_flavor_even_if_renamed(self) -> None:
        header = bytearray(16)
        header[:4] = b"SCE\0"
        header[8:10] = (0x0004).to_bytes(2, "big")
        with self.assertRaisesRegex(ValueError, "signed for HEN"):
            await updates.store_upload(
                FakeUpload("zucchini.sprx", bytes(header)), "0.11.0", "gex"
            )

    async def test_rejects_non_self_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing SCE header"):
            await updates.store_upload(
                FakeUpload("zucchini.sprx", b"not a self"), "0.11.0", "gex"
            )


if __name__ == "__main__":
    unittest.main()
