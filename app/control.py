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

from . import agents, auth, cabinets
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
# `H\n` heartbeats carry the cabinet's whole song inventory plus its config
# file. The cabinet builds them in a fixed 192 KiB buffer and refuses to send
# anything larger, so this only has to be a sane ceiling above that.
MAX_HEARTBEAT_BYTES = 256 * 1024
# `P\n` package-state slices come out of a 64 KiB cabinet buffer.
MAX_PACKAGE_BYTES = 96 * 1024
ITAIKO_SETTING_LIMITS = {
    **{key: 4095 for key in range(0, 4)},
    **{key: 1000 for key in range(4, 9)},
    9: 1,
    **{key: 4095 for key in range(10, 18)},
    46: 50,
}
ITAIKO_STATES = {"disconnected", "busy", "ready", "error"}
# One drum per player. The cabinet numbers them in USB attach order and
# addresses them by that index; a cabinet with no ITAIKO drum simply never
# reports one, which is what hides the panel.
ITAIKO_MAX_DEVICES = 2


def _token_ok(candidate: str) -> bool:
    expected = settings.api_token
    return not expected or hmac.compare_digest(candidate, expected)


class ControlHub:
    def __init__(self) -> None:
        self._lock = Lock()
        self._cabinets: dict[str, WebSocket] = {}
        self._operators: dict[str, WebSocket] = {}
        self._viewers: dict[str, set[WebSocket]] = {}
        self._itaiko: dict[str, dict[int, dict[str, object]]] = {}

    def status(self, cabinet_id: str) -> dict[str, bool]:
        with self._lock:
            return {
                "control_online": cabinet_id in self._cabinets,
                "control_operator": cabinet_id in self._operators,
            }

    def decorate(self, cabinet: dict) -> dict:
        """Merge live, non-persisted state onto a stored cabinet record.

        Agent presence rides along here so every endpoint and the status
        broadcast report it without each having to know about the agent hub.
        """
        cabinet_id = str(cabinet.get("cabinet_id") or "")
        with self._lock:
            drums = self._itaiko.get(cabinet_id, {})
            itaiko = [
                {"index": index, **drums[index]} for index in sorted(drums)
            ]
        return {
            **cabinet,
            **self.status(cabinet_id),
            **agents.hub.status(cabinet_id),
            "itaiko": itaiko,
        }

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
    ) -> bool:
        """Drop this socket if it is still the registered one.

        Returns False when a newer connection already took the slot — the
        caller must not then announce the cabinet as offline, because it isn't.
        """
        with self._lock:
            if table.get(cabinet_id) is websocket:
                table.pop(cabinet_id, None)
                return True
        return False

    def _get(self, table: dict[str, WebSocket], cabinet_id: str) -> WebSocket | None:
        with self._lock:
            return table.get(cabinet_id)

    async def request_inventory(self, cabinet_id: str) -> bool:
        """Ask a cabinet to push a fresh `H` snapshot.

        The cabinet sends one unprompted on connect and whenever its library or
        config actually changes, so this is only for the cases the connector
        cannot infer — an operator opening a dashboard against state this
        process never saw. It is never called on a timer: the snapshot is
        ~100 KiB and the cabinet builds and sends it on the same thread that
        services remote input.
        """
        cabinet = self._get(self._cabinets, cabinet_id)
        if cabinet is None:
            return False
        try:
            await cabinet.send_text("R\n")
            return True
        except RuntimeError:
            self._remove(self._cabinets, cabinet_id, cabinet)
            return False

    async def request_exit(self, cabinet_id: str) -> bool:
        """Ask a cabinet to close the game.

        A PS3 title exits to XMB, but the drum is a DualShock, so an operator
        holding the remote controls can walk the cabinet back into the game
        from there. That round trip is what applies a downloaded plugin update
        — it is only read at launch — without a site visit.
        """
        cabinet = self._get(self._cabinets, cabinet_id)
        if cabinet is None:
            return False
        try:
            await cabinet.send_text("X\n")
            return True
        except RuntimeError:
            self._remove(self._cabinets, cabinet_id, cabinet)
            return False

    async def request_screenshot(self, cabinet_id: str) -> bool:
        """Ask the plugin to grab the current frame.

        This is the in-game route: webMAN refuses to capture while a game
        runs, but the plugin is inside that game and already draws overlays
        into the very surface being scanned out.
        """
        cabinet = self._get(self._cabinets, cabinet_id)
        if cabinet is None:
            return False
        try:
            await cabinet.send_text("G\n")
            return True
        except RuntimeError:
            self._remove(self._cabinets, cabinet_id, cabinet)
            return False

    @staticmethod
    def validate_itaiko_device(index: object) -> int:
        try:
            device = int(index)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid drum index") from exc
        if device < 0 or device >= ITAIKO_MAX_DEVICES:
            raise ValueError("Invalid drum index")
        return device

    async def request_itaiko_read(self, cabinet_id: str, index: object) -> bool:
        device = self.validate_itaiko_device(index)
        cabinet = self._get(self._cabinets, cabinet_id)
        if cabinet is None:
            return False
        try:
            await cabinet.send_text(f"I GET {device}\n")
            return True
        except RuntimeError:
            self._remove(self._cabinets, cabinet_id, cabinet)
            return False

    @staticmethod
    def validate_itaiko_settings(settings_value: object) -> dict[int, int]:
        if not isinstance(settings_value, dict) or not settings_value:
            raise ValueError("At least one ITAIKO setting is required")
        normalized: dict[int, int] = {}
        for raw_key, raw_value in settings_value.items():
            try:
                key = int(raw_key)
            except (TypeError, ValueError) as exc:
                raise ValueError("ITAIKO settings must be integers") from exc
            if str(key) != str(raw_key):
                raise ValueError(f"Invalid ITAIKO setting key: {raw_key}")
            if isinstance(raw_value, bool):
                raise ValueError("ITAIKO settings must be integers")
            if isinstance(raw_value, int):
                value = raw_value
            elif isinstance(raw_value, str) and raw_value.isdigit():
                value = int(raw_value)
            else:
                raise ValueError("ITAIKO settings must be integers")
            maximum = ITAIKO_SETTING_LIMITS.get(key)
            if maximum is None:
                raise ValueError(f"ITAIKO setting {key} is not remotely configurable")
            if value < 0 or value > maximum:
                raise ValueError(
                    f"ITAIKO setting {key} must be between 0 and {maximum}"
                )
            normalized[key] = value
        return normalized

    async def request_itaiko_settings(
        self, cabinet_id: str, index: object, settings_value: object
    ) -> bool:
        device = self.validate_itaiko_device(index)
        normalized = self.validate_itaiko_settings(settings_value)
        cabinet = self._get(self._cabinets, cabinet_id)
        if cabinet is None:
            return False
        pairs = " ".join(f"{key}:{normalized[key]}" for key in sorted(normalized))
        try:
            await cabinet.send_text(f"I SET {device} {pairs}\n")
            return True
        except RuntimeError:
            self._remove(self._cabinets, cabinet_id, cabinet)
            return False

    @staticmethod
    def _parse_itaiko_frame(frame: str) -> tuple[str, int, dict[str, object]]:
        fields: dict[str, str] = {}
        for line in frame.splitlines():
            if "=" not in line:
                raise ValueError("Malformed ITAIKO status")
            key, value = line.split("=", 1)
            if key in fields:
                raise ValueError("Duplicate ITAIKO status field")
            fields[key] = value.strip()
        state = fields.get("state", "")
        if state not in ITAIKO_STATES:
            raise ValueError("Invalid ITAIKO state")
        # A plugin that predates per-drum addressing reports a single drum
        # and no `dev` line; treat it as drum 0 rather than dropping the
        # cabinet's socket over a missing field.
        device = ControlHub.validate_itaiko_device(fields.get("dev", "0"))
        version = fields.get("version", "")
        edition = fields.get("edition", "")
        # Which player the drum is wired to: identical drums are
        # indistinguishable over USB, so the firmware reports its USB mode.
        mode = fields.get("mode", "")
        error = fields.get("error", "")
        if any(len(value) > 160 for value in (version, edition, mode, error)):
            raise ValueError("ITAIKO status field is too long")
        raw_settings: dict[str, int] = {}
        settings_line = fields.get("settings", "")
        if settings_line:
            pairs: dict[str, str] = {}
            for token in settings_line.split():
                if ":" not in token:
                    raise ValueError("Malformed ITAIKO setting")
                key, value = token.split(":", 1)
                if key in pairs:
                    raise ValueError("Duplicate ITAIKO setting")
                pairs[key] = value
            normalized = ControlHub.validate_itaiko_settings(pairs)
            raw_settings = {str(key): value for key, value in normalized.items()}
        return fields.get("id", ""), device, {
            "state": state,
            "version": version,
            "edition": edition,
            "mode": mode,
            "settings": raw_settings,
            "error": error,
        }

    def _set_itaiko(
        self, cabinet_id: str, index: int, value: dict[str, object]
    ) -> None:
        with self._lock:
            drums = self._itaiko.setdefault(cabinet_id, {})
            # An unplugged drum leaves the panel rather than sitting there
            # greyed out: cabinets without ITAIKO hardware show nothing.
            if value["state"] == "disconnected":
                drums.pop(index, None)
            else:
                drums[index] = value

    async def _operator_status(self, cabinet_id: str, online: bool) -> None:
        operator = self._get(self._operators, cabinet_id)
        if operator is None:
            return
        try:
            await operator.send_json({"type": "cabinet", "online": online})
        except RuntimeError:
            pass

    async def _broadcast_status(self, cabinet_id: str) -> None:
        # Reads and parses the cabinet file, which on a full library is a few
        # hundred KB of JSON — keep it off the loop thread.
        cab = await asyncio.to_thread(cabinets.load, cabinet_id)
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
                    # `H\n` is the full heartbeat (inventory + config); `P\n` is
                    # an advisory per-song package-state slice; `T\n` is compact
                    # operation telemetry. Same grammar, same handler, different
                    # size budgets, and only `H` may replace the inventory.
                    is_heartbeat = message.startswith("H\n")
                    is_packages = message.startswith("P\n")
                    is_itaiko = message.startswith("I\n")
                    limit = MAX_MESSAGE_BYTES
                    if is_heartbeat:
                        limit = MAX_HEARTBEAT_BYTES
                    elif is_packages:
                        limit = MAX_PACKAGE_BYTES
                    if len(message.encode("utf-8")) > limit:
                        await websocket.close(code=1009, reason="Message too large")
                        break
                    if (
                        is_heartbeat
                        or is_packages
                        or is_itaiko
                        or message.startswith("T\n")
                    ):
                        frame = message[2:]
                        reported_id = next(
                            (
                                line[3:].strip()
                                for line in frame.splitlines()
                                if line.startswith("id=")
                            ),
                            "",
                        )
                        if reported_id != cabinet_id:
                            await websocket.close(
                                code=1008, reason="Cabinet ID mismatch"
                            )
                            break
                        if is_itaiko:
                            try:
                                _, device, itaiko = self._parse_itaiko_frame(
                                    frame
                                )
                            except ValueError:
                                await websocket.close(
                                    code=1008, reason="Invalid ITAIKO status"
                                )
                                break
                            self._set_itaiko(cabinet_id, device, itaiko)
                        else:
                            # Persistent cabinet frames are parsed and written
                            # off-loop; live ITAIKO state stays in memory.
                            await asyncio.to_thread(
                                cabinets.handle_frame, frame, is_heartbeat
                            )
                        await self._broadcast_status(cabinet_id)
                except asyncio.TimeoutError:
                    pass

                command = await asyncio.to_thread(cabinets.command_for, cabinet_id)
                if command != last_command:
                    await websocket.send_text("M\n" + command)
                    last_command = command
        except WebSocketDisconnect:
            pass
        finally:
            # A cabinet that reconnects registers its new socket before this
            # one finishes tearing down. Announcing offline unconditionally
            # here overwrote the new connection's online notice, leaving the
            # operator view stuck at "Cabinet offline" while frames flowed.
            if self._remove(self._cabinets, cabinet_id, websocket):
                # Drums are live state, not cabinet records: an offline
                # cabinet reports none, and the plugin re-announces what is
                # plugged in when it reconnects.
                with self._lock:
                    self._itaiko.pop(cabinet_id, None)
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
        # An operator is now looking at this cabinet: make sure what they see
        # is current rather than whatever the last change event left behind.
        cab = cabinets.load(cabinet_id)
        if cab is None or not cab["have"]:
            await self.request_inventory(cabinet_id)
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
