from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from tja2fumen.constants import COURSE_IDS
from tja2fumen.converters import convert_tja_to_fumen, fix_dk_note_types_course
from tja2fumen.parsers import parse_tja
from tja2fumen.writers import write_fumen

from . import cabinets, catalog, osu
from .config import settings


COURSE_NAME_BY_ID = {value: key for key, value in COURSE_IDS.items()}

_CONVERSION_POOL = ThreadPoolExecutor(
    max_workers=settings.conversion_workers,
    thread_name_prefix="tjarepo-convert",
)
_CONVERSION_FUTURES: dict[str, Future[None]] = {}
_CONVERSION_LOCK = RLock()
_BROKEN_IDS: set[str] = set()


def enqueue(song_id: str) -> dict[str, Any]:
    """Prepare a song and ensure at most one conversion is running for it."""
    with _CONVERSION_LOCK:
        future = _CONVERSION_FUTURES.get(song_id)
    if future is not None and not future.done():
        return status_for(song_id)

    with _CONVERSION_LOCK:
        data = prepare(song_id)
        state = str(data.get("status", ""))
        future = _CONVERSION_FUTURES.get(song_id)
        if state in {"queued", "processing"} and (
            future is None or future.done()
        ):
            future = _CONVERSION_POOL.submit(convert, song_id)
            _CONVERSION_FUTURES[song_id] = future
            _add_done_callback(song_id, future)
        return data


def enqueue_many(song_ids: list[str]) -> dict[str, int | str]:
    seen: set[str] = set()
    scheduled = 0
    already_scheduled = 0
    not_found = 0

    for song_id in song_ids:
        if song_id in seen:
            continue
        seen.add(song_id)
        if catalog.song(song_id) is None:
            not_found += 1
            continue

        with _CONVERSION_LOCK:
            future = _CONVERSION_FUTURES.get(song_id)
            if future is not None and not future.done():
                already_scheduled += 1
                continue
            future = _CONVERSION_POOL.submit(_convert_if_needed, song_id)
            _CONVERSION_FUTURES[song_id] = future
            _add_done_callback(song_id, future)
            scheduled += 1

    return {
        "status": "accepted",
        "requested": len(song_ids),
        "accepted": len(seen),
        "scheduled": scheduled,
        "already_scheduled": already_scheduled,
        "not_found": not_found,
    }


def shutdown() -> None:
    _CONVERSION_POOL.shutdown(wait=False, cancel_futures=True)


def _conversion_done(song_id: str, future: Future[None]) -> None:
    with _CONVERSION_LOCK:
        if _CONVERSION_FUTURES.get(song_id) is future:
            del _CONVERSION_FUTURES[song_id]


def _add_done_callback(song_id: str, future: Future[None]) -> None:
    future.add_done_callback(
        lambda completed, queued_id=song_id: _conversion_done(
            queued_id, completed
        )
    )


def _convert_if_needed(song_id: str) -> None:
    data = prepare(song_id)
    if data.get("status") in {"queued", "processing"}:
        convert(song_id)


def prepare(song_id: str, *, retry: bool = False) -> dict[str, Any]:
    entry = catalog.song(song_id)
    if entry is None:
        return {"status": "not_found", "song_id": song_id}
    if manifest_ready(entry):
        return ready_status(entry)

    status = status_for(song_id)
    if status.get("status") in {"queued", "processing"}:
        return status
    if status.get("status") == "failed" and not retry:
        return status

    queued = {
        "status": "queued",
        "song_id": song_id,
        "title": entry["title"],
        "source_hash": catalog.source_hash(entry),
        "updated_at": _now(),
    }
    _write_json(_status_path(song_id), queued)
    return queued


