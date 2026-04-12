"""
AthenaScout integration endpoint.

    POST /api/v1/grabs/from-athenascout

Accepts a batch of MAM torrent URLs (or bare IDs) from AthenaScout's
"Send to Hermeece" button. Each URL is parsed into a torrent_id and
routed through `inject_grab`, which handles the full
filter-skip → fetch → qBit pipeline.

Authors from the request are optionally auto-trained to the allow
list if they're not already present. This covers the case where
AthenaScout knows the author (because the user is scanning their
library) but Hermeece doesn't (because it hasn't seen that author
in an IRC announce yet).

No MAM API key or special auth beyond the existing session cookie
middleware — AthenaScout and Hermeece are both LAN services behind
the same auth boundary.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.database import get_db
from app.orchestrator.auto_train import train_author
from app.orchestrator.dispatch import inject_grab

_log = logging.getLogger("hermeece.routers.athenascout")

router = APIRouter(prefix="/api/v1/grabs", tags=["athenascout"])

_MAM_URL_RX = re.compile(r"/t/(\d+)")
_BARE_ID_RX = re.compile(r"^\d+$")


class GrabItem(BaseModel):
    url_or_id: str
    author: Optional[str] = None


class AthenascoutRequest(BaseModel):
    items: list[GrabItem] = Field(..., min_length=1, max_length=100)


class GrabResultItem(BaseModel):
    torrent_id: str
    ok: bool
    action: Optional[str] = None
    error: Optional[str] = None


class AthenascoutResponse(BaseModel):
    submitted: int
    failed: int
    results: list[GrabResultItem]


def _extract_torrent_id(url_or_id: str) -> Optional[str]:
    s = url_or_id.strip()
    if _BARE_ID_RX.match(s):
        return s
    m = _MAM_URL_RX.search(s)
    return m.group(1) if m else None


@router.post("/from-athenascout", response_model=AthenascoutResponse)
async def from_athenascout(body: AthenascoutRequest) -> AthenascoutResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    results: list[GrabResultItem] = []
    submitted = 0
    failed = 0

    for item in body.items:
        tid = _extract_torrent_id(item.url_or_id)
        if tid is None:
            results.append(
                GrabResultItem(
                    torrent_id=item.url_or_id,
                    ok=False,
                    error=f"could not parse torrent ID from: {item.url_or_id}",
                )
            )
            failed += 1
            continue

        # Auto-train the author if provided and not already known.
        if item.author:
            db = await get_db()
            try:
                await train_author(db, item.author, source="athenascout")
            except Exception:
                pass
            finally:
                await db.close()

        try:
            result = await inject_grab(
                state.dispatcher,
                torrent_id=tid,
                author_blob=item.author or "",
                raw_line=f"athenascout:{item.url_or_id}",
            )
            ok = result.action in ("submit", "queue") and result.error is None
            results.append(
                GrabResultItem(
                    torrent_id=tid,
                    ok=ok,
                    action=result.action,
                    error=result.error,
                )
            )
            if ok:
                submitted += 1
            else:
                failed += 1
        except Exception as e:
            results.append(
                GrabResultItem(
                    torrent_id=tid,
                    ok=False,
                    error=str(e),
                )
            )
            failed += 1

    _log.info(
        "athenascout batch: %d submitted, %d failed out of %d",
        submitted, failed, len(body.items),
    )
    return AthenascoutResponse(
        submitted=submitted, failed=failed, results=results
    )
