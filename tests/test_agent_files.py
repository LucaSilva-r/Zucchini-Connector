"""File manager: what the agent may be asked for, and what it may be handed.

The write side is the part worth a test. The connector never sends a
destination path — only a kind — including for the agent's own config.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from app import agents, cabinets, main
from app.config import settings


CABINET_ID = "files01"


def _cabinet(cabinet_id: str = CABINET_ID) -> None:
    settings.cabinets_root.mkdir(parents=True, exist_ok=True)
    cabinets._save(dict(cabinets._defaults(), cabinet_id=cabinet_id))


class ConsolePathTests(unittest.TestCase):
    def test_absolute_paths_are_accepted(self) -> None:
        for path in ("/", "/dev_hdd0", "/dev_hdd0/plugins/taiko/zucchini.sprx"):
            self.assertEqual(agents.validate_console_path(path), path)

    def test_traversal_and_junk_are_refused(self) -> None:
        for path in ("", "dev_hdd0", "/dev_hdd0/../dev_flash", "/a\nb", "/" + "x" * 500):
            with self.assertRaises(ValueError):
                agents.validate_console_path(path)

    def test_the_command_escapes_the_path(self) -> None:
        """Spaces are ordinary in PS3 directory names and must survive."""
        self.assertEqual(
            agents.list_command("/dev_hdd0/game/SCEEXE001 GREEN"),
            "ls\t%2Fdev_hdd0%2Fgame%2FSCEEXE001%20GREEN",
        )


class PushTargetTests(unittest.TestCase):
    def test_only_fixed_kinds_exist(self) -> None:
        self.assertEqual(
            set(agents.PUSH_KINDS),
            {"agent", "mod", "config", "agent_config", "firmware"},
        )

    def test_the_agent_config_has_a_fixed_push_target(self) -> None:
        self.assertEqual(agents.push_command("agent_config"), "put\tagent_config")
        for kind in ("agent-cfg", "zucchini_agent.cfg", ""):
            with self.assertRaises(ValueError):
                agents.push_command(kind)

    def test_the_command_carries_a_kind_and_never_a_path(self) -> None:
        self.assertEqual(agents.push_command("mod"), "put\tmod")


class DirReportTests(unittest.TestCase):
    def test_directories_sort_first_and_names_are_unescaped(self) -> None:
        report = agents.parse_dir_report(
            b"version=1\npath=%2Fdev_hdd0\n"
            b"f\tzucchini.sprx\t22990\t1754000000\n"
            b"d\tSCEEXE001%20GREEN\t0\t1754000001\n"
        )
        self.assertEqual(report["path"], "/dev_hdd0")
        self.assertFalse(report["error"])
        self.assertEqual(
            [(e["name"], e["directory"], e["size"]) for e in report["entries"]],
            [("SCEEXE001 GREEN", True, 0), ("zucchini.sprx", False, 22990)],
        )

    def test_an_unreadable_directory_is_reported_not_raised(self) -> None:
        report = agents.parse_dir_report(b"version=1\npath=%2Fnope\nerror=1\n")
        self.assertTrue(report["error"])
        self.assertEqual(report["entries"], [])

    def test_malformed_input_is_refused(self) -> None:
        for body in (b"", b"path=/x\n", b"version=1\nf\tonly\ttwo\n"):
            with self.assertRaises(ValueError):
                agents.parse_dir_report(body)


class FileManagerRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        agents.hub = agents.AgentHub()
        _cabinet()

    async def test_listing_waits_for_the_console_to_answer(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        listing = asyncio.create_task(main.cabinet_fs_list(CABINET_ID, "/dev_hdd0"))
        await asyncio.sleep(0.05)

        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait(CABINET_ID), timeout=1),
            ["ls\t%2Fdev_hdd0"],
        )
        agents.hub.note_result(
            CABINET_ID,
            "ls",
            {"path": "/dev_hdd0", "entries": [], "error": False, "truncated": False},
        )
        self.assertEqual((await listing)["path"], "/dev_hdd0")

    async def test_an_offline_agent_is_a_conflict_not_a_queued_command(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await main.cabinet_fs_list(CABINET_ID, "/dev_hdd0")
        self.assertEqual(raised.exception.status_code, 409)

    async def test_a_stale_reply_cannot_answer_the_next_request(self) -> None:
        """arm() runs before the command is queued, so the slot is empty."""
        agents.hub.note_result(CABINET_ID, "ls", {"path": "/old", "entries": []})
        agents.hub.note_seen(CABINET_ID, "xmb")
        listing = asyncio.create_task(main.cabinet_fs_list(CABINET_ID, "/dev_hdd0"))
        await asyncio.sleep(0.05)
        await asyncio.wait_for(agents.hub.wait(CABINET_ID), timeout=1)

        agents.hub.note_result(
            CABINET_ID, "ls", {"path": "/dev_hdd0", "entries": [], "error": False}
        )
        self.assertEqual((await listing)["path"], "/dev_hdd0")

    async def test_a_silent_console_times_out_rather_than_hanging(self) -> None:
        agents.hub.note_seen(CABINET_ID, "xmb")
        original = main.FS_LIST_TIMEOUT
        main.FS_LIST_TIMEOUT = 0.05
        try:
            with self.assertRaises(HTTPException) as raised:
                await main.cabinet_fs_list(CABINET_ID, "/dev_hdd0")
        finally:
            main.FS_LIST_TIMEOUT = original
        self.assertEqual(raised.exception.status_code, 504)


class PushRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        agents.hub = agents.AgentHub()
        main._firmware_transfer_jobs.clear()
        main._active_firmware_transfers.clear()
        _cabinet("files02")

    async def _push(self, kind: str, body: bytes):
        class _Upload:
            def __init__(self) -> None:
                self._body = body

            async def read(self, size: int) -> bytes:
                chunk, self._body = self._body[:size], self._body[size:]
                return chunk

        agents.hub.note_seen("files02", "xmb")
        if kind == "firmware":
            agents.hub.note_capabilities("files02", "firmware01")
        elif kind == "agent_config":
            agents.hub.note_capabilities("files02", "agentconfig01")
        task = asyncio.create_task(main.cabinet_fs_push("files02", kind, _Upload()))
        await asyncio.sleep(0.05)
        return task

    async def test_an_unsigned_sprx_never_reaches_the_console(self) -> None:
        task = await self._push("agent", b"not a self at all")
        with self.assertRaises(HTTPException) as raised:
            await task
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(agents.hub.online("files02") and agents.hub._pending.get("files02"))

    async def test_firmware_validation_is_left_to_the_console(self) -> None:
        task = await self._push("firmware", b"the updater decides")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("files02"), timeout=1),
            ["put\tfirmware"],
        )
        agents.hub.note_result(
            "files02", "put", {"kind": "firmware", "ok": True, "size": 19}
        )
        self.assertEqual((await task)["status"], "staged")

    async def test_firmware_can_be_uploaded_in_sequential_chunks(self) -> None:
        class _Request:
            headers: dict[str, str] = {}

            def __init__(self, body: bytes) -> None:
                self.body = body

            async def stream(self):
                yield self.body

        agents.hub.note_seen("files02", "xmb")
        agents.hub.note_capabilities("files02", "firmware01")
        upload_id = "0123456789abcdef0123456789abcdef"
        first = await main.cabinet_fs_push_chunk(
            "files02", _Request(b"first "), "firmware", upload_id, 0, 12
        )
        self.assertEqual(first, {"status": "uploading", "kind": "firmware", "bytes": 6})
        self.assertEqual(agents.hub._pending.get("files02"), None)

        final = await main.cabinet_fs_push_chunk(
            "files02", _Request(b"second"), "firmware", upload_id, 6, 12
        )
        self.assertEqual(final["status"], "queued")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("files02"), timeout=1),
            ["put\tfirmware"],
        )
        self.assertEqual(main._push_path("files02", "firmware").read_bytes(), b"first second")

        response = await main.agent_blob("files02", "firmware")
        downloaded = b""
        async for chunk in response.body_iterator:
            downloaded += chunk
        self.assertEqual(downloaded, b"first second")
        progress = await main.cabinet_fs_push_status("files02", upload_id)
        self.assertEqual(progress["status"], "downloading")
        self.assertEqual(progress["downloaded"], 12)

        agents.hub.note_result(
            "files02", "put", {"kind": "firmware", "ok": True, "size": 12}
        )
        await asyncio.gather(*list(main._firmware_transfer_tasks))
        self.assertEqual(
            (await main.cabinet_fs_push_status("files02", upload_id))["status"],
            "staged",
        )

    async def test_firmware_chunk_offset_must_match_assembled_file(self) -> None:
        class _Request:
            headers: dict[str, str] = {}

            async def stream(self):
                yield b"chunk"

        agents.hub.note_seen("files02", "xmb")
        agents.hub.note_capabilities("files02", "firmware01")
        upload_id = "abcdef0123456789abcdef0123456789"
        await main.cabinet_fs_push_chunk(
            "files02", _Request(), "firmware", upload_id, 0, 10
        )
        with self.assertRaises(HTTPException) as raised:
            await main.cabinet_fs_push_chunk(
                "files02", _Request(), "firmware", upload_id, 4, 10
            )
        self.assertEqual(raised.exception.status_code, 409)

    async def test_old_agent_cannot_receive_the_firmware_command(self) -> None:
        agents.hub.note_seen("files02", "xmb")

        class _Upload:
            async def read(self, _size: int) -> bytes:
                return b""

        with self.assertRaises(HTTPException) as raised:
            await main.cabinet_fs_push("files02", "firmware", _Upload())
        self.assertEqual(raised.exception.status_code, 409)

    async def test_old_agent_cannot_receive_its_config(self) -> None:
        agents.hub.note_seen("files02", "xmb")

        class _Upload:
            async def read(self, _size: int) -> bytes:
                return b"connector_host = 10.0.0.2\n"

        with self.assertRaises(HTTPException) as raised:
            await main.cabinet_fs_push("files02", "agent_config", _Upload())
        self.assertEqual(raised.exception.status_code, 409)

    async def test_a_signed_sprx_is_staged_and_the_console_told_to_take_it(self) -> None:
        task = await self._push("mod", b"SCE\0" + b"\x00" * 64)
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("files02"), timeout=1), ["put\tmod"]
        )
        self.assertTrue(main._push_path("files02", "mod").is_file())

        agents.hub.note_result("files02", "put", {"kind": "mod", "ok": True, "size": 68})
        self.assertEqual((await task)["status"], "installed")

    async def test_the_config_kind_needs_no_sce_header(self) -> None:
        task = await self._push("config", b"[network]\nconnector_host = 10.0.0.2\n")
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("files02"), timeout=1), ["put\tconfig"]
        )
        agents.hub.note_result("files02", "put", {"kind": "config", "ok": True, "size": 34})
        self.assertEqual((await task)["status"], "installed")

    async def test_the_agent_config_kind_needs_no_sce_header(self) -> None:
        body = b"connector_host = 10.0.0.2\nagent_token = replacement\n"
        task = await self._push("agent_config", body)
        self.assertEqual(
            await asyncio.wait_for(agents.hub.wait("files02"), timeout=1),
            ["put\tagent_config"],
        )
        agents.hub.note_result(
            "files02", "put", {"kind": "agent_config", "ok": True, "size": len(body)}
        )
        self.assertEqual((await task)["status"], "installed")

    async def test_a_console_that_refuses_the_file_is_not_reported_as_installed(self) -> None:
        task = await self._push("mod", b"SCE\0" + b"\x00" * 64)
        await asyncio.wait_for(agents.hub.wait("files02"), timeout=1)
        agents.hub.note_result("files02", "put", {"kind": "mod", "ok": False, "size": 0})
        with self.assertRaises(HTTPException) as raised:
            await task
        self.assertEqual(raised.exception.status_code, 502)

    async def test_unknown_config_alias_cannot_be_pushed(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await (await self._push("agent-cfg", b"anything"))
        self.assertEqual(raised.exception.status_code, 400)


class AgentWireTests(unittest.IsolatedAsyncioTestCase):
    """The endpoints the PS3 actually calls, with the URLs it actually builds.

    The agent is C with no test harness of its own, so the contract is pinned
    from this side: a renamed query parameter here is a silent failure there.
    """

    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        agents.hub = agents.AgentHub()
        _cabinet("wire01")
        app = FastAPI()
        app.include_router(main.agent_api, prefix="/api/agent")
        self.client = TestClient(app)
        self.auth = {"Authorization": f"Bearer {settings.agent_token}"}

    def test_dir_post_lands_as_a_listing_result(self) -> None:
        response = self.client.post(
            "/api/agent/dir?id=wire01",
            headers=self.auth,
            content=b"version=1\npath=%2Fdev_hdd0\nd\tgame\t0\t0\n",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["entries"], 1)

    def test_file_post_stores_the_body_and_error_reports_a_failure(self) -> None:
        response = self.client.post(
            "/api/agent/file?id=wire01&path=%2Fdev_hdd0%2Ftmp%2Fx.log",
            headers=self.auth,
            content=b"hello console",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(main._pull_path("wire01").read_bytes(), b"hello console")

        self.client.post(
            "/api/agent/file?id=wire01&path=%2Fnope&error=1", headers=self.auth
        )
        self.assertTrue(agents.hub._results[("wire01", "get")]["error"])

    def test_blob_is_served_by_kind_and_the_result_is_recorded(self) -> None:
        staged = main._push_path("wire01", "mod")
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(b"SCE\0payload")

        blob = self.client.get("/api/agent/blob?id=wire01&kind=mod", headers=self.auth)
        self.assertEqual(blob.status_code, 200)
        self.assertEqual(blob.content, b"SCE\0payload")
        # The agent needs a length to stream against; it refuses a reply without one.
        self.assertEqual(blob.headers["content-length"], "11")

        self.client.post(
            "/api/agent/blob?id=wire01&kind=mod&ok=1&size=11", headers=self.auth
        )
        self.assertEqual(
            agents.hub._results[("wire01", "put")],
            {"kind": "mod", "ok": True, "size": 11},
        )

    def test_agent_config_blob_is_served_only_by_its_kind(self) -> None:
        staged = main._push_path("wire01", "agent_config")
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(b"connector_host = 10.0.0.2\n")
        response = self.client.get(
            "/api/agent/blob?id=wire01&kind=agent_config", headers=self.auth
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"connector_host = 10.0.0.2\n")

        for kind in ("cfg", "../zucchini_agent.cfg"):
            response = self.client.get(
                f"/api/agent/blob?id=wire01&kind={kind}", headers=self.auth
            )
            self.assertIn(response.status_code, (400, 404))


if __name__ == "__main__":
    unittest.main()
