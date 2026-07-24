"""Content-addressed zucchini.sprx storage for remote cabinet updates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from fastapi import UploadFile

from .config import settings


MAX_UPDATE_BYTES = 32 * 1024 * 1024
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,31}\Z")
_UPDATE_ID_RE = re.compile(r"[0-9a-f]{40}\Z")


def _artifact_path(update_id: str) -> Path:
    if not _UPDATE_ID_RE.fullmatch(update_id):
        raise ValueError("Invalid update id")
    return settings.updates_root / f"{update_id}.sprx"


def _metadata_path(update_id: str) -> Path:
    if not _UPDATE_ID_RE.fullmatch(update_id):
        raise ValueError("Invalid update id")
    return settings.updates_root / f"{update_id}.json"


def _filename_flavor(filename: str) -> str:
    lower = filename.lower()
    if "-gex" in lower or "_gex" in lower:
        return "gex"
    if "-hen" in lower or "_hen" in lower:
        return "hen"
    return ""


def _self_flavor(header: bytes) -> str:
    """Read the SELF key revision used by our two release flavors."""
    if len(header) < 10 or header[:4] != b"SCE\0":
        return ""
    key_revision = int.from_bytes(header[8:10], "big")
    if key_revision == 0x8000:
        return "gex"
    if key_revision == 0x0004:
        return "hen"
    return ""


async def store_upload(
    upload: UploadFile, version: str, expected_flavor: str = "", note: str = ""
) -> dict[str, object]:
    """Validate and atomically store one immutable SPRX artifact."""
    version = version.strip()
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(
            "Version must be 1-32 characters using letters, numbers, '.', '_', '+', or '-'"
        )
    filename = Path(upload.filename or "").name
    note = note.strip()
    if len(note) > 500:
        raise ValueError("Update note cannot exceed 500 characters")
    if not filename.lower().endswith(".sprx"):
        raise ValueError("Select a .sprx file")
    named_flavor = _filename_flavor(filename)
    if expected_flavor and named_flavor and named_flavor != expected_flavor:
        raise ValueError(
            f"That file is marked {named_flavor.upper()}, but this cabinet runs {expected_flavor.upper()}"
        )

    settings.updates_root.mkdir(parents=True, exist_ok=True)
    tmp = settings.updates_root / f".upload-{os.getpid()}-{time.time_ns()}.tmp"
    digest = hashlib.sha1()
    total = 0
    first = b""
    try:
        with tmp.open("xb") as fh:
            while True:
                chunk = await upload.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPDATE_BYTES:
                    raise ValueError("SPRX exceeds the 32 MiB update limit")
                if len(first) < 16:
                    first = (first + chunk)[:16]
                digest.update(chunk)
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        if total == 0:
            raise ValueError("SPRX file is empty")
        if first[:4] != b"SCE\0":
            raise ValueError("File is not a signed PS3 SELF/SPRX (missing SCE header)")
        actual_flavor = _self_flavor(first)
        if not actual_flavor:
            raise ValueError("SPRX uses an unsupported SELF key revision")
        if named_flavor and named_flavor != actual_flavor:
            raise ValueError(
                f"Filename says {named_flavor.upper()}, but the SPRX is signed for {actual_flavor.upper()}"
            )
        if expected_flavor and actual_flavor != expected_flavor:
            raise ValueError(
                f"That SPRX is signed for {actual_flavor.upper()}, but this cabinet runs {expected_flavor.upper()}"
            )

        update_id = digest.hexdigest()
        destination = _artifact_path(update_id)
        if destination.exists():
            tmp.unlink()
        else:
            os.replace(tmp, destination)
        item = {
            "id": update_id,
            "sha1": update_id,
            "version": version,
            "size": total,
            "filename": filename,
            "flavor": actual_flavor,
            "note": note,
            "uploaded_at": int(time.time()),
        }
        metadata_tmp = settings.updates_root / f".{update_id}-{time.time_ns()}.json.tmp"
        try:
            metadata_tmp.write_text(json.dumps(item, ensure_ascii=False, indent=1))
            os.replace(metadata_tmp, _metadata_path(update_id))
        finally:
            try:
                metadata_tmp.unlink()
            except FileNotFoundError:
                pass
        return item
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def artifact(update_id: str) -> dict[str, object] | None:
    try:
        path = _artifact_path(update_id)
        size = path.stat().st_size
    except (ValueError, OSError):
        return None
    item: dict[str, object] = {
        "id": update_id,
        "sha1": update_id,
        "path": path,
        "size": size,
    }
    try:
        saved = json.loads(_metadata_path(update_id).read_text())
        if isinstance(saved, dict):
            item.update(saved)
            item["path"] = path
            item["size"] = size
            item["id"] = update_id
            item["sha1"] = update_id
    except (OSError, ValueError):
        pass
    return item


def list_artifacts() -> list[dict[str, object]]:
    """Return stored, annotated builds newest first."""
    items: list[dict[str, object]] = []
    if not settings.updates_root.is_dir():
        return items
    for metadata in settings.updates_root.glob("*.json"):
        update_id = metadata.stem
        item = artifact(update_id)
        if item is None or "version" not in item:
            continue
        public = {key: value for key, value in item.items() if key != "path"}
        items.append(public)
    return sorted(items, key=lambda item: int(item.get("uploaded_at", 0)), reverse=True)
