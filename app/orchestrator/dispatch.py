"""
The dispatcher.

Two public functions, both following the same shape:

  - `handle_announce(deps, announce)` — called by the IRC listener
    for every parsed announce. Runs the filter, evaluates the rate
    limiter, fetches the .torrent file (if allowed), submits to
    qBit (if budget allows), and updates all the persistence
    layers in the right order.

  - `inject_grab(deps, torrent_id, ...)` — called by the manual-
    inject HTTP endpoint. Skips the filter (the user already
    decided they want this) but still goes through the rate
    limiter so a manually-injected grab respects the snatch budget.

The `Dispatcher` dataclass below is the dependency container —
everything the dispatcher needs is passed in explicitly so the
tests can construct one with fakes and verify the orchestration
without any global state. In production, `main.py`'s lifespan
builds a singleton Dispatcher with real implementations and
hands it to the IRC listener and the inject router.

State transitions written by this module:

    decide=submit, fetch ok, qBit ok      → STATE_SUBMITTED
    decide=submit, fetch=cookie_expired   → STATE_FAILED_COOKIE_EXPIRED
    decide=submit, fetch=torrent_not_found → STATE_FAILED_TORRENT_GONE
    decide=submit, fetch=other failure    → STATE_FAILED_UNKNOWN
    decide=submit, fetch ok, qBit reject  → STATE_FAILED_QBIT_REJECTED
    decide=submit, fetch ok, qBit auth    → STATE_FAILED_UNKNOWN
    decide=queue,  fetch ok               → STATE_PENDING_QUEUE (queued)
    decide=queue,  fetch failure          → same as submit-failure
    decide=drop                           → no grab row, only audit
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

import aiosqlite

from app.clients.base import AddResult, TorrentClient
from app.filter.gate import Announce, Decision, FilterConfig, evaluate_announce
from app.mam.grab import GrabResult
from app.mam.torrent_meta import BencodeError, info_hash
from app.rate_limit import decide_grab_action
from app.rate_limit import ledger as ledger_mod
from app.rate_limit import queue as queue_mod
from app.storage import grabs as grabs_storage

_log = logging.getLogger("hermeece.orchestrator.dispatch")


# ─── Dependency container ────────────────────────────────────


# Type aliases for the injectable callables. Production code uses
# `app.mam.grab.fetch_torrent` and a `QbitClient` instance; tests
# pass in fakes that record what they were called with.
GrabFetchFn = Callable[[str, str], Awaitable[GrabResult]]


class _DbProvider(Protocol):
    """Anything that can hand back an aiosqlite.Connection on demand.

    Defined as a Protocol so the test fixture can pass a simple
    `lambda: get_db()` factory and production code can pass the
    same factory bound to the real APP_DB_PATH.
    """

    async def __call__(self) -> aiosqlite.Connection: ...


@dataclass
class DispatcherDeps:
    """Bag of injected dependencies for the dispatcher functions.

    Tests construct one of these with fakes and pass it to
    `handle_announce` / `inject_grab` directly. The dispatcher
    never reaches into module globals — every effect goes through
    one of these fields.
    """

    # Read-only knobs
    filter_config: FilterConfig
    mam_token: str
    qbit_category: str
    budget_cap: int
    queue_max: int
    queue_mode_enabled: bool
    seed_seconds_required: int

    # Behavior
    db_factory: _DbProvider
    fetch_torrent: GrabFetchFn
    qbit: TorrentClient

    # Tag list to apply to every torrent Hermeece submits to qBit.
    # Default empty list = no tagging. Production wires this from
    # settings.json `qbit_tag` (default "hermeece-seed") so the
    # user's existing manual-seed / autobrr-seed / hermeece-seed
    # tag taxonomy stays consistent. Defaulted to empty in the
    # dataclass so existing tests don't have to opt in.
    qbit_tags: list[str] = field(default_factory=list)

    # Optional: an audit hook for tests / future observability.
    on_event: Optional[Callable[[str, dict], None]] = None


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of a single dispatch call.

    `action` mirrors the rate-limit decision (`submit`/`queue`/`drop`)
    when the filter allowed the announce, or `"skip"` when the filter
    rejected it. `grab_id` is the row id in `grabs` (None for skip
    and drop). `error` is set when fetching or submitting failed.
    """

    action: str               # "skip" | "submit" | "queue" | "drop"
    reason: str               # human-readable + machine-stable
    announce_id: int          # always set — every dispatch produces an audit row
    grab_id: Optional[int] = None
    qbit_hash: Optional[str] = None
    error: Optional[str] = None


# ─── Public surface ──────────────────────────────────────────


