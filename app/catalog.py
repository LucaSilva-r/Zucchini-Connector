from __future__ import annotations

import hashlib
import re
from pathlib import Path
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
        return sorted(merged, key=lambda s: str(s["title"]).casefold())

    if category == "root":
        candidates = [p for p in root.iterdir() if p.is_dir() and not re.match(r"^\d{2} ", p.name)]
        return _songs_from_dirs(candidates, "root")

    base = _safe_path(category)
    if base is None or not base.is_dir():
        return []
    return _songs_from_dirs([p for p in base.iterdir() if p.is_dir()], category)


def song(song_id: str) -> dict[str, Any] | None:
    for entry in songs():
        if entry["id"] == song_id:
            return entry
    return None


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
