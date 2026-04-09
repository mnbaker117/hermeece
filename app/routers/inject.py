"""
Manual grab-injection HTTP endpoint.

POST /api/v1/grabs/inject

Three callers will hit this endpoint:

  1. **Cookie-rotation manual test recipe** — paste a torrent ID,
     verify the full grab path works, then rotate the cookie and
     repeat to verify the failure + retry flow.
  2. **AthenaScout integration** (Phase 3, with the metadata bundle
     extension preserved as an optional `metadata_bundle` field).
  3. **Operator manual override** — when an announce is missed
     (Hermeece was offline) and the operator wants to grab it
     anyway from the MAM web UI's "Recent Activity" page.

The endpoint reads the dispatcher singleton out of `app.state`,
calls `inject_grab`, and serializes the result as JSON. There's
no auth on this endpoint in Phase 1 — Hermeece runs on the LAN
behind whatever auth its container provides. Phase 3 wires up
the AthenaScout-style auth_secret session cookies.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.orchestrator.dispatch import inject_grab

router = APIRouter(prefix="/api/v1/grabs", tags=["grabs"])


class InjectRequest(BaseModel):
    """Request body for POST /api/v1/grabs/inject.

    Only `torrent_id` is required. The metadata fields exist for
    audit-log readability (so the UI shows a name + author instead
    of just an ID), but the dispatcher doesn't need them to operate.
    """

    torrent_id: str = Field(..., min_length=1)
    torrent_name: str = ""
    category: str = ""
    author_blob: str = ""
    source: str = "manual_inject"


class InjectResponse(BaseModel):
    """Response body for POST /api/v1/grabs/inject.

    Mirrors `DispatchResult` plus a top-level `ok` boolean for
    machine consumers that just want a thumbs-up. The full result
    fields are included so the UI can render the audit row link
    or the queue position immediately.
    """

    ok: bool
    action: str
    reason: str
    announce_id: int
    grab_id: Optional[int] = None
    qbit_hash: Optional[str] = None
    error: Optional[str] = None


@router.post("/inject", response_model=InjectResponse)
async def inject_endpoint(request: InjectRequest) -> InjectResponse:
    if state.dispatcher is None:
        # Hit during startup before lifespan completed, or during
        # tests that forgot to install a dispatcher fixture. Return
        # a 503 rather than a 500 so the client knows it can retry.
        raise HTTPException(
            status_code=503,
            detail="dispatcher not initialized yet",
        )

    result = await inject_grab(
        state.dispatcher,
        torrent_id=request.torrent_id,
        torrent_name=request.torrent_name,
        category=request.category,
        author_blob=request.author_blob,
        raw_line=f"manual_inject:source={request.source}",
    )

    # ok=True means the grab successfully entered the pipeline
    # (submit or queue) with no error. A drop is not an error per
    # se — it's a valid outcome — but the client probably wants
    # ok=False so its UI can surface "this didn't go anywhere."
    # Same for fetch / qBit failures: action might still be
    # "submit" or "queue" (the rate decision), but the grab is in
    # a failed state and `error` is set, so ok must be False.
    pipeline_ok = (
        result.action in ("submit", "queue") and result.error is None
    )
    return InjectResponse(
        ok=pipeline_ok,
        action=result.action,
        reason=result.reason,
        announce_id=result.announce_id,
        grab_id=result.grab_id,
        qbit_hash=result.qbit_hash,
        error=result.error,
    )
