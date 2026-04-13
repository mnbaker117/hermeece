"""
Post-download pipeline orchestrator.

The post-download pipeline has two halves with a mandatory manual
review queue between them:

    HALF 1 (`process_completion`):
        1. Locate book files in the download directory
        2. Optionally copy to staging
        3. Extract + enrich metadata (announce + embedded)
        4. Patch metadata into a temp copy of the epub
        5a. If review_queue_enabled: copy the patched file to the
            review staging dir and insert a `book_review_queue` row,
            set pipeline state to `awaiting_review`, STOP. The user
            (or the auto-add timeout job) resumes with `deliver_reviewed`.
        5b. If review_queue_enabled is False (legacy/direct path):
            fall straight through to HALF 2.

    HALF 2 (`deliver_reviewed`):
        6. Route to the configured sink
        7. Auto-train: add author(s) to the allow list
        8. Send ntfy notification
        9. Record a calibre_additions row + mark pipeline complete

The split matters because CWA's inotify watcher only reacts to the
final atomic rename — if we handed CWA a partial or unenriched file
during review, it would ingest it before the user could approve or
edit the metadata. Keeping the patched file in a separate review
staging dir until the user signs off is what makes the manual review
step actually manual.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import aiosqlite

from app.metadata.covers import fetch_cover
from app.metadata.extract import BookMetadata, extract as extract_metadata
from app.metadata.enricher import MetadataEnricher
from app.metadata.record import MetaRecord
from app.metadata.writer import patch_epub_metadata
from app.notify import ntfy
from app.orchestrator.auto_train import train_authors_from_blob
from app.orchestrator.download_watcher import CompletionEvent
from app.orchestrator.file_copier import copy_to_staging, find_book_files
from app.sinks.base import SinkResult
from app.sinks.audiobookshelf import AudiobookshelfSink
from app.sinks.calibre import CalibreSink
from app.sinks.cwa import CWASink
from app.sinks.folder import FolderSink
from app.storage import calibre_adds as calibre_adds_storage
from app.storage import grabs as grabs_storage
from app.storage import pipeline as pipe_storage
from app.storage import review_queue as review_storage

_log = logging.getLogger("hermeece.orchestrator.pipeline")


def _get_mam_token() -> str:
    """Read the current MAM token from the cookie module's in-memory cache."""
    try:
        from app.mam.cookie import get_current_token
        return get_current_token() or ""
    except Exception:
        return ""


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
    review_queue_enabled: bool = False,
    review_staging_path: str = "",
    per_event_notifications: bool = False,
    metadata_enricher: Optional[MetadataEnricher] = None,
) -> bool:
    """Drive one completed download through the pipeline.

    When `review_queue_enabled` is True, the pipeline stops after
    inserting a `book_review_queue` row and leaves the patched file
    in `review_staging_path`. The return value is still True for
    "successfully staged for review" — failures (missing files,
    patch errors, etc.) return False and record the error.

    When `review_queue_enabled` is False, the legacy straight-to-sink
    path is used — kept for tests and for users who explicitly disable
    review in settings.

    Never raises on expected-failure paths. All errors go through
    `_fail()` and end up on the pipeline_run row.
    """
    run_id = event.pipeline_run_id

    try:
        prep = await _prepare_book(
            db, event, staging_path=staging_path, run_id=run_id,
            ntfy_url=ntfy_url, ntfy_topic=ntfy_topic,
            metadata_enricher=metadata_enricher,
        )
        if prep is None:
            return False

        if per_event_notifications and ntfy_url and ntfy_topic:
            try:
                await ntfy.notify_download_complete(
                    ntfy_url, ntfy_topic,
                    event.torrent_name,
                    prep.metadata.author or "",
                )
            except Exception:
                _log.exception(
                    "per-event notify_download_complete failed (non-fatal)"
                )

        if review_queue_enabled:
            ok = await _stage_for_review(
                db, event, prep,
                review_staging_path=review_staging_path,
                ntfy_url=ntfy_url, ntfy_topic=ntfy_topic,
            )
            return ok

        return await _deliver_prepared(
            db, event, prep,
            default_sink=default_sink,
            calibre_library_path=calibre_library_path,
            folder_sink_path=folder_sink_path,
            audiobookshelf_library_path=audiobookshelf_library_path,
            cwa_ingest_path=cwa_ingest_path,
            ntfy_url=ntfy_url,
            ntfy_topic=ntfy_topic,
            auto_train_enabled=auto_train_enabled,
            review_id=None,
            was_timeout=False,
        )
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


