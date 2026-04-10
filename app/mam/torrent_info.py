"""
MAM torrent-info lookup.

`get_torrent_info()` queries MAM's search API for a single torrent by
ID to retrieve economic metadata that the IRC announce doesn't always
carry:

  - vip: bool         — permanent or temporary VIP (download is free)
  - free: bool        — global freeleech
  - fl_vip: bool      — freeleech OR VIP (convenience union flag)
  - personal_freeleech: bool — user has already bought FL for this torrent

The IRC announce only carries a `(VIP)` suffix for VIP torrents.
Freeleech status and wedge applicability require this API lookup.

Routes through `cookie._do_post` so cookie auto-rotation fires on
every response.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.mam.cookie import MAM_SEARCH_URL, _do_post

_log = logging.getLogger("hermeece.mam")

# Cache TTL in seconds (2 minutes — shorter than user_status because
# VIP/FL status can change when site-wide freeleech events start/end).
_CACHE_TTL = 120


@dataclass(frozen=True)
class TorrentInfo:
    """Economic metadata for a single MAM torrent."""

    torrent_id: str
    vip: bool
    free: bool
    fl_vip: bool
    personal_freeleech: bool
    category: str       # e.g. "Audiobooks - Urban Fantasy"
    title: str
    size: str           # e.g. "6324306932" (bytes as string)


# ─── In-memory cache ────────────────────────────────────────

_cache: dict[str, tuple[float, TorrentInfo]] = {}


def invalidate_cache() -> None:
    """Clear the torrent-info cache."""
    _cache.clear()


# ─── Public API ─────────────────────────────────────────────


async def get_torrent_info(
    torrent_id: str,
    token: Optional[str] = None,
    ttl: int = _CACHE_TTL,
) -> TorrentInfo:
    """Look up a single torrent's economic metadata from MAM.

    Returns a cached result if one exists within `ttl` seconds.
    Raises `TorrentInfoError` on any failure.

    Args:
        torrent_id: The numeric MAM torrent ID (string).
        token: Explicit mam_id cookie value. If None, uses the
               module-level current token from cookie.py.
        ttl: Cache lifetime in seconds. Pass 0 to force a fresh fetch.
    """
    now = time.monotonic()

    if ttl > 0 and torrent_id in _cache:
        cached_at, cached_info = _cache[torrent_id]
        if now - cached_at < ttl:
            _log.debug("torrent_info cache hit for tid=%s", torrent_id)
            return cached_info

    _log.info("Fetching MAM torrent info for tid=%s", torrent_id)

    payload = json.dumps({
        "tor": {
            "id": torrent_id,
            "searchType": "all",
            "searchIn": "torrents",
            "cat": ["0"],
            "sortType": "default",
            "startNumber": "0",
        },
        "perpage": 1,
    })

    try:
        resp = await _do_post(MAM_SEARCH_URL, token=token, payload=payload, timeout=15)
    except Exception as exc:
        raise TorrentInfoError(f"network error: {exc}") from exc

    if resp.status_code != 200:
        raise TorrentInfoError(f"HTTP {resp.status_code} from search API")

    if not resp.text:
        raise TorrentInfoError("empty response from search API — cookie may be invalid")

    try:
        data = resp.json()
    except Exception as exc:
        raise TorrentInfoError(f"invalid JSON: {resp.text[:200]}") from exc

    items = data.get("data", [])
    if not items:
        raise TorrentInfoError(f"torrent {torrent_id} not found in search results")

    item = items[0]

    info = TorrentInfo(
        torrent_id=str(item.get("id", torrent_id)),
        vip=_to_bool(item.get("vip")),
        free=_to_bool(item.get("free")),
        fl_vip=_to_bool(item.get("fl_vip")),
        personal_freeleech=_to_bool(item.get("personal_freeleech")),
        category=str(item.get("catname", "")),
        title=str(item.get("title", item.get("name", ""))),
        size=str(item.get("size", "")),
    )

    _cache[torrent_id] = (now, info)
    _log.info(
        "MAM torrent tid=%s: vip=%s, free=%s, fl_vip=%s, pfl=%s",
        torrent_id,
        info.vip,
        info.free,
        info.fl_vip,
        info.personal_freeleech,
    )
    return info


def _to_bool(value) -> bool:
    """Coerce MAM's mixed boolean representations to Python bool.

    MAM's search API returns booleans as strings ("0"/"1"), integers,
    or actual booleans depending on the field and the response format.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


class TorrentInfoError(Exception):
    """Raised when the torrent-info lookup fails."""
