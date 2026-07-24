"""Durable connector metadata.

Large source/package files stay on disk. SQLite owns the small state required
to recover scans, conversions, and per-cabinet package reconciliation.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from .config import settings


SCHEMA_VERSION = 1
_lock = RLock()
_initialized = False


def connect() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path, timeout=15.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = FULL")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    with _lock:
        db = connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def initialize() -> None:
    global _initialized
    with transaction() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scan_runs (
                generation INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER NOT NULL,
                completed_at INTEGER,
                song_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS songs (
                song_id TEXT PRIMARY KEY,
                source_revision TEXT NOT NULL,
                package_revision TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                scan_generation INTEGER NOT NULL,
                available INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS packages (
                song_id TEXT PRIMARY KEY,
                source_revision TEXT NOT NULL,
                package_revision TEXT NOT NULL,
                recipe_version TEXT NOT NULL,
                state TEXT NOT NULL,
                manifest_json TEXT,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(song_id) REFERENCES songs(song_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS conversion_jobs (
                song_id TEXT PRIMARY KEY,
                package_revision TEXT NOT NULL,
                state TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                retryable INTEGER NOT NULL DEFAULT 1,
                next_retry_at INTEGER,
                lease_expires_at INTEGER,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(song_id) REFERENCES songs(song_id)
                    ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS cabinet_package_state (
                cabinet_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                desired_revision TEXT NOT NULL DEFAULT '',
                installed_revision TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(cabinet_id, song_id)
            );
            """
        )
        db.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
    _initialized = True


def _ensure_initialized() -> None:
    if not _initialized:
        initialize()


def begin_scan() -> int:
    _ensure_initialized()
    with transaction() as db:
        cur = db.execute(
            "INSERT INTO scan_runs(started_at) VALUES(?)", (int(time.time()),)
        )
        return int(cur.lastrowid)


def finish_scan(
    generation: int,
    songs: list[dict[str, Any]],
    *,
    error: str = "",
) -> None:
    _ensure_initialized()
    now = int(time.time())
    with transaction() as db:
        if not error:
            db.execute("UPDATE songs SET available=0")
            for song in songs:
                db.execute(
                    """
                    INSERT INTO songs(
                        song_id, source_revision, package_revision,
                        metadata_json, scan_generation, available
                    ) VALUES(?, ?, ?, ?, ?, 1)
                    ON CONFLICT(song_id) DO UPDATE SET
                        source_revision=excluded.source_revision,
                        package_revision=excluded.package_revision,
                        metadata_json=excluded.metadata_json,
                        scan_generation=excluded.scan_generation,
                        available=1
                    """,
                    (
                        str(song["id"]),
                        str(song.get("source_revision", "")),
                        str(song.get("package_revision", "")),
                        json.dumps(song, ensure_ascii=False, separators=(",", ":")),
                        generation,
                    ),
                )
        db.execute(
            """
            UPDATE scan_runs
               SET completed_at=?, song_count=?, error_count=?, error=?
             WHERE generation=?
            """,
            (now, len(songs), 1 if error else 0, error[:1000], generation),
        )