def status_for(song_id: str) -> dict[str, Any]:
    entry = catalog.song(song_id)
    if entry is None:
        return {"status": "not_found", "song_id": song_id}
    if manifest_ready(entry):
        return ready_status(entry)
    path = _status_path(song_id)
    if path.is_file():
        try:
            status = json.loads(path.read_text())
            if status.get("source_hash") != catalog.source_hash(entry):
                return {
                    "status": "missing",
                    "song_id": song_id,
                    "title": entry["title"],
                    "source_hash": catalog.source_hash(entry),
                }
            if status.get("status") == "ready":
                return {
                    "status": "missing",
                    "song_id": song_id,
                    "title": entry["title"],
                    "source_hash": catalog.source_hash(entry),
                }
            return status
        except json.JSONDecodeError:
            pass
    return {
        "status": "missing",
        "song_id": song_id,
        "title": entry["title"],
        "source_hash": catalog.source_hash(entry),
    }


def convert(song_id: str) -> None:
    entry = catalog.song(song_id)
    if entry is None:
        _write_json(_status_path(song_id), {"status": "not_found", "song_id": song_id, "updated_at": _now()})
        return

    _write_json(_status_path(song_id), {
        "status": "processing",
        "song_id": song_id,
        "title": entry["title"],
        "source_hash": catalog.source_hash(entry),
        "updated_at": _now(),
    })

    try:
        manifest = _convert_package(entry, _package_root(song_id), catalog.source_hash(entry))
        _write_json(_manifest_path(song_id), manifest)
        _write_json(_status_path(song_id), {
            "status": "ready",
            "song_id": song_id,
            "title": entry["title"],
            "source_hash": manifest["source_hash"],
            "manifest": manifest,
            "updated_at": _now(),
        })
        with _CONVERSION_LOCK:
            _BROKEN_IDS.discard(song_id)
    except Exception as exc:
        shutil.rmtree(_package_root(song_id).parent / "package.tmp", ignore_errors=True)
        _write_json(_status_path(song_id), {
            "status": "failed",
            "song_id": song_id,
            "title": entry["title"],
            "source_hash": catalog.source_hash(entry),
            "message": str(exc),
            "updated_at": _now(),
        })
        with _CONVERSION_LOCK:
            _BROKEN_IDS.add(song_id)
        cabinets.remove_songs_everywhere({song_id})


