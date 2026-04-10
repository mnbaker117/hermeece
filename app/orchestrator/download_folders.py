"""
Monthly download folder management.

Computes the current month's download subfolder path and ensures it
exists. The folder naming convention matches the user's existing
manual organization: `[YYYY-MM]/` inside the qBit download directory.

Examples:
    [mam-complete]/[2026-04]/
    [mam-complete]/[2026-05]/

The qBit `save_path` parameter is set to this folder when submitting
a torrent, so downloads land directly in the organized structure
without needing a post-download move/copy step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger("hermeece.orchestrator.download_folders")


def current_month_folder(
    base_path: str,
    *,
    now: Optional[datetime] = None,
) -> str:
    """Compute the current month's download folder path.

    Args:
        base_path: The qBit base download directory
                   (e.g. "/downloads/[mam-complete]" or
                   "/mnt/user/downloads/[mam-complete]").
        now: Override for testing. Defaults to UTC now.

    Returns the full path including the month subfolder,
    e.g. "/downloads/[mam-complete]/[2026-04]".
    Returns base_path unchanged if it's empty.
    """
    if not base_path:
        return ""

    dt = now or datetime.now(timezone.utc)
    folder_name = f"[{dt.strftime('%Y-%m')}]"
    return str(Path(base_path) / folder_name)


def translate_path(
    path: str,
    from_prefix: str,
    to_prefix: str,
) -> str:
    """Translate a path between container mount namespaces.

    E.g. translate_path("/data/[mam-complete]/book", "/data", "/downloads")
         → "/downloads/[mam-complete]/book"

    Returns the path unchanged if it doesn't start with from_prefix.
    """
    if not path or not from_prefix:
        return path
    from_prefix = from_prefix.rstrip("/")
    to_prefix = to_prefix.rstrip("/")
    if path.startswith(from_prefix + "/") or path == from_prefix:
        return to_prefix + path[len(from_prefix):]
    return path


def ensure_folder_exists(path: str) -> bool:
    """Create the folder if it doesn't exist.

    Returns True if the folder exists (or was created), False on error.
    This is called before submitting to qBit so the save_path is valid.
    In Docker, the container needs write access to the mounted volume.
    """
    if not path:
        return False
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        _log.exception("failed to create download folder: %s", path)
        return False