async def handle_announce(
    deps: DispatcherDeps, announce: Announce, *, raw_line: str = ""
) -> DispatchResult:
    """Process one announce end-to-end.

    Called by the IRC listener's `on_announce` callback. Runs the
    full pipeline:

      1. Evaluate the filter
      2. Always write the audit row in `announces`
      3. If filter says skip → return "skip"
      4. If filter says allow → consult the rate limiter
      5. If decision is drop → return "drop" (no grab row, only audit)
      6. Fetch the .torrent file
      7. If fetch fails → write a failed grab row, return failure
      8. If decision is submit → submit to qBit, record in ledger
      9. If decision is queue → enqueue (file already fetched)

    Returns a `DispatchResult` describing the outcome. Never raises
    on the happy or expected-failure paths — the IRC listener
    iterates over many announces and a single bad one shouldn't
    take down the loop.
    """
    decision = evaluate_announce(announce, deps.filter_config)
    return await _dispatch_with_decision(
        deps,
        announce=announce,
        raw_line=raw_line,
        filter_decision=decision,
        skip_filter=False,
    )


async def inject_grab(
    deps: DispatcherDeps,
    *,
    torrent_id: str,
    torrent_name: str = "",
    category: str = "",
    author_blob: str = "",
    raw_line: str = "manual_inject",
) -> DispatchResult:
    """Manually queue a grab by torrent ID.

    Skips the filter (the user already decided they want this) but
    DOES go through the rate limiter — a manually-injected grab
    still counts against the snatch budget like any other.

    Used by:
      - the manual-inject HTTP endpoint (Phase 1)
      - the cookie-rotation manual test recipe
      - the eventual AthenaScout integration (Phase 3)

    The metadata fields (`torrent_name`, `category`, `author_blob`)
    are only used for audit-log readability — the dispatcher doesn't
    need them to operate. Callers that have the data should pass it;
    the inject endpoint passes them as empty strings when called
    with just a torrent ID.
    """
    fake_announce = Announce(
        torrent_id=torrent_id,
        torrent_name=torrent_name or f"manual_inject_{torrent_id}",
        category=category,
        author_blob=author_blob,
    )
    # Synthetic "allow" decision so the audit row reflects that this
    # was a manual override (reason `manual_inject` rather than the
    # filter's allowed_author / category_not_allowed / etc.).
    fake_decision = Decision(
        action="allow",
        reason="manual_inject",
        matched_author=author_blob,
    )
    return await _dispatch_with_decision(
        deps,
        announce=fake_announce,
        raw_line=raw_line,
        filter_decision=fake_decision,
        skip_filter=True,
    )


# ─── Internals ───────────────────────────────────────────────


