"""
Shared mutable state for Hermeece's background tasks.

Mirrors the AthenaScout state-module pattern: module-level singletons
that the lifespan startup, the routers, and the background workers
all read and mutate. Because Python modules are singletons within a
process, every importer sees the same values.

IMPORTANT — module attribute access:
    Always use `state.foo`, not `from app.state import foo`. Direct
    imports create a local binding that won't see updates from other
    modules. For REASSIGNMENT, you MUST use the module attribute form
    (`state.foo = new_value`) — bare assignment inside a function
    rebinds a local variable instead of mutating the shared state.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

_log = logging.getLogger("hermeece")


def supervised_task(
    coro_factory: Callable[[], Awaitable[None]],
    *,
    name: str,
    restart_on_crash: bool = True,
    restart_delay: float = 5.0,
) -> asyncio.Task:
    """Wrap a long-running background coroutine with exception logging.

    Lifted from AthenaScout's `app/state.py`. The problem it solves:
    `asyncio.create_task(some_coro())` silently loses exceptions unless
    the task is awaited. For fire-and-forget workers (the IRC listener,
    the qBit poller, the snatch-budget watcher) a crash would otherwise
    show up as a one-line "Task exception was never retrieved" at
    interpreter shutdown — no traceback, no restart, no visible failure.

    `coro_factory` is a zero-arg callable that RETURNS a fresh coroutine
    on each call (not a coroutine object), because restarting the task
    requires building a new one — coroutines can only be awaited once.

    Cancellation propagates: if the caller cancels the returned task,
    CancelledError bubbles out without being logged or restarted.
    """
    async def _runner():
        while True:
            try:
                await coro_factory()
                _log.info(f"supervised task {name!r} completed normally")
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(f"supervised task {name!r} crashed")
                if not restart_on_crash:
                    return
                _log.warning(
                    f"supervised task {name!r} restarting in {restart_delay}s"
                )
                try:
                    await asyncio.sleep(restart_delay)
                except asyncio.CancelledError:
                    raise

    return asyncio.create_task(_runner(), name=name)


# ─── IRC listener state ──────────────────────────────────────
# `_irc_task` is the supervised wrapper around the IrcClient's
# run_forever loop; `irc_client` is the IrcClient instance itself,
# kept reachable so the lifespan shutdown can call stop() on it
# before cancelling the task wrapper.
_irc_task: Optional[asyncio.Task] = None
irc_client: Optional[Any] = None
_irc_status: Dict[str, Any] = {
    "connected": False,
    "last_connect_at": None,
    "last_disconnect_at": None,
    "last_error": "",
    "announces_seen": 0,
    "announces_allowed": 0,
    "announces_skipped": 0,
}


# ─── Budget watcher state ───────────────────────────────────
_budget_watcher_task: Optional[asyncio.Task] = None


# ─── qBit poller state ──────────────────────────────────────
_qbit_poll_task: Optional[asyncio.Task] = None
_qbit_status: Dict[str, Any] = {
    "reachable": False,
    "last_poll_at": None,
    "last_error": "",
    "active_torrents": 0,
}


# ─── Snatch budget state (read-only mirror for the dashboard) ─
# Authoritative numbers live in the snatch_ledger table; this dict is
# the cached/derived snapshot the UI polls.
_snatch_budget: Dict[str, Any] = {
    "used": 0,
    "cap": 0,
    "queued": 0,
    "next_release_at": None,
    "last_updated_at": None,
}


# ─── Dispatcher singleton ────────────────────────────────────
# Set by main.py's lifespan during startup. The inject router and
# the IRC listener both read this attribute, so swapping in a test
# dispatcher is just `state.dispatcher = test_dispatcher` — no
# monkey-patching, no DI framework.
dispatcher: Optional[Any] = None