def refresh_broken_index() -> set[str]:
    """Load conversion failures that still match the current source files."""
    found: set[str] = set()
    if settings.convert_root.is_dir():
        for path in settings.convert_root.glob("*/status.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if data.get("status") != "failed":
                continue
            song_id = str(data.get("song_id") or path.parent.name)
            entry = catalog.song(song_id)
            if entry is not None and data.get("source_hash") == catalog.source_hash(entry):
                found.add(song_id)
    with _CONVERSION_LOCK:
        _BROKEN_IDS.clear()
        _BROKEN_IDS.update(found)
    return found


def broken_song_ids() -> set[str]:
    with _CONVERSION_LOCK:
        candidates = set(_BROKEN_IDS)
    stale: set[str] = set()
    for song_id in candidates:
        entry = catalog.song(song_id)
        data = status_for(song_id)
        if (entry is None or data.get("status") not in {"failed", "queued", "processing"} or
                data.get("source_hash") != catalog.source_hash(entry)):
            stale.add(song_id)
    if stale:
        with _CONVERSION_LOCK:
            _BROKEN_IDS.difference_update(stale)
    return candidates - stale


# Per-song management_status cache keyed on (source_hash, status/manifest
# mtimes): /library/manage would otherwise parse thousands of JSON files
# per request. Bounded by the number of songs.
_MGMT_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def management_status(song_id: str, rev: str | None = None) -> dict[str, Any]:
    """rev is the source_hash prefix already embedded in the cached library
    payload; passing it avoids re-statting every source file per song on the
    /library/manage request path."""
    entry = catalog.song(song_id)
    if entry is None:
        return {
            "conversion_status": "not_found",
            "conversion_error": "",
            "conversion_updated_at": "",
        }
    if not rev:
        rev = catalog.source_hash(entry)[:12]
    cache_key = (
        rev,
        _mtime_ns(_status_path(song_id)),
        _mtime_ns(_manifest_path(song_id)),
    )
    cached = _MGMT_CACHE.get(song_id)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    data: dict[str, Any] = {}
    path = _status_path(song_id)
    if path.is_file():
        try:
            candidate = json.loads(path.read_text())
            if str(candidate.get("source_hash") or "")[: len(rev)] == rev:
                data = candidate
        except (OSError, ValueError):
            pass
    state = str(data.get("status") or "unconverted")
    if state == "ready":
        manifest = _read_manifest(song_id)
        if manifest is None or str(manifest.get("source_hash") or "")[: len(rev)] != rev:
            state = "unconverted"
    result = {
        "conversion_status": state,
        "conversion_error": str(data.get("message") or ""),
        "conversion_updated_at": str(data.get("updated_at") or ""),
    }
    _MGMT_CACHE[song_id] = (cache_key, result)
    return result


def retry(song_id: str) -> dict[str, Any]:
    entry = catalog.song(song_id)
    if entry is None:
        return {"status": "not_found", "song_id": song_id}
    with _CONVERSION_LOCK:
        future = _CONVERSION_FUTURES.get(song_id)
        if future is not None and not future.done():
            return status_for(song_id)
        data = prepare(song_id, retry=True)
        future = _CONVERSION_POOL.submit(convert, song_id)
        _CONVERSION_FUTURES[song_id] = future
        _add_done_callback(song_id, future)
        return data


def remove_artifacts(song_id: str) -> None:
    with _CONVERSION_LOCK:
        future = _CONVERSION_FUTURES.get(song_id)
        if future is not None and not future.done():
            raise RuntimeError("The song is currently converting")
        _BROKEN_IDS.discard(song_id)
    shutil.rmtree(settings.convert_root / song_id, ignore_errors=True)


def asset(song_id: str, asset_path: str) -> dict[str, Any] | None:
    if not asset_path or "\0" in asset_path or ".." in asset_path or asset_path.startswith("/"):
        return None
    manifest = _read_manifest(song_id)
    if manifest is None:
        return None

    allowed: dict[str, str] = {}
    for item in manifest.get("assets", []):
        allowed[str(item["name"])] = str(item.get("sha1", ""))
    for item in manifest.get("courses", []):
        allowed[str(item["chart"])] = str(item.get("sha1", ""))
    if asset_path not in allowed:
        return None

    root = _package_root(song_id).resolve()
    path = (root / asset_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return {"path": path, "sha1": allowed[asset_path] or _sha1_file(path)}


def manifest_ready(entry: dict[str, Any]) -> bool:
    manifest = _read_manifest(str(entry["id"]))
    if (manifest is None or
            manifest.get("schema") != 2 or
            manifest.get("fumen_endian") != "big" or
            manifest.get("source_hash") != catalog.source_hash(entry)):
        return False
    for item in manifest.get("assets", []):
        if asset(str(entry["id"]), str(item.get("name", ""))) is None:
            return False
    for item in manifest.get("courses", []):
        if asset(str(entry["id"]), str(item.get("chart", ""))) is None:
            return False
    return True


def ready_status(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ready",
        "song_id": entry["id"],
        "title": entry["title"],
        "source_hash": catalog.source_hash(entry),
        "manifest": _read_manifest(str(entry["id"])),
    }


def _convert_package(entry: dict[str, Any], package_root: Path, source_hash: str) -> dict[str, Any]:
    source_type = str(entry.get("source_type") or "tja")
    if source_type == "osz":
        if not entry.get("osz_path") or not Path(str(entry["osz_path"])).is_file():
            raise RuntimeError("osu! song has no readable OSZ file.")
    else:
        if not entry.get("audio_path") or not Path(str(entry["audio_path"])).is_file():
            raise RuntimeError("TJA song has no readable audio file.")
        if not entry.get("tja_path") or not Path(str(entry["tja_path"])).is_file():
            raise RuntimeError("TJA song has no readable TJA file.")

    tmp = package_root.parent / "package.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "solo").mkdir(parents=True, exist_ok=True)

    courses = (
        _convert_osz_charts(entry, tmp)
        if source_type == "osz"
        else _convert_charts(entry, tmp)
    )
    if not courses:
        raise RuntimeError("Song does not contain any supported Taiko course.")

    song_id = str(entry["id"])
    song_upper = song_id.upper()
    nub_name = f"SONG_{song_upper}.nub"
    nsh_name = f"SONG_{song_upper}.nsh"
    if source_type == "osz":
        member = str(entry.get("audio_member") or "")
        if not member:
            raise RuntimeError("OSZ has no readable audio member.")
        suffix = Path(member).suffix or ".audio"
        with tempfile.TemporaryDirectory(prefix="tjarepo-osz-audio-") as tmpdir:
            source = Path(tmpdir) / f"source{suffix}"
            source.write_bytes(
                osu.read_member(Path(str(entry["osz_path"])), member, osu.MAX_AUDIO_BYTES)
            )
            _convert_audio(source, tmp / nub_name, tmp / nsh_name)
    else:
        _convert_audio(Path(str(entry["audio_path"])), tmp / nub_name, tmp / nsh_name)

    assets = [
        _asset_manifest(tmp, nsh_name),
        _asset_manifest(tmp, nub_name),
    ]

    shutil.rmtree(package_root, ignore_errors=True)
    package_root.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(package_root)

    return {
        "schema": 2,
        "id": song_id,
        "title": entry["title"],
        "source_path": entry["source_path"],
        "source_hash": source_hash,
        "fumen_endian": "big",
        "courses": courses,
        "assets": assets,
        "updated_at": _now(),
    }


def _convert_charts(entry: dict[str, Any], tmp: Path) -> list[dict[str, Any]]:
    parsed = parse_tja(str(entry["tja_path"]))
    song_id = str(entry["id"])
    out: list[dict[str, Any]] = []
    for course in entry.get("courses", []):
        course_id = str(course["id"])
        course_name = COURSE_NAME_BY_ID.get(course_id)
        if course_name is None or course_name not in parsed.courses:
            continue
        chart = f"solo/{song_id}_{course_id}.bin"
        path = tmp / chart
        fumen = convert_tja_to_fumen(parsed.courses[course_name])
        fumen.header.order = ">"
        fix_dk_note_types_course(fumen)
        write_fumen(str(path), fumen)
        out.append({
            "id": course_id,
            "label": course.get("label", course_name),
            "stars": course.get("stars", 0),
            "chart": chart,
            "size": path.stat().st_size,
            "sha1": _sha1_file(path),
        })
    return out


def _convert_osz_charts(entry: dict[str, Any], tmp: Path) -> list[dict[str, Any]]:
    archive = Path(str(entry["osz_path"]))
    song_id = str(entry["id"])
    out: list[dict[str, Any]] = []
    for course in entry.get("courses", []):
        course_id = str(course["id"])
        course_name = COURSE_NAME_BY_ID.get(course_id)
        member = str(course.get("osu_member") or "")
        if course_name is None or not member:
            continue
        chart = f"solo/{song_id}_{course_id}.bin"
        path = tmp / chart
        raw = osu.read_member(archive, member, osu.MAX_OSU_BYTES)
        fumen = osu.fumen_from_osu(raw, course_name, int(course.get("stars") or 1))
        write_fumen(str(path), fumen)
        out.append({
            "id": course_id,
            "label": course.get("label", course_name),
            "stars": course.get("stars", 0),
            "osu_stars": course.get("osu_stars"),
            "version": course.get("version", ""),
            "chart": chart,
            "size": path.stat().st_size,
            "sha1": _sha1_file(path),
        })
    return out


def _convert_audio(source: Path, nub: Path, nsh: Path) -> None:
    if not settings.ps3_at3tool_path.is_file():
        raise RuntimeError(f"ps3_at3tool.exe not found: {settings.ps3_at3tool_path}")
    with tempfile.TemporaryDirectory(prefix="tjarepo-audio-") as tmpdir:
        tmp = Path(tmpdir)
        wav = tmp / "audio.wav"
        at3 = tmp / "audio.at3"
        # ffmpeg (not sox): sox's libmad decoder ignores LAME gapless tags,
        # leaving ~25-50ms of encoder-delay silence at the start of mp3s,
        # which made every osu! note land early relative to the audio.
        _run([
            settings.ffmpeg_path,
            "-y",
            "-loglevel", "error",
            "-i", str(source),
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(wav),
        ])
        # ps3_at3tool.exe is a Windows binary: run it natively on Windows,
        # through Wine everywhere else.
        at3_cmd = [] if os.name == "nt" else [settings.wine_path]
        at3_cmd += [
            str(settings.ps3_at3tool_path),
            "-e",
            "-br", str(settings.audio_bitrate_kbps),
            str(wav),
            str(at3),
        ]
        _run(at3_cmd, env={**os.environ, "WINEDEBUG": "-all"})
        riff = at3.read_bytes()
    header = _nub_header(riff)
    nsh.write_bytes(header)
    nub.write_bytes(header + riff)


def _nub_header(riff: bytes) -> bytes:
    if riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
        raise RuntimeError("AT3 output is not a RIFF/WAVE payload.")
    chunks = _riff_chunks(riff)
    fmt = chunks.get(b"fmt ")
    fact = chunks.get(b"fact")
    if fmt is None or fact is None:
        raise RuntimeError("AT3 RIFF payload is missing fmt or fact chunk.")

    header = bytearray(2048)
    payload_size = len(riff)

    def put32(offset: int, value: int) -> None:
        header[offset:offset + 4] = struct.pack(">I", value & 0xFFFFFFFF)

    def put_bytes(offset: int, value: bytes) -> None:
        header[offset:offset + len(value)] = value

    put32(0x00, 0x00020100)
    put32(0x08, 0x0000009A)
    put32(0x0C, 1)
    put32(0x10, 0x800)
    put32(0x14, payload_size)
    put32(0x18, 0x20)
    put32(0x1C, 0x30)
    put32(0x20, 0x30)
    put_bytes(0x30, b"at3\0")
    put32(0x34, 0x0000009A)
    put32(0x3C, 3)
    put32(0x44, max(0, payload_size - 12))
    put32(0x4C, 0x40)
    put32(0x60, 0xFFFFFFFF)
    put32(0x64, 0xC0800000)
    put32(0x68, 0xC2C60000)
    put32(0x74, 0x42700000)
    put32(0x78, 0x3F800000)
    put32(0x88, 0x3F800000)
    put32(0x8C, 0x3F800000)
    put32(0x90, 0x0B)
    put32(0x9C, 0xC2C80000)
    put32(0xA0, 1000)
    put32(0xA4, 100)
    put32(0xAC, 1)
    put32(0xBC, 0x3F800000)
    put32(0xC0, 20)
    put32(0xD8, 0x3F800000)
    put32(0xDC, 4)
    put32(0xE0, 0x2026)
    put_bytes(0xEC, fmt[:0x34])
    put_bytes(0x120, fact[:0x0C])
    return bytes(header)


def _riff_chunks(riff: bytes) -> dict[bytes, bytes]:
    chunks: dict[bytes, bytes] = {}
    offset = 12
    while offset + 8 <= len(riff):
        name = riff[offset:offset + 4]
        size = struct.unpack("<I", riff[offset + 4:offset + 8])[0]
        data_offset = offset + 8
        if data_offset + size > len(riff):
            break
        chunks[name] = riff[data_offset:data_offset + size]
        offset = data_offset + size + (size % 2)
    return chunks


def _asset_manifest(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    return {"name": relative, "size": path.stat().st_size, "sha1": _sha1_file(path)}


def _run(args: list[str], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or f"Command failed: {args[0]}")


def _package_root(song_id: str) -> Path:
    return settings.convert_root / song_id / "package"


def _manifest_path(song_id: str) -> Path:
    return settings.convert_root / song_id / "manifest.json"


def _status_path(song_id: str) -> Path:
    return settings.convert_root / song_id / "status.json"


def _read_manifest(song_id: str) -> dict[str, Any] | None:
    path = _manifest_path(song_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
