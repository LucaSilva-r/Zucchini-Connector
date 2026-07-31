"""Standalone VSH-agent channel.

The Zucchini plugin dies with the game, so it cannot act during the gap that
matters most — after a title exits and before the next one starts. The agent is
a separate VSH plugin, which is therefore up whenever the console is.

It long-polls this module over plain HTTP: webMAN has no TLS, and a PS3 game
process has no route to its own console, so neither WSS nor a loopback relay
was available. Commands are webMAN web-command paths, executed on the console
by webMAN's own handler. Agent-native verbs also report installed HDD games,
configure autoboot, and launch a selected directory through game_ext_plugin.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from urllib.parse import quote, unquote

# How long a poll is held open before answering with an empty body. Long
# enough that an idle cabinet is nearly silent, short enough that the agent's
# own receive timeout never fires first.
POLL_HOLD_SECONDS = 25
# An agent that has not polled within this window is treated as gone. Two
# missed polls, so one lost round trip does not flap the UI.
PRESENCE_SECONDS = POLL_HOLD_SECONDS * 2 + 10
MAX_INSTALLED_GAMES = 256
DEFAULT_SENSORS = {"cpu_temp": 0, "rsx_temp": 0, "fan_percent": 0}
MAX_HEALTH_BYTES = 64 * 1024
# What the operator sees when the parser found nothing it recognised.
MAX_HEALTH_TEXT = 2000

_BREAK = re.compile(r"(?i)<\s*(br|hr|/tr|/div|/h1|/h2|/p|/font|/table)[^>]*>")
_TAG = re.compile(r"<[^>]*>")
_BLANK = re.compile(r"\n{2,}")


def validate_game_directory(directory: str) -> str:
    """Validate one direct child name below /dev_hdd0/game."""
    if (
        not directory
        or len(directory.encode("utf-8")) >= 64
        or directory in {".", ".."}
        or any(ord(c) < 0x20 or c in "/\\" or ord(c) == 0x7F for c in directory)
    ):
        raise ValueError("Invalid installed-game directory")
    return directory


def _game_directory(value: str) -> str:
    return validate_game_directory(unquote(value))


def parse_games_report(body: bytes) -> dict[str, object]:
    """Parse the VSH agent's percent-escaped installed-game report.

    The format is deliberately line-oriented so the PS3 needs no JSON writer:

      version=1
      autoboot=<escaped directory>
      delay=15
      game<TAB>dir<TAB>title_id<TAB>title<TAB>version<TAB>has_icon
    """
    if not body or len(body) > 64 * 1024:
        raise ValueError("Bad installed-game report size")
    text = body.decode("utf-8", errors="strict")
    protocol = 0
    autoboot = ""
    delay = 15
    games: list[dict[str, object]] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        if raw.startswith("version="):
            protocol = int(raw.removeprefix("version=") or "0")
        elif raw.startswith("autoboot="):
            encoded = raw.removeprefix("autoboot=")
            autoboot = _game_directory(encoded) if encoded else ""
        elif raw.startswith("delay="):
            delay = max(0, min(600, int(raw.removeprefix("delay=") or "15")))
        elif raw.startswith("game\t"):
            fields = raw.split("\t")
            if len(fields) != 6 or len(games) >= MAX_INSTALLED_GAMES:
                raise ValueError("Malformed installed-game entry")
            directory = _game_directory(fields[1])
            if directory in seen:
                continue
            seen.add(directory)
            title_id = unquote(fields[2])[:32]
            title = unquote(fields[3])[:256]
            game_version = unquote(fields[4])[:32]
            if not title_id or not title:
                raise ValueError("Installed-game entry lacks title metadata")
            games.append(
                {
                    "directory": directory,
                    "title_id": title_id,
                    "title": title,
                    "version": game_version,
                    "has_icon": fields[5] == "1",
                }
            )

    if protocol != 1:
        raise ValueError("Unsupported installed-game report version")
    games.sort(key=lambda game: (str(game["title"]).casefold(), str(game["directory"])))
    return {
        "installed_games": games,
        "autoboot_dir": autoboot,
        "autoboot_delay": delay,
    }


def health_text(body: bytes) -> str:
    """webMAN's console-info page as plain lines.

    The agent forwards the page unparsed: it is C in VSH with no string
    library worth the name, and webMAN already read every figure on it —
    including the ones behind syscalls lv2 gates, which the agent cannot get
    on its own. Undoing the markup here costs three regexes.
    """
    if len(body) > MAX_HEALTH_BYTES:
        raise ValueError("Console info page too large")
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", body.decode("utf-8", errors="replace"))))
    text = "\n".join(line.strip() for line in text.replace("\xa0", " ").splitlines())
    return _BLANK.sub("\n", text).strip()


def _number(pattern: str, text: str, group: int = 1) -> int:
    found = re.search(pattern, text, re.IGNORECASE)
    if not found:
        return 0
    try:
        return int(found.group(group).replace(",", "").replace(".", ""))
    except ValueError:
        return 0


def _phrase(pattern: str, text: str) -> str:
    found = re.search(pattern, text, re.IGNORECASE)
    return found.group(1).strip()[:64] if found else ""


def parse_health(body: bytes) -> dict[str, object]:
    """Pull the figures worth a dashboard out of webMAN's info page.

    Every field is optional. Editions differ, several labels are translated,
    and overclocking figures only exist on builds compiled with it — so this
    anchors on the numbers and their units rather than on webMAN's wording,
    and keeps the stripped text so an operator can read whatever was missed.
    """
    text = health_text(body)
    return {
        "cpu_temp": _number(r"CPU:\s*(\d{1,3})\s*°?\s*C", text),
        "rsx_temp": _number(r"RSX:\s*(\d{1,3})\s*°?\s*C", text),
        "max_temp": _number(r"MAX:\s*(\d{1,3})\s*°?\s*C", text),
        # The fan label is localised; the raw 8-bit value beside it is not.
        "fan_percent": _number(r"(\d{1,3})\s*%\s*\(0x[0-9A-F]{1,2}\)", text),
        "mem_kb": _number(r"MEM:\s*([\d,]+)\s*KB", text),
        "gpu_mhz": _number(r"GPU:\s*([\d,]+)\s*Mhz", text),
        "vram_mhz": _number(r"VRAM:\s*([\d,]+)\s*Mhz", text),
        "hdd_free": _phrase(r"HDD:\s*([^\n]+)", text),
        "firmware": _phrase(r"Firmware:\s*([^\n]+)", text),
        # "206d 23:34:09 • 4,649 ON • 4,627 OFF (22)" — lifetime, not uptime.
        "runtime": _phrase(r"([\d,]+d\s+[\d:]+\s*•[^\n]*ON[^\n]*)", text),
        "power_ons": _number(r"([\d,]+)\s*ON\b", text),
        "text": text[:MAX_HEALTH_TEXT],
        "updated_at": int(time.time()),
    }


def launch_command(directory: str) -> str:
    return "launch\t" + quote(validate_game_directory(directory), safe="")


def autoboot_command(directory: str, delay: int) -> str:
    encoded = quote(validate_game_directory(directory), safe="") if directory else ""
    return f"autoboot\t{encoded}\t{max(0, min(600, int(delay)))}"


def parse_sensor(value: str, ceiling: int) -> int:
    """One poll-carried reading, or 0 when absent or implausible.

    The agent omits a field its syscall refused, so a missing value is normal
    and never an error. 0 also stands in for "no reading": a PS3 reporting a
    literal 0 C or a stopped fan is broken in a way this display cannot help.
    """
    if not value.isdigit():
        return 0
    number = int(value)
    return number if 0 < number <= ceiling else 0


class AgentHub:
    def __init__(self) -> None:
        self._pending: dict[str, list[str]] = {}
        self._wakeups: dict[str, asyncio.Event] = {}
        self._seen: dict[str, tuple[float, str]] = {}
        self._sensors: dict[str, dict[str, int]] = {}
        self._health: dict[str, dict[str, object]] = {}

    def _event(self, cabinet_id: str) -> asyncio.Event:
        event = self._wakeups.get(cabinet_id)
        if event is None:
            event = asyncio.Event()
            self._wakeups[cabinet_id] = event
        return event

    def online(self, cabinet_id: str) -> bool:
        seen = self._seen.get(cabinet_id)
        return seen is not None and (time.time() - seen[0]) < PRESENCE_SECONDS

    def status(self, cabinet_id: str) -> dict[str, object]:
        seen = self._seen.get(cabinet_id)
        return {
            "agent_online": self.online(cabinet_id),
            # "xmb" or "game": what the console is doing, which is knowable
            # even while the cabinet's own control socket is down.
            "agent_state": seen[1] if seen else "",
            "agent_seen": int(seen[0]) if seen else 0,
            # Console health, 0 when the cabinet did not report it. Kept in
            # memory only: it is worth nothing once it is a minute old, so it
            # is not worth a write per poll per cabinet.
            **self.health(cabinet_id),
        }

    def health(self, cabinet_id: str) -> dict[str, object]:
        """The relayed page, with the poll's own readings laid over it.

        Both sources are webMAN's numbers in the end, but they arrive at
        different rates: the poll carries temperatures every 25 s from lv2
        directly, the page every two minutes with everything else. Whichever
        has a value wins, so a gated fan reading still shows up.
        """
        page = dict(self._health.get(cabinet_id) or {})
        merged = dict(DEFAULT_SENSORS, **page)
        for key, value in (self._sensors.get(cabinet_id) or {}).items():
            if value:
                merged[key] = value
        return {"agent_health": merged}

    def note_sensors(self, cabinet_id: str, cpu: str, rsx: str, fan: str) -> None:
        self._sensors[cabinet_id] = {
            "cpu_temp": parse_sensor(cpu, 120),
            "rsx_temp": parse_sensor(rsx, 120),
            "fan_percent": parse_sensor(fan, 100),
        }

    def note_health(self, cabinet_id: str, page: dict[str, object]) -> None:
        self._health[cabinet_id] = page

    def note_seen(self, cabinet_id: str, state: str) -> None:
        # Log the transitions only. An operator bringing up a new cabinet needs
        # to see the first poll arrive; after that this is one line per outage.
        if not self.online(cabinet_id):
            print(f"[connector] webMAN agent online: {cabinet_id} ({state})", flush=True)
        self._seen[cabinet_id] = (time.time(), state[:16])

    def enqueue(self, cabinet_id: str, path: str) -> bool:
        """Queue one web command. False when no agent is polling for it.

        Refusing to queue for an absent agent keeps the caller's fallback
        chain honest: a command parked for a console that is off would look
        delivered and then fire at an unpredictable moment.
        """
        return self.enqueue_many(cabinet_id, [path])

    def enqueue_many(self, cabinet_id: str, commands: list[str]) -> bool:
        """Queue an ordered batch that one poll drains atomically."""
        if not self.online(cabinet_id) or not commands:
            return False
        self._pending.setdefault(cabinet_id, []).extend(commands)
        self._event(cabinet_id).set()
        return True

    async def wait(self, cabinet_id: str) -> list[str]:
        """Drain queued commands, holding the poll open while there are none."""
        queued = self._pending.pop(cabinet_id, [])
        if queued:
            self._event(cabinet_id).clear()
            return queued

        event = self._event(cabinet_id)
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=POLL_HOLD_SECONDS)
        except asyncio.TimeoutError:
            return []
        return self._pending.pop(cabinet_id, [])


hub = AgentHub()
