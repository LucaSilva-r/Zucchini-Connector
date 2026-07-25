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
        # Charts whose first note lands sooner than this are given silence at
        # the start of the audio (and the chart is moved back to match), so the
        # player gets time to react. Set to 0 to disable.
        # 1500 ms comes from the game's own fumen files: across 4252 official
        # solo charts the earliest first note is 1122 ms, p5 is 1491 ms, and
        # none start under 1000 ms.
        self.min_lead_in_ms = max(0, int(env("MIN_LEAD_IN_MS", "1500")))
        # Bump only when generated chart/audio/package bytes may change.
        self.package_recipe_version = env("PACKAGE_RECIPE_VERSION", "2")
        self.conversion_timeout_seconds = max(
            30, int(env("CONVERSION_TIMEOUT_SECONDS", "900"))
        )
        self.library_full_rescan_seconds = max(
            30, int(env("LIBRARY_FULL_RESCAN_SECONDS", "300"))
        )
        # Conversion is mostly ffmpeg/at3tool subprocesses, so the workers sit
        # in wait() rather than holding the GIL: one per core is what makes a
        # full-library rebuild finish in a reasonable time.
        default_workers = os.cpu_count() or 1
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
