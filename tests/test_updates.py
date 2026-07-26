from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app import main, updates
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


class FakeRequest:
    def __init__(self, offset: int, length: int) -> None:
        self.query_params = {"offset": str(offset), "length": str(length)}


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

    async def test_serves_resumable_chunks_to_cabinets(self) -> None:
        """The cabinet fetches the SPRX with the same ranged reader it uses for
        song assets: `?offset=&length=`, driven by X-Asset-Size and verified
        against X-Asset-Sha1."""
        header = bytearray(16)
        header[:4] = b"SCE\0"
        header[8:10] = (0x8000).to_bytes(2, "big")
        body = bytes(header) + bytes(range(64))
        item = await updates.store_upload(
            FakeUpload("zucchini-gex.sprx", body), "0.11.0", "gex"
        )

        first = main.update_asset(str(item["id"]), FakeRequest(offset=0, length=32))
        self.assertEqual(first.status_code, 206)
        self.assertEqual(first.body, body[:32])
        self.assertEqual(first.headers["X-Asset-Size"], str(len(body)))
        self.assertEqual(first.headers["X-Asset-Sha1"], item["id"])

        rest = main.update_asset(str(item["id"]), FakeRequest(offset=32, length=4096))
        self.assertEqual(rest.body, body[32:])
        self.assertEqual(
            rest.headers["Content-Range"], f"bytes 32-{len(body) - 1}/{len(body)}"
        )

        exhausted = main.update_asset(
            str(item["id"]), FakeRequest(offset=len(body), length=4096)
        )
        self.assertEqual(exhausted.status_code, 416)


if __name__ == "__main__":
    unittest.main()