# ─── Phase halves ───────────────────────────────────────────────


class _PreparedBook:
    """Internal carrier for the outputs of `_prepare_book`."""
    __slots__ = (
        "book_path", "book_filename", "book_format",
        "metadata", "enriched", "announce_author",
        "delivery_source", "temp_dir", "cleanup_temp",
    )

    def __init__(
        self,
        *,
        book_path: Path,
        book_filename: str,
        book_format: str,
        metadata: BookMetadata,
        announce_author: str,
        delivery_source: Path,
        temp_dir: Optional[Path],
        cleanup_temp: bool,
        enriched: Optional[MetaRecord] = None,
    ):
        self.book_path = book_path
        self.book_filename = book_filename
        self.book_format = book_format
        self.metadata = metadata
        self.enriched = enriched
        self.announce_author = announce_author
        self.delivery_source = delivery_source
        self.temp_dir = temp_dir
        self.cleanup_temp = cleanup_temp


async def _prepare_book(
    db: aiosqlite.Connection,
    event: CompletionEvent,
    *,
    staging_path: str,
    run_id: int,
    ntfy_url: str,
    ntfy_topic: str,
    metadata_enricher: Optional[MetadataEnricher] = None,
) -> Optional[_PreparedBook]:
    """Steps 1-4: locate file, optional staging, metadata, patch.

    Returns a `_PreparedBook` on success, or None after recording
    a failure on the pipeline run.
    """
    loop = asyncio.get_event_loop()
    source = Path(event.save_path)
    book_files = await loop.run_in_executor(None, find_book_files, source)

    if not book_files:
        await _fail(db, run_id, event,
                    f"no book files found in {source}",
                    ntfy_url, ntfy_topic)
        return None

    primary_book = book_files[0]
    book_filename = primary_book.name
    book_format = primary_book.suffix.lstrip(".").lower()

    _log.info(
        "pipeline: found %d book file(s) for grab_id=%d, primary=%s",
        len(book_files), event.grab_id, book_filename,
    )

    # Optional staging copy.
    if staging_path:
        copy_result = await loop.run_in_executor(
            None, copy_to_staging, source, Path(staging_path), event.torrent_name,
        )
        if not copy_result.success:
            await _fail(db, run_id, event,
                        f"staging failed: {copy_result.error}",
                        ntfy_url, ntfy_topic)
            return None
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
        book_path = primary_book
        await pipe_storage.set_state(
            db, run_id, pipe_storage.PIPE_EXTRACTED,
            staged_path=str(source),
            book_filename=book_filename,
            book_format=book_format,
        )

    # Extract + enrich metadata.
    file_metadata = BookMetadata()
    if book_path.exists():
        file_metadata = extract_metadata(book_path)

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

    # Tier 4: enrich via online metadata sources (Goodreads, etc.).
    # Only runs when an enricher was passed AND the enricher itself
    # is enabled. Result fills nulls in `metadata` — we never
    # overwrite values we already have from embedded metadata.
    enriched: Optional[MetaRecord] = None
    if metadata_enricher is not None:
        try:
            enriched = await metadata_enricher.enrich(
                title=metadata.title,
                author=metadata.author,
                mam_torrent_id=grab.mam_torrent_id if grab else "",
                mam_token=_get_mam_token(),
            )
        except Exception:
            _log.exception(
                "pipeline: enricher crashed for grab_id=%d (non-fatal)",
                event.grab_id,
            )
            enriched = None

    if enriched is not None:
        metadata = BookMetadata(
            title=metadata.title or enriched.title or "",
            author=metadata.author or ", ".join(enriched.authors) or "",
            series=metadata.series or enriched.series,
            series_index=metadata.series_index or enriched.series_index,
            language=metadata.language or enriched.language,
            publisher=metadata.publisher or enriched.publisher,
            description=metadata.description or enriched.description,
            isbn=metadata.isbn or enriched.isbn,
            format=metadata.format,
        )

    await pipe_storage.set_state(
        db, run_id, pipe_storage.PIPE_METADATA_DONE,
        metadata_title=metadata.title or None,
        metadata_author=metadata.author or None,
        metadata_series=metadata.series or None,
        metadata_language=metadata.language or None,
    )

    # Patch metadata into a temp copy of the epub so the seeding
    # original is untouched.
    delivery_source = book_path
    temp_dir: Optional[Path] = None
    if (
        book_path.exists()
        and book_path.suffix.lower() == ".epub"
        and metadata.author
    ):
        temp_dir = Path(tempfile.mkdtemp(prefix="hermeece-patch-"))
        try:
            temp_book = temp_dir / book_path.name
            shutil.copy2(str(book_path), str(temp_book))
            authors = [a.strip() for a in metadata.author.split(",") if a.strip()]
            patched_ok = patch_epub_metadata(
                temp_book,
                title=metadata.title or None,
                authors=authors if authors else None,
                series=metadata.series or None,
                series_index=metadata.series_index or None,
            )
            if patched_ok:
                delivery_source = temp_book
                _log.info(
                    "pipeline: patched epub metadata for grab_id=%d",
                    event.grab_id,
                )
        except Exception:
            _log.exception(
                "pipeline: failed to patch epub for grab_id=%d, "
                "using original file", event.grab_id,
            )

    return _PreparedBook(
        book_path=book_path,
        book_filename=book_filename,
        book_format=book_format,
        metadata=metadata,
        announce_author=announce_author,
        delivery_source=delivery_source,
        temp_dir=temp_dir,
        cleanup_temp=True,
        enriched=enriched,
    )


