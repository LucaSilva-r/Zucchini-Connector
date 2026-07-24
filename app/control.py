"""Live cabinet remote-control relay.

The connector never interprets game state. It validates browser messages and
relays one compact, authoritative input bitmask to the cabinet. The cabinet
owns edge generation and a dead-man timeout, so disconnects cannot leave a
button held.
"""

from __future__ import annotations

import asyncio
import json
import hmac
from threading import Lock

from fastapi import WebSocket, WebSocketDisconnect

from . import auth, cabinets
from .config import settings


BUTTON_BITS = {
    "hit_side_left": 0,
    "hit_center_left": 1,
    "hit_center_right": 2,
    "hit_side_right": 3,
    "enter": 4,
    "service": 5,
    "test": 6,
    "coin": 7,
    "up": 8,
    "down": 9,
    "p2_hit_side_left": 10,
    "p2_hit_center_left": 11,
    "p2_hit_center_right": 12,
    "p2_hit_side_right": 13,
}
BUTTON_MASK = (1 << len(BUTTON_BITS)) - 1
MAX_MESSAGE_BYTES = 4096


def _token_ok(candidate: str) -> bool:
    expected = settings.api_token
    return not expected or hmac.compare_digest(candidate, expected)


class ControlHub:
    def __init__(self) -> None:
        self._lock = Lock()
        self._cabinets: dict[str, WebSocket] = {}
        self._operators: dict[str, WebSocket] = {}
        self._viewers: dict[str, set[WebSocket]] = {}

    def status(self, cabinet_id: str) -> dict[str, bool]:
        with self._lock:
            return {
                "control_online": cabinet_id in self._cabinets,
                "control_operator": cabinet_id in self._operators,
            }

    def decorate(self, cabinet: dict) -> dict:
        cabinet_id = str(cabinet.get("cabinet_id") or "")
        return {**cabinet, **self.status(cabinet_id)}

    async def _replace(
        self, table: dict[str, WebSocket], cabinet_id: str, websocket: WebSocket
    ) -> WebSocket | None:
        with self._lock:
            previous = table.get(cabinet_id)
            table[cabinet_id] = websocket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1012, reason="Connection replaced")
            except RuntimeError:
                pass
        return previous

    def _remove(
        self, table: dict[str, WebSocket], cabinet_id: str, websocket: WebSocket
    ) -> None:
        with self._lock:
            if table.get(cabinet_id) is websocket:
                table.pop(cabinet_id, None)

    def _get(self, table: dict[str, WebSocket], cabinet_id: str) -> WebSocket | None:
        with self._lock:
            return table.get(cabinet_id)

    async def _operator_status(self, cabinet_id: str, online: bool) -> None:
        operator = self._get(self._operators, cabinet_id)
        if operator is None:
            return
        try:
            await operator.send_json({"type": "cabinet", "online": online})
        except RuntimeError:
            pass

    async def _broadcast_status(self, cabinet_id: str) -> None:
        cab = cabinets.load(cabinet_id)
        if cab is None:
            return
        with self._lock:
            viewers = list(self._viewers.get(cabinet_id, set()))
        dead: list[WebSocket] = []
        payload = {"type": "status", "cabinet": self.decorate(cab)}
        for viewer in viewers:
            try:
                await viewer.send_json(payload)
            except RuntimeError:
                dead.append(viewer)
        if dead:
            with self._lock:
                current = self._viewers.get(cabinet_id)
                if current is not None:
                    current.difference_update(dead)
                    if not current:
                        self._viewers.pop(cabinet_id, None)

    async def cabinet(self, websocket: WebSocket, cabinet_id: str) -> None:
        token = websocket.headers.get("authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if not cabinet_id or not _token_ok(token):
            await websocket.close(code=4401, reason="Unauthorized")
            return

        await websocket.accept()
        await self._replace(self._cabinets, cabinet_id, websocket)
        await websocket.send_text("READY\n")
        await self._operator_status(cabinet_id, True)
        last_command = ""
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.receive_text(), timeout=0.25
                    )
                    if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                        await websocket.close(code=1009, reason="Message too large")
                        break
                    if message.startswith("T\n"):
                        telemetry = message[2:]
                        reported_id = next(
                            (
                                line[3:].strip()
                                for line in telemetry.splitlines()
                                if line.startswith("id=")
                            ),
                            "",
                        )
                        if reported_id != cabinet_id:
                            await websocket.close(
                                code=1008, reason="Cabinet ID mismatch"
                            )
                            break
                        cabinets.handle_poll(telemetry)
                        await self._broadcast_status(cabinet_id)
                except asyncio.TimeoutError:
                    pass

                command = cabinets.command_for(cabinet_id)
                if command != last_command:
                    await websocket.send_text("M\n" + command)
                    last_command = command
        except WebSocketDisconnect:
            pass
        finally:
            self._remove(self._cabinets, cabinet_id, websocket)
            await self._operator_status(cabinet_id, False)
            await self._broadcast_status(cabinet_id)

    async def viewer(self, websocket: WebSocket, cabinet_id: str) -> None:
        if not cabinet_id:
            await websocket.close(code=4400, reason="Cabinet ID required")
            return
        await websocket.accept()
        with self._lock:
            self._viewers.setdefault(cabinet_id, set()).add(websocket)
        await self._broadcast_status(cabinet_id)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            with self._lock:
                viewers = self._viewers.get(cabinet_id)
                if viewers is not None:
                    viewers.discard(websocket)
                    if not viewers:
                        self._viewers.pop(cabinet_id, None)

    @staticmethod
    def _parse_state(message: str) -> tuple[int, int]:
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("Message too large")
        data = json.loads(message)
        if not isinstance(data, dict) or data.get("type") != "state":
            raise ValueError("Expected a state message")
        seq = int(data.get("seq", 0))
        if seq <= 0 or seq > 0x7FFFFFFF:
            raise ValueError("Invalid sequence")
        buttons = data.get("buttons", [])
        if not isinstance(buttons, list) or len(buttons) > len(BUTTON_BITS):
            raise ValueError("Invalid buttons")
        mask = 0
        for button in buttons:
            bit = BUTTON_BITS.get(str(button))
            if bit is None:
                raise ValueError(f"Unknown button: {button}")
            mask |= 1 << bit
        return seq, mask & BUTTON_MASK

    async def operator(self, websocket: WebSocket, cabinet_id: str) -> None:
        if not cabinet_id or not auth.websocket_authorized(websocket):
            await websocket.close(code=4401, reason="Unauthorized")
            return

        await websocket.accept()
        await self._replace(self._operators, cabinet_id, websocket)
        cabinet = self._get(self._cabinets, cabinet_id)
        await websocket.send_json(
            {"type": "cabinet", "online": cabinet is not None}
        )
        last_seq = 0
        try:
            while True:
                message = await websocket.receive_text()
                try:
                    seq, mask = self._parse_state(message)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                if seq <= last_seq:
                    continue
                last_seq = seq
                cabinet = self._get(self._cabinets, cabinet_id)
                if cabinet is None:
                    await websocket.send_json(
                        {"type": "cabinet", "online": False}
                    )
                    continue
                try:
                    await cabinet.send_text(f"S {seq} {mask:08x}\n")
                except RuntimeError:
                    self._remove(self._cabinets, cabinet_id, cabinet)
                    await websocket.send_json(
                        {"type": "cabinet", "online": False}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            self._remove(self._operators, cabinet_id, websocket)
            cabinet = self._get(self._cabinets, cabinet_id)
            if cabinet is not None:
                try:
                    await cabinet.send_text("CLEAR\n")
                except RuntimeError:
                    self._remove(self._cabinets, cabinet_id, cabinet)


hub = ControlHub()
