"""Per-cabinet management state for the pull-model cabinet poll.

One JSON file per cabinet under settings.cabinets_root, keyed by the
plugin-generated cabinet_id (dongle serials collide across cabinets).
The poll protocol is plain text both ways so the PS3 side needs no JSON
writer; see handle_frame().
"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from threading import Lock

from .config import settings
from . import database

_lock = Lock()

# Build code -> operator-friendly variant name, keyed by the series part only.
# A code is <prefix><series><variant> ("ST87" = series ST8, variant 7; "S111" =
# series S11, variant 1) and one game ships under several variants, so the
# trailing digit is dropped before the lookup. Series numbers follow the arcade
# release order, which is what this table used to get wrong: ST7 is White, not
# Sorairo, and S10 is Blue, not Yellow. Unmapped codes are shown raw.
GAME_NAMES = {
    "ST2": "Katsu-don",
    "ST3": "Sorairo",
    "ST4": "Momoiro",
    "ST5": "Kimidori",
    "ST6": "Murasaki",
    "ST7": "White",
    "ST8": "Red",
    "ST9": "Yellow",
    "S10": "Blue",
    "S11": "Green",
}


def game_name(code: str) -> str:
    """Operator-facing name for a reported build code, or the code itself."""
    return GAME_NAMES.get(code[:-1], code) if code else ""

_DEFAULT = {
    "cabinet_id": "",
    "serial": "",
    "name": "",
    "game": "",
    "game_name": "",
    "build": "",
    "version": "",
    "flavor": "",
    # Whether this build's song-select injection sites were resolved on the
    # cabinet. Reported by the plugin, not inferred from the build name: it is
    # the plugin that knows whether custom songs will actually show up.
    "song_inject": False,
    # taiko_config.cfg schema version the cabinet reports. The webMAN agent
    # needs keys that only exist from v22, and a cabinet below that cannot be
    # provisioned at all — worth showing rather than leaving the agent to idle
    # silently forever.
    "config_version": 0,
    # Sticky: set the first time a webMAN agent polls for this cabinet. The
    # console-control UI keys off it, so a cabinet that has never had an agent
    # (an RPCS3 instance, say) is not shown buttons that cannot work — and one
    # that has keeps them while the agent is merely offline.
    "agent_ever": False,
    # Reported by the VSH agent from direct children of /dev_hdd0/game only.
    "installed_games": [],
    "games_updated_at": 0,
    "autoboot_dir": "",
    "autoboot_delay": 15,
    "last_seen": 0,
    "have": [],
    "reported_cfg": "",
    "managed": False,
    "selection": [],
    "queued_selection": None,
    "selection_seq": 0,
    "acked_seq": 0,
    "desired_ack": 0,
    "active_seq": 0,
    "verify_generation": 0,
    "verify_ack": 0,
    "package_states": {},
    "operation_seq": 0,
    "operation_phase": "idle",
    "operation_done": 0,
    "operation_total": 0,
    "operation_failed": 0,
    "operation_song": "",
    "operation_error": "",
    "transfer_active": False,
    "transfer_asset": "",
    "transfer_done": 0,
    "transfer_total": 0,
    "transfer_bps": 0,
    "config_pending": {},
    "update_pending": None,
    "update_dispatched": False,
    "update_installed_id": "",
    "update_installed_version": "",
    "update_phase": "idle",
    "update_done": 0,
    "update_total": 0,
    "update_error": "",
}


def _defaults() -> dict:
    """A private copy of the defaults.

    _DEFAULT holds nested mutables (config_pending, have, package_states).
    Spreading it shares those objects between every cabinet that omits the
    key, so one cabinet's queued config would appear on the next cabinet
    created in the same process.
    """
    return copy.deepcopy(_DEFAULT)


def _path(cabinet_id: str) -> Path:
    safe = "".join(c for c in cabinet_id if c.isalnum() or c in "-_")
    return settings.cabinets_root / f"{safe}.json"


def load(cabinet_id: str) -> dict | None:
    try:
        return {**_defaults(), **json.loads(_path(cabinet_id).read_text())}
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
                    out.append({**_defaults(), **json.loads(p.read_text())})
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
        if cab["selection_seq"] > cab["desired_ack"]:
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


def mark_agent_seen(cabinet_id: str) -> None:
    """Record that a webMAN agent exists for this cabinet, once.

    Called from the agent poll, which repeats every ~25 s, so it writes only on
    the transition. A cabinet the connector has never heard of is skipped: the
    record appears when the plugin first heartbeats.
    """
    with _lock:
        cab = load(cabinet_id)
        if cab is None or cab["agent_ever"]:
            return
        cab["agent_ever"] = True
        _save(cab)


def set_installed_games(
    cabinet_id: str,
    games: list[dict[str, object]],
    autoboot_dir: str,
    autoboot_delay: int,
) -> dict | None:
    """Replace the last complete installed-HDD inventory from the VSH agent."""
    with _lock:
        cab = load(cabinet_id)
        if cab is None:
            return None
        cab["agent_ever"] = True
        cab["installed_games"] = copy.deepcopy(games)
        cab["games_updated_at"] = int(time.time())
        cab["autoboot_dir"] = autoboot_dir
        cab["autoboot_delay"] = max(0, min(600, int(autoboot_delay)))
        _save(cab)
        return cab


def force_resync(cabinet_id: str) -> dict | None:
    """Bump the selection sequence without changing the selection, so the
    cabinet re-runs its sync job (re-verifies and fetches anything missing)."""
    with _lock:
        cab = load(cabinet_id)
        if cab is None or not cab["managed"]:
            return cab
        cab["verify_generation"] += 1
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
                cab = {**_defaults(), **json.loads(path.read_text())}
            except (OSError, ValueError):
                continue
            desired = cab["queued_selection"]
            if desired is None:
                desired = cab["selection"]
            cleaned = [sid for sid in desired if sid not in song_ids]
            stale_package_ids = song_ids.intersection(cab["package_states"])
            if cleaned == desired and not stale_package_ids:
                continue
            if cleaned != desired and cab["selection_seq"] > cab["desired_ack"]:
                cab["queued_selection"] = (
                    None if cleaned == cab["selection"] else cleaned
                )
            elif cleaned != desired:
                cab["selection"] = cleaned
                cab["queued_selection"] = None
                if cab["managed"]:
                    cab["selection_seq"] += 1
            for song_id in stale_package_ids:
                cab["package_states"].pop(song_id, None)
            _save(cab)
            changed += 1
    return changed


def remove_unavailable_songs(available_song_ids: set[str]) -> int:
    """Repair cabinet desired state after a successful library scan."""
    selected_song_ids: set[str] = set()
    with _lock:
        if not settings.cabinets_root.is_dir():
            return 0
        for path in settings.cabinets_root.glob("*.json"):
            try:
                cab = {**_defaults(), **json.loads(path.read_text())}
            except (OSError, ValueError):
                continue
            selected_song_ids.update(cab["selection"])
            selected_song_ids.update(cab["queued_selection"] or [])
            selected_song_ids.update(cab["package_states"])
    return remove_songs_everywhere(selected_song_ids - available_song_ids)


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


def queue_update(cabinet_id: str, artifact: dict[str, object]) -> dict | None:
    with _lock:
        cab = load(cabinet_id)
        if cab is None:
            return None
        current = cab["update_pending"]
        if current and current.get("id") == artifact.get("id"):
            return cab
        if (
            current
            and cab["update_phase"] in {"downloading", "verifying", "installing"}
        ):
            raise RuntimeError("The cabinet is already installing an update")
        if cab["update_installed_id"] == artifact.get("id"):
            return cab
        cab["update_pending"] = artifact
        cab["update_dispatched"] = False
        cab["update_phase"] = "queued"
        cab["update_done"] = 0
        cab["update_total"] = int(artifact.get("size", 0))
        cab["update_error"] = ""
        _save(cab)
        return cab


def cancel_update(cabinet_id: str) -> dict | None:
    with _lock:
        cab = load(cabinet_id)
        if cab is None:
            return None
        if cab["update_dispatched"] and cab["update_phase"] != "failed":
            raise RuntimeError("The cabinet is already installing the update")
        cab["update_pending"] = None
        cab["update_dispatched"] = False
        cab["update_phase"] = "idle"
        cab["update_done"] = 0
        cab["update_total"] = 0
        cab["update_error"] = ""
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


def _command_text(cab: dict, mark_update_dispatched: bool) -> str:
    lines = []
    if cab["managed"]:
        lines.append("managed=1")
        lines.append(f"seq={cab['selection_seq']}")
        lines.extend(f"sel {sid}" for sid in cab["selection"])
        lines.append(f"verify={cab['verify_generation']}")
    lines.extend(f"cfg {k}={v}" for k, v in cab["config_pending"].items())
    pending = cab["update_pending"]
    if pending:
        lines.append(
            f"update {pending['id']} {int(pending['size'])} {pending['version']}"
        )
        if mark_update_dispatched and not cab["update_dispatched"]:
            cab["update_dispatched"] = True
            _save(cab)
    return "\n".join(lines) + "\n"


def command_for(cabinet_id: str) -> str:
    """Return the current authoritative command snapshot for WebSocket push.

    """
    with _lock:
        cab = load(cabinet_id)
        return _command_text(cab, True) if cab is not None else "\n"


def handle_frame(body: str, inventory: bool) -> str:
    """Apply one cabinet frame from the control socket.

    `inventory` is True for a full `H` heartbeat and False for a compact `T`
    status frame or a `P` package-state slice. All three share this grammar;
    only the heartbeat carries a complete `have` list and the config body, so
    only it may replace them, and only if `have_count` agrees with the number
    of `have` lines that actually arrived.

    Lines: id=, serial=, name=, game=, build=, version=, flavor=,
    song_inject=, seq=, have_count=,
    op_seq=, op_phase=, op_done=, op_total=, op_failed=, op_song=,
    op_error=, update_ack=, update_work_id=, update_phase=,
    update_done=, update_total=, update_error=,
    applied=<section.key>=<value> (repeatable), have <song_id> (repeatable,
    heartbeat only), pkg <song_id> <revision> <state> [error] (repeatable,
    `P` frames), then a blank line and the raw taiko_config.cfg contents.

    Returns the command text for this cabinet: managed=1, seq=N,
    cfg <section.key>=<value>, sel <song_id>, update <sha1> <size> <version>.
    """
    head, _, raw_cfg = body.partition("\n\n")
    fields: dict[str, str] = {}
    applied: list[str] = []
    have: list[str] = []
    package_states: list[tuple[str, str, str, str]] = []
    for line in head.splitlines():
        line = line.strip()
        if line.startswith("have "):
            have.append(line[5:].strip())
        elif line.startswith("pkg "):
            parts = line.split(" ", 4)
            if len(parts) >= 4:
                package_states.append((
                    parts[1][:64],
                    parts[2][:64],
                    parts[3][:24],
                    parts[4][:64] if len(parts) > 4 else "",
                ))
        elif line.startswith("applied="):
            applied.append(line[8:].strip())
        elif "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip()

    cabinet_id = fields.get("id", "")
    if not cabinet_id:
        return "error=missing id\n"

    with _lock:
        cab = load(cabinet_id) or dict(_defaults(), cabinet_id=cabinet_id)
        cab["serial"] = fields.get("serial", cab["serial"])
        cab["name"] = fields.get("name", cab["name"])
        cab["game"] = fields.get("game", cab["game"])
        cab["game_name"] = game_name(cab["game"])
        cab["build"] = fields.get("build", cab["build"])
        cab["song_inject"] = fields.get(
            "song_inject", "1" if cab["song_inject"] else "0"
        ) == "1"
        cab["version"] = fields.get("version", cab["version"])
        cab["flavor"] = fields.get("flavor", cab["flavor"])
        cab["last_seen"] = int(time.time())
        # A status frame carries no inventory: the cabinet only sends a
        # heartbeat when it has a complete one, so retain the last complete
        # list instead of flashing back to zero mid-operation.
        # `have_count` is the cabinet's own count of the lines it wrote, and
        # every heartbeat carries one. A missing or mismatched count means the
        # list was truncated in transit, and a truncated inventory is worse than
        # a stale one: `have` is authoritative, so everything missing from it is
        # reported as absent from the cabinet. Keep the previous list instead.
        declared = fields.get("have_count", "")
        complete = declared.isdigit() and int(declared) == len(have)
        if inventory and not complete:
            print(
                f"[cabinet {cabinet_id}] inventory rejected: {len(have)} songs "
                f"arrived, have_count={declared or 'absent'}; keeping the previous list",
                flush=True,
            )
        if inventory and complete:
            cab["have"] = have
            have_set = set(have)
            cab["package_states"] = {
                song_id: value
                for song_id, value in cab["package_states"].items()
                if song_id in have_set
            }
        if raw_cfg.strip():
            cab["reported_cfg"] = raw_cfg
        for item in applied:
            key, _, value = item.partition("=")
            # The ack carries the applied value, so a delayed ack cannot clear
            # a newer value queued for the same key.
            if cab["config_pending"].get(key) == value:
                cab["config_pending"].pop(key, None)
        if raw_cfg.strip():
            reported = _parse_reported_cfg(raw_cfg)
            for key, value in list(cab["config_pending"].items()):
                if reported.get(key) == value:
                    cab["config_pending"].pop(key, None)
            try:
                cab["config_version"] = int(reported.get("meta.config_version", 0))
            except ValueError:
                pass
            # Provision the webMAN agent's credential without operator work.
            # The agent runs outside the plugin and cannot see the token baked
            # into zucchini.sprx, and that token must not be reused here
            # anyway, so the connector pushes its own through the config
            # channel the cabinet already applies and saves.
            if "network.agent_token" in reported and (
                reported["network.agent_token"] != settings.agent_token
            ):
                cab["config_pending"]["network.agent_token"] = settings.agent_token
        try:
            cab["acked_seq"] = max(cab["acked_seq"], int(fields.get("seq", "0")))
        except ValueError:
            pass
        for field, key in (
            ("desired_ack", "desired_ack"),
            ("active_seq", "active_seq"),
            ("verify_ack", "verify_ack"),
        ):
            try:
                cab[key] = max(cab[key], int(fields.get(field, "0")))
            except ValueError:
                pass
        recorded = [row for row in package_states if row[0]]
        for song_id, revision, package_state, error_code in recorded:
            cab["package_states"][song_id] = {
                "revision": revision,
                "state": package_state,
                "error_code": error_code,
            }
        database.record_cabinet_package_states(cabinet_id, recorded)

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
        if "xfer_active" in fields:
            cab["transfer_active"] = fields["xfer_active"] == "1"
            cab["transfer_asset"] = fields.get("xfer_asset", "")[:128]
            for field, key in (
                ("xfer_done", "transfer_done"),
                ("xfer_total", "transfer_total"),
                ("xfer_bps", "transfer_bps"),
            ):
                try:
                    cab[key] = max(0, int(fields.get(field, "0")))
                except ValueError:
                    cab[key] = 0

        update_ack = fields.get("update_ack", "")
        update_ack_valid = len(update_ack) == 40 and all(
            char in "0123456789abcdef" for char in update_ack
        )
        acknowledged_update = False
        pending = cab["update_pending"]
        if update_ack_valid:
            cab["update_installed_id"] = update_ack
            if pending and pending.get("id") == update_ack:
                cab["update_installed_version"] = str(pending.get("version", ""))
                cab["update_pending"] = None
                cab["update_dispatched"] = False
                cab["update_phase"] = "complete"
                cab["update_done"] = int(pending.get("size", 0))
                cab["update_total"] = int(pending.get("size", 0))
                cab["update_error"] = ""
                pending = None
                acknowledged_update = True

        work_id = fields.get("update_work_id", "")
        if not acknowledged_update and "update_phase" in fields and (
            not pending or not work_id or pending.get("id") == work_id
        ):
            cab["update_phase"] = fields["update_phase"][:32]
            cab["update_error"] = fields.get("update_error", "")[:160]
            for field, key in (
                ("update_done", "update_done"),
                ("update_total", "update_total"),
            ):
                try:
                    cab[key] = max(0, int(fields.get(field, "0")))
                except ValueError:
                    cab[key] = 0

        # Promote exactly one queued edit only after the cabinet atomically
        # applied and acknowledged the immutable active sequence.
        if (cab["desired_ack"] >= cab["selection_seq"]
                and cab["queued_selection"] is not None):
            queued = cab["queued_selection"]
            cab["queued_selection"] = None
            if queued != cab["selection"]:
                cab["selection"] = queued
                cab["selection_seq"] += 1
        _save(cab)

        return _command_text(cab, True)