def record_job(
    song_id: str,
    package_revision: str,
    state: str,
    *,
    attempt_delta: int = 0,
    retryable: bool = True,
    next_retry_at: int | None = None,
    lease_expires_at: int | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    _ensure_initialized()
    now = int(time.time())
    with transaction() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO songs(
                song_id, source_revision, package_revision, metadata_json,
                scan_generation, available
            ) VALUES(?, '', ?, '{}', 0, 1)
            """,
            (song_id, package_revision),
        )
        db.execute(
            """
            INSERT INTO conversion_jobs(
                song_id, package_revision, state, attempt_count, retryable,
                next_retry_at, lease_expires_at, error_code, error_message,
                updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(song_id) DO UPDATE SET
                package_revision=excluded.package_revision,
                state=excluded.state,
                attempt_count=conversion_jobs.attempt_count + ?,
                retryable=excluded.retryable,
                next_retry_at=excluded.next_retry_at,
                lease_expires_at=excluded.lease_expires_at,
                error_code=excluded.error_code,
                error_message=excluded.error_message,
                updated_at=excluded.updated_at
            """,
            (
                song_id,
                package_revision,
                state,
                max(0, attempt_delta),
                1 if retryable else 0,
                next_retry_at,
                lease_expires_at,
                error_code,
                error_message[:2000],
                now,
                max(0, attempt_delta),
            ),
        )


def record_package(
    song_id: str,
    manifest: dict[str, Any] | None,
    state: str,
    *,
    error_code: str = "",
    error_message: str = "",
) -> None:
    _ensure_initialized()
    manifest = manifest or {}
    with transaction() as db:
        package_revision = str(
            manifest.get("package_revision")
            or manifest.get("source_hash")
            or ""
        )
        db.execute(
            """
            INSERT OR IGNORE INTO songs(
                song_id, source_revision, package_revision, metadata_json,
                scan_generation, available
            ) VALUES(?, ?, ?, '{}', 0, 1)
            """,
            (
                song_id,
                str(manifest.get("source_revision", "")),
                package_revision,
            ),
        )
        db.execute(
            """
            INSERT INTO packages(
                song_id, source_revision, package_revision, recipe_version,
                state, manifest_json, error_code, error_message, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(song_id) DO UPDATE SET
                source_revision=excluded.source_revision,
                package_revision=excluded.package_revision,
                recipe_version=excluded.recipe_version,
                state=excluded.state,
                manifest_json=excluded.manifest_json,
                error_code=excluded.error_code,
                error_message=excluded.error_message,
                updated_at=excluded.updated_at
            """,
            (
                song_id,
                str(manifest.get("source_revision", "")),
                package_revision,
                str(manifest.get("recipe_version", "")),
                state,
                json.dumps(manifest, ensure_ascii=False) if manifest else None,
                error_code,
                error_message[:2000],
                int(time.time()),
            ),
        )


def recoverable_jobs() -> list[str]:
    _ensure_initialized()
    with closing(connect()) as db:
        rows = db.execute(
            """
            SELECT song_id FROM conversion_jobs
             WHERE retryable=1
               AND state IN ('queued', 'processing', 'retrying')
            """
        ).fetchall()
    return [str(row["song_id"]) for row in rows]


def due_jobs(now: int | None = None) -> list[str]:
    _ensure_initialized()
    when = int(time.time()) if now is None else now
    with closing(connect()) as db:
        rows = db.execute(
            """
            SELECT song_id FROM conversion_jobs
             WHERE retryable=1
               AND state='retrying'
               AND COALESCE(next_retry_at, 0) <= ?
            """,
            (when,),
        ).fetchall()
    return [str(row["song_id"]) for row in rows]


def job_attempt_count(song_id: str) -> int:
    _ensure_initialized()
    with closing(connect()) as db:
        row = db.execute(
            "SELECT attempt_count FROM conversion_jobs WHERE song_id=?",
            (song_id,),
        ).fetchone()
    return int(row["attempt_count"]) if row is not None else 0


def scan_health() -> dict[str, Any]:
    _ensure_initialized()
    with closing(connect()) as db:
        row = db.execute(
            "SELECT * FROM scan_runs ORDER BY generation DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else {}


def record_cabinet_package_state(
    cabinet_id: str,
    song_id: str,
    installed_revision: str,
    state: str,
    error_code: str = "",
) -> None:
    _ensure_initialized()
    with transaction() as db:
        db.execute(
            """
            INSERT INTO cabinet_package_state(
                cabinet_id, song_id, installed_revision, state,
                error_code, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(cabinet_id, song_id) DO UPDATE SET
                installed_revision=excluded.installed_revision,
                state=excluded.state,
                error_code=excluded.error_code,
                updated_at=excluded.updated_at
            """,
            (
                cabinet_id,
                song_id,
                installed_revision,
                state,
                error_code,
                int(time.time()),
            ),
        )