async def _dispatch_with_decision(
    deps: DispatcherDeps,
    *,
    announce: Announce,
    raw_line: str,
    filter_decision: Decision,
    skip_filter: bool,
) -> DispatchResult:
    """The shared pipeline body used by both handle_announce and
    inject_grab. The only thing they differ on is whether the filter
    decision came from `evaluate_announce` or was synthesized.
    """
    db = await deps.db_factory()
    try:
        announce_id = await grabs_storage.record_announce(
            db,
            raw=raw_line,
            torrent_id=announce.torrent_id,
            torrent_name=announce.torrent_name,
            category=announce.category,
            author_blob=announce.author_blob,
            decision=filter_decision,
        )
        _emit(deps, "announce_recorded", {"announce_id": announce_id})

        if filter_decision.action == "skip":
            _emit(
                deps,
                "filter_skip",
                {
                    "torrent_id": announce.torrent_id,
                    "reason": filter_decision.reason,
                },
            )
            return DispatchResult(
                action="skip",
                reason=filter_decision.reason,
                announce_id=announce_id,
            )

        # Filter said allow (or we're injecting). Consult the rate
        # limiter — read current budget + queue counters from the DB.
        budget_used = await ledger_mod.count_active(db)
        queue_size = await queue_mod.size(db)

        rate_decision = decide_grab_action(
            budget_used=budget_used,
            budget_cap=deps.budget_cap,
            queue_size=queue_size,
            queue_max=deps.queue_max,
            queue_mode_enabled=deps.queue_mode_enabled,
        )
        _emit(deps, "rate_decision", {"action": rate_decision.action})

        if rate_decision.action == "drop":
            return DispatchResult(
                action="drop",
                reason=rate_decision.reason,
                announce_id=announce_id,
            )

        # Submit or queue path: create the grab row, fetch the torrent.
        initial_state = (
            grabs_storage.STATE_FETCHED
            if rate_decision.action == "submit"
            else grabs_storage.STATE_PENDING_QUEUE
        )
        grab_id = await grabs_storage.create_grab(
            db,
            announce_id=announce_id,
            mam_torrent_id=announce.torrent_id,
            torrent_name=announce.torrent_name,
            category=announce.category,
            author_blob=announce.author_blob,
            state=initial_state,
        )

        fetch_result = await deps.fetch_torrent(
            announce.torrent_id, deps.mam_token
        )

        if not fetch_result.success:
            failed_state = _grab_failure_state(fetch_result)
            await grabs_storage.set_state(
                db,
                grab_id,
                failed_state,
                failed_reason=fetch_result.failure_detail,
            )
            _emit(
                deps,
                "fetch_failed",
                {
                    "grab_id": grab_id,
                    "kind": fetch_result.failure_kind,
                    "detail": fetch_result.failure_detail,
                },
            )
            return DispatchResult(
                action=rate_decision.action,
                reason=f"fetch_failed:{fetch_result.failure_kind}",
                announce_id=announce_id,
                grab_id=grab_id,
                error=fetch_result.failure_detail,
            )

        # Fetch succeeded. Compute the info hash from the bytes so
        # we can record the ledger entry deterministically without
        # round-tripping qBit.
        torrent_bytes = fetch_result.torrent_bytes or b""
        try:
            qbit_hash = info_hash(torrent_bytes)
        except BencodeError as e:
            _log.warning(
                f"grab {grab_id}: torrent bytes did not parse as bencode: {e}"
            )
            await grabs_storage.set_state(
                db,
                grab_id,
                grabs_storage.STATE_FAILED_QBIT_REJECTED,
                failed_reason=f"unparseable torrent file: {e}",
            )
            return DispatchResult(
                action=rate_decision.action,
                reason="bad_torrent_file",
                announce_id=announce_id,
                grab_id=grab_id,
                error=str(e),
            )

        if rate_decision.action == "queue":
            # Park the grab in the pending queue. The .torrent bytes
            # ARE NOT persisted to disk in Phase 1 — the queue holds
            # the grab id; the budget watcher (in a later phase)
            # re-fetches when popping. This is intentional: keeping
            # bytes only in memory means a crash loses queued grabs
            # but never leaves stale .torrent files lying around.
            # The Phase 2 follow-up will add disk persistence.
            await queue_mod.enqueue(db, grab_id)
            await grabs_storage.set_state(
                db,
                grab_id,
                grabs_storage.STATE_PENDING_QUEUE,
                qbit_hash=qbit_hash,
            )
            _emit(deps, "queued", {"grab_id": grab_id})
            return DispatchResult(
                action="queue",
                reason=rate_decision.reason,
                announce_id=announce_id,
                grab_id=grab_id,
                qbit_hash=qbit_hash,
            )

        # Submit path: hand the bytes to qBit.
        add_result = await deps.qbit.add_torrent(
            torrent_bytes,
            category=deps.qbit_category,
            tags=deps.qbit_tags or None,
        )

        if not add_result.success:
            failed_state = _add_failure_state(add_result)
            await grabs_storage.set_state(
                db,
                grab_id,
                failed_state,
                failed_reason=add_result.failure_detail,
                qbit_hash=qbit_hash,
            )
            _emit(
                deps,
                "qbit_failed",
                {
                    "grab_id": grab_id,
                    "kind": add_result.failure_kind,
                    "detail": add_result.failure_detail,
                },
            )
            return DispatchResult(
                action="submit",
                reason=f"qbit_failed:{add_result.failure_kind}",
                announce_id=announce_id,
                grab_id=grab_id,
                qbit_hash=qbit_hash,
                error=add_result.failure_detail,
            )

        # qBit accepted it. Record the ledger entry against our
        # computed hash. The grab is now in the active budget.
        await grabs_storage.set_state(
            db,
            grab_id,
            grabs_storage.STATE_SUBMITTED,
            qbit_hash=qbit_hash,
        )
        await ledger_mod.record_grab(db, grab_id, qbit_hash)
        _emit(
            deps,
            "submitted",
            {"grab_id": grab_id, "qbit_hash": qbit_hash},
        )
        return DispatchResult(
            action="submit",
            reason="ok",
            announce_id=announce_id,
            grab_id=grab_id,
            qbit_hash=qbit_hash,
        )
    finally:
        await db.close()


def _grab_failure_state(result: GrabResult) -> str:
    """Map a GrabResult.failure_kind to a `grabs.state` value."""
    kind = result.failure_kind
    if kind == "cookie_expired":
        return grabs_storage.STATE_FAILED_COOKIE_EXPIRED
    if kind == "torrent_not_found":
        return grabs_storage.STATE_FAILED_TORRENT_GONE
    return grabs_storage.STATE_FAILED_UNKNOWN


def _add_failure_state(result: AddResult) -> str:
    """Map an AddResult.failure_kind to a `grabs.state` value."""
    kind = result.failure_kind
    if kind == "rejected":
        return grabs_storage.STATE_FAILED_QBIT_REJECTED
    if kind == "duplicate":
        return grabs_storage.STATE_DUPLICATE_IN_QBIT
    return grabs_storage.STATE_FAILED_UNKNOWN


def _emit(deps: DispatcherDeps, event: str, payload: dict) -> None:
    """Fire the optional observability hook, swallowing exceptions."""
    if deps.on_event is None:
        return
    try:
        deps.on_event(event, payload)
    except Exception:
        _log.exception(f"on_event hook raised for {event}")
