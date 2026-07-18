"""One-time migration to the storage/SONGS layout.

Song ids are sha1(path relative to the song root), so moving ESE -> SONGS/TJA
and renaming "NN Category" folders to plain names changed every id. This
script rescues the expensive state instead of regenerating it:

- renames each CONVERTED/<old_id> package to its new id (dir, chart bins,
  SONG_* assets) and rewrites manifest.json / status.json, including the
  recomputed source_hash;
- remaps cabinet selections to the new ids and bumps selection_seq so
  cabinets resync.

Old ids are recovered from the manifests' stored source_path. Run from the
repo root, inside the venv, with the server stopped:

    python scripts/migrate_songs_layout.py          # dry run
    python scripts/migrate_songs_layout.py --apply
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "app"))

from app import catalog  # noqa: E402
from app.config import settings  # noqa: E402

APPLY = "--apply" in sys.argv


def new_source_path(old: str) -> str:
    head, sep, rest = old.partition("/")
    return re.sub(r"^\d{2}\s+", "", head) + sep + rest


def old_id_for(prefix: str, old_relpath: str) -> str:
    return prefix + hashlib.sha1(old_relpath.encode()).hexdigest()[:16]


def main() -> int:
    entries = catalog.songs()
    by_path = {str(e["source_path"]): e for e in entries}
    valid_ids = {str(e["id"]) for e in entries}
    print(f"catalog: {len(entries)} songs under the new layout")

    # New plain name -> old numbered folder. Manifests override these defaults,
    # which keeps the script recoverable even if an earlier apply was stopped
    # after migrating every package in one category but before cabinet state.
    old_prefix = {
        name: f"{index:02d} {name}"
        for index, name in enumerate(catalog.CATEGORY_ORDER, start=1)
    }
    plans: list[tuple[Path, str, str, dict]] = []
    skipped = 0

    for manifest_path in sorted(settings.convert_root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            print(f"  ! unreadable manifest: {manifest_path}")
            continue
        old_id = str(manifest.get("id") or manifest_path.parent.name)
        old_source = str(manifest.get("source_path") or "")
        new_source = new_source_path(old_source)
        old_head = old_source.partition("/")[0]
        new_head = new_source.partition("/")[0]
        if old_head != new_head:
            old_prefix[new_head] = old_head
        entry = by_path.get(new_source)
        if entry is None:
            print(f"  ! no source for {old_id} ({new_source}); leaving as-is")
            skipped += 1
            continue
        new_id = str(entry["id"])
        if new_id == old_id:
            continue
        plans.append((manifest_path.parent, old_id, new_id, manifest))

    print(f"packages to migrate: {len(plans)} (skipped {skipped})")

    id_map: dict[str, str] = {}
    for directory, old_id, new_id, manifest in plans:
        id_map[old_id] = new_id
        package = directory / "package"
        renames: list[tuple[Path, Path]] = []
        for course in manifest.get("courses", []):
            old_chart = str(course["chart"])
            new_chart = old_chart.replace(old_id, new_id)
            if old_chart != new_chart:
                renames.append((package / old_chart, package / new_chart))
            course["chart"] = new_chart
        for asset in manifest.get("assets", []):
            old_name = str(asset["name"])
            new_name = old_name.replace(old_id.upper(), new_id.upper())
            if old_name != new_name:
                renames.append((package / old_name, package / new_name))
            asset["name"] = new_name

        entry = by_path[new_source_path(str(manifest["source_path"]))]
        manifest["id"] = new_id
        manifest["source_path"] = str(entry["source_path"])
        manifest["source_hash"] = catalog.source_hash(entry)

        if not APPLY:
            continue
        for src, dst in renames:
            if src.is_file():
                src.rename(dst)
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        status_path = directory / "status.json"
        try:
            status = json.loads(status_path.read_text())
        except (OSError, ValueError):
            status = {}
        status.update({
            "song_id": new_id,
            "source_hash": manifest["source_hash"],
        })
        if "manifest" in status:
            status["manifest"] = manifest
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
        directory.rename(directory.with_name(new_id))

    # Full id map (converted or not) via the learned folder-prefix map, so
    # cabinet selections of unconverted songs survive too.
    for entry in entries:
        source = str(entry["source_path"])
        if entry.get("source_type") == "osz":
            old = old_id_for("osu_", source)
        else:
            head, sep, rest = source.partition("/")
            old_head = old_prefix.get(head, head)
            old = old_id_for("ese_", old_head + sep + rest)
        id_map.setdefault(old, str(entry["id"]))

    remapped_cabs = 0
    for cab_path in sorted(settings.cabinets_root.glob("*.json")):
        try:
            cab = json.loads(cab_path.read_text())
        except (OSError, ValueError):
            continue
        changed = False
        for key in ("selection", "queued_selection"):
            ids = cab.get(key)
            if not ids:
                continue
            mapped = [id_map.get(i, i) for i in ids]
            dropped = [i for i in mapped if i not in valid_ids]
            if dropped:
                print(f"  ! {cab_path.name}: dropping {len(dropped)} unmappable ids from {key}")
                mapped = [i for i in mapped if i not in set(dropped)]
            if mapped != ids:
                cab[key] = mapped
                changed = True
        if changed:
            cab["selection_seq"] = int(cab.get("selection_seq") or 0) + 1
            remapped_cabs += 1
            print(f"  cabinet {cab_path.stem}: selection remapped, seq -> {cab['selection_seq']}")
            if APPLY:
                cab_path.write_text(json.dumps(cab, ensure_ascii=False, indent=1))

    print(f"cabinets remapped: {remapped_cabs}")
    print("APPLIED" if APPLY else "DRY RUN — rerun with --apply to write changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