async def _stage_for_review(
    db: aiosqlite.Connection,
    event: CompletionEvent,
    prep: _PreparedBook,
    *,
    review_staging_path: str,
    ntfy_url: str,
    ntfy_topic: str,
) -> bool:
    """Move the patched file into the review staging dir and insert a
    `book_review_queue` row. Pipeline transitions to awaiting_review.
    """
    run_id = event.pipeline_run_id

    if not review_staging_path:
        await _fail(db, run_id, event,
                    "review_queue_enabled but review_staging_path not configured",
                    ntfy_url, ntfy_topic)
        return False

    try:
        target_dir = Path(review_staging_path) / f"grab-{event.grab_id}"
        target_dir.mkdir(parents=True, exist_ok=True)
        # Copy the (possibly patched) delivery source into the review
        # staging dir. Don't move the temp file — keep _prepare_book's
        # cleanup semantics simple.
        src = prep.delivery_source
        dest = target_dir / src.name
        if src.exists():
            shutil.copy2(str(src), str(dest))
        else:
            await _fail(db, run_id, event,
                        "prepared book file missing before review staging",
                        ntfy_url, ntfy_topic)
            return False
    except Exception as e:
        _log.exception("pipeline: review staging copy failed")
        await _fail(db, run_id, event,
                    f"review staging copy failed: {type(e).__name__}: {e}",
                    ntfy_url, ntfy_topic)
        return False
    finally:
        if prep.cleanup_temp and prep.temp_dir and prep.temp_dir.exists():
            shutil.rmtree(str(prep.temp_dir), ignore_errors=True)

    # Fetch cover images. MAM poster is the primary (authoritative),
    # Goodreads/enricher cover is the alternative the user can choose.
    # Both are best-effort — missing covers aren't pipeline failures.
    from app.metadata.covers import fetch_mam_cover

    mam_cover_str: Optional[str] = None
    enricher_cover_str: Optional[str] = None
    grab = await grabs_storage.get_grab(db, event.grab_id)

    # MAM cover (primary): uses the CDN poster endpoint + cookie auth.
    if grab and grab.mam_torrent_id:
        try:
            mam_token = _get_mam_token()
            mam_path = await fetch_mam_cover(
                grab.mam_torrent_id,
                dest_dir=target_dir,
                basename="cover-mam",
                token=mam_token,
            )
            if mam_path is not None:
                mam_cover_str = str(mam_path)
        except Exception:
            _log.exception(
                "pipeline: MAM cover fetch crashed for grab_id=%d", event.grab_id
            )

    # Enricher cover (alternative): from Goodreads or other scrapers.
    if prep.enriched and prep.enriched.cover_url:
        try:
            enricher_path = await fetch_cover(
                prep.enriched.cover_url,
                dest_dir=target_dir,
                basename="cover-enriched",
            )
            if enricher_path is not None:
                enricher_cover_str = str(enricher_path)
        except Exception:
            _log.exception(
                "pipeline: enricher cover fetch crashed for grab_id=%d", event.grab_id
            )

    # Use MAM cover as the primary, enricher as fallback.
    cover_path_str = mam_cover_str or enricher_cover_str

    # Insert the review queue row. Metadata serialized as plain dict,
    # merged with the enriched source record so the UI can display
    # both provider-side fields (description, page count, etc.) and
    # the embedded-file values.
    metadata_dict = {k: v for k, v in asdict(prep.metadata).items() if v is not None}
    if prep.enriched is not None:
        enriched_dict = prep.enriched.to_dict()
        metadata_dict["enriched"] = enriched_dict
    # Store both cover paths so the UI can show both + let user pick.
    metadata_dict["cover_mam"] = mam_cover_str
    metadata_dict["cover_enriched"] = enricher_cover_str
    await review_storage.create_entry(
        db,
        grab_id=event.grab_id,
        pipeline_run_id=run_id,
        staged_path=str(target_dir),
        book_filename=dest.name,
        book_format=prep.book_format,
        metadata=metadata_dict,
        cover_path=cover_path_str,
    )
    await pipe_storage.set_state(db, run_id, pipe_storage.PIPE_AWAITING_REVIEW)
    await grabs_storage.set_state(
        db, event.grab_id, grabs_storage.STATE_PROCESSING
    )

    _log.info(
        "pipeline: staged for review grab_id=%d %s → %s",
        event.grab_id, event.torrent_name, dest,
    )

    if ntfy_url and ntfy_topic:
        try:
            await ntfy.notify_pipeline_complete(
                ntfy_url, ntfy_topic,
                event.torrent_name, "review_queue",
            )
        except Exception:
            _log.exception("ntfy review-queue notify failed (non-fatal)")

    return True


