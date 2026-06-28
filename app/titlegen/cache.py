from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from PIL import Image

from .png import TITLE_RENDER_VERSION, TITLE_VARIANTS, generate_title_png
from .. import catalog
from ..config import settings


VARIANT_TO_FILE = {
    "hshort": "songname_hshort.png",
    "hlong": "songname_hlong.png",
    "vshort": "songname_vshort.png",
    "vlong": "songname_vlong.png",
}

_WARM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="titlegen")
_LOCKS: dict[str, Lock] = {}
_LOCKS_GUARD = Lock()


def title_image(song_id: str, variant: str) -> Path | None:
    filename = VARIANT_TO_FILE.get(variant)
    if filename is None:
        return None

    entry = catalog.song(song_id)
    if entry is None:
        return None

    source_hash = _title_source_hash(entry)
    root = _cache_root(song_id)
    meta_path = root / "title.json"
    image_path = root / filename

    if not _cache_ready(root, meta_path, source_hash, filename):
        with _song_lock(song_id):
            if not _cache_ready(root, meta_path, source_hash, filename):
                _generate(entry, root, meta_path, source_hash, filename)

    if image_path.is_file():
        return image_path
    return None


def title_argb(song_id: str, variant: str) -> tuple[Path, int, int] | None:
    png_path = title_image(song_id, variant)
    if png_path is None:
        return None

    root = png_path.parent
    raw_path = root / f"{png_path.stem}.argb"
    with _song_lock(song_id):
        if (not raw_path.is_file() or
                raw_path.stat().st_mtime < png_path.stat().st_mtime):
            with Image.open(png_path) as img:
                rgba = img.convert("RGBA")
                r, g, b, a = rgba.split()
                raw_path.write_bytes(Image.merge("RGBA", (a, r, g, b)).tobytes())
    width, height, _ = TITLE_VARIANTS[png_path.name]
    return raw_path, width, height


def warm_title_cache(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        song_id = str(entry.get("id", ""))
        if song_id:
            _WARM_POOL.submit(title_argb, song_id, "vshort")


def title_links(song_id: str) -> dict[str, str]:
    return {
        key: f"/api/tjarepo/songs/{song_id}/title/{key}.png"
        for key in VARIANT_TO_FILE
    }


def _cache_root(song_id: str) -> Path:
    return settings.title_cache_root / song_id


def _song_lock(song_id: str) -> Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(song_id)
        if lock is None:
            lock = Lock()
            _LOCKS[song_id] = lock
        return lock


def _cache_ready(root: Path, meta_path: Path, source_hash: str, filename: str) -> bool:
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return False
    if meta.get("source_hash") != source_hash:
        return False
    return (root / filename).is_file()


def _generate(
    entry: dict[str, Any],
    root: Path,
    meta_path: Path,
    source_hash: str,
    filename: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not _meta_matches(meta_path, source_hash):
        for old in root.glob("songname_*"):
            if old.is_file():
                old.unlink()
    generate_title_png(
        root,
        filename,
        str(entry.get("title") or "Untitled"),
        str(entry.get("subtitle") or "") or None,
        str(entry.get("category") or ""),
    )
    generated = sorted(f"title/{p.name}" for p in root.glob("songname_*.png"))
    meta_path.write_text(
        json.dumps(
            {
                "song_id": entry["id"],
                "title": entry.get("title", ""),
                "subtitle": entry.get("subtitle", ""),
                "category": entry.get("category", ""),
                "source_hash": source_hash,
                "variants": generated,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def _meta_matches(meta_path: Path, source_hash: str) -> bool:
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return False
    return meta.get("source_hash") == source_hash


def _title_source_hash(entry: dict[str, Any]) -> str:
    h = hashlib.sha1()
    h.update(f"title-render-v{TITLE_RENDER_VERSION}|".encode())
    h.update(str(entry.get("source_path", "")).encode())
    h.update(f"|{entry.get('category', '')}|".encode())
    for key in ("title", "subtitle"):
        h.update(f"|{entry.get(key, '')}|".encode("utf-8"))
    path_value = entry.get("tja_path")
    if path_value:
        path = Path(str(path_value))
        if path.is_file():
            st = path.stat()
            h.update(
                f"|{path.name}|{st.st_size}|{st.st_mtime_ns}|".encode()
            )
    return h.hexdigest()
