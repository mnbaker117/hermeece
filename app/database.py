"""
Database layer.

Single SQLite database under DATA_DIR (no per-library multiplexing —
Hermeece operates on one workflow at a time, unlike AthenaScout which
supports multiple Calibre libraries).

Schema and migrations both live in this file. SCHEMA is the up-to-date
target shape; MIGRATIONS is the ordered list of statements that bring an
older database forward. `PRAGMA user_version` tracks how many migrations
have been applied so subsequent startups skip the work.

Connection pragmas:
  - WAL mode: keeps readers unblocked during writes (important for
    background workers + UI polling concurrency)
  - foreign_keys=ON: enforced at runtime, not just declared
  - busy_timeout=30s: long enough to wait out a slow background writer

The schema is minimal in Phase 1 (just enough for the filter, the
snatch ledger, and the announce audit log). Phase 2 adds the
metadata_review and sink_runs tables; Phase 3 adds auth tables.
"""
import logging

import aiosqlite

from app.config import APP_DB_PATH

_log = logging.getLogger("hermeece.database")


# ─── Schema ──────────────────────────────────────────────────
# CREATE TABLE IF NOT EXISTS is safe to run on every startup. Indexes
# follow the same pattern.
SCHEMA = """
CREATE TABLE IF NOT EXISTS authors_allowed (
    name              TEXT PRIMARY KEY,
    normalized        TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL,
    added_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authors_ignored (
    name              TEXT PRIMARY KEY,
    normalized        TEXT NOT NULL UNIQUE,
    source            TEXT NOT NULL,
    added_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authors_weekly_skip (
    name              TEXT PRIMARY KEY,
    normalized        TEXT NOT NULL UNIQUE,
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
    hits_count        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS announces (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    seen_at           TEXT NOT NULL DEFAULT (datetime('now')),
    raw               TEXT NOT NULL,
    torrent_id        TEXT,
    torrent_name      TEXT,
    category          TEXT,
    author_blob       TEXT,
    decision          TEXT NOT NULL,
    decision_reason   TEXT NOT NULL,
    matched_author    TEXT
);

CREATE TABLE IF NOT EXISTS grabs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    announce_id       INTEGER REFERENCES announces(id) ON DELETE SET NULL,
    mam_torrent_id    TEXT NOT NULL,
    torrent_name      TEXT NOT NULL,
    category          TEXT,
    author_blob       TEXT,
    torrent_file_path TEXT,
    qbit_hash         TEXT,
    state             TEXT NOT NULL,
    state_updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    grabbed_at        TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at      TEXT,
    completed_at      TEXT,
    failed_reason     TEXT,
    failed_with_cookie_id INTEGER
);

CREATE TABLE IF NOT EXISTS snatch_ledger (
    grab_id                  INTEGER PRIMARY KEY REFERENCES grabs(id) ON DELETE CASCADE,
    qbit_hash                TEXT,
    seeding_seconds          INTEGER NOT NULL DEFAULT 0,
    last_check_at            TEXT,
    released_at              TEXT,
    released_reason          TEXT
);

CREATE TABLE IF NOT EXISTS pending_queue (
    grab_id     INTEGER PRIMARY KEY REFERENCES grabs(id) ON DELETE CASCADE,
    priority    INTEGER NOT NULL DEFAULT 0,
    queued_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mam_session (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cookie              TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    last_validated_at   TEXT,
    validation_ok       INTEGER NOT NULL DEFAULT 0,
    superseded_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_announces_seen_at ON announces(seen_at);
CREATE INDEX IF NOT EXISTS idx_announces_decision ON announces(decision);
CREATE INDEX IF NOT EXISTS idx_grabs_state ON grabs(state);
CREATE INDEX IF NOT EXISTS idx_grabs_torrent_id ON grabs(mam_torrent_id);
CREATE INDEX IF NOT EXISTS idx_snatch_ledger_released ON snatch_ledger(released_at);
CREATE INDEX IF NOT EXISTS idx_pending_queue_priority ON pending_queue(priority, queued_at);
"""


# ─── Migrations ──────────────────────────────────────────────
# Append-only ordered list. Each entry is one SQL statement that brings
# an older database forward by exactly one step. `PRAGMA user_version`
# tracks how many entries have been applied.
#
# Empty in Phase 1 — the schema above is the v0 baseline. Migrations
# only get added when we need to evolve the schema after Hermeece is
# running in production.
MIGRATIONS: list[str] = []


async def get_db() -> aiosqlite.Connection:
    """Open a connection with the standard pragmas applied."""
    db = await aiosqlite.connect(str(APP_DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=30000")
    return db


async def init_db():
    """Create schema and run migrations.

    Idempotent: safe to call on every startup. Skips already-applied
    migrations via PRAGMA user_version.
    """
    db = await get_db()
    try:
        # Read current schema version (0 for fresh DBs).
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0
        target_version = len(MIGRATIONS)

        # Always ensure base tables + indexes exist.
        await db.executescript(SCHEMA)
        await db.commit()

        # Apply only the migrations we haven't seen.
        if current_version < target_version:
            _log.info(
                f"Migrating database schema: v{current_version} → v{target_version}"
            )
            for i, migration in enumerate(MIGRATIONS):
                if i < current_version:
                    continue
                try:
                    await db.execute(migration)
                except aiosqlite.OperationalError as e:
                    msg = str(e).lower()
                    # Tolerate the harmless "already there" cases that show
                    # up when migrating a legacy database that had columns
                    # added by an older always-run loop.
                    if (
                        "duplicate column" in msg
                        or "already exists" in msg
                        or "no such column" in msg
                    ):
                        continue
                    _log.warning(
                        f"Migration #{i} failed unexpectedly: {e} "
                        f"(SQL: {migration[:80]}...)"
                    )
            await db.commit()
            await db.execute(f"PRAGMA user_version = {target_version}")
            await db.commit()
    finally:
        await db.close()
