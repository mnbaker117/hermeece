"""
Post-download pipeline orchestrator.

`process_completion()` drives a single completed download through the
full pipeline:

    1. Locate book files in the download directory
    2. Optionally copy to staging (only if staging_path is configured)
    3. Extract metadata from the primary book file
    4. Route to the configured sink (Calibre or folder)
    5. Auto-train: add author(s) to the allow list
    6. Send ntfy notification
    7. Update pipeline_runs and grabs tables

When monthly download folders are in use, the files are already
organized in the download directory (e.g. [mam-complete]/[2026-04]/).
In this case, Calibre sink delivers directly from the download dir —
no copy needed. The staging step is only used when explicitly
configured (e.g. for manual metadata review before import).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from app.metadata.extract import BookMetadata, extract as extract_metadata
from app.notify import ntfy
from app.orchestrator.auto_train import train_authors_from_blob
from app.orchestrator.download_watcher import CompletionEvent
from app.orchestrator.file_copier import copy_to_staging, find_book_files
from app.sinks.base import SinkResult
from app.sinks.audiobookshelf import AudiobookshelfSink
from app.sinks.calibre import CalibreSink
from app.sinks.cwa import CWASink
from app.sinks.folder import FolderSink
from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipe_storage

_log = logging.getLogger("hermeece.orchestrator.pipeline")


async def process_completion(
    db: aiosqlite.Connection,
    event: CompletionEvent,
    *,
    staging_path: str,
    default_sink: str,
    calibre_library_path: str,
    folder_sink_path: str,
    audiobookshelf_library_path: str = "",
    cwa_ingest_path: str = "",
    category_routing: dict[str, str] = None,
    ntfy_url: str,
    ntfy_topic: str,
    auto_train_enabled: bool = True,
) -> bool:
    """Drive one completed download through the full pipeline.

    Returns True if the pipeline completed successfully, False otherwise.
    All errors are logged and recorded on the pipeline_run row — this
    function never raises.
    """
    run_id = event.pipeline_run_id

    try:
        # ── Step 1: locate book files ───────────────────────
        source = Path(event.save_path)
        book_files = await asyncio.get_event_loop().run_in_executor(
            None, find_book_files, source
        )

        if not book_files:
            await _fail(db, run_id, event,
                        f"no book files found in {source}",
                        ntfy_url, ntfy_topic)
            return False

        primary_book = book_files[0]  # largest file
        book_filename = primary_book.name
        book_format = primary_book.suffix.lstrip(".").lower()

        _log.info(
            "pipeline: found %d book file(s) for grab_id=%d, primary=%s",
            len(book_files), event.grab_id, book_filename,
        )

        # ── Step 2: staging (optional) ──────────────────────
        # If staging_path is configured, copy there first.
        # Otherwise, work directly from the download directory.
        if staging_path:
            copy_result = await asyncio.get_event_loop().run_in_executor(
                None,
                copy_to_staging,
                source,
                Path(staging_path),
                event.torrent_name,
            )
            if not copy_result.success:
                await _fail(db, run_id, event,
                            f"staging failed: {copy_result.error}",
                            ntfy_url, ntfy_topic)
                return False

            book_dir = Path(copy_result.staged_path)
            book_path = book_dir / (copy_result.book_filename or book_filename)
            book_filename = copy_result.book_filename or book_filename
            book_format = copy_result.book_format or book_format

            await pipe_storage.set_state(
                db, run_id, pipe_storage.PIPE_EXTRACTED,
                staged_path=str(book_dir),
                book_filename=book_filename,
                book_format=book_format,
            )
        else:
            # Direct mode — use the file in the download directory.
            book_path = primary_book
            await pipe_storage.set_state(
                db, run_id, pipe_storage.PIPE_EXTRACTED,
                staged_path=str(source),
                book_filename=book_filename,
                book_format=book_format,
            )

        # ── Step 3: extract metadata ────────────────────────
        # Read embedded metadata from the book file, then enrich
        # with the announce data (which comes from MAM's IRC parser
        # and is more reliable than epub metadata for author names).
        file_metadata = BookMetadata()
        if book_path.exists():
            file_metadata = extract_metadata(book_path)

        # Enrich: prefer announce data for author, fall back to file.
        grab = await grabs_storage.get_grab(db, event.grab_id)
        announce_author = grab.author_blob if grab else ""
        announce_title = grab.torrent_name if grab else ""

        metadata = BookMetadata(
            title=file_metadata.title or announce_title or "",
            author=announce_author or file_metadata.author or "",
            series=file_metadata.series,
            series_index=file_metadata.series_index,
            language=file_metadata.language,
            publisher=file_metadata.publisher,
            description=file_metadata.description,
            isbn=file_metadata.isbn,
            format=file_metadata.format,
        )

        await pipe_storage.set_state(
            db, run_id, pipe_storage.PIPE_METADATA_DONE,
            metadata_title=metadata.title or None,
            metadata_author=metadata.author or None,
            metadata_series=metadata.series or None,
            metadata_language=metadata.language or None,
        )

        # ── Step 4: route to sink ───────────────────────────
        sink = _pick_sink(
            default_sink, calibre_library_path,
            folder_sink_path, audiobookshelf_library_path,
            cwa_ingest_path,
        )

        if book_path.exists():
            sink_result = await sink.deliver(str(book_path), metadata)
        else:
            sink_result = SinkResult(
                success=False,
                sink_name=sink.name,
                error="no book file to deliver",
            )

        if not sink_result.success:
            await _fail(db, run_id, event,
                        f"sink {sink_result.sink_name} failed: {sink_result.error}",
                        ntfy_url, ntfy_topic)
            return False

        await pipe_storage.set_state(
            db, run_id, pipe_storage.PIPE_SUNK,
            sink_name=sink_result.sink_name,
            sink_result=sink_result.detail,
        )

        # ── Step 5: auto-train ──────────────────────────────
        if auto_train_enabled:
            # Always use the announce author_blob for training — it
            # comes from MAM's IRC parser and reliably contains the
            # real author names. Epub metadata author is a fallback
            # only (often contains publisher names or other junk).
            author_blob = announce_author or file_metadata.author or ""
            if author_blob:
                added = await train_authors_from_blob(db, author_blob)
                if added:
                    _log.info(
                        "pipeline: auto-trained %d author(s) from %s",
                        added, event.torrent_name,
                    )

        # ── Step 6: mark complete ───────────────────────────
        await pipe_storage.set_state(db, run_id, pipe_storage.PIPE_COMPLETE)
        await grabs_storage.set_state(
            db, event.grab_id, grabs_storage.STATE_COMPLETE
        )

        _log.info(
            "pipeline: complete grab_id=%d %s → %s",
            event.grab_id, event.torrent_name, sink_result.sink_name,
        )

        # ── Step 7: notify ──────────────────────────────────
        if ntfy_url and ntfy_topic:
            await ntfy.notify_pipeline_complete(
                ntfy_url, ntfy_topic,
                event.torrent_name, sink_result.sink_name,
            )

        return True

    except Exception:
        _log.exception("pipeline: unexpected error for grab_id=%d", event.grab_id)
        try:
            await pipe_storage.set_state(
                db, run_id, pipe_storage.PIPE_FAILED,
                error="unexpected error (see logs)",
            )
        except Exception:
            pass
        return False


def _pick_sink(
    default_sink: str,
    calibre_library_path: str,
    folder_sink_path: str,
    audiobookshelf_library_path: str,
    cwa_ingest_path: str,
):
    """Pick the right sink based on the default_sink setting."""
    if default_sink == "calibre":
        return CalibreSink(calibre_library_path)
    if default_sink == "cwa":
        return CWASink(cwa_ingest_path)
    if default_sink == "audiobookshelf":
        return AudiobookshelfSink(audiobookshelf_library_path)
    return FolderSink(folder_sink_path)


async def _fail(
    db: aiosqlite.Connection,
    run_id: int,
    event: CompletionEvent,
    error: str,
    ntfy_url: str,
    ntfy_topic: str,
) -> None:
    """Record a pipeline failure and optionally notify."""
    _log.warning(
        "pipeline: failed grab_id=%d %s: %s",
        event.grab_id, event.torrent_name, error,
    )
    await pipe_storage.set_state(
        db, run_id, pipe_storage.PIPE_FAILED, error=error,
    )
    if ntfy_url and ntfy_topic:
        await ntfy.notify_error(ntfy_url, ntfy_topic, event.torrent_name, error)
