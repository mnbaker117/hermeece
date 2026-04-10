"""
Hermeece FastAPI entrypoint.

Phase 1 wires:
  - The dispatcher singleton (built once at startup from settings)
  - The manual-inject endpoint
  - The MAM IRC listener (auto-starts on boot, supervised + restarts
    on crash, reconnects with exponential backoff on disconnect)
  - The snatch budget watcher loop (polls qBit, reconciles ledger,
    drains pending_queue when budget frees)

If settings change at runtime (via the eventual Settings UI in
Phase 3), the dispatcher will need to be rebuilt — that plumbing
lives where the Settings UI does, not here.

Both background loops are wrapped in `state.supervised_task` so
they restart automatically on unexpected crashes and a fatal
exception in one doesn't take down the other.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app import state
from app.clients.qbittorrent import QbitClient
from app.config import (
    ENV_VERBOSE_LOGGING,
    apply_logging,
    load_settings,
    save_settings,
)
from app.database import get_db, init_db
from app.filter.gate import Announce, FilterConfig
from app.filter.normalize import normalize_category
from app.mam.cookie import (
    aclose_session,
    set_current_token,
    set_rotation_callback,
)
from app.mam.grab import fetch_torrent
from app.mam.irc import IrcClient, IrcConfig
from app.orchestrator.budget_watcher import run_loop as budget_watcher_loop
from app.orchestrator.dispatch import DispatcherDeps, handle_announce
from app.routers.inject import router as inject_router

# Configure logging once at import time. The verbose toggle gets re-applied
# from settings.json after load_settings() runs in the lifespan.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
apply_logging(ENV_VERBOSE_LOGGING)

_log = logging.getLogger("hermeece")


def _build_filter_config(settings: dict) -> FilterConfig:
    """Construct a FilterConfig from a settings snapshot.

    Allow / ignore lists are sourced from the database in Phase 1+
    via the weekly audit job; for now they start empty and the
    inject endpoint (which bypasses the filter) is the only way to
    grab anything until Phase 3 wires up the author UI.
    """
    return FilterConfig(
        allowed_categories=frozenset(
            normalize_category(c) for c in settings.get("allowed_categories", [])
        ),
        allowed_authors=frozenset(),
        ignored_authors=frozenset(),
    )


def _build_dispatcher(settings: dict) -> DispatcherDeps:
    """Build the dispatcher from a settings snapshot."""
    qbit = QbitClient(
        base_url=settings.get("qbit_url", ""),
        username=settings.get("qbit_username", ""),
        password=settings.get("qbit_password", ""),
    )
    return DispatcherDeps(
        filter_config=_build_filter_config(settings),
        mam_token=settings.get("mam_session_id", ""),
        qbit_category=settings.get("qbit_watch_category", "[mam-reseed]"),
        budget_cap=int(settings.get("snatch_budget_cap", 200)),
        queue_max=int(settings.get("snatch_queue_max", 100)),
        queue_mode_enabled=settings.get("snatch_full_mode", "queue") == "queue",
        seed_seconds_required=int(
            settings.get("snatch_seed_hours_required", 72)
        ) * 3600,
        db_factory=get_db,
        fetch_torrent=fetch_torrent,
        qbit=qbit,
    )


# ─── Debounced cookie-rotation persistence ───────────────────
#
# MAM rotates the session cookie on every API call. If Hermeece is
# running hot (several inject calls + a budget-watcher poll every
# 60s + any IRC-triggered grabs), that could mean a few
# settings.json writes per minute. Debounce so we only flush to disk
# at most every `_ROTATION_PERSIST_DEBOUNCE_SECONDS` — the in-memory
# token is always current; the only thing at stake is whether a
# hard container crash would lose up to 60s of rotation progress.
# Even the worst case is harmless: on restart we use the slightly
# older cookie from settings.json, immediately get a fresh one on
# the first MAM call, and we're back in sync.
_ROTATION_PERSIST_DEBOUNCE_SECONDS = 60.0
_rotation_pending_token: Optional[str] = None
_rotation_persist_task: Optional[asyncio.Task] = None


async def _rotation_callback(new_token: str) -> None:
    """Persist the rotated cookie to settings.json, debounced.

    Called by `app.mam.cookie._handle_response_cookie` on every
    successful rotation. We stash the new token and (re)schedule a
    background task to flush it to disk after the debounce window.
    Multiple rotations within the window collapse into a single
    disk write of whichever token was seen last.
    """
    global _rotation_pending_token, _rotation_persist_task
    _rotation_pending_token = new_token

    # Cancel any existing pending flush so the debounce timer
    # resets — the most recent rotation wins, and we don't want a
    # stale token hitting disk while a fresher one is already in
    # memory.
    if _rotation_persist_task is not None and not _rotation_persist_task.done():
        _rotation_persist_task.cancel()

    _rotation_persist_task = asyncio.create_task(
        _debounced_persist_rotation()
    )


async def _debounced_persist_rotation() -> None:
    """Wait out the debounce window, then write the latest token."""
    try:
        await asyncio.sleep(_ROTATION_PERSIST_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # superseded by a newer rotation; nothing to do

    token = _rotation_pending_token
    if not token:
        return

    try:
        settings = load_settings()
        if settings.get("mam_session_id") == token:
            return  # already on disk (someone else wrote it)
        settings = dict(settings)
        settings["mam_session_id"] = token
        save_settings(settings)
        _log.info("MAM session cookie persisted to settings.json")
    except Exception:
        _log.exception("failed to persist rotated MAM cookie to settings.json")


def _build_irc_config(settings: dict) -> IrcConfig:
    """Construct an IrcConfig from a settings snapshot.

    Returns a config with `auth_mode="none"` if no IRC credentials
    are configured — the lifespan won't start the listener in that
    case (Hermeece runs as a synchronous-call pipeline, useful for
    testing without IRC).
    """
    nick = settings.get("mam_irc_nick", "")
    account = settings.get("mam_irc_account", "")
    password = settings.get("mam_irc_password", "")
    auth_mode = "sasl" if (account and password) else "none"
    return IrcConfig(
        nick=nick,
        account=account,
        password=password,
        auth_mode=auth_mode,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup + shutdown wiring."""
    settings = load_settings()
    apply_logging(settings.get("verbose_logging", False))
    _log.info("Hermeece starting")
    await init_db()
    _log.info("Database initialized")

    # Seed the MAM cookie rotation layer with whatever's in
    # settings.json right now. Every subsequent MAM API call will
    # update this in-memory value automatically, and the debounced
    # rotation callback writes changes back to disk.
    set_current_token(settings.get("mam_session_id", ""))
    set_rotation_callback(_rotation_callback)
    _log.info("MAM cookie rotation handler wired")

    state.dispatcher = _build_dispatcher(settings)
    _log.info("Dispatcher initialized")

    # ── Background loops (supervised) ────────────────────────
    #
    # Both loops capture the dispatcher singleton at startup time.
    # If a settings change rebuilds the dispatcher, the loops will
    # need to be restarted — that plumbing lives with the Settings
    # UI in Phase 3.

    deps_for_loops = state.dispatcher

    # Budget watcher: polls qBit, reconciles ledger, drains queue.
    # Auto-disabled if qBit isn't configured (the loop would just
    # error out on every tick otherwise).
    if settings.get("qbit_url"):
        interval = float(
            settings.get("qbit_poll_interval_seconds", 60)
        )

        async def _budget_loop_factory():
            await budget_watcher_loop(deps_for_loops, interval_seconds=interval)

        state._budget_watcher_task = state.supervised_task(
            _budget_loop_factory, name="snatch-budget-watcher"
        )
        _log.info(
            f"Budget watcher started (interval={interval}s, "
            f"qbit_category={settings.get('qbit_watch_category', '[mam-reseed]')})"
        )
    else:
        _log.info("Budget watcher disabled (qbit_url not configured)")

    # IRC listener: connects to MAM, parses announces, dispatches
    # to handle_announce. Auto-disabled if MAM auth isn't configured
    # OR if the user explicitly toggled mam_irc_enabled off in
    # settings (e.g. during cookie rotation, or in dry-run-friendly
    # test setups).
    irc_enabled = settings.get("mam_irc_enabled", True)
    irc_config = _build_irc_config(settings)
    if irc_enabled and irc_config.auth_mode != "none" and irc_config.nick:
        async def _on_announce(announce: Announce) -> None:
            # Bridge the IRC callback signature to the dispatcher.
            # The dispatcher's own try/except keeps a single bad
            # announce from killing the listener; this thin wrapper
            # is just signature glue.
            await handle_announce(deps_for_loops, announce)

        irc_client = IrcClient(irc_config, _on_announce)
        state.irc_client = irc_client

        async def _irc_loop_factory():
            await irc_client.run_forever()

        state._irc_task = state.supervised_task(
            _irc_loop_factory, name="mam-irc-listener"
        )
        _log.info(
            f"IRC listener started (server={irc_config.server}, "
            f"channel={irc_config.channel}, nick={irc_config.nick})"
        )
    else:
        _log.info(
            "IRC listener disabled (set mam_irc_nick + mam_irc_account + "
            "mam_irc_password to enable)"
        )

    # Phase 3 wiring lands here:
    #   - APScheduler with cookie_check / weekly_audit / daily_digest jobs

    try:
        yield
    finally:
        _log.info("Hermeece shutting down")

        # Stop accepting new rotation notifications. Any request
        # in flight right now might still try to fire the callback
        # between now and when its response lands, and we don't
        # want that racing against the settings.json flush below
        # or the disk teardown.
        set_rotation_callback(None)

        # If there's a pending debounced rotation waiting to write,
        # either flush it immediately or cancel the timer — we do
        # both: cancel the timer so it doesn't sleep for 60s during
        # an otherwise-fast shutdown, then write the pending token
        # synchronously. This guarantees we never lose the most
        # recent cookie to a shutdown.
        global _rotation_persist_task
        if _rotation_persist_task is not None and not _rotation_persist_task.done():
            _rotation_persist_task.cancel()
            try:
                await _rotation_persist_task
            except (asyncio.CancelledError, Exception):
                pass
            _rotation_persist_task = None
        if _rotation_pending_token:
            try:
                current = load_settings()
                if current.get("mam_session_id") != _rotation_pending_token:
                    merged = dict(current)
                    merged["mam_session_id"] = _rotation_pending_token
                    save_settings(merged)
                    _log.info(
                        "Flushed pending MAM cookie rotation during shutdown"
                    )
            except Exception:
                _log.exception("error flushing pending cookie rotation on shutdown")

        # Stop the IRC listener cleanly first so its run_forever
        # loop sees the stop signal and breaks out of any backoff
        # wait, instead of being hard-cancelled mid-handshake.
        if state.irc_client is not None:
            try:
                await state.irc_client.stop()
            except Exception:
                _log.exception("error stopping IRC client during shutdown")

        # Cancel the supervised tasks. supervised_task wraps the
        # coroutines with restart-on-crash logic, so we need to
        # cancel the wrapper task itself — the inner coroutine sees
        # CancelledError and unwinds cleanly.
        for task_attr in ("_irc_task", "_budget_watcher_task"):
            task = getattr(state, task_attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(state, task_attr, None)

        # Tear down whatever the dispatcher owns. The qBit client
        # holds an httpx.AsyncClient; the cookie module holds
        # another one. Both expose async close methods that are
        # safe to call multiple times.
        if state.dispatcher is not None:
            try:
                await state.dispatcher.qbit.aclose()
            except Exception:
                _log.exception("error closing qBit client during shutdown")
        try:
            await aclose_session()
        except Exception:
            _log.exception("error closing MAM cookie session during shutdown")
        state.dispatcher = None
        state.irc_client = None


app = FastAPI(
    title="Hermeece",
    description="Hermes for the meece — MAM courier and Calibre ingest pipeline",
    version="0.0.1",
    lifespan=lifespan,
)
app.include_router(inject_router)


@app.get("/api/health")
async def health():
    """Liveness check."""
    return {
        "status": "ok",
        "service": "hermeece",
        "dispatcher_ready": state.dispatcher is not None,
    }
