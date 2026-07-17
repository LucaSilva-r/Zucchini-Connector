"""Per-cabinet management state for the pull-model cabinet poll.

One JSON file per cabinet under settings.cabinets_root, keyed by the
plugin-generated cabinet_id (dongle serials collide across cabinets).
The poll protocol is plain text both ways so the PS3 side needs no JSON
writer; see handle_poll().
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock

from .config import settings

_lock = Lock()

# PARAM.SFO title-bracket code -> operator-friendly variant name.
# Codes documented in TaikoZucchini core/game_version.h; unmapped codes
# are shown raw in the UI — extend as cabinets report them.
GAME_NAMES = {
    "S111": "Green",
    "S101": "Yellow",
    "ST91": "White",
    "ST87": "Red",
    "ST71": "Sorairo",
}

_DEFAULT = {
    "cabinet_id": "",
    "serial": "",
    "name": "",
    "game": "",
    "game_name": "",
    "version": "",
    "last_seen": 0,
    "have": [],
    "reported_cfg": "",
    "managed": False,
    "selection": [],
    "queued_selection": None,
    "selection_seq": 0,
    "acked_seq": 0,
    "operation_seq": 0,
    "operation_phase": "idle",
    "operation_done": 0,
    "operation_total": 0,
    "operation_failed": 0,
    "operation_song": "",
    "operation_error": "",
    "config_pending": {},
}


def _path(cabinet_id: str) -> Path:
    safe = "".join(c for c in cabinet_id if c.isalnum() or c in "-_")
    return settings.cabinets_root / f"{safe}.json"


def load(cabinet_id: str) -> dict | None:
    try:
        return {**_DEFAULT, **json.loads(_path(cabinet_id).read_text())}
    except (OSError, ValueError):
        return None


def _save(cab: dict) -> None:
    settings.cabinets_root.mkdir(parents=True, exist_ok=True)
    path = _path(cab["cabinet_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cab, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def list_all() -> list[dict]:
    with _lock:
        out = []
        if settings.cabinets_root.is_dir():
            for p in sorted(settings.cabinets_root.glob("*.json")):
                try:
                    out.append({**_DEFAULT, **json.loads(p.read_text())})
                except (OSError, ValueError):
                    continue
        return out


def delete(cabinet_id: str) -> bool:
    with _lock:
        try:
            _path(cabinet_id).unlink()
            return True
        except OSError:
            return False


def set_selection(cabinet_id: str, song_ids: list[str]) -> dict | None:
    with _lock:
        cab = load(cabinet_id)
        if cab is None:
            return None
        selection = sorted(set(song_ids))
        cab["managed"] = True
        if cab["selection_seq"] > cab["acked_seq"]:
            # The active sequence is immutable. Operators may keep editing,
            # but only the latest draft becomes the next job after its ack.
            cab["queued_selection"] = (
                None if selection == cab["selection"] else selection
            )
        elif selection != cab["selection"] or cab["selection_seq"] == 0:
            cab["selection"] = selection
            cab["queued_selection"] = None
            cab["selection_seq"] += 1
        _save(cab)
        return cab


def force_resync(cabinet_id: str) -> dict | None:
    """Bump the selection sequence without changing the selection, so the
    cabinet re-runs its sync job (re-verifies and fetches anything missing)."""
    with _lock:
        cab = load(cabinet_id)
        if cab is None or not cab["managed"]:
            return cab
        cab["selection_seq"] += 1
        _save(cab)
        return cab


def remove_songs_everywhere(song_ids: set[str]) -> int:
    """Remove unavailable songs from every active or queued cabinet selection.

    An in-flight sequence remains immutable. In that case the cleaned selection
    becomes the queued sequence and is promoted after the cabinet acknowledges
    the active job.
    """
    if not song_ids:
        return 0
    changed = 0
    with _lock:
        if not settings.cabinets_root.is_dir():
            return 0
        for path in sorted(settings.cabinets_root.glob("*.json")):
            try:
                cab = {**_DEFAULT, **json.loads(path.read_text())}
            except (OSError, ValueError):
                continue
            desired = cab["queued_selection"]
            if desired is None:
                desired = cab["selection"]
            cleaned = [sid for sid in desired if sid not in song_ids]
            if cleaned == desired:
                continue
            if cab["selection_seq"] > cab["acked_seq"]:
                cab["queued_selection"] = (
                    None if cleaned == cab["selection"] else cleaned
                )
            else:
                cab["selection"] = cleaned
                cab["queued_selection"] = None
                if cab["managed"]:
                    cab["selection_seq"] += 1
            _save(cab)
            changed += 1
    return changed


def set_config(cabinet_id: str, kv: dict[str, str]) -> dict | None:
    """Merge section.key -> value pairs into the pending queue.
    Empty value removes a pending (not-yet-applied) key.

    Keys are validated against the cabinet's reported taiko_config.cfg: a
    typo'd key would sit in the pending queue forever because the game never
    acknowledges keys it does not know. Skipped when the cabinet has not
    reported its config yet (nothing to validate against). Raises ValueError
    with the offending keys."""
    with _lock:
        cab = load(cabinet_id)
        if cab is None:
            return None
        known = set(_parse_reported_cfg(cab["reported_cfg"]))
        if known:
            unknown = sorted(k for k, v in kv.items() if v != "" and k not in known)
            if unknown:
                raise ValueError(
                    "Unknown config key(s): " + ", ".join(unknown)
                    + ". The cabinet only applies keys present in its reported taiko_config.cfg."
                )
        for key, value in kv.items():
            if value == "":
                cab["config_pending"].pop(key, None)
            else:
                cab["config_pending"][str(key)] = str(value)
        _save(cab)
        return cab


def _parse_reported_cfg(raw_cfg: str) -> dict[str, str]:
    """Flatten the reported INI into section.key -> value strings.

    This is intentionally small and mirrors the subset emitted by zucchini's
    config writer. It lets a reboot acknowledge values that were saved before
    the cabinet had a chance to echo an explicit applied= line.
    """
    values: dict[str, str] = {}
    section = ""
    for raw_line in raw_cfg.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if not section or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("#", 1)[0].strip()
        if key:
            values[f"{section}.{key}"] = value
    return values


def handle_poll(body: str) -> str:
    """Heartbeat + ack + fetch-pending, one round trip.

    Request lines: id=, serial=, name=, game=, version=, seq=,
    op_seq=, op_phase=, op_done=, op_total=, op_failed=, op_song=,
    op_error=,
    applied=<section.key>=<value> (repeatable), have <song_id> (repeatable),
    then a blank line and the raw taiko_config.cfg contents.

    Response lines: managed=1, seq=N, cfg <section.key>=<value>,
    sel <song_id>.
    """
    head, _, raw_cfg = body.partition("\n\n")
    fields: dict[str, str] = {}
    applied: list[str] = []
    have: list[str] = []
    for line in head.splitlines():
        line = line.strip()
        if line.startswith("have "):
            have.append(line[5:].strip())
        elif line.startswith("applied="):
            applied.append(line[8:].strip())
        elif "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip()

    cabinet_id = fields.get("id", "")
    if not cabinet_id:
        return "error=missing id\n"

    with _lock:
        cab = load(cabinet_id) or dict(_DEFAULT, cabinet_id=cabinet_id)
        cab["serial"] = fields.get("serial", cab["serial"])
        cab["name"] = fields.get("name", cab["name"])
        cab["game"] = fields.get("game", cab["game"])
        cab["game_name"] = GAME_NAMES.get(cab["game"], cab["game"])
        cab["version"] = fields.get("version", cab["version"])
        cab["last_seen"] = int(time.time())
        # Long song operations own and mutate the in-memory cache index. During
        # that window the cabinet sends have_complete=0 and omits the list;
        # retain the last complete inventory instead of flashing back to zero.
        if fields.get("have_complete", "1") != "0":
            cab["have"] = have
        if raw_cfg.strip():
            cab["reported_cfg"] = raw_cfg
        for item in applied:
            key, sep, value = item.partition("=")
            # New clients include the applied value, preventing a delayed ack
            # from clearing a newer value queued for the same key. Keep bare
            # key support for the original POC client.
            if not sep or cab["config_pending"].get(key) == value:
                cab["config_pending"].pop(key, None)
        if raw_cfg.strip():
            reported = _parse_reported_cfg(raw_cfg)
            for key, value in list(cab["config_pending"].items()):
                if reported.get(key) == value:
                    cab["config_pending"].pop(key, None)
        try:
            cab["acked_seq"] = max(cab["acked_seq"], int(fields.get("seq", "0")))
        except ValueError:
            pass

        if "op_phase" in fields:
            cab["operation_phase"] = fields["op_phase"][:32]
            cab["operation_song"] = fields.get("op_song", "")[:64]
            cab["operation_error"] = fields.get("op_error", "")[:160]
            for field, key in (
                ("op_seq", "operation_seq"),
                ("op_done", "operation_done"),
                ("op_total", "operation_total"),
                ("op_failed", "operation_failed"),
            ):
                try:
                    cab[key] = max(0, int(fields.get(field, "0")))
                except ValueError:
                    cab[key] = 0

        # Promote exactly one queued edit only after the cabinet atomically
        # applied and acknowledged the immutable active sequence.
        if cab["acked_seq"] >= cab["selection_seq"] and cab["queued_selection"] is not None:
            queued = cab["queued_selection"]
            cab["queued_selection"] = None
            if queued != cab["selection"]:
                cab["selection"] = queued
                cab["selection_seq"] += 1
        _save(cab)

        lines = []
        if cab["managed"]:
            lines.append("managed=1")
            lines.append(f"seq={cab['selection_seq']}")
            lines.extend(f"sel {sid}" for sid in cab["selection"])
        lines.extend(f"cfg {k}={v}" for k, v in cab["config_pending"].items())
        return "\n".join(lines) + "\n"
