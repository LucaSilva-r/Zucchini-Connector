from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str) -> str:
    # CONNECTOR_* preferred; TJAREPO_* kept as legacy alias.
    value = os.getenv(f"CONNECTOR_{name}")
    if value is None:
        value = os.getenv(f"TJAREPO_{name}")
    return default if value is None else value


class Settings:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parents[1]
        self.ese_root = Path(env("ESE_ROOT", str(base / "storage" / "ESE"))).resolve()
        self.osu_root = Path(env("OSU_ROOT", str(base / "storage" / "OSU"))).resolve()
        self.convert_root = Path(env("CONVERT_ROOT", str(base / "storage" / "ESE-convert"))).resolve()
        self.title_cache_root = Path(
            env("TITLE_CACHE_ROOT", str(base / "storage" / "title-cache"))
        ).resolve()
        self.cabinets_root = Path(env("CABINETS_ROOT", str(base / "storage" / "cabinets"))).resolve()
        self.ffmpeg_path = env("FFMPEG_PATH", "ffmpeg")
        self.wine_path = env("WINE_PATH", "wine")
        self.ps3_at3tool_path = Path(env("PS3_AT3TOOL_PATH", "/opt/ps3_at3tool.exe"))
        self.audio_bitrate_kbps = int(env("AT3_BITRATE_KBPS", "256"))
        default_workers = min(4, os.cpu_count() or 1)
        self.conversion_workers = max(
            1,
            min(32, int(env("CONVERSION_WORKERS", str(default_workers)))),
        )
        # PS3 streams asset responses straight to disk, so serve the whole file
        # in one request when possible. Bounded by file size per request anyway.
        self.asset_chunk_bytes = int(env("ASSET_CHUNK_BYTES", str(32 * 1024 * 1024)))
        self.api_token = env("API_TOKEN", "")


settings = Settings()
