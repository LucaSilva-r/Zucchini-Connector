from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any

from .config import settings


COURSE_MAP = {
    "0": ("e", "Easy"),
    "easy": ("e", "Easy"),
    "1": ("n", "Normal"),
    "normal": ("n", "Normal"),
    "2": ("h", "Hard"),
    "hard": ("h", "Hard"),
    "3": ("m", "Oni"),
    "oni": ("m", "Oni"),
    "extreme": ("m", "Oni"),
    "4": ("x", "Ura"),
    "edit": ("x", "Ura"),
    "ura": ("x", "Ura"),
}

# Canonical difficulty order (Easy..Ura) for the flat "diffs" index string.
_DIFF_ORDER = ("e", "n", "h", "m", "x")

_SONG_INDEX_TTL_SECONDS = 60.0
_SONG_INDEX_LOCK = RLock()
_SONG_INDEX: dict[str, dict[str, Any]] = {}
_SONG_INDEX_AT = 0.0


def categories() -> list[dict[str, Any]]:
    root = settings.ese_root
    if not root.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for child in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.casefold()):
        if not re.match(r"^\d{2} ", child.name):
            continue
        count = len(songs(child.name))
        if count == 0:
            continue
        entries.append({
            "id": child.name,
            "title": re.sub(r"^\d{2}\s+", "", child.name),
            "path": child.name,
            "song_count": count,
        })

    root_songs = songs("root")
    if root_songs:
        entries.insert(0, {
            "id": "root",
            "title": "Unsorted",
            "path": "root",
            "song_count": len(root_songs),
        })

    return sorted(entries, key=lambda e: str(e["id"]).casefold())


def songs(category: str | None = None) -> list[dict[str, Any]]:
    root = settings.ese_root
    if not root.is_dir():
        return []

    if not category:
        merged: list[dict[str, Any]] = []
        for entry in categories():
            merged.extend(songs(str(entry["id"])))
        result = sorted(merged, key=lambda s: str(s["title"]).casefold())
        _remember_songs(result)
        return result

    if category == "root":
        candidates = [p for p in root.iterdir() if p.is_dir() and not re.match(r"^\d{2} ", p.name)]
        result = _songs_from_dirs(candidates, "root")
        _remember_songs(result)
        return result

    base = _safe_path(category)
    if base is None or not base.is_dir():
        return []
    result = _songs_from_dirs([p for p in base.iterdir() if p.is_dir()], category)
    _remember_songs(result)
    return result


def library() -> dict[str, Any]:
    """Whole library in one payload (categories + songs id/title/category) with
    a content hash. The PS3 caches this and only re-downloads when the hash
    changes, so it never pages the server during navigation."""
    cats = categories()
    cat_out = [
        {"id": c["id"], "title": c["title"], "song_count": c["song_count"]}
        for c in cats
    ]
    song_out: list[dict[str, str]] = []
    for c in cats:
        for s in songs(str(c["id"])):
            song_out.append(
                {
                    "id": s["id"],
                    "title": s["title"],
                    # Always present (even "") so the PS3's forward-scan parser
                    # never grabs a neighbouring song's subtitle.
                    "subtitle": s.get("subtitle") or "",
                    "category": str(c["id"]),
                    # Flat "id:stars,..." in canonical order so the PS3 gets star
                    # counts straight from the index — no per-song conversion.
                    "diffs": _diffs_str(s.get("courses") or []),
                }
            )
    payload = {"categories": cat_out, "songs": song_out}
    h = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {"hash": h, **payload}


def _diffs_str(courses: list[dict[str, Any]]) -> str:
    stars = {str(c.get("id")): int(c.get("stars") or 0) for c in courses}
    return ",".join(f"{d}:{stars[d]}" for d in _DIFF_ORDER if d in stars)


def library_hash() -> str:
    return library()["hash"]


def song(song_id: str) -> dict[str, Any] | None:
    cached = _SONG_INDEX.get(song_id)
    if cached is not None:
        return cached

    now = time.monotonic()
    global _SONG_INDEX_AT
    if now - _SONG_INDEX_AT > _SONG_INDEX_TTL_SECONDS:
        with _SONG_INDEX_LOCK:
            now = time.monotonic()
            if now - _SONG_INDEX_AT > _SONG_INDEX_TTL_SECONDS:
                _SONG_INDEX.clear()
                _SONG_INDEX.update({entry["id"]: entry for entry in songs()})
                _SONG_INDEX_AT = now
    return _SONG_INDEX.get(song_id)


