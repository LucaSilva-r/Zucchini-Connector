import asyncio
import unittest
from unittest.mock import patch

from app import debug_tunnel


class DebugTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_cabinet_without_wss(self) -> None:
        tunnel = debug_tunnel.DebugTunnel()
        with patch.object(debug_tunnel.agents.hub, "stream_online", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "active WSS agent"):
                await tunnel.configure("cabinet", True)

    async def test_bridges_one_local_client_to_agent_frames(self) -> None:
        tunnel = debug_tunnel.DebugTunnel()
        opened = asyncio.Event()
        forwarded = asyncio.Event()
        text_frames: list[str] = []
        byte_frames: list[bytes] = []

        def send_text(_cabinet_id: str, payload: str) -> bool:
            text_frames.append(payload)
            if payload == "debug-open\n":
                opened.set()
            return True

        def send_bytes(_cabinet_id: str, payload: bytes) -> bool:
            byte_frames.append(payload)
            forwarded.set()
            return True

        with (
            patch.object(debug_tunnel.settings, "debug_host", "127.0.0.1"),
            patch.object(debug_tunnel.settings, "debug_port", 0),
            patch.object(debug_tunnel.agents.hub, "stream_online", return_value=True),
            patch.object(debug_tunnel.agents.hub, "send_text", side_effect=send_text),
            patch.object(debug_tunnel.agents.hub, "send_bytes", side_effect=send_bytes),
        ):
            await tunnel.configure("cabinet", True)
            self.assertIsNotNone(tunnel._server)
            port = tunnel._server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(opened.wait(), timeout=1)

            writer.write(b"to-console")
            await writer.drain()
            await asyncio.wait_for(forwarded.wait(), timeout=1)
            self.assertEqual(byte_frames, [b"to-console"])

            await tunnel.from_agent("cabinet", b"to-prodg")
            self.assertEqual(await asyncio.wait_for(reader.readexactly(8), 1), b"to-prodg")

            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0)
            await tunnel.configure("cabinet", False)

        self.assertEqual(text_frames[0], "debug-open\n")
        self.assertIn("debug-close\n", text_frames)

