"""
Book review queue HTTP endpoints.

    GET    /api/v1/review              — list pending reviews
    GET    /api/v1/review/{id}         — fetch one pending review
    POST   /api/v1/review/{id}/approve — approve (+ optional metadata edits)
    POST   /api/v1/review/{id}/reject  — reject + delete staged file

Approval triggers sink delivery via `deliver_reviewed`. Rejection
removes the staged file from disk (seeding original is untouched)
and marks the queue row rejected.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import state
from app.database import get_db
from app.orchestrator.pipeline import deliver_reviewed
from app.storage import review_queue as review_storage

_log = logging.getLogger("hermeece.routers.review")

router = APIRouter(prefix="/api/v1/review", tags=["review"])


class ReviewItem(BaseModel):
    id: int
    grab_id: int
    staged_path: str
    book_filename: str
    book_format: Optional[str]
    metadata: dict[str, Any]
    cover_path: Optional[str]
    status: str
    created_at: str
    decided_at: Optional[str]
    decision_note: Optional[str]


class ReviewListResponse(BaseModel):
    items: list[ReviewItem]
    pending_count: int


class ApproveRequest(BaseModel):
    metadata: Optional[dict[str, Any]] = None
    note: Optional[str] = None


class RejectRequest(BaseModel):
    note: Optional[str] = None


class ReviewActionResponse(BaseModel):
    ok: bool
    id: int
    status: str
    error: Optional[str] = None


def _to_item(row: review_storage.ReviewRow) -> ReviewItem:
    return ReviewItem(
        id=row.id,
        grab_id=row.grab_id,
        staged_path=row.staged_path,
        book_filename=row.book_filename,
        book_format=row.book_format,
        metadata=row.metadata,
        cover_path=row.cover_path,
        status=row.status,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


@router.get("", response_model=ReviewListResponse)
async def list_pending() -> ReviewListResponse:
    db = await get_db()
    try:
        rows = await review_storage.list_pending(db, limit=500)
        count = await review_storage.count_by_status(
            db, review_storage.STATUS_PENDING
        )
        return ReviewListResponse(
            items=[_to_item(r) for r in rows], pending_count=count
        )
    finally:
        await db.close()


@router.get("/{review_id}", response_model=ReviewItem)
async def get_one(review_id: int) -> ReviewItem:
    db = await get_db()
    try:
        row = await review_storage.get_entry(db, review_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")
        return _to_item(row)
    finally:
        await db.close()


@router.post("/{review_id}/approve", response_model=ReviewActionResponse)
async def approve(review_id: int, body: ApproveRequest) -> ReviewActionResponse:
    if state.dispatcher is None:
        raise HTTPException(status_code=503, detail="dispatcher not initialized")
    deps = state.dispatcher
    db = await get_db()
    try:
        row = await review_storage.get_entry(db, review_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")
        if row.status != review_storage.STATUS_PENDING:
            return ReviewActionResponse(
                ok=False, id=review_id, status=row.status,
                error=f"already in status {row.status}",
            )

        # Persist any user metadata edits before sink delivery.
        if body.metadata:
            merged = dict(row.metadata)
            merged.update(body.metadata)
            await review_storage.set_status(
                db, review_id, review_storage.STATUS_PENDING,
                metadata=merged,
            )

        ok = await deliver_reviewed(
            db,
            review_id=review_id,
            default_sink=deps.default_sink,
            calibre_library_path=deps.calibre_library_path,
            folder_sink_path=deps.folder_sink_path,
            audiobookshelf_library_path=deps.audiobookshelf_library_path,
            cwa_ingest_path=deps.cwa_ingest_path,
            ntfy_url=deps.ntfy_url,
            ntfy_topic=deps.ntfy_topic,
            auto_train_enabled=deps.auto_train_enabled,
            was_timeout=False,
        )
        refreshed = await review_storage.get_entry(db, review_id)
        return ReviewActionResponse(
            ok=ok,
            id=review_id,
            status=refreshed.status if refreshed else "unknown",
            error=None if ok else "sink delivery failed",
        )
    finally:
        await db.close()


@router.post("/{review_id}/reject", response_model=ReviewActionResponse)
async def reject(review_id: int, body: RejectRequest) -> ReviewActionResponse:
    db = await get_db()
    try:
        row = await review_storage.get_entry(db, review_id)
        if row is None:
            raise HTTPException(status_code=404, detail="review not found")
        if row.status != review_storage.STATUS_PENDING:
            return ReviewActionResponse(
                ok=False, id=review_id, status=row.status,
                error=f"already in status {row.status}",
            )

        # Remove the staged file + its enclosing grab-<id> dir. The
        # seeding original in the download directory is untouched.
        try:
            staged_dir = Path(row.staged_path)
            if staged_dir.exists():
                shutil.rmtree(str(staged_dir), ignore_errors=True)
        except Exception:
            _log.exception(
                "review reject: failed to remove staged dir for review_id=%d",
                review_id,
            )

        await review_storage.set_status(
            db, review_id, review_storage.STATUS_REJECTED,
            decision_note=body.note or "user rejected",
        )
        return ReviewActionResponse(
            ok=True, id=review_id, status=review_storage.STATUS_REJECTED,
        )
    finally:
        await db.close()