def warm_song_index() -> int:
    entries = songs()
    global _SONG_INDEX_AT
    with _SONG_INDEX_LOCK:
        _SONG_INDEX.clear()
        _SONG_INDEX.update({entry["id"]: entry for entry in entries})
        _SONG_INDEX_AT = time.monotonic()
    return len(_SONG_INDEX)


def _remember_songs(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    with _SONG_INDEX_LOCK:
        for entry in entries:
            _SONG_INDEX[str(entry["id"])] = entry


def source_hash(entry: dict[str, Any]) -> str:
    h = hashlib.sha1()
    h.update(str(entry.get("source_path", "")).encode())
    for key in ("tja_path", "audio_path"):
        path_value = entry.get(key)
        if not path_value:
            continue
        path = Path(str(path_value))
        if not path.is_file():
            continue
        st = path.stat()
        h.update(f"|{path.name}|{st.st_size}|{int(st.st_mtime)}|".encode())
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def public_song(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out.pop("tja_path", None)
    out.pop("audio_path", None)
    song_id = str(out.get("id", ""))
    out["title_images"] = {
        "hshort": f"/api/tjarepo/songs/{song_id}/title/hshort.png",
        "hlong": f"/api/tjarepo/songs/{song_id}/title/hlong.png",
        "vshort": f"/api/tjarepo/songs/{song_id}/title/vshort.png",
        "vlong": f"/api/tjarepo/songs/{song_id}/title/vlong.png",
    }
    return out


def _songs_from_dirs(dirs: list[Path], category: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for song_dir in sorted(dirs, key=lambda p: p.name.casefold()):
        for tja in sorted(song_dir.glob("*.tja"), key=lambda p: p.name.casefold()):
            entry = _entry_for_tja(tja, category)
            if entry is not None:
                result.append(entry)
    return sorted(result, key=lambda s: str(s["title"]).casefold())


def _entry_for_tja(tja: Path, category: str) -> dict[str, Any] | None:
    try:
        real_tja = tja.resolve()
        relative = real_tja.relative_to(settings.ese_root)
    except ValueError:
        return None

    meta = _parse_tja_meta(real_tja)
    relative_tja = relative.as_posix()
    audio = _audio_path(real_tja.parent, meta.get("wave"))
    return {
        "id": "ese_" + hashlib.sha1(relative_tja.encode()).hexdigest()[:16],
        "title": meta["title"] or real_tja.parent.name,
        "subtitle": meta["subtitle"],
        "category": category,
        "source_path": relative_tja,
        "folder_path": relative.parent.as_posix(),
        "tja_path": str(real_tja),
        "audio_path": str(audio) if audio else None,
        "audio_name": audio.name if audio else None,
        "courses": meta["courses"],
    }


def _parse_tja_meta(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp932")

    title = ""
    subtitle = None
    wave = None
    current_course: dict[str, Any] | None = None
    courses: dict[str, dict[str, Any]] = {}

    for raw in text.splitlines():
        line = re.sub(r"^\ufeff", "", raw.strip())
        if not line or line.startswith("//"):
            continue
        match = re.match(r"^([A-Z0-9]+)\s*:\s*(.*)$", line, flags=re.I)
        if not match:
            continue
        key = match.group(1).upper()
        value = match.group(2).strip()
        if key == "TITLE" and not title:
            title = value
        elif key == "SUBTITLE" and subtitle is None:
            subtitle = value.lstrip("-")
        elif key == "WAVE" and wave is None:
            wave = value
        elif key == "COURSE":
            normalized = COURSE_MAP.get(value.lower())
            if normalized:
                current_course = {"id": normalized[0], "label": normalized[1], "stars": 0}
                courses[current_course["id"]] = current_course
        elif key == "LEVEL" and current_course is not None:
            try:
                current_course["stars"] = int(float(value))
            except ValueError:
                current_course["stars"] = 0

    return {"title": title, "subtitle": subtitle, "wave": wave, "courses": list(courses.values())}


def _audio_path(directory: Path, wave: str | None) -> Path | None:
    if wave and ".." not in wave:
        candidate = (directory / wave).resolve()
        if candidate.is_file():
            return candidate
    for pattern in ("*.ogg", "*.wav", "*.mp3", "*.flac"):
        matches = sorted(directory.glob(pattern), key=lambda p: p.name.casefold())
        if matches:
            return matches[0].resolve()
    return None


def _safe_path(relative: str) -> Path | None:
    if "\0" in relative or ".." in relative or relative.startswith("/"):
        return None
    try:
        path = (settings.ese_root / relative).resolve()
        path.relative_to(settings.ese_root)
        return path
    except ValueError:
        return None
