from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from mira.config.paths import paths
from mira.obs.logging import log_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_state (
    user_id TEXT PRIMARY KEY,
    pending_json TEXT,
    pending_created_at REAL,
    recent_turns_json TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    when_hint TEXT,
    fire_at REAL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL,
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
CREATE INDEX IF NOT EXISTS idx_reminders_fire_at ON reminders(fire_at) WHERE status='open';

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT 'local',
    ts REAL NOT NULL,
    transcript TEXT NOT NULL,
    reply TEXT,
    status TEXT,
    via TEXT,
    embedding BLOB,
    -- Identifier of the model used to produce `embedding`. Different
    -- embedders (OpenAI 1536-d vs BGE-small 384-d) are not cosine-compatible;
    -- recall() filters by this column so mixed-model corpora coexist safely.
    -- NULL on pre-migration rows is treated as 'text-embedding-3-small'.
    embedding_model TEXT,
    -- Normalized transcript (lowercase, filler/punct stripped) for O(1) dedup
    -- lookups. Populated by record_episode. NULL on pre-migration rows.
    norm_transcript TEXT
);

CREATE INDEX IF NOT EXISTS idx_episodes_user_ts ON episodes(user_id, ts);
-- idx_episodes_norm is created by _migrate_episodes (after the column is
-- guaranteed to exist on legacy databases).

CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Observability tables (Batch 14). `turns` is the canonical summary row
-- used by the dashboard + `mira turns` CLI; `events` is the raw structured
-- event firehose that powers per-turn trace views. Both are inserted
-- best-effort by `obs.recorder` — failures are logged but never raised.

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    transcript TEXT,
    reply TEXT,
    status TEXT,
    via TEXT,
    started_at REAL,
    ended_at REAL,
    latency_ms REAL,
    cost_usd REAL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_turns_ended_at ON turns(ended_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    turn_id TEXT,
    span_id TEXT,
    parent_span_id TEXT,
    event TEXT NOT NULL,
    fields_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_turn_id ON events(turn_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_event ON events(event);
"""


_lock = threading.Lock()
_initialized = False

# Set once at first successful extension load; read by memory.recall() to
# decide between in-SQL cosine and the Python fallback. If the platform
# sqlite ships without loadable-extension support (some distro builds),
# we keep the old path and log one warning.
_vec_loaded = False
_vec_checked = False


def _ensure() -> None:
    """Idempotent schema setup. WAL mode so readers don't block writers —
    cheap insurance for the CLI + future long-lived menu-bar process."""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        paths.ensure()
        conn = sqlite3.connect(str(paths.sqlite_db), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            _migrate_reminders(conn)
            _migrate_episodes(conn)
            conn.commit()
        finally:
            conn.close()
        _initialized = True
        log_event("store.ready", path=str(paths.sqlite_db))
        # One-shot retention sweep. Kept here (not in memory.py) so it runs
        # exactly once per process boot, regardless of which caller warmed
        # the store first.
        try:
            from mira.config.settings import get_settings
            days = int(get_settings().episode_retention_days)
            if days > 0:
                import time as _time
                cutoff = _time.time() - days * 86400.0
                conn2 = sqlite3.connect(str(paths.sqlite_db), timeout=5.0)
                try:
                    cur = conn2.execute(
                        "DELETE FROM episodes WHERE ts < ?", (cutoff,)
                    )
                    conn2.commit()
                    removed = int(cur.rowcount or 0)
                finally:
                    conn2.close()
                if removed:
                    log_event(
                        "store.pruned_episodes",
                        removed=removed,
                        older_than_days=days,
                    )
        except Exception as exc:
            log_event("store.prune_error", error=repr(exc))


def _migrate_episodes(conn: sqlite3.Connection) -> None:
    """Forward-migrate older episodes tables that lacked embedding_model /
    norm_transcript columns. Same introspection pattern as reminders — safe on
    every boot, no-op once columns exist."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
    if "embedding_model" not in existing:
        conn.execute("ALTER TABLE episodes ADD COLUMN embedding_model TEXT")
    if "norm_transcript" not in existing:
        conn.execute("ALTER TABLE episodes ADD COLUMN norm_transcript TEXT")
    # Always (re-)assert the index — CREATE INDEX IF NOT EXISTS is a no-op
    # when it already exists, and this is the only site guaranteed to run
    # after the column exists on both fresh and upgraded databases.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodes_norm "
        "ON episodes(user_id, norm_transcript, ts)"
    )


def _migrate_reminders(conn: sqlite3.Connection) -> None:
    """Add columns the scheduler needs to any pre-Batch-9 reminders table.
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` isn't supported on older sqlite,
    so we introspect PRAGMA and skip gracefully."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(reminders)")}
    if "fire_at" not in existing:
        conn.execute("ALTER TABLE reminders ADD COLUMN fire_at REAL")


def _load_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec on a fresh connection. Extensions don't persist across
    connections, so this runs on every `connect()`. Failures flip a global
    flag off and fall back to Python cosine — never fatal."""
    global _vec_loaded, _vec_checked
    if _vec_checked and not _vec_loaded:
        return  # Already determined this platform can't load it.
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        if not _vec_checked:
            _vec_loaded = True
            _vec_checked = True
            log_event("store.vec_loaded")
    except Exception as exc:
        if not _vec_checked:
            _vec_loaded = False
            _vec_checked = True
            log_event("store.vec_unavailable", error=repr(exc))


def vec_available() -> bool:
    """True once sqlite-vec has been loaded at least once. Callers use this
    to pick the SQL-cosine path vs. the Python fallback."""
    return _vec_loaded


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Short-lived connection per call. Cheaper than a pool for our write
    volumes, and sidesteps the one-loop-per-connection asyncio pitfalls.

    Use like: `with connect() as conn: conn.execute(...)`.
    """
    _ensure()
    conn = sqlite3.connect(str(paths.sqlite_db), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _load_vec(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