async def _deliver_prepared(
    db: aiosqlite.Connection,
    event: CompletionEvent,
    prep: _PreparedBook,
    *,
    default_sink: str,
    calibre_library_path: str,
    folder_sink_path: str,
    audiobookshelf_library_path: str,
    cwa_ingest_path: str,
    ntfy_url: str,
    ntfy_topic: str,
    auto_train_enabled: bool,
    review_id: Optional[int],
    was_timeout: bool,
) -> bool:
    """Steps 6-9: sink delivery, auto-train, counter, notify."""
    run_id = event.pipeline_run_id

    sink = _pick_sink(
        default_sink, calibre_library_path,
        folder_sink_path, audiobookshelf_library_path,
        cwa_ingest_path,
    )

    try:
        if prep.delivery_source.exists():
            sink_result = await sink.deliver(
                str(prep.delivery_source), prep.metadata
            )
        else:
            sink_result = SinkResult(
                success=False,
                sink_name=sink.name,
                error="no book file to deliver",
            )
    finally:
        if prep.cleanup_temp and prep.temp_dir and prep.temp_dir.exists():
            shutil.rmtree(str(prep.temp_dir), ignore_errors=True)

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

    if auto_train_enabled:
        author_blob = prep.announce_author or prep.metadata.author or ""
        if author_blob:
            added = await train_authors_from_blob(db, author_blob)
            if added:
                _log.info(
                    "pipeline: auto-trained %d author(s) from %s",
                    added, event.torrent_name,
                )

    await pipe_storage.set_state(db, run_id, pipe_storage.PIPE_COMPLETE)
    await grabs_storage.set_state(
        db, event.grab_id, grabs_storage.STATE_COMPLETE
    )

    # Record the Calibre-additions counter for digest reporting.
    try:
        await calibre_adds_storage.record_addition(
            db,
            grab_id=event.grab_id,
            review_id=review_id,
            title=prep.metadata.title or None,
            author=prep.metadata.author or None,
            sink_name=sink_result.sink_name,
            was_timeout=was_timeout,
        )
    except Exception:
        _log.exception("calibre_additions record failed (non-fatal)")

    _log.info(
        "pipeline: complete grab_id=%d %s → %s",
        event.grab_id, event.torrent_name, sink_result.sink_name,
    )

    if ntfy_url and ntfy_topic:
        try:
            await ntfy.notify_pipeline_complete(
                ntfy_url, ntfy_topic,
                event.torrent_name, sink_result.sink_name,
            )
        except Exception:
            _log.exception("ntfy pipeline-complete notify failed (non-fatal)")

    return True


