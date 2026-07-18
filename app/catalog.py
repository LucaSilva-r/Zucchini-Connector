from __future__ import annotations

import functools
import hashlib
import json
import re
import time
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any

from .config import settings
from . import osu


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

_LIBRARY_LOCK = RLock()
_LIBRARY_CACHE: dict[str, Any] | None = None
_LIBRARY_DIRTY = Event()
_WATCH_ACTIVE = False
# Fallback when the filesystem watch is unavailable: age out the cache so new
# songs still appear without a restart, just slower.
_LIBRARY_TTL_SECONDS = 60.0
_LIBRARY_AT = 0.0


# Canonical Taiko genre folders, in game menu order. Created under both song
# roots at startup; category id == title == the plain folder name.
CATEGORY_ORDER = (
    "Pop",
    "Anime",
    "Vocaloid",
    "Children and Folk",
    "Variety",
    "Classical",
    "Game Music",
    "Namco Original",
)


def ensure_category_dirs() -> None:
    for root in (settings.tja_root, settings.osu_root):
        for name in CATEGORY_ORDER:
            (root / name).mkdir(parents=True, exist_ok=True)


def _category_sort_key(name: str) -> tuple[int, str]:
    try:
        return (CATEGORY_ORDER.index(name), "")
    except ValueError:
        return (len(CATEGORY_ORDER), name.casefold())


def category_names() -> list[str]:
    """Accepted categories: the TJA root's subfolders (plain names), plus any
    extra OSU subfolders, in game menu order."""
    names: dict[str, None] = {}
    for root in (settings.tja_root, settings.osu_root):
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    names[child.name] = None
    return sorted(names, key=_category_sort_key)


def categories() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in category_names():
        count = len(songs(name))
        if count == 0:
            continue
        entries.append({
            "id": name,
            "title": name,
            "path": name,
            "song_count": count,
        })
    return entries


# A library build lists every category twice (once for counts, once for the
# payload); this short-lived cache makes that one directory walk. Invalidated
# at the start of every refresh_library, so rebuilds always see fresh files.
_SONGS_TTL_SECONDS = 2.0
_SONGS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def songs(category: str | None = None) -> list[dict[str, Any]]:
    if not category:
        merged: list[dict[str, Any]] = []
        for name in category_names():
            merged.extend(songs(name))
        result = sorted(merged, key=lambda s: str(s["title"]).casefold())
        _remember_songs(result)
        return result

    cached = _SONGS_CACHE.get(category)
    if cached is not None and time.monotonic() - cached[0] < _SONGS_TTL_SECONDS:
        return cached[1]

    base = _safe_path(category)
    candidates = (
        [p for p in base.iterdir() if p.is_dir()]
        if base is not None and base.is_dir()
        else []
    )
    result = _songs_from_dirs(candidates, category) + _songs_from_osz(category)
    result.sort(key=lambda s: str(s["title"]).casefold())
    _remember_songs(result)
    _SONGS_CACHE[category] = (time.monotonic(), result)
    return result


def library() -> dict[str, Any]:
    """Whole library in one payload (categories + songs id/title/category) with
    a content hash. The PS3 caches this and only re-downloads when the hash
    changes, so it never pages the server during navigation.

    Always served from memory: a build is a full filesystem re-scan taking
    seconds, so it happens at startup and in the background whenever the
    filesystem watch reports a change — never on the request path (except the
    very first request after boot, before the warm thread finishes)."""
    cached = _LIBRARY_CACHE
    if cached is not None and (
        _WATCH_ACTIVE or time.monotonic() - _LIBRARY_AT < _LIBRARY_TTL_SECONDS
    ):
        return cached
    return refresh_library()


def refresh_library() -> dict[str, Any]:
    """Rescan the song roots and swap in a fresh library payload."""
    global _LIBRARY_CACHE, _LIBRARY_AT
    with _LIBRARY_LOCK:
        _invalidate_osz_files()
        _SONGS_CACHE.clear()
        built = _build_library()
        _LIBRARY_CACHE = built
        _LIBRARY_AT = time.monotonic()
        return built


