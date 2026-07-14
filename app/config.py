from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parents[1]
        self.ese_root = Path(os.getenv("TJAREPO_ESE_ROOT", base / "storage" / "ESE")).resolve()
        self.osu_root = Path(os.getenv("TJAREPO_OSU_ROOT", base / "storage" / "OSU")).resolve()
        self.convert_root = Path(os.getenv("TJAREPO_CONVERT_ROOT", base / "storage" / "ESE-convert")).resolve()
        self.title_cache_root = Path(
            os.getenv("TJAREPO_TITLE_CACHE_ROOT", base / "storage" / "title-cache")
        ).resolve()
        self.sox_path = os.getenv("TJAREPO_SOX_PATH", "sox")
        self.wine_path = os.getenv("TJAREPO_WINE_PATH", "wine")
        self.ps3_at3tool_path = Path(os.getenv("TJAREPO_PS3_AT3TOOL_PATH", "/opt/ps3_at3tool.exe"))
        self.audio_bitrate_kbps = int(os.getenv("TJAREPO_AT3_BITRATE_KBPS", "256"))
        default_workers = min(4, os.cpu_count() or 1)
        self.conversion_workers = max(
            1,
            min(32, int(os.getenv("TJAREPO_CONVERSION_WORKERS", str(default_workers)))),
        )
        # PS3 streams asset responses straight to disk, so serve the whole file
        # in one request when possible. Bounded by file size per request anyway.
        self.asset_chunk_bytes = int(os.getenv("TJAREPO_ASSET_CHUNK_BYTES", str(32 * 1024 * 1024)))
        self.api_token = os.getenv("TJAREPO_API_TOKEN", "")


settings = Settings()
