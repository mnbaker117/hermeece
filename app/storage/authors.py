"""
Small CRUD helpers for the author taxonomy tables.

Three lists form a strict hierarchy used by the filter + review flows:

    authors_allowed             — filter passes; grabs proceed
    authors_tentative_review    — filter routed to tentative review;
                                  rejected tentative torrents put the
                                  author here for ONE weekly pass of
                                  manual review
    authors_ignored             — filter skips outright; still captured
                                  to ignored_torrents_seen for weekly
                                  "change your mind?" review

Moves between the lists happen via explicit `promote_to_*` functions
so the audit trail is clear. The filter gate doesn't consult
`authors_tentative_review` — tentative routing is driven by the
dispatcher when the ONLY reason for skipping was the author list.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite

from app.filter.normalize import normalize_author


async def _is_in(
    db: aiosqlite.Connection, table: str, normalized: str
) -> bool:
    cursor = await db.execute(
        f"SELECT 1 FROM {table} WHERE normalized = ?", (normalized,)
    )
    return await cursor.fetchone() is not None


async def is_allowed(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_allowed", normalize_author(name))


async def is_ignored(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_ignored", normalize_author(name))


async def is_tentative_review(db: aiosqlite.Connection, name: str) -> bool:
    return await _is_in(db, "authors_tentative_review", normalize_author(name))


async def add_tentative_review(
    db: aiosqlite.Connection,
    name: str,
    *,
    source: str = "tentative_reject",
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    try:
        await db.execute(
            """
            INSERT INTO authors_tentative_review (name, normalized, source)
            VALUES (?, ?, ?)
            """,
            (name.strip(), normalized, source),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def remove_tentative_review(
    db: aiosqlite.Connection, name: str
) -> None:
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalize_author(name),),
    )
    await db.commit()


async def add_ignored(
    db: aiosqlite.Connection, name: str, *, source: str = "manual"
) -> bool:
    normalized = normalize_author(name)
    if not normalized:
        return False
    try:
        await db.execute(
            """
            INSERT INTO authors_ignored (name, normalized, source)
            VALUES (?, ?, ?)
            """,
            (name.strip(), normalized, source),
        )
        await db.commit()
        return True
    except Exception:
        return False


async def promote_tentative_to_allowed(
    db: aiosqlite.Connection, name: str
) -> None:
    normalized = normalize_author(name)
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_allowed (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (name.strip(), normalized, "tentative_promote"),
    )
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalized,),
    )
    await db.commit()


async def promote_tentative_to_ignored(
    db: aiosqlite.Connection, name: str
) -> None:
    normalized = normalize_author(name)
    await db.execute(
        """
        INSERT OR IGNORE INTO authors_ignored (name, normalized, source)
        VALUES (?, ?, ?)
        """,
        (name.strip(), normalized, "tentative_auto_ignore"),
    )
    await db.execute(
        "DELETE FROM authors_tentative_review WHERE normalized = ?",
        (normalized,),
    )
    await db.commit()


async def list_tentative_review(
    db: aiosqlite.Connection,
) -> list[dict]:
    cursor = await db.execute(
        """
        SELECT name, normalized, source, added_at
        FROM authors_tentative_review
        ORDER BY added_at DESC
        """
    )
    rows = await cursor.fetchall()
    return [
        {
            "name": r["name"],
            "normalized": r["normalized"],
            "source": r["source"],
            "added_at": r["added_at"],
        }
        for r in rows
    ]
