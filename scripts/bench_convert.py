#!/usr/bin/env python3
"""Time each stage of a single-song conversion to see where the time goes.

Usage:  python3 scripts/bench_convert.py [song_id]
        (no id -> first song in the catalog)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from app import catalog, converter


def timed(label, fn):
    t = time.perf_counter()
    r = fn()
    print(f"{label:28s} {(time.perf_counter() - t) * 1000:9.1f} ms")
    return r


# Time every subprocess (ffmpeg / wine+at3tool) individually.
_orig_run = converter._run
_tool_ms: dict[str, float] = {}


def _timed_run(args, env=None):
    t = time.perf_counter()
    _orig_run(args, env=env)
    dt = (time.perf_counter() - t) * 1000
    tool = Path(str(args[0])).name
    _tool_ms[tool] = _tool_ms.get(tool, 0.0) + dt
    print(f"  [_run] {tool:22s} {dt:9.1f} ms")


converter._run = _timed_run


def main() -> int:
    song_id = sys.argv[1] if len(sys.argv) > 1 else None
    entry = catalog.song(song_id) if song_id else (catalog.songs() or [None])[0]
    if not entry:
        print("no song found (set TJA_ROOT / pass a song_id)")
        return 1
    print(f"song: {entry['id']}  {entry['title']}")
    print(f"tja:   {entry.get('tja_path')}")
    print(f"audio: {entry.get('audio_path')}\n")

    t0 = time.perf_counter()
    timed("source_hash", lambda: catalog.source_hash(entry))
    timed("parse_tja", lambda: converter.parse_tja(str(entry["tja_path"])))

    tmp = Path(tempfile.mkdtemp(prefix="bench-convert-"))
    (tmp / "solo").mkdir(parents=True, exist_ok=True)
    try:
        timed("convert_charts", lambda: converter._convert_charts(entry, tmp))
        up = str(entry["id"]).upper()
        nub, nsh = tmp / f"SONG_{up}.nub", tmp / f"SONG_{up}.nsh"
        timed("convert_audio (total)",
              lambda: converter._convert_audio(Path(str(entry["audio_path"])), nub, nsh))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TOTAL':28s} {(time.perf_counter() - t0) * 1000:9.1f} ms")
    if _tool_ms:
        print("per-tool:", {k: f"{v:.0f} ms" for k, v in _tool_ms.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
