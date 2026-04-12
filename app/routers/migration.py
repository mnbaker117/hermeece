"""
Migration wizard endpoints.

    GET  /api/v1/migration/preview   — scan qBit torrents + compute target
                                       month folders based on file mtime
    POST /api/v1/migration/execute   — run pause → setLocation → recheck →
                                       poll → resume for selected hashes

The migration wizard moves existing downloads from flat directories
into the `[YYYY-MM]/` monthly folder structure that Hermeece uses
for new grabs. Each torrent's target month is derived from the mtime
of the primary book file inside its download directory — the most
reliable proxy for "when did MAM upload this".

File operations happen in qBit's namespace (the `qbit_download_path`
setting), but mtime reads happen in Hermeece's namespace (the
`local_path_prefix` translation). The path translation logic in
`download_folders.py` handles the mapping.

The execute endpoint processes torrents sequentially (not in parallel)
to avoid overwhelming qBit with concurrent moves. Each torrent goes
through the full pause → setLocation → recheck → poll → resume cycle
before the next one starts. Status updates are logged; the response
includes per-hash results so the UI can report failures granularly.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.clients.qbittorrent import QbitClient
from app.config import load_settings
from app.orchestrator.download_folders import (
    current_month_folder,
    ensure_folder_exists,
    translate_path,
)

_log = logging.getLogger("hermeece.routers.migration")

router = APIRouter(prefix="/api/v1/migration", tags=["migration"])


class PreviewItem(BaseModel):
    hash: str
    name: str
    current_path: str
    target_month: Optional[str]  # e.g. "[2025-08]"
    target_path: Optional[str]   # full qBit-namespace path
    needs_move: bool
    file_mtime: Optional[str]    # ISO date


class PreviewResponse(BaseModel):
    items: list[PreviewItem]
    need_move_count: int
    already_ok_count: int


class ExecuteRequest(BaseModel):
    hashes: list[str] = Field(..., min_length=1, max_length=500)
    dry_run: bool = False


class ExecuteResultItem(BaseModel):
    hash: str
    name: str
    ok: bool
    error: Optional[str] = None
    action: Optional[str] = None  # what was/would be done


class ExecuteResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    dry_run: bool = False
    results: list[ExecuteResultItem]


def _month_folder_for_mtime(ts: float) -> str:
    """Convert a unix timestamp → '[YYYY-MM]' folder name."""
    dt = datetime.fromtimestamp(ts)
    return f"[{dt.strftime('%Y-%m')}]"


def _find_primary_mtime(local_dir: Path) -> Optional[float]:
    """Walk a download directory and return the mtime of the largest file.

    This is the same heuristic the pipeline uses (find_book_files picks
    the largest), applied to mtime rather than content. Falls back to
    the directory mtime if the dir is empty.
    """
    if not local_dir.exists():
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
    try:
        return local_dir.stat().st_mtime
    except OSError:
        return None


@router.get("/preview", response_model=PreviewResponse)
async def preview() -> PreviewResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    deps = state.dispatcher
    settings = load_settings()
    qbit_download_path = settings.get("qbit_download_path", "") or ""
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

        mtime = _find_primary_mtime(local_dir)
        if mtime is None:
            # Try the save_path itself (flat single-file torrent)
            mtime = _find_primary_mtime(Path(local_save))

        if mtime is not None:
            month = _month_folder_for_mtime(mtime)
            target_qbit = f"{qbit_download_path}/{month}"
        else:
            month = None
            target_qbit = None

        already_in_month = (
            month is not None
            and month in t.save_path
        )

        items.append(
            PreviewItem(
                hash=t.hash,
                name=t.name,
                current_path=t.save_path,
                target_month=month,
                target_path=target_qbit,
                needs_move=not already_in_month and target_qbit is not None,
                file_mtime=(
                    datetime.fromtimestamp(mtime).isoformat()
                    if mtime
                    else None
                ),
            )
        )
        if already_in_month:
            already_ok += 1
        elif target_qbit:
            need_move += 1

    return PreviewResponse(
        items=items,
        need_move_count=need_move,
        already_ok_count=already_ok,
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute(body: ExecuteRequest) -> ExecuteResponse:
    if state.dispatcher is None:
        raise HTTPException(503, "dispatcher not initialized")

    deps = state.dispatcher
    settings = load_settings()
    qbit_download_path = settings.get("qbit_download_path", "") or ""
    if not qbit_download_path:
        raise HTTPException(400, "qbit_download_path not configured")

    qbit: QbitClient = deps.qbit  # type: ignore

    # Build the preview so we know each torrent's target path.
    all_torrents = await deps.qbit.list_torrents(category=deps.qbit_category)
    torrent_map = {t.hash: t for t in all_torrents}

    results: list[ExecuteResultItem] = []
    succeeded = 0
    failed = 0

    for h in body.hashes:
        t = torrent_map.get(h)
        if t is None:
            results.append(
                ExecuteResultItem(hash=h, name="?", ok=False, error="not found in qBit")
            )
            failed += 1
            continue

        local_save = translate_path(
            t.save_path, deps.qbit_path_prefix, deps.local_path_prefix
        )
        local_dir = Path(local_save) / t.name if t.name else Path(local_save)
        mtime = _find_primary_mtime(local_dir)
        if mtime is None:
            mtime = _find_primary_mtime(Path(local_save))
        if mtime is None:
            results.append(
                ExecuteResultItem(hash=h, name=t.name, ok=False, error="could not determine mtime")
            )
            failed += 1
            continue

        month = _month_folder_for_mtime(mtime)
        target_qbit = f"{qbit_download_path}/{month}"

        if month in t.save_path:
            results.append(
                ExecuteResultItem(hash=h, name=t.name, ok=True, error="already in target folder",
                                  action="skip (already correct)")
            )
            succeeded += 1
            continue

        action_desc = f"move {t.save_path} → {target_qbit}"

        if body.dry_run:
            # Dry run: validate that the local target can be created,
            # but don't actually touch qBit or move any files.
            local_target = translate_path(
                target_qbit, deps.qbit_path_prefix, deps.local_path_prefix
            )
            # Check that the source exists.
            src_exists = local_dir.exists() if local_dir else False
            if not src_exists:
                src_exists = Path(local_save).exists()

            results.append(
                ExecuteResultItem(
                    hash=h, name=t.name, ok=True,
                    action=f"DRY RUN: would {action_desc}",
                    error=None if src_exists else "WARNING: source path not found on disk",
                )
            )
            succeeded += 1
            continue

        # Real execution: pre-create folder + run the full cycle.
        local_target = translate_path(
            target_qbit, deps.qbit_path_prefix, deps.local_path_prefix
        )
        ensure_folder_exists(local_target)

        try:
            ok = await _migrate_one(qbit, h, target_qbit)
        except Exception as e:
            _log.exception("migration failed for %s", h)
            ok = False

        if ok:
            results.append(ExecuteResultItem(hash=h, name=t.name, ok=True, action=action_desc))
            succeeded += 1
            _log.info("migrated %s → %s", t.name, target_qbit)
        else:
            results.append(
                ExecuteResultItem(hash=h, name=t.name, ok=False,
                                  error="pause/move/recheck cycle failed", action=action_desc)
            )
            failed += 1

    return ExecuteResponse(
        total=len(body.hashes),
        succeeded=succeeded,
        failed=failed,
        dry_run=body.dry_run,
        results=results,
    )


async def _migrate_one(qbit: QbitClient, torrent_hash: str, target_path: str) -> bool:
    """Full pause → setLocation → recheck → poll → resume cycle for one torrent."""
    if not await qbit.pause_torrent(torrent_hash):
        return False
    await asyncio.sleep(1)

    if not await qbit.set_location(torrent_hash, target_path):
        await qbit.resume_torrent(torrent_hash)
        return False
    await asyncio.sleep(1)

    if not await qbit.recheck_torrent(torrent_hash):
        await qbit.resume_torrent(torrent_hash)
        return False

    # Poll until recheck completes (state exits "checkingUP"/"checkingDL").
    for _ in range(120):
        await asyncio.sleep(2)
        info = await qbit.get_torrent(torrent_hash)
        if info is None:
            break
        if "checking" not in info.state.lower():
            break

    await qbit.resume_torrent(torrent_hash)
    return True
