"""
Migration wizard endpoints — v2 with correct path matching.

    GET  /api/v1/migration/preview   — scan + compute targets
    POST /api/v1/migration/execute   — batch migrate with progress
    POST /api/v1/migration/resume-all — resume all stopped torrents

The migration wizard moves existing downloads into the configured
folder structure (monthly [YYYY-MM], yearly [YYYY], or flat).

Path matching logic: a torrent is "already correct" ONLY if its
save_path ends with a folder that EXACTLY matches the target
pattern. Everything else — date folders like [2026-03-15], named
folders like [Random Seeding], the bare root path — is fair game
for migration.

Batch size is capped at 50 per request to avoid HTTP timeouts.
The frontend paginates through the full list in chunks.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.clients.qbittorrent import QbitClient
from app.config import load_settings
from app.orchestrator.download_folders import (
    ensure_folder_exists,
    translate_path,
)

_log = logging.getLogger("hermeece.routers.migration")

router = APIRouter(prefix="/api/v1/migration", tags=["migration"])

# Regex patterns for "already in the right structure" checks.
_MONTHLY_RX = re.compile(r"^.*\[(\d{4}-\d{2})\]$")  # [2026-04]
_YEARLY_RX = re.compile(r"^.*\[(\d{4})\]$")          # [2026]

BATCH_LIMIT = 50


class PreviewItem(BaseModel):
    hash: str
    name: str
    current_path: str
    current_folder: str       # last path component
    target_folder: Optional[str]
    target_path: Optional[str]
    needs_move: bool
    file_mtime: Optional[str]


class PreviewResponse(BaseModel):
    items: list[PreviewItem]
    need_move_count: int
    already_ok_count: int
    total: int


class ExecuteRequest(BaseModel):
    hashes: list[str] = Field(..., min_length=1, max_length=50)
    dry_run: bool = False


class ExecuteResultItem(BaseModel):
    hash: str
    name: str
    ok: bool
    error: Optional[str] = None
    action: Optional[str] = None


class ExecuteResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    dry_run: bool = False
    results: list[ExecuteResultItem]


def _target_folder_for_mtime(ts: float, structure: str) -> str:
    """Compute the target folder name based on the folder structure setting."""
    dt = datetime.fromtimestamp(ts)
    if structure == "yearly":
        return f"[{dt.strftime('%Y')}]"
    elif structure == "flat":
        return ""  # no subfolder
    else:  # monthly (default)
        return f"[{dt.strftime('%Y-%m')}]"


def _is_already_correct(save_path: str, target_folder: str, structure: str) -> bool:
    """Check if the torrent's save_path already ends with the exact target folder.

    Only returns True for EXACT matches of the configured folder structure.
    Date folders like [2026-03-15], named folders like [Random Seeding],
    and the bare root path all return False.
    """
    if structure == "flat":
        # For flat, the torrent should be in the root download path
        # (no subfolder). Check that there's no bracket folder at the end.
        last = save_path.rstrip("/").rsplit("/", 1)[-1]
        return not last.startswith("[")

    if not target_folder:
        return False

    # The save_path should end with exactly the target folder.
    normalized = save_path.rstrip("/")
    return normalized.endswith(f"/{target_folder}") or normalized == target_folder


def _find_primary_mtime(local_dir: Path) -> Optional[float]:
    """Walk a download directory and return the mtime of the largest file.

    Only inspects the given directory — never scans parent directories.
    If the path is a regular file (single-file torrent), returns its mtime.
    Falls back to the directory's own mtime only if the directory exists
    but contains no regular files (unlikely for a completed download).
    """
    if not local_dir.exists():
        return None

    # Single-file torrent: local_dir is actually a file.
    if local_dir.is_file():
        try:
            return local_dir.stat().st_mtime
        except OSError:
            return None

    best_size = 0
    best_mtime: Optional[float] = None
    try:
        for f in local_dir.rglob("*"):
            if f.is_file():
                sz = f.stat().st_size
                if sz > best_size:
                    best_size = sz
                    best_mtime = f.stat().st_mtime
    except OSError:
        pass
    if best_mtime is not None:
        return best_mtime
    # Last resort: directory's own mtime (when the dir exists but we
    # couldn't iterate its contents — e.g. permission denied on children).
    try:
        return local_dir.stat().st_mtime
    except OSError:
        return None


def _last_folder(path: str) -> str:
    """Extract the last path component."""
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


@router.get("/preview", response_model=PreviewResponse)
async def preview() -> PreviewResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    deps = state.dispatcher
    settings = load_settings()
    qbit_download_path = settings.get("qbit_download_path", "") or ""
    structure = settings.get("download_folder_structure", "monthly") or "monthly"
    if not qbit_download_path:
        raise HTTPException(400, "qbit_download_path not configured")

    torrents = await deps.qbit.list_torrents(category=deps.qbit_category)

    items: list[PreviewItem] = []
    need_move = 0
    already_ok = 0

    for t in torrents:
        local_save = translate_path(
            t.save_path, deps.qbit_path_prefix, deps.local_path_prefix
        )
        local_dir = Path(local_save) / t.name if t.name else Path(local_save)

        # Only inspect the specific torrent directory — never scan the
        # parent, which would pick up another torrent's file dates.
        mtime = _find_primary_mtime(local_dir)

        if mtime is not None:
            target_folder = _target_folder_for_mtime(mtime, structure)
            target_qbit = f"{qbit_download_path}/{target_folder}" if target_folder else qbit_download_path
        else:
            target_folder = None
            target_qbit = None

        correct = _is_already_correct(t.save_path, target_folder or "", structure) if target_folder is not None else False

        items.append(PreviewItem(
            hash=t.hash,
            name=t.name,
            current_path=t.save_path,
            current_folder=_last_folder(t.save_path),
            target_folder=target_folder,
            target_path=target_qbit,
            needs_move=not correct and target_qbit is not None,
            file_mtime=datetime.fromtimestamp(mtime).isoformat() if mtime else None,
        ))
        if correct:
            already_ok += 1
        elif target_qbit:
            need_move += 1

    return PreviewResponse(
        items=items,
        need_move_count=need_move,
        already_ok_count=already_ok,
        total=len(torrents),
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute(body: ExecuteRequest) -> ExecuteResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    deps = state.dispatcher
    settings = load_settings()
    qbit_download_path = settings.get("qbit_download_path", "") or ""
    structure = settings.get("download_folder_structure", "monthly") or "monthly"
    if not qbit_download_path:
        raise HTTPException(400, "qbit_download_path not configured")

    qbit: QbitClient = deps.qbit  # type: ignore

    results: list[ExecuteResultItem] = []
    succeeded = 0
    failed = 0

    for h in body.hashes:
        # Re-fetch each torrent individually so we always have its
        # current save_path — critical when processing a batch where
        # earlier items may have changed qBit state.
        t = await deps.qbit.get_torrent(h)
        if t is None:
            results.append(ExecuteResultItem(hash=h, name="?", ok=False, error="not found"))
            failed += 1
            continue

        local_save = translate_path(
            t.save_path, deps.qbit_path_prefix, deps.local_path_prefix
        )
        local_dir = Path(local_save) / t.name if t.name else Path(local_save)

        # Only inspect the specific torrent directory — never the parent.
        mtime = _find_primary_mtime(local_dir)
        if mtime is None:
            results.append(ExecuteResultItem(hash=h, name=t.name, ok=False, error="could not determine mtime"))
            failed += 1
            continue

        target_folder = _target_folder_for_mtime(mtime, structure)
        target_qbit = f"{qbit_download_path}/{target_folder}" if target_folder else qbit_download_path

        if _is_already_correct(t.save_path, target_folder, structure):
            results.append(ExecuteResultItem(hash=h, name=t.name, ok=True, action="already correct"))
            succeeded += 1
            continue

        action_desc = f"{_last_folder(t.save_path)} → {target_folder or 'root'}"

        if body.dry_run:
            local_target = translate_path(target_qbit, deps.qbit_path_prefix, deps.local_path_prefix)
            src_exists = local_dir.exists() or Path(local_save).exists()
            results.append(ExecuteResultItem(
                hash=h, name=t.name, ok=True,
                action=f"DRY RUN: would move {action_desc}",
                error=None if src_exists else "WARNING: source not found on disk",
            ))
            succeeded += 1
            continue

        # Pre-create the target folder.
        local_target = translate_path(target_qbit, deps.qbit_path_prefix, deps.local_path_prefix)
        ensure_folder_exists(local_target)

        try:
            ok = await _migrate_one(qbit, h, target_qbit)
        except Exception:
            _log.exception("migration failed for %s", h)
            ok = False

        if ok:
            results.append(ExecuteResultItem(hash=h, name=t.name, ok=True, action=action_desc))
            succeeded += 1
            _log.info("migrated %s: %s", t.name, action_desc)
        else:
            results.append(ExecuteResultItem(hash=h, name=t.name, ok=False, error="move/recheck failed", action=action_desc))
            failed += 1

    return ExecuteResponse(
        total=len(body.hashes), succeeded=succeeded, failed=failed,
        dry_run=body.dry_run, results=results,
    )


@router.post("/resume-all")
async def resume_all():
    """Resume all stopped torrents in the watched category."""
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")
    deps = state.dispatcher
    qbit: QbitClient = deps.qbit  # type: ignore
    torrents = await deps.qbit.list_torrents(category=deps.qbit_category)
    resumed = 0
    for t in torrents:
        if t.state.lower() in ("pausedup", "pauseddl", "stoppedup", "stoppeddl", "stopped"):
            ok = await qbit.resume_torrent(t.hash)
            if ok:
                resumed += 1
    return {"ok": True, "resumed": resumed, "total": len(torrents)}


async def _migrate_one(qbit: QbitClient, torrent_hash: str, target_path: str) -> bool:
    """Relocate one torrent: [pause if active] → setSavePath → verify → recheck → [resume].

    After calling setSavePath we verify that qBit actually updated the
    torrent's save_path to the target. qBit v5's setSavePath can return
    200 without doing anything if the target directory doesn't exist or
    isn't writable — the 200 is NOT a reliable success signal.
    """
    info = await qbit.get_torrent(torrent_hash)
    if info is None:
        return False

    was_active = info.state.lower() not in (
        "pausedup", "pauseddl", "stoppedup", "stoppeddl", "stopped",
    )

    if was_active:
        if not await qbit.pause_torrent(torrent_hash):
            return False
        await asyncio.sleep(1)

    if not await qbit.set_location(torrent_hash, target_path):
        if was_active:
            await qbit.resume_torrent(torrent_hash)
        return False

    # Give qBit time to process the move, then verify it took effect.
    await asyncio.sleep(2)

    verify = await qbit.get_torrent(torrent_hash)
    if verify is None:
        _log.warning("torrent %s disappeared after setSavePath", torrent_hash)
        return False

    actual = verify.save_path.rstrip("/")
    expected = target_path.rstrip("/")
    if actual != expected:
        _log.warning(
            "setSavePath for %s did NOT take effect: expected %r, got %r",
            torrent_hash, expected, actual,
        )
        if was_active:
            await qbit.resume_torrent(torrent_hash)
        return False

    if not await qbit.recheck_torrent(torrent_hash):
        if was_active:
            await qbit.resume_torrent(torrent_hash)
        return False

    # Poll until recheck completes.
    for _ in range(120):
        await asyncio.sleep(2)
        check_info = await qbit.get_torrent(torrent_hash)
        if check_info is None:
            break
        if "checking" not in check_info.state.lower():
            break

    if was_active:
        await qbit.resume_torrent(torrent_hash)

    return True