# ─── Review-queue resume entrypoint ─────────────────────────────


async def deliver_reviewed(
    db: aiosqlite.Connection,
    *,
    review_id: int,
    default_sink: str,
    calibre_library_path: str,
    folder_sink_path: str,
    audiobookshelf_library_path: str = "",
    cwa_ingest_path: str = "",
    ntfy_url: str = "",
    ntfy_topic: str = "",
    auto_train_enabled: bool = True,
    was_timeout: bool = False,
) -> bool:
    """Deliver a reviewed book from the review queue to the sink.

    Called by:
      - the approve endpoint (user said yes)
      - the auto-add timeout job (grace period expired)
    """
    entry = await review_storage.get_entry(db, review_id)
    if entry is None:
        _log.warning("deliver_reviewed: review_id=%d not found", review_id)
        return False
    if entry.status != review_storage.STATUS_PENDING:
        _log.info(
            "deliver_reviewed: review_id=%d already in status %s",
            review_id, entry.status,
        )
        return False

    grab = await grabs_storage.get_grab(db, entry.grab_id)
    if grab is None:
        await review_storage.set_status(
            db, review_id, review_storage.STATUS_FAILED,
            decision_note="grab row missing",
        )
        return False

    staged = Path(entry.staged_path) / entry.book_filename
    if not staged.exists():
        _log.warning(
            "deliver_reviewed: review_id=%d staged file missing (%s)",
            review_id, staged,
        )
        await review_storage.set_status(
            db, review_id, review_storage.STATUS_FAILED,
            decision_note=f"staged file missing: {staged}",
        )
        return False

    metadata = BookMetadata(
        title=entry.metadata.get("title", "") or "",
        author=entry.metadata.get("author", "") or "",
        series=entry.metadata.get("series"),
        series_index=entry.metadata.get("series_index"),
        language=entry.metadata.get("language"),
        publisher=entry.metadata.get("publisher"),
        description=entry.metadata.get("description"),
        isbn=entry.metadata.get("isbn"),
        format=entry.metadata.get("format"),
    )

    prep = _PreparedBook(
        book_path=staged,
        book_filename=entry.book_filename,
        book_format=entry.book_format or "",
        metadata=metadata,
        announce_author=entry.metadata.get("author", "") or grab.author_blob,
        delivery_source=staged,
        temp_dir=None,
        cleanup_temp=False,
    )

    # Synthesize a CompletionEvent so _deliver_prepared can reuse
    # its existing pipeline-run bookkeeping.
    synthetic_event = CompletionEvent(
        grab_id=entry.grab_id,
        qbit_hash=grab.qbit_hash or "",
        torrent_name=grab.torrent_name,
        save_path=str(Path(entry.staged_path)),
        pipeline_run_id=entry.pipeline_run_id or 0,
    )

    ok = await _deliver_prepared(
        db, synthetic_event, prep,
        default_sink=default_sink,
        calibre_library_path=calibre_library_path,
        folder_sink_path=folder_sink_path,
        audiobookshelf_library_path=audiobookshelf_library_path,
        cwa_ingest_path=cwa_ingest_path,
        ntfy_url=ntfy_url,
        ntfy_topic=ntfy_topic,
        auto_train_enabled=auto_train_enabled,
        review_id=review_id,
        was_timeout=was_timeout,
    )

    if ok:
        await review_storage.set_status(
            db, review_id, review_storage.STATUS_DELIVERED,
            decision_note="timeout auto-add" if was_timeout else "approved",
        )
        # Clean up the review staging dir now that the book has
        # been delivered.
        try:
            review_dir = Path(entry.staged_path)
            if review_dir.exists():
                shutil.rmtree(str(review_dir), ignore_errors=True)
        except Exception:
            pass
    else:
        # Sink failed. Track the attempt count and either queue for
        # retry or dump to the emergency export folder.
        prev_note = entry.decision_note or ""
        attempt = 1
        if "sink_attempt:" in prev_note:
            try:
                attempt = int(prev_note.split("sink_attempt:")[1].split()[0]) + 1
            except (ValueError, IndexError):
                pass

        from app.config import load_settings
        settings = load_settings()
        max_retries = int(settings.get("sink_max_retries", 3))
        emergency_path = settings.get("emergency_export_path", "") or ""

        if attempt >= max_retries and emergency_path:
            # Max retries exceeded — dump to emergency folder.
            try:
                emer_dir = Path(emergency_path)
                emer_dir.mkdir(parents=True, exist_ok=True)
                staged = Path(entry.staged_path) / entry.book_filename
                if staged.exists():
                    dest = emer_dir / entry.book_filename
                    shutil.copy2(str(staged), str(dest))
                    _log.warning(
                        "pipeline: sink failed %d times for review_id=%d — "
                        "exported to emergency folder: %s",
                        attempt, review_id, dest,
                    )
            except Exception:
                _log.exception("pipeline: emergency export failed")
            await review_storage.set_status(
                db, review_id, review_storage.STATUS_FAILED,
                decision_note=f"sink failed after {attempt} attempts, exported to emergency folder",
            )
        else:
            # Queue for retry on next review-timeout tick.
            await review_storage.set_status(
                db, review_id, review_storage.STATUS_SINK_PENDING,
                decision_note=f"sink_attempt:{attempt} — will retry on next tick",
            )
            _log.info(
                "pipeline: sink delivery failed for review_id=%d (attempt %d/%d), "
                "queued for retry",
                review_id, attempt, max_retries,
            )

    return ok


# ─── Sink picker + failure recorder ─────────────────────────────


def _pick_sink(
    default_sink: str,
    calibre_library_path: str,
    folder_sink_path: str,
    audiobookshelf_library_path: str,
    cwa_ingest_path: str,
):
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
    _log.warning(
        "pipeline: failed grab_id=%d %s: %s",
        event.grab_id, event.torrent_name, error,
    )
    await pipe_storage.set_state(
        db, run_id, pipe_storage.PIPE_FAILED, error=error,
    )
    if ntfy_url and ntfy_topic:
        try:
            await ntfy.notify_error(ntfy_url, ntfy_topic, event.torrent_name, error)
        except Exception:
            _log.exception("ntfy error notify failed (non-fatal)")
