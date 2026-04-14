"""
Read-only database browser.

Power-user tool for inspecting Hermeece's SQLite database without
SSH'ing into the container and running `sqlite3` by hand. Useful for
debugging review-queue issues, confirming author-list state,
checking grab history, etc.

v1.1 scope is **read-only**. Cell editing, inserts, and deletes are
deferred to a future release (plan item 4.3 says "Read-only table
browser as MVP, with cell editing as a follow-up"). Shipping it
read-only keeps the blast radius bounded — the only way for this
router to corrupt data is via a schema-name injection, which we
prevent by whitelisting every table name against `_TABLES`.

  GET /api/v1/db/tables                — list tables + row counts
  GET /api/v1/db/table/{name}/schema   — column metadata
  GET /api/v1/db/table/{name}          — paginated rows
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.database import get_db

_log = logging.getLogger("hermeece.routers.db_editor")

router = APIRouter(prefix="/api/v1/db", tags=["db_editor"])

# Whitelist of tables the browser is allowed to read. Tables not
# listed here are rejected with 404 — the name is never interpolated
# into SQL without passing through this check, so a caller can't
# point the browser at sqlite_master or anything else interesting.
_TABLES: frozenset[str] = frozenset({
    "authors_allowed",
    "authors_ignored",
    "authors_weekly_skip",
    "authors_tentative_review",
    "announces",
    "grabs",
    "snatch_ledger",
    "pending_queue",
    "mam_session",
    "pipeline_runs",
    "book_review_queue",
    "tentative_torrents",
    "ignored_torrents_seen",
    "calibre_additions",
})


def _check_table(name: str) -> None:
    if name not in _TABLES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown or disallowed table: {name!r}",
        )


# ─── Response models ──────────────────────────────────────────


class TableEntry(BaseModel):
    name: str
    row_count: int


class TablesResponse(BaseModel):
    tables: list[TableEntry]


class ColumnInfo(BaseModel):
    name: str
    type: str
    not_null: bool
    primary_key: bool


class SchemaResponse(BaseModel):
    table: str
    columns: list[ColumnInfo]


class RowsResponse(BaseModel):
    table: str
    total: int
    page: int
    per_page: int
    rows: list[dict[str, Any]]


# ─── Endpoints ────────────────────────────────────────────────


@router.get("/tables", response_model=TablesResponse)
async def list_tables() -> TablesResponse:
    """List every whitelisted table with its current row count."""
    db = await get_db()
    try:
        entries: list[TableEntry] = []
        for name in sorted(_TABLES):
            cur = await db.execute(f"SELECT COUNT(*) FROM [{name}]")
            row = await cur.fetchone()
            entries.append(TableEntry(name=name, row_count=int(row[0]) if row else 0))
    finally:
        await db.close()
    return TablesResponse(tables=entries)


@router.get("/table/{name}/schema", response_model=SchemaResponse)
async def table_schema(name: str) -> SchemaResponse:
    """Column metadata for a whitelisted table.

    Wraps SQLite's PRAGMA table_info; shapes each row into a
    small dataclass rather than returning the 6-tuple raw.
    """
    _check_table(name)
    db = await get_db()
    try:
        cur = await db.execute(f"PRAGMA table_info([{name}])")
        rows = await cur.fetchall()
    finally:
        await db.close()
    columns = [
        ColumnInfo(
            name=str(r[1]),
            type=str(r[2] or ""),
            not_null=bool(r[3]),
            primary_key=bool(r[5]),
        )
        for r in rows
    ]
    return SchemaResponse(table=name, columns=columns)


@router.get("/table/{name}", response_model=RowsResponse)
async def list_rows(
    name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
) -> RowsResponse:
    """Paginated row list for a whitelisted table.

    `search` does a case-insensitive substring match against every
    TEXT column in the table. Keeps the query simple — no per-column
    filter UI yet; the MVP scope expects the user to use browser
    find-in-page for narrower queries.
    """
    _check_table(name)
    db = await get_db()
    try:
        # Column set for the search filter.
        sch = await db.execute(f"PRAGMA table_info([{name}])")
        col_info = await sch.fetchall()
        text_cols = [str(r[1]) for r in col_info if "TEXT" in str(r[2] or "").upper()]

        where = ""
        params: list[Any] = []
        if search and text_cols:
            needle = f"%{search}%"
            clauses = [f"[{c}] LIKE ?" for c in text_cols]
            where = " WHERE " + " OR ".join(clauses)
            params = [needle] * len(text_cols)

        count_cur = await db.execute(
            f"SELECT COUNT(*) FROM [{name}]{where}", params,
        )
        count_row = await count_cur.fetchone()
        total = int(count_row[0]) if count_row else 0

        offset = (page - 1) * per_page
        cur = await db.execute(
            f"SELECT * FROM [{name}]{where} LIMIT ? OFFSET ?",
            [*params, per_page, offset],
        )
        rows = await cur.fetchall()
        row_dicts = [dict(r) for r in rows]
    finally:
        await db.close()

    return RowsResponse(
        table=name,
        total=total,
        page=page,
        per_page=per_page,
        rows=row_dicts,
    )
