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
        self.tja_root = Path(
            env("TJA_ROOT", str(base / "storage" / "SONGS" / "TJA"))
        ).resolve()
        self.osu_root = Path(
            env("OSU_ROOT", str(base / "storage" / "SONGS" / "OSU"))
        ).resolve()
        self.convert_root = Path(
            env("CONVERT_ROOT", str(base / "storage" / "SONGS" / "CONVERTED"))
        ).resolve()
        self.cabinets_root = Path(env("CABINETS_ROOT", str(base / "storage" / "cabinets"))).resolve()
        self.updates_root = Path(
            env("UPDATES_ROOT", str(base / "storage" / "updates"))
        ).resolve()
        self.database_path = Path(
            env("DATABASE_PATH", str(base / "storage" / "connector.db"))
        ).resolve()
        self.ffmpeg_path = env("FFMPEG_PATH", "ffmpeg")
        self.wine_path = env("WINE_PATH", "wine")
        self.ps3_at3tool_path = Path(
            env("PS3_AT3TOOL_PATH", str(base / "storage" / "ps3_at3tool.exe"))
        )
        self.audio_bitrate_kbps = int(env("AT3_BITRATE_KBPS", "256"))
        # Bump only when generated chart/audio/package bytes may change.
        self.package_recipe_version = env("PACKAGE_RECIPE_VERSION", "1")
        self.conversion_timeout_seconds = max(
            30, int(env("CONVERSION_TIMEOUT_SECONDS", "900"))
        )
        self.library_full_rescan_seconds = max(
            30, int(env("LIBRARY_FULL_RESCAN_SECONDS", "300"))
        )
        default_workers = min(4, os.cpu_count() or 1)
        self.conversion_workers = max(
            1,
            min(32, int(env("CONVERSION_WORKERS", str(default_workers)))),
        )
        # PS3 streams asset responses straight to disk, so serve the whole file
        # in one request when possible. Bounded by file size per request anyway.
        self.asset_chunk_bytes = int(env("ASSET_CHUNK_BYTES", str(32 * 1024 * 1024)))
        self.api_token = env("API_TOKEN", "")
        # Fall back to the existing API token so upgrades retain a usable
        # management credential until a dedicated PIN is configured.
        self.management_pin = env("MANAGEMENT_PIN", "") or self.api_token
        self.management_session_seconds = max(
            300, int(env("MANAGEMENT_SESSION_SECONDS", str(8 * 60 * 60)))
        )


settings = Settings()
