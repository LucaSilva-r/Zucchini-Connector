from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from . import cabinets, catalog, converter, osu
from .config import settings


_AUDIO_SUFFIXES = {".ogg", ".wav", ".mp3", ".flac"}
_TJA_ALLOWED_SUFFIXES = _AUDIO_SUFFIXES | {".tja", ".png", ".jpg", ".jpeg"}


# Cache keyed on (library hash, broken set): dumping + sha1-ing ~3000 songs
# on every /library request adds up.
_AVAILABLE_CACHE: tuple[tuple[str, frozenset[str]], dict[str, Any]] | None = None


def available_library() -> dict[str, Any]:
    global _AVAILABLE_CACHE
    broken = converter.broken_song_ids()
    source = catalog.library()
    cache_key = (str(source["hash"]), frozenset(broken))
    if _AVAILABLE_CACHE is not None and _AVAILABLE_CACHE[0] == cache_key:
        return _AVAILABLE_CACHE[1]
    songs = [song for song in source["songs"] if song["id"] not in broken]
    counts: dict[str, int] = {}
    for song in songs:
        category = str(song["category"])
        counts[category] = counts.get(category, 0) + 1
    categories = [
        {**category, "song_count": counts.get(str(category["id"]), 0)}
        for category in source["categories"]
        if counts.get(str(category["id"]), 0)
    ]
    payload = {"categories": categories, "songs": songs}
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    result = {"hash": digest, **payload}
    _AVAILABLE_CACHE = (cache_key, result)
    return result


def management_library() -> dict[str, Any]:
    songs: list[dict[str, Any]] = []
    for entry in catalog.library()["songs"]:
        songs.append({
            **entry,
            **converter.management_status(str(entry["id"]), rev=str(entry.get("rev") or "")),
        })
    return {
        "categories": upload_categories(),
        "songs": songs,
    }


def upload_categories() -> list[dict[str, str]]:
    return [{"id": name, "title": name} for name in catalog.category_names()]


async def upload_osz(file: UploadFile, category: str) -> dict[str, Any]:
    if Path(file.filename or "").suffix.lower() != ".osz":
        raise ValueError("Choose an .osz file")
    destination_dir = _category_path(settings.osu_root, category)
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(file.filename or "song.osz", ".osz")
    destination = _unique_path(destination_dir / filename)
    temp = Path(tempfile.mkstemp(prefix=".upload-", suffix=".osz", dir=destination_dir)[1])
    try:
        await _copy_upload(file, temp)
        if osu.inspect_osz(temp) is None:
            raise ValueError("The OSZ does not contain a supported Taiko chart and audio")
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
        await file.close()
    catalog.refresh_after_mutation()
    return _entry_for_source(destination)


async def upload_tja(files: list[UploadFile], category: str) -> dict[str, Any]:
    if not files:
        raise ValueError("Choose a TJA package")
    destination_dir = _category_path(settings.tja_root, category)
    destination_dir.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    for upload in files:
        name = Path(upload.filename or "").name
        if not name or Path(name).suffix.lower() not in _TJA_ALLOWED_SUFFIXES:
            raise ValueError(f"Unsupported package file: {name or 'unnamed'}")
        if name.casefold() in names:
            raise ValueError(f"Duplicate package filename: {name}")
        names.add(name.casefold())
    if not any(Path(upload.filename or "").suffix.lower() == ".tja" for upload in files):
        raise ValueError("A TJA package must contain at least one .tja file")

    tja = next(upload for upload in files if Path(upload.filename or "").suffix.lower() == ".tja")
    raw_tja_name = (tja.filename or "song.tja").replace("\\", "/")
    parts = [part for part in raw_tja_name.split("/") if part]
    package_name = _safe_stem(parts[-2] if len(parts) > 1 else Path(parts[-1]).stem)
    destination = _unique_path(destination_dir / package_name)
    temp = Path(tempfile.mkdtemp(prefix=".upload-", dir=destination_dir))
    try:
        for upload in files:
            target = temp / Path(upload.filename or "").name
            await _copy_upload(upload, target)
        for tja in temp.glob("*.tja"):
            meta = catalog._parse_tja_meta(tja)
            if catalog._audio_path(temp, meta.get("wave")) is None:
                raise ValueError(f"No readable audio file for {tja.name}")
        os.replace(temp, destination)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
        for upload in files:
            await upload.close()
    catalog.refresh_after_mutation()
    return _entry_for_source(destination)


def delete_song(song_id: str) -> dict[str, str]:
    entry = catalog.song(song_id)
    if entry is None:
        raise FileNotFoundError("Song not found")
    _delete_song_files(song_id, entry)
    catalog.refresh_after_mutation()
    cabinets.remove_songs_everywhere({song_id})
    return {"status": "deleted", "song_id": song_id}


def delete_songs(song_ids: list[str]) -> dict[str, Any]:
    """Bulk delete: remove all files first, then refresh the catalog once —
    a per-song refresh would be a full rescan per song."""
    deleted: list[str] = []
    missing: list[str] = []
    for song_id in dict.fromkeys(song_ids):
        entry = catalog.song(song_id)
        if entry is None:
            missing.append(song_id)
            continue
        _delete_song_files(song_id, entry)
        deleted.append(song_id)
    if deleted:
        catalog.refresh_after_mutation()
        cabinets.remove_songs_everywhere(set(deleted))
    return {"status": "deleted", "deleted": deleted, "missing": missing}


def _delete_song_files(song_id: str, entry: dict[str, Any]) -> None:
    converter.remove_artifacts(song_id)
    if entry.get("source_type") == "osz":
        Path(str(entry["osz_path"])).unlink()
    else:
        tja = Path(str(entry["tja_path"]))
        audio = Path(str(entry["audio_path"])) if entry.get("audio_path") else None
        tja.unlink()
        if audio and audio.is_file():
            used_elsewhere = any(
                other != tja and catalog._audio_path(other.parent, catalog._parse_tja_meta(other).get("wave")) == audio
                for other in tja.parent.glob("*.tja")
            )
            if not used_elsewhere:
                audio.unlink()
        try:
            tja.parent.rmdir()
        except OSError:
            pass


def _category_path(root: Path, category: str) -> Path:
    valid = {item["id"] for item in upload_categories()}
    if category not in valid:
        raise ValueError("Unknown song category")
    resolved = (root / category).resolve()
    resolved.relative_to(root)
    return resolved


async def _copy_upload(upload: UploadFile, destination: Path) -> None:
    with destination.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            output.write(chunk)


def _safe_filename(name: str, suffix: str) -> str:
    stem = _safe_stem(Path(name).stem)
    return f"{stem}{suffix}"


def _safe_stem(value: str) -> str:
    clean = re.sub(r"[^\w .()\[\]-]+", "_", value, flags=re.UNICODE).strip(" .")
    return clean[:120] or "song"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not choose a unique destination name")


def _entry_for_source(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    for entry in catalog.songs():
        source = entry.get("osz_path") or entry.get("tja_path")
        if source and (resolved == Path(str(source)).resolve() or resolved in Path(str(source)).resolve().parents):
            return catalog.public_song(entry)
    return {"status": "uploaded", "path": path.name}
