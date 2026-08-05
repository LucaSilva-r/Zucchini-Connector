"""Local-only ProDG/DECI3 TCP bridge over an authenticated agent WSS.

Only one cabinet can own Connector port 1000 at a time. In Docker the server
binds the container interface, while Compose publishes it exclusively on host
127.0.0.1; native runs default to binding 127.0.0.1 directly.
"""

from __future__ import annotations

import asyncio

from . import agents
from .config import settings


class DebugTunnel:
    def __init__(self) -> None:
        self._cabinet_id = ""
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, object]:
        return {
            "enabled": self._server is not None,
            "cabinet_id": self._cabinet_id,
            "client_connected": self._writer is not None,
            "host": "127.0.0.1",
            "port": settings.debug_port,
        }

    async def configure(self, cabinet_id: str, enabled: bool) -> dict[str, object]:
        async with self._lock:
            await self._stop_locked()
            if enabled:
                if not agents.hub.stream_online(cabinet_id):
                    raise RuntimeError("Cabinet does not have an active WSS agent")
                self._cabinet_id = cabinet_id
                self._server = await asyncio.start_server(
                    self._accept, settings.debug_host, settings.debug_port
                )
            return self.status()

    async def _stop_locked(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if self._cabinet_id:
            agents.hub.send_text(self._cabinet_id, "debug-close\n")
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        self._cabinet_id = ""

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        async with self._lock:
            if self._writer is not None or not agents.hub.send_text(
                self._cabinet_id, "debug-open\n"
            ):
                writer.close()
                await writer.wait_closed()
                return
            self._writer = writer
            cabinet_id = self._cabinet_id
        try:
            while data := await reader.read(16 * 1024):
                if not agents.hub.send_bytes(cabinet_id, data):
                    break
        finally:
            async with self._lock:
                if self._writer is writer:
                    self._writer = None
                    agents.hub.send_text(cabinet_id, "debug-close\n")
            writer.close()
            await writer.wait_closed()

    async def from_agent(self, cabinet_id: str, data: bytes) -> None:
        writer = self._writer
        if writer is None or cabinet_id != self._cabinet_id:
            return
        writer.write(data)
        try:
            await writer.drain()
        except (ConnectionError, OSError):
            writer.close()

    async def agent_message(self, cabinet_id: str, message: str) -> None:
        if cabinet_id != self._cabinet_id:
            return
        if message.rstrip("\n") not in {"debug-closed", "debug-error"}:
            return
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            await writer.wait_closed()

    async def agent_disconnected(self, cabinet_id: str) -> None:
        if cabinet_id == self._cabinet_id:
            await self.agent_message(cabinet_id, "debug-closed")


tunnel = DebugTunnel()