def start_library_watch() -> bool:
    """Watch the song roots and rebuild the library in the background after
    changes settle. Returns True when the watch is running; on failure the
    TTL fallback in library() keeps new songs appearing, just slower."""
    global _WATCH_ACTIVE
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        print("[connector] watchdog not installed; using library TTL rescan", flush=True)
        return False

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event: Any) -> None:
            _LIBRARY_DIRTY.set()

    observer = Observer()
    scheduled = 0
    for root in (settings.tja_root, settings.osu_root):
        if root.is_dir():
            observer.schedule(_Handler(), str(root), recursive=True)
            scheduled += 1
    if not scheduled:
        return False
    observer.daemon = True
    observer.start()

    def _worker() -> None:
        while True:
            _LIBRARY_DIRTY.wait()
            # Debounce: a song drop is many events (copies, temp files); wait
            # until the filesystem has been quiet for a moment, then rebuild.
            while True:
                _LIBRARY_DIRTY.clear()
                time.sleep(2.0)
                if not _LIBRARY_DIRTY.is_set():
                    break
            try:
                built = refresh_library()
                print(
                    f"[connector] library rescan: {len(built['songs'])} songs",
                    flush=True,
                )
            except Exception as exc:  # keep the watch alive on scan errors
                print(f"[connector] library rescan failed: {exc}", flush=True)

    Thread(target=_worker, daemon=True, name="tjarepo-library-watch").start()
    _WATCH_ACTIVE = True
    return True


def _build_library() -> dict[str, Any]:
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
                    # Always present so the PS3's forward-scan JSON parser
                    # cannot consume a neighbouring song's fallback title.
                    "display_title": s.get("display_title") or s["title"],
                    # Always present (even "") so the PS3's forward-scan parser
                    # never grabs a neighbouring song's subtitle.
                    "subtitle": s.get("subtitle") or "",
                    "category": str(c["id"]),
                    # Stable public origin metadata for lightweight clients.
                    # Keep the converter's internal "osz" dispatch name out
                    # of the API: users see charts as TJA or osu! sources.
                    "source": (
                        "osu" if s.get("source_type") == "osz" else "tja"
                    ),
                    # Flat "id:stars,..." in canonical order so the PS3 gets star
                    # counts straight from the index — no per-song conversion.
                    "diffs": _diffs_str(s.get("courses") or []),
                    # Short source_hash prefix. The PS3 compares this against
                    # its cached manifest's source_hash to spot songs whose
                    # source (or converter) changed, with no extra requests.
                    "rev": source_hash(s)[:12],
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


def refresh_after_mutation() -> dict[str, Any]:
    """Synchronously invalidate every catalog cache after an admin write.

    _source_hash_cached is keyed on (path, size, mtime), so changed files miss
    the cache naturally — no wholesale clear, which would force re-reading
    every source file on the next build. The rebuild repopulates the song
    index via _remember_songs, so no separate warm pass is needed."""
    global _LIBRARY_CACHE, _LIBRARY_AT, _SONG_INDEX_AT
    with _SONG_INDEX_LOCK:
        _SONG_INDEX.clear()
        _SONG_INDEX_AT = 0.0
    with _LIBRARY_LOCK:
        _LIBRARY_CACHE = None
        _LIBRARY_AT = 0.0
    built = refresh_library()
    with _SONG_INDEX_LOCK:
        _SONG_INDEX_AT = time.monotonic()
    return built


