"""The cabinet no longer polls over HTTP in steady state.

Its full heartbeat (identity, song inventory, config body) now arrives as an
`H\\n` frame on the control socket, and compact `T\\n` telemetry frames must
still not clobber the inventory that heartbeat established.

Drives ControlHub directly with a stub socket: the real TestClient needs httpx,
which is not a dependency of this project.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi import WebSocketDisconnect

from app import cabinets
from app.config import settings
from app.control import MAX_HEARTBEAT_BYTES, ControlHub


CABINET_ID = "ab12cd34"

HEARTBEAT = (
    "H\n"
    f"id={CABINET_ID}\n"
    "serial=268410000000\n"
    "name=Front Left\n"
    "game=S111\n"
    "version=1.5.0\n"
    "seq=0\n"
    "have tja_x1\n"
    "have tja_x2\n"
    "have_count=2\n"
    "\n"
    "[network]\nconnector_host = 10.0.0.2\n"
)

TELEMETRY = (
    "T\n"
    f"id={CABINET_ID}\n"
    "op_phase=downloading\nop_done=1\nop_total=2\nop_failed=0\n"
    "op_song=tja_x2\nop_error=\n"
    "\n"
)


PACKAGES = (
    "P\n"
    f"id={CABINET_ID}\n"
    "pkg tja_x1 " + "a" * 40 + " installed\n"
)


class StubSocket:
    """Feeds a fixed script of frames, then disconnects.

    With `hold=True` it stays connected once the script drains, until the test
    calls `release()` — needed to observe anything the connector sends the
    cabinet after registration.
    """

    def __init__(self, incoming: list[str], hold: bool = False) -> None:
        self.headers: dict[str, str] = {}
        self.incoming = list(incoming)
        self.sent: list[str] = []
        self.sent_json: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.hold = hold
        self.released = asyncio.Event()

    def release(self) -> None:
        self.released.set()

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        if not self.incoming:
            if self.hold:
                await self.released.wait()
            raise WebSocketDisconnect(1000)
        return self.incoming.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)
        self.incoming.clear()


def run(incoming: list[str]) -> StubSocket:
    socket = StubSocket(incoming)
    asyncio.run(ControlHub().cabinet(socket, CABINET_ID))
    return socket


class ControlHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_token = settings.api_token
        settings.api_token = ""  # no Authorization header required
        settings.cabinets_root.mkdir(parents=True, exist_ok=True)
        for path in settings.cabinets_root.glob("*.json"):
            path.unlink()

    def tearDown(self) -> None:
        settings.api_token = self.old_token

    def test_heartbeat_frame_registers_inventory_and_config(self) -> None:
        socket = run([HEARTBEAT])
        self.assertEqual(socket.sent[0], "READY\n")
        self.assertIsNone(socket.closed)

        cab = cabinets.load(CABINET_ID)
        self.assertEqual(cab["have"], ["tja_x1", "tja_x2"])
        self.assertEqual(cab["game_name"], "Green")
        self.assertIn("[network]", cab["reported_cfg"])
        self.assertGreater(cab["last_seen"], 0)

    def test_telemetry_frame_does_not_clear_inventory(self) -> None:
        run([HEARTBEAT, TELEMETRY])
        cab = cabinets.load(CABINET_ID)
        self.assertEqual(cab["have"], ["tja_x1", "tja_x2"])
        self.assertEqual(cab["operation_phase"], "downloading")

    def test_package_frame_does_not_clear_inventory(self) -> None:
        """`P` carries advisory package state only; `have` stays the heartbeat's."""
        run([HEARTBEAT, PACKAGES])
        cab = cabinets.load(CABINET_ID)
        self.assertEqual(cab["have"], ["tja_x1", "tja_x2"])
        self.assertEqual(cab["package_states"]["tja_x1"]["state"], "installed")

    def test_truncated_inventory_is_rejected(self) -> None:
        """A `have` list shorter than its own have_count must not be trusted."""
        run([HEARTBEAT])
        truncated = (
            "H\n"
            f"id={CABINET_ID}\n"
            "have tja_x1\n"
            "have_count=2\n"
            "\n"
        )
        run([truncated])
        cab = cabinets.load(CABINET_ID)
        self.assertEqual(cab["have"], ["tja_x1", "tja_x2"])

    def test_complete_inventory_with_matching_count_is_applied(self) -> None:
        run([HEARTBEAT])
        replacement = (
            "H\n"
            f"id={CABINET_ID}\n"
            "have tja_x3\n"
            "have_count=1\n"
            "\n"
        )
        run([replacement])
        self.assertEqual(cabinets.load(CABINET_ID)["have"], ["tja_x3"])

    def test_heartbeat_id_must_match_the_connection(self) -> None:
        socket = run([HEARTBEAT.replace(f"id={CABINET_ID}", "id=deadbeef")])
        self.assertIsNotNone(socket.closed)
        self.assertEqual(socket.closed[0], 1008)
        self.assertIsNone(cabinets.load(CABINET_ID))

    def test_oversized_heartbeat_is_rejected(self) -> None:
        padding = "have tja_pad\n"
        oversized = HEARTBEAT + padding * (MAX_HEARTBEAT_BYTES // len(padding) + 1)
        self.assertGreater(len(oversized.encode()), MAX_HEARTBEAT_BYTES)
        socket = run([oversized])
        self.assertIsNotNone(socket.closed)
        self.assertEqual(socket.closed[0], 1009)

    def test_telemetry_keeps_the_small_size_limit(self) -> None:
        """A `T\\n` frame must not inherit the heartbeat's 256 KiB budget."""
        socket = run([TELEMETRY + "x" * 8192])
        self.assertIsNotNone(socket.closed)
        self.assertEqual(socket.closed[0], 1009)

    def test_inventory_request_is_sent_to_a_connected_cabinet(self) -> None:
        hub = ControlHub()
        socket = StubSocket([HEARTBEAT], hold=True)

        async def scenario() -> None:
            task = asyncio.ensure_future(hub.cabinet(socket, CABINET_ID))
            # Let the cabinet register before asking it for anything.
            for _ in range(50):
                await asyncio.sleep(0)
                if hub.status(CABINET_ID)["control_online"]:
                    break
            self.assertTrue(await hub.request_inventory(CABINET_ID))
            socket.release()
            await task

        asyncio.run(scenario())
        self.assertIn("R\n", socket.sent)

    def test_reconnect_teardown_does_not_mark_the_cabinet_offline(self) -> None:
        """The old socket finishes after the new one registered; it must not
        announce offline, or the operator view sticks at "Cabinet offline"."""
        hub = ControlHub()
        operator = StubSocket([], hold=True)
        first = StubSocket([HEARTBEAT], hold=True)
        second = StubSocket([HEARTBEAT], hold=True)

        async def settle() -> None:
            for _ in range(50):
                await asyncio.sleep(0)

        async def scenario() -> None:
            hub._operators[CABINET_ID] = operator  # stand in for a control page
            task_a = asyncio.ensure_future(hub.cabinet(first, CABINET_ID))
            await settle()
            # Reconnect: the new socket takes the slot, then the old one ends.
            task_b = asyncio.ensure_future(hub.cabinet(second, CABINET_ID))
            await settle()
            first.release()
            await task_a
            await settle()
            self.assertTrue(hub.status(CABINET_ID)["control_online"])
            self.assertEqual(
                [msg["online"] for msg in operator.sent_json], [True, True]
            )
            second.release()
            await task_b
            self.assertFalse(hub.status(CABINET_ID)["control_online"])
            self.assertEqual(operator.sent_json[-1]["online"], False)

        asyncio.run(scenario())

    def test_inventory_request_to_an_offline_cabinet_is_a_no_op(self) -> None:
        hub = ControlHub()
        self.assertFalse(asyncio.run(hub.request_inventory("nosuchcab")))

    def test_heartbeat_sized_just_under_the_cap_is_accepted(self) -> None:
        padding = "have tja_pad\n"
        count = (MAX_HEARTBEAT_BYTES - len(HEARTBEAT)) // len(padding) - 1
        body = HEARTBEAT.replace(
            "have_count=2\n", padding * count + f"have_count={count + 2}\n"
        )
        self.assertLess(len(body.encode()), MAX_HEARTBEAT_BYTES)
        socket = run([body])
        self.assertIsNone(socket.closed)
        self.assertEqual(len(cabinets.load(CABINET_ID)["have"]), count + 2)


if __name__ == "__main__":
    unittest.main()
