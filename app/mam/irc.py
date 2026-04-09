"""
MAM IRC client.

A minimal, single-purpose IRC client that connects to
`irc.myanonamouse.net`, authenticates via SASL PLAIN (or NickServ as
a fallback), joins `#announce`, listens for PRIVMSGs from MouseBot,
parses them via `mam.announce.parse_announce`, and dispatches each
parsed `Announce` to a user-supplied callback.

Why a hand-rolled client instead of `pydle`?

  Hermeece's IRC needs are extraordinarily narrow — one server, one
  channel, one bot we listen to, two auth modes, ping handling,
  reconnect. We use ~5% of pydle's surface area. A hand-rolled
  ~250-line client gives us full control over reconnect semantics,
  trivially testable I/O (we inject a connect_fn that returns a
  fake StreamReader/StreamWriter pair), and zero external dependency
  to fight. The IRC protocol is just lines of text — implementing
  the slice we need is smaller than the test scaffolding pydle would
  require.

Reconnect strategy is lifted from the Autobrr research findings:

  - Exponential backoff starting at 15 seconds, capped at 10 minutes
  - Up to 25 back-to-back reconnect attempts before giving up
  - 4-minute read timeout (matches Autobrr's KeepAlive)
  - The "manual-stop guard" from Autobrr issue #1239: when a stop
    signal arrives DURING a reconnect backoff, break out immediately
    instead of completing the wait and trying to reconnect.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from app.filter.gate import Announce
from app.mam.announce import parse_announce

_log = logging.getLogger("hermeece.mam.irc")


# ─── Config ──────────────────────────────────────────────────


@dataclass(frozen=True)
class IrcConfig:
    """Everything the IRC client needs to know to talk to MAM.

    Defaults are MAM-specific because Hermeece is MAM-only by design.
    The fields are exposed so tests can override anything (especially
    timeouts and reconnect counts) without monkey-patching.
    """

    server: str = "irc.myanonamouse.net"
    port: int = 6697
    tls: bool = True
    # MAM IRC has historically used a self-signed-ish cert that
    # Autobrr's config tells you to skip-verify. We default to that
    # behavior for the same compat reason; can be flipped via Settings.
    tls_verify: bool = False

    # The bot identity registered with NickServ on MAM IRC.
    nick: str = ""
    user: str = "hermeece"
    realname: str = "Hermeece courier bot"

    # SASL/NickServ account credentials. For SASL PLAIN, `account` is
    # the authcid (your NickServ account name); `password` is the
    # NickServ password. For NickServ identify, `password` is sent in
    # a PRIVMSG to NickServ after the welcome.
    account: str = ""
    password: str = ""

    # "sasl" → CAP/SASL PLAIN handshake (preferred, what OP uses)
    # "nickserv" → plain NICK/USER then PRIVMSG NickServ IDENTIFY
    # "none" → no auth (test mode, won't work against real MAM)
    auth_mode: str = "sasl"

    channel: str = "#announce"
    # Only PRIVMSGs from this nick in `channel` are treated as
    # announces. Anything else is logged at debug and ignored.
    announcer_nick: str = "MouseBot"

    # ── Reconnect / liveness (Autobrr-derived) ──────────────
    initial_backoff_seconds: float = 15.0
    max_backoff_seconds: float = 600.0
    max_reconnect_attempts: int = 25

    # 4 minutes — if we don't see ANY traffic from the server in this
    # window the connection is presumed dead and we cycle through
    # reconnect. The server should be sending PINGs at least every
    # ~2 minutes, so 4 is a generous lower bound.
    read_timeout_seconds: float = 240.0

    # Per-handshake-step timeout. If SASL auth or channel join hangs
    # this long, we abort and reconnect.
    handshake_timeout_seconds: float = 30.0


# ─── IRC line parser ─────────────────────────────────────────


@dataclass
class IrcMessage:
    """One parsed IRC protocol line."""

    raw: str
    prefix: str = ""        # everything between : and the first space (host or nick!user@host)
    nick: str = ""          # extracted from prefix, if present
    command: str = ""       # uppercased command or 3-digit numeric
    params: list[str] = field(default_factory=list)
    trailing: str = ""      # the post-" :" text


_PREFIX_NICK_RX = re.compile(r"^([^!@\s]+)")


def parse_irc_line(line: str) -> Optional[IrcMessage]:
    """Parse one IRC protocol line into an IrcMessage.

    Returns None for empty input. Tolerant of malformed lines — IRC
    has been around long enough that everything in the wild is at
    least *almost* well-formed, and the read loop should drop bad
    lines silently rather than crashing.
    """
    if not line:
        return None
    msg = IrcMessage(raw=line)
    rest = line

    # Optional prefix: ":sender ..."
    if rest.startswith(":"):
        space = rest.find(" ")
        if space < 0:
            return None
        msg.prefix = rest[1:space]
        m = _PREFIX_NICK_RX.match(msg.prefix)
        if m:
            msg.nick = m.group(1)
        rest = rest[space + 1:]

    # Trailing parameter starts with " :" — everything after the
    # delimiter is one big param including spaces.
    trailing_idx = rest.find(" :")
    if trailing_idx >= 0:
        msg.trailing = rest[trailing_idx + 2:]
        head = rest[:trailing_idx]
    else:
        head = rest

    parts = head.split(" ")
    if not parts or not parts[0]:
        return None
    msg.command = parts[0].upper()
    msg.params = [p for p in parts[1:] if p]
    return msg


# ─── The client ──────────────────────────────────────────────


# Type alias for the connection factory the client uses to open a
# socket. Production code uses the default `_real_connect`. Tests
# inject a fake that returns an in-memory reader/writer pair.
ConnectFn = Callable[
    [],
    Awaitable[tuple[asyncio.StreamReader, Any]],
]


class IrcClient:
    """Connects to MAM IRC, parses announces, dispatches to a callback.

    Lifecycle:
      - `await client.run_forever()` runs until `await client.stop()`
        is called from another task. Internally manages the
        connect → auth → join → read loop and reconnects on failure.
      - `client.connected`, `client.authenticated`, `client.joined`,
        `client.last_error`, `client.announces_seen`,
        `client.announces_dispatched` are read-only status fields the
        dashboard polls.

    The `on_announce` callback is awaited for every successfully-parsed
    announce. Parser failures (announces that don't match the regex)
    are logged at debug and dropped — they're typically MouseBot
    status messages or non-torrent PRIVMSGs we don't care about.

    `connect_fn` is a test hook. Production code leaves it None and
    the client uses asyncio's real network transport.
    """

    def __init__(
        self,
        config: IrcConfig,
        on_announce: Callable[[Announce], Awaitable[None]],
        *,
        connect_fn: Optional[ConnectFn] = None,
    ) -> None:
        self.config = config
        self.on_announce = on_announce
        self._connect_fn = connect_fn

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Any = None
        self._stop = asyncio.Event()

        # Status fields (read by app.state mirror + dashboard).
        self.connected = False
        self.authenticated = False
        self.joined = False
        self.last_error = ""
        self.announces_seen = 0
        self.announces_dispatched = 0

    # ─── Public API ──────────────────────────────────────────

    async def run_forever(self) -> None:
        """Connect, run, reconnect on failure, until stop() is called."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._run_once()
                # If _run_once returned cleanly (server disconnected
                # us), reset the backoff counter — we made it through
                # a real connection cycle, so the next attempt is a
                # "first attempt" again.
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                _log.warning(f"IRC connection error: {self.last_error}")

            self._reset_status()

            if self._stop.is_set():
                _log.info("IRC stop requested; not reconnecting")
                break

            attempt += 1
            if attempt > self.config.max_reconnect_attempts:
                _log.error(
                    f"IRC giving up after {attempt - 1} reconnect attempts; "
                    f"last error: {self.last_error}"
                )
                break

            delay = self._compute_backoff(attempt)
            _log.info(
                f"IRC reconnecting in {delay:.0f}s "
                f"(attempt {attempt}/{self.config.max_reconnect_attempts})"
            )

            # Manual-stop guard (Autobrr issue #1239): wait for either
            # the backoff timer OR a stop signal. If stop fires during
            # the wait, break immediately instead of attempting another
            # connection that we'd just have to tear down.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                # If wait_for returned without timeout, stop is set.
                _log.info("IRC stop signaled during reconnect backoff")
                break
            except asyncio.TimeoutError:
                pass  # delay elapsed normally; fall through to retry

    async def stop(self) -> None:
        """Signal the run loop to exit and tear down the open connection."""
        self._stop.set()
        await self._close_connection()

    # ─── Connection lifecycle ────────────────────────────────

    async def _run_once(self) -> None:
        """One full connect → auth → join → read cycle.

        Returns normally on a clean disconnect; raises on any failure
        the run_forever loop should treat as "try reconnecting."
        """
        await self._open_connection()
        try:
            if self.config.auth_mode == "sasl":
                await asyncio.wait_for(
                    self._sasl_handshake(),
                    timeout=self.config.handshake_timeout_seconds,
                )
            else:
                await self._send_nick_user()

            await asyncio.wait_for(
                self._wait_for_welcome(),
                timeout=self.config.handshake_timeout_seconds,
            )

            if self.config.auth_mode == "nickserv":
                await self._nickserv_identify()

            await asyncio.wait_for(
                self._join_channel(),
                timeout=self.config.handshake_timeout_seconds,
            )

            self.authenticated = True
            self.joined = True
            await self._read_loop()
        finally:
            await self._close_connection()

    async def _open_connection(self) -> None:
        if self._connect_fn is not None:
            self._reader, self._writer = await self._connect_fn()
        else:
            self._reader, self._writer = await self._real_connect()
        self.connected = True
        _log.info(f"IRC connected to {self.config.server}:{self.config.port}")

    async def _real_connect(self):
        ssl_ctx: Optional[ssl.SSLContext] = None
        if self.config.tls:
            ssl_ctx = ssl.create_default_context()
            if not self.config.tls_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        return await asyncio.open_connection(
            self.config.server, self.config.port, ssl=ssl_ctx
        )

    async def _close_connection(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._writer = None
        self._reader = None
        try:
            writer.close()
            wait_closed = getattr(writer, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
        except Exception as e:
            _log.debug(f"IRC writer close raised: {e}")

    def _reset_status(self) -> None:
        self.connected = False
        self.authenticated = False
        self.joined = False

    # ─── Wire I/O ────────────────────────────────────────────

    async def _send(self, line: str) -> None:
        """Write one CRLF-terminated line to the server."""
        if self._writer is None:
            raise ConnectionError("send: writer is closed")
        # Strip any embedded CR/LF for safety — IRC injection via
        # newline-in-channel-name is a real bug class.
        clean = line.replace("\r", "").replace("\n", "")
        # Don't log auth payloads at INFO — they contain credentials.
        if clean.upper().startswith("AUTHENTICATE ") and clean != "AUTHENTICATE PLAIN":
            _log.debug("IRC > AUTHENTICATE <redacted>")
        elif clean.upper().startswith("PRIVMSG NICKSERV"):
            _log.debug("IRC > PRIVMSG NickServ <redacted>")
        else:
            _log.debug(f"IRC > {clean}")
        self._writer.write((clean + "\r\n").encode("utf-8", errors="replace"))
        drain = getattr(self._writer, "drain", None)
        if drain is not None:
            await drain()

    async def _read_line(self, timeout: float) -> Optional[str]:
        """Read one CRLF-terminated line from the server.

        Returns None on clean EOF. Raises asyncio.TimeoutError if no
        data arrives within `timeout`.
        """
        if self._reader is None:
            return None
        raw = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        _log.debug(f"IRC < {line}")
        return line

    async def _expect(
        self,
        *commands: str,
        timeout: Optional[float] = None,
    ) -> IrcMessage:
        """Read until we see a message whose command matches any given.

        Handles PINGs transparently along the way (responds with
        PONG and keeps reading) so callers don't have to. The
        connection-keepalive logic is the same in every state of the
        handshake — keeping it here means each handshake step is two
        lines (`_send`, `_expect`) without per-step PING handling.
        """
        if timeout is None:
            timeout = self.config.handshake_timeout_seconds
        deadline = time.monotonic() + timeout
        wanted = {c.upper() for c in commands}
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"timeout waiting for IRC command(s) {sorted(wanted)}"
                )
            line = await self._read_line(timeout=remaining)
            if line is None:
                raise ConnectionError("connection closed during handshake")
            msg = parse_irc_line(line)
            if msg is None:
                continue
            if msg.command == "PING":
                await self._send(f"PONG :{msg.trailing or (msg.params[0] if msg.params else '')}")
                continue
            if msg.command in wanted:
                return msg

    # ─── Handshake stages ────────────────────────────────────

    async def _send_nick_user(self) -> None:
        await self._send(f"NICK {self.config.nick}")
        await self._send(
            f"USER {self.config.user} 0 * :{self.config.realname}"
        )

    async def _sasl_handshake(self) -> None:
        """IRCv3 SASL PLAIN handshake.

        Order matters: CAP LS → CAP REQ → NICK/USER → AUTHENTICATE
        flow → CAP END. The NICK/USER pair has to be sent BEFORE we
        finish CAP negotiation, or the server will close us with
        "ERROR :Connection registration timed out".
        """
        await self._send("CAP LS 302")
        await self._expect("CAP")  # CAP * LS :sasl ...

        await self._send("CAP REQ :sasl")
        await self._send_nick_user()

        ack = await self._expect("CAP")  # CAP * ACK :sasl
        # ack.trailing should contain "sasl" — if it doesn't, SASL was
        # rejected (or NAK'd) and we should fall back / fail.
        if "sasl" not in ack.trailing.lower():
            raise ConnectionError(
                f"server NAKed SASL: {ack.raw}"
            )

        await self._send("AUTHENTICATE PLAIN")
        await self._expect("AUTHENTICATE")

        # SASL PLAIN payload: base64(authzid \0 authcid \0 password)
        # authzid empty, authcid = account name, password follows.
        auth_string = f"\0{self.config.account}\0{self.config.password}"
        encoded = base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
        await self._send(f"AUTHENTICATE {encoded}")

        # 903 = SASL successful, 904 = failed, 905 = too long, 906 = aborted
        result = await self._expect("903", "904", "905", "906")
        if result.command != "903":
            raise ConnectionError(
                f"SASL authentication failed: {result.command} {result.raw}"
            )

        await self._send("CAP END")

    async def _wait_for_welcome(self) -> None:
        """Wait for the server's 001 numeric (RPL_WELCOME)."""
        await self._expect("001")

    async def _nickserv_identify(self) -> None:
        """Send PRIVMSG NickServ :IDENTIFY <password>.

        Used in the `nickserv` auth mode (fallback). Doesn't wait for
        a response — NickServ replies are NOTICEs, and the read loop
        ignores them. The server is already letting us join channels
        at this point so identification proceeds in parallel.
        """
        await self._send(
            f"PRIVMSG NickServ :IDENTIFY {self.config.password}"
        )

    async def _join_channel(self) -> None:
        await self._send(f"JOIN {self.config.channel}")
        # Look for either the JOIN echo or 366 (RPL_ENDOFNAMES). Some
        # servers send 332/333/353 between, which `_expect` ignores
        # because we're only matching on JOIN/366.
        await self._expect("JOIN", "366")

    # ─── Read loop ───────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Run until the connection drops or stop is signaled.

        Handles PING transparently. Routes PRIVMSGs in our channel
        from the configured announcer nick through `parse_announce`
        and dispatches results to the user callback. Everything else
        is dropped silently.
        """
        while not self._stop.is_set():
            line = await self._read_line(
                timeout=self.config.read_timeout_seconds
            )
            if line is None:
                _log.info("IRC server closed the connection")
                return

            msg = parse_irc_line(line)
            if msg is None:
                continue

            if msg.command == "PING":
                await self._send(
                    f"PONG :{msg.trailing or (msg.params[0] if msg.params else '')}"
                )
                continue

            if msg.command == "PRIVMSG":
                await self._handle_privmsg(msg)
                continue

            if msg.command == "ERROR":
                # Server-initiated disconnect (k-line, server restart,
                # etc). Treat as a connection failure so the run loop
                # cycles through reconnect.
                raise ConnectionError(f"server ERROR: {msg.trailing}")

    async def _handle_privmsg(self, msg: IrcMessage) -> None:
        # PRIVMSG params: [target] [text]
        # Target is the channel; text is the trailing.
        if not msg.params:
            return
        target = msg.params[0]
        if target.lower() != self.config.channel.lower():
            return
        if msg.nick.lower() != self.config.announcer_nick.lower():
            return

        self.announces_seen += 1
        announce = parse_announce(msg.trailing)
        if announce is None:
            _log.debug(f"IRC PRIVMSG didn't parse as announce: {msg.trailing[:80]}")
            return

        try:
            await self.on_announce(announce)
            self.announces_dispatched += 1
        except Exception:
            _log.exception(
                f"on_announce callback raised for tid={announce.torrent_id}"
            )

    # ─── Backoff ─────────────────────────────────────────────

    def _compute_backoff(self, attempt: int) -> float:
        """Exponential backoff capped at max_backoff_seconds.

        attempt 1 → initial; 2 → 2×; 3 → 4×; etc. Capped so a long
        outage doesn't push the next retry an hour into the future.
        """
        delay = self.config.initial_backoff_seconds * (2 ** (attempt - 1))
        return min(delay, self.config.max_backoff_seconds)