def _remember_songs(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    with _SONG_INDEX_LOCK:
        for entry in entries:
            _SONG_INDEX[str(entry["id"])] = entry


def source_hash(entry: dict[str, Any]) -> str:
    files: list[tuple[str, str, int, int]] = []
    for key in ("tja_path", "audio_path", "osz_path"):
        path_value = entry.get(key)
        if not path_value:
            continue
        path = Path(str(path_value))
        if not path.is_file():
            continue
        st = path.stat()
        files.append((str(path), path.name, st.st_size, st.st_mtime_ns))
    return _source_hash_cached(
        str(entry.get("source_path", "")),
        entry.get("source_type") == "osz",
        tuple(files),
    )


@functools.lru_cache(maxsize=65536)
def _source_hash_cached(
    source_path: str,
    is_osz: bool,
    files: tuple[tuple[str, str, int, int], ...],
) -> str:
    """Full-file sha1, memoized on (path, size, mtime) so /library can embed a
    per-song rev without re-reading every source on each request. The digest
    input must stay byte-identical to the historical formula: changing it would
    flip every stored manifest's source_hash and force a global re-download."""
    h = hashlib.sha1()
    h.update(source_path.encode())
    if is_osz:
        h.update(f"|osu-converter-v{osu.CONVERTER_VERSION}|".encode())
    for path, name, size, mtime_ns in files:
        h.update(f"|{name}|{size}|{mtime_ns // 1_000_000_000}|".encode())
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def public_song(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out.pop("tja_path", None)
    out.pop("audio_path", None)
    out.pop("osz_path", None)
    out.pop("audio_member", None)
    out["courses"] = [
        {key: value for key, value in course.items() if key != "osu_member"}
        for course in out.get("courses", [])
    ]
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
        relative = real_tja.relative_to(settings.tja_root)
    except ValueError:
        return None

    meta = _parse_tja_meta(real_tja)
    relative_tja = relative.as_posix()
    audio = _audio_path(real_tja.parent, meta.get("wave"))
    return {
        "id": "tja_" + hashlib.sha1(relative_tja.encode()).hexdigest()[:16],
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


def _songs_from_osz(category: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _osz_files():
        if _osu_folder(path) != category:
            continue
        entry = _entry_for_osz(path, category)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_for_osz(path: Path, category: str) -> dict[str, Any] | None:
    try:
        real_path = path.resolve()
        relative = real_path.relative_to(settings.osu_root)
    except ValueError:
        return None
    meta = osu.inspect_osz(real_path)
    if meta is None:
        return None
    relative_path = relative.as_posix()
    return {
        "id": "osu_" + hashlib.sha1(relative_path.encode()).hexdigest()[:16],
        "title": meta["title"] or real_path.stem,
        "display_title": meta.get("display_title") or meta["title"] or real_path.stem,
        "subtitle": meta["subtitle"],
        "category": category,
        "source_type": "osz",
        "source_path": relative_path,
        "folder_path": relative.parent.as_posix(),
        "osz_path": str(real_path),
        "audio_member": meta["audio_member"],
        "audio_name": Path(str(meta["audio_member"])).name,
        "creator": meta["creator"],
        "courses": meta["courses"],
    }


# One library rescan calls _osz_files once per category; cache the walk for a
# couple of seconds so a scan does it once. refresh_library invalidates it, so
# uploads never see a stale listing.
_OSZ_FILES_TTL_SECONDS = 2.0
_OSZ_FILES_CACHE: tuple[float, list[Path]] | None = None


def _invalidate_osz_files() -> None:
    global _OSZ_FILES_CACHE
    _OSZ_FILES_CACHE = None


def _osz_files() -> list[Path]:
    global _OSZ_FILES_CACHE
    cached = _OSZ_FILES_CACHE
    now = time.monotonic()
    if cached is not None and now - cached[0] < _OSZ_FILES_TTL_SECONDS:
        return cached[1]
    if not settings.osu_root.is_dir():
        return []
    result = sorted(
        [
            path
            for path in settings.osu_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".osz"
        ],
        key=lambda path: path.as_posix().casefold(),
    )
    _OSZ_FILES_CACHE = (now, result)
    return result


def _osu_folder(path: Path) -> str | None:
    """OSZ files live directly inside a category folder; anything at the OSU
    root (or nested deeper) is not part of an accepted category."""
    try:
        relative = path.resolve().relative_to(settings.osu_root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) == 2 else None


def _parse_tja_meta(path: Path) -> dict[str, Any]:
    """Mtime-keyed cache: a library rescan touches every TJA, so parsing must
    only happen for files that actually changed."""
    try:
        stat = path.stat()
    except OSError:
        return {"title": "", "subtitle": None, "wave": None, "courses": []}
    return _parse_tja_meta_cached(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=16384)
def _parse_tja_meta_cached(path_str: str, _size: int, _mtime_ns: int) -> dict[str, Any]:
    path = Path(path_str)
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
    if not settings.tja_root.is_dir():
        return None
    try:
        path = (settings.tja_root / relative).resolve()
        path.relative_to(settings.tja_root)
        return path
    except ValueError:
        return None
