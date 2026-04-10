"""
ntfy.sh notification sender.

`send()` posts a notification to the configured ntfy topic. Used for:
  - Grab events (new book grabbed)
  - Download completion
  - Pipeline errors (Calibre rejection, staging failure)
  - Daily digest summaries

ntfy.sh is a simple HTTP-based pub/sub notification service. Sending a
notification is just an HTTP POST with the message body as plain text
and metadata in headers. No authentication is required for public
topics; self-hosted ntfy servers may need auth (not yet supported —
add when a user needs it).

The module is a no-op when `ntfy_url` is empty in settings, so callers
don't need to guard against "notifications not configured".
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

_log = logging.getLogger("hermeece.notify")

# Module-level httpx client for connection reuse.
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _client


async def aclose() -> None:
    """Tear down the HTTP client (called during app shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        finally:
            _client = None


async def send(
    *,
    url: str,
    topic: str,
    title: str,
    message: str,
    priority: int = 3,
    tags: Optional[list[str]] = None,
) -> bool:
    """Send a notification via ntfy.

    Args:
        url: The ntfy server URL (e.g. "https://ntfy.sh").
        topic: The topic to publish to (e.g. "hermeece").
        title: Notification title.
        message: Notification body.
        priority: 1-5 (1=min, 3=default, 5=max).
        tags: Optional list of emoji/tag strings (e.g. ["books", "white_check_mark"]).

    Returns True on success, False on failure (logged but never raised).
    """
    if not url or not topic:
        return False

    endpoint = f"{url.rstrip('/')}/{topic}"
    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = await _get_client().post(
            endpoint,
            content=message.encode("utf-8"),
            headers=headers,
        )
        if resp.status_code == 200:
            _log.debug("ntfy sent: %s", title)
            return True
        _log.warning("ntfy HTTP %d for %s: %s", resp.status_code, endpoint, resp.text[:200])
        return False
    except Exception:
        _log.exception("ntfy send failed")
        return False


# ─── Convenience senders ────────────────────────────────────


async def notify_grab(
    url: str, topic: str, torrent_name: str, author: str, category: str
) -> bool:
    """Notify that a new book was grabbed."""
    return await send(
        url=url,
        topic=topic,
        title="New book grabbed",
        message=f"{torrent_name}\nby {author}\n{category}",
        tags=["books"],
    )


async def notify_download_complete(
    url: str, topic: str, torrent_name: str, author: str
) -> bool:
    """Notify that a download completed."""
    return await send(
        url=url,
        topic=topic,
        title="Download complete",
        message=f"{torrent_name}\nby {author}",
        tags=["white_check_mark"],
    )


async def notify_pipeline_complete(
    url: str, topic: str, torrent_name: str, sink: str
) -> bool:
    """Notify that the post-download pipeline completed."""
    return await send(
        url=url,
        topic=topic,
        title=f"Added to {sink}",
        message=torrent_name,
        tags=["books", "white_check_mark"],
    )


async def notify_error(
    url: str, topic: str, torrent_name: str, error: str
) -> bool:
    """Notify of a pipeline error."""
    return await send(
        url=url,
        topic=topic,
        title="Pipeline error",
        message=f"{torrent_name}\n{error}",
        priority=4,
        tags=["warning"],
    )
