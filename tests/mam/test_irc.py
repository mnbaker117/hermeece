"""
Unit tests for the MAM IRC client.

Layered:

  1. **Pure parser tests** — `parse_irc_line` against canonical
     IRC protocol shapes. No async, no fixtures.
  2. **Backoff math** — exponential growth, max cap, attempt counter.
  3. **SASL handshake** — drive the full CAP/AUTHENTICATE flow
     against the fake IRC server, assert each protocol step.
  4. **Read loop** — PING/PONG, PRIVMSG dispatch (real announce
     fixture lines), wrong-channel/wrong-nick filtering, ERROR
     causing reconnect.
  5. **Reconnect** — server EOF triggers a fresh connection cycle,
     and the manual-stop guard exits cleanly mid-backoff.

The fake IRC server lives in `tests/fake_irc.py`. The handshake
helper `drive_sasl_handshake` skips boilerplate for tests that just
want a connected, joined client as their starting state.
"""
import asyncio
import re

from app.filter.gate import Announce
from app.mam.irc import IrcClient, IrcConfig, parse_irc_line
from tests.fake_irc import drive_sasl_handshake


# ─── Helpers ─────────────────────────────────────────────────


def _make_config(**overrides) -> IrcConfig:
    base = {
        "server": "irc.fake",
        "port": 6697,
        "tls": False,
        "nick": "testbot",
        "user": "hermeece",
        "realname": "test",
        "account": "testacct",
        "password": "testpass",
        "auth_mode": "sasl",
        "channel": "#announce",
        "announcer_nick": "MouseBot",
        # Tight timeouts so a broken test fails fast instead of
        # hanging the suite.
        "initial_backoff_seconds": 0.05,
        "max_backoff_seconds": 0.2,
        "max_reconnect_attempts": 3,
        "read_timeout_seconds": 5.0,
        "handshake_timeout_seconds": 2.0,
    }
    base.update(overrides)
    return IrcConfig(**base)


class _Collector:
    """Async-callable that records every dispatched announce."""

    def __init__(self):
        self.announces: list[Announce] = []

    async def __call__(self, announce: Announce) -> None:
        self.announces.append(announce)


# ─── parse_irc_line ──────────────────────────────────────────


class TestParseIrcLine:
    def test_simple_command(self):
        msg = parse_irc_line("PING :token123")
        assert msg is not None
        assert msg.command == "PING"
        assert msg.trailing == "token123"
        assert msg.prefix == ""

    def test_prefix_with_nick(self):
        msg = parse_irc_line(":MouseBot!~bot@host PRIVMSG #announce :hello world")
        assert msg is not None
        assert msg.command == "PRIVMSG"
        assert msg.nick == "MouseBot"
        assert msg.prefix == "MouseBot!~bot@host"
        assert msg.params == ["#announce"]
        assert msg.trailing == "hello world"

    def test_numeric_command(self):
        msg = parse_irc_line(":server 001 testbot :Welcome")
        assert msg is not None
        assert msg.command == "001"
        assert msg.params == ["testbot"]
        assert msg.trailing == "Welcome"

    def test_no_trailing(self):
        msg = parse_irc_line(":server JOIN #announce")
        assert msg is not None
        assert msg.command == "JOIN"
        assert msg.params == ["#announce"]
        assert msg.trailing == ""

    def test_multiple_params_with_trailing(self):
        msg = parse_irc_line(":server CAP * ACK :sasl")
        assert msg is not None
        assert msg.command == "CAP"
        assert msg.params == ["*", "ACK"]
        assert msg.trailing == "sasl"

    def test_empty_returns_none(self):
        assert parse_irc_line("") is None

    def test_command_uppercased(self):
        # Lowercase command in input should be normalized.
        msg = parse_irc_line("ping :x")
        assert msg is not None
        assert msg.command == "PING"


# ─── Backoff math ────────────────────────────────────────────


class TestBackoff:
    def test_first_attempt_uses_initial(self):
        config = _make_config(initial_backoff_seconds=10, max_backoff_seconds=600)
        client = IrcClient(config, _Collector())
        assert client._compute_backoff(1) == 10

    def test_doubles_each_attempt(self):
        config = _make_config(initial_backoff_seconds=5, max_backoff_seconds=10000)
        client = IrcClient(config, _Collector())
        assert client._compute_backoff(1) == 5
        assert client._compute_backoff(2) == 10
        assert client._compute_backoff(3) == 20
        assert client._compute_backoff(4) == 40

    def test_caps_at_max(self):
        config = _make_config(initial_backoff_seconds=5, max_backoff_seconds=30)
        client = IrcClient(config, _Collector())
        assert client._compute_backoff(10) == 30  # would be 2560 uncapped
        assert client._compute_backoff(20) == 30


# ─── Connection lifecycle (SASL handshake + JOIN) ────────────


class TestSaslHandshake:
    async def test_full_sasl_to_join(self, fake_irc):
        config = _make_config()
        collector = _Collector()
        client = IrcClient(config, collector, connect_fn=fake_irc.connect_fn)

        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            # At this point the client should be in the read loop.
            # Give it a tick to settle into the read loop.
            await asyncio.sleep(0.01)
            assert client.connected is True
            assert client.authenticated is True
            assert client.joined is True
        finally:
            await client.stop()
            await asyncio.wait_for(task, timeout=1.0)

    async def test_nick_user_sent_after_cap_req(self, fake_irc):
        config = _make_config()
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await fake_irc.wait_for_line("CAP LS 302")
            fake_irc.feed_line(":server CAP * LS :sasl")
            await fake_irc.wait_for_line("CAP REQ :sasl")
            # NICK and USER must be sent after CAP REQ but before
            # CAP END (i.e. somewhere during the SASL flow).
            await fake_irc.wait_for_line("NICK testbot")
            await fake_irc.wait_for_line(re.compile(r"^USER hermeece "))
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_sasl_payload_is_base64_plain(self, fake_irc):
        config = _make_config(account="myacct", password="mypass")
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await fake_irc.wait_for_line("CAP LS 302")
            fake_irc.feed_line(":server CAP * LS :sasl")
            await fake_irc.wait_for_line("CAP REQ :sasl")
            fake_irc.feed_line(":server CAP * ACK :sasl")
            await fake_irc.wait_for_line("AUTHENTICATE PLAIN")
            fake_irc.clear_writes()
            fake_irc.feed_line("AUTHENTICATE +")

            payload_line = await fake_irc.wait_for_line(
                re.compile(r"^AUTHENTICATE [A-Za-z0-9+/=]+$")
            )
            # Decode and verify it's base64(\0account\0password).
            import base64

            encoded = payload_line.split(" ", 1)[1]
            decoded = base64.b64decode(encoded).decode("utf-8")
            assert decoded == "\0myacct\0mypass"
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_sasl_904_failure_triggers_reconnect_loop(self, fake_irc):
        # SASL 904 = "SASL authentication failed". The client should
        # raise out of _run_once, the run_forever loop should record
        # the error, attempt one reconnect (which will hit the
        # handshake timeout because we don't feed the second cycle),
        # and then bail out completely.
        #
        # Tight handshake timeout so the second-cycle wait fires
        # quickly and the whole test stays under a second.
        config = _make_config(
            max_reconnect_attempts=1, handshake_timeout_seconds=0.3
        )
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await fake_irc.wait_for_line("CAP LS 302")
            fake_irc.feed_line(":server CAP * LS :sasl")
            await fake_irc.wait_for_line("CAP REQ :sasl")
            fake_irc.feed_line(":server CAP * ACK :sasl")
            await fake_irc.wait_for_line("AUTHENTICATE PLAIN")
            fake_irc.clear_writes()
            fake_irc.feed_line("AUTHENTICATE +")
            await fake_irc.wait_for_line(re.compile(r"^AUTHENTICATE [A-Za-z0-9+/=]+$"))
            fake_irc.feed_line(":server 904 testbot :SASL authentication failed")

            # The run loop should bail out within max_reconnect_attempts.
            # First cycle fails immediately on 904. Second cycle tries to
            # handshake but we don't feed any responses, so it hits the
            # 0.3s handshake timeout and bails. Total budget: ~1s.
            await asyncio.wait_for(task, timeout=3.0)
            assert (
                "904" in client.last_error
                or "sasl" in client.last_error.lower()
                or "timeout" in client.last_error.lower()
            )
        finally:
            await client.stop()


# ─── Read loop: PING, PRIVMSG dispatch, ERROR, EOF ───────────


class TestReadLoop:
    async def test_ping_pong(self, fake_irc):
        config = _make_config()
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            fake_irc.clear_writes()
            fake_irc.feed_line("PING :keepalive123")
            pong = await fake_irc.wait_for_line("PONG :keepalive123")
            assert "keepalive123" in pong
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_real_announce_dispatched(self, fake_irc):
        # Use a real fixture announce line and verify the parsed
        # Announce makes it to the callback.
        config = _make_config()
        collector = _Collector()
        client = IrcClient(config, collector, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            fake_irc.feed_line(
                ":MouseBot!~bot@host PRIVMSG #announce :"
                "New Torrent: The Demon King By: Peter V Brett "
                "Category: ( Audiobooks - Fantasy ) Size: ( 921.91 MiB ) "
                "Filetype: ( m4b ) Language: ( English ) "
                "Link: ( https://www.myanonamouse.net/t/1233592 ) VIP"
            )

            # Wait for the dispatch.
            for _ in range(50):
                if collector.announces:
                    break
                await asyncio.sleep(0.01)

            assert len(collector.announces) == 1
            announce = collector.announces[0]
            assert announce.torrent_id == "1233592"
            assert announce.author_blob == "Peter V Brett"
            assert client.announces_seen == 1
            assert client.announces_dispatched == 1
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_privmsg_from_wrong_nick_ignored(self, fake_irc):
        config = _make_config()
        collector = _Collector()
        client = IrcClient(config, collector, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            fake_irc.feed_line(
                ":SomeOtherUser!~x@y PRIVMSG #announce :"
                "New Torrent: Foo By: Bar Category: ( Ebooks - Fantasy ) "
                "Size: ( 1 MB ) Filetype: ( epub ) Language: ( English ) "
                "Link: ( https://www.myanonamouse.net/t/999999 )"
            )
            await asyncio.sleep(0.05)
            assert collector.announces == []
            assert client.announces_seen == 0
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_privmsg_to_wrong_channel_ignored(self, fake_irc):
        config = _make_config()
        collector = _Collector()
        client = IrcClient(config, collector, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            fake_irc.feed_line(
                ":MouseBot!~bot@host PRIVMSG #other-channel :"
                "New Torrent: Foo By: Bar Category: ( Ebooks - Fantasy ) "
                "Size: ( 1 MB ) Filetype: ( epub ) Language: ( English ) "
                "Link: ( https://www.myanonamouse.net/t/999999 )"
            )
            await asyncio.sleep(0.05)
            assert collector.announces == []
            assert client.announces_seen == 0
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_unparseable_privmsg_increments_seen_but_not_dispatched(
        self, fake_irc
    ):
        config = _make_config()
        collector = _Collector()
        client = IrcClient(config, collector, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            # Right channel, right nick, but the message body isn't a
            # MAM-format announce. Should be counted as seen but not
            # dispatched.
            fake_irc.feed_line(
                ":MouseBot!~bot@host PRIVMSG #announce :Server back online"
            )
            await asyncio.sleep(0.05)

            assert collector.announces == []
            assert client.announces_seen == 1
            assert client.announces_dispatched == 0
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_callback_exception_doesnt_kill_loop(self, fake_irc):
        # If the on_announce callback raises, the read loop must
        # keep going — one bad announce shouldn't take down the
        # IRC listener for the rest of the process lifetime.
        async def bad_callback(announce):
            raise RuntimeError("simulated downstream failure")

        config = _make_config()
        client = IrcClient(config, bad_callback, connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)

            fake_irc.feed_line(
                ":MouseBot!~bot@host PRIVMSG #announce :"
                "New Torrent: A By: B Category: ( Ebooks - Fantasy ) "
                "Size: ( 1 MB ) Filetype: ( epub ) Language: ( English ) "
                "Link: ( https://www.myanonamouse.net/t/1 )"
            )
            # Now feed a PING — if the loop is still alive, we'll
            # see a PONG response.
            await asyncio.sleep(0.05)
            fake_irc.feed_line("PING :stillalive")
            await fake_irc.wait_for_line("PONG :stillalive")
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


# ─── Reconnect + manual stop ─────────────────────────────────


class TestReconnect:
    async def test_server_eof_triggers_new_connection_cycle(self, fake_irc):
        config = _make_config(initial_backoff_seconds=0.01)
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())
        try:
            await drive_sasl_handshake(fake_irc, nick=config.nick)
            assert fake_irc.connect_count == 1

            # Simulate server disconnecting us. Clear writes BEFORE
            # the EOF so the next wait_for_line only sees output from
            # the new connection cycle (the buffer still has CAP LS
            # 302 from the first cycle, which would otherwise short-
            # circuit the wait below).
            fake_irc.clear_writes()
            fake_irc.eof()

            # The client should reconnect and start a new SASL
            # handshake on the second connection.
            await fake_irc.wait_for_line("CAP LS 302", timeout=2.0)
            assert fake_irc.connect_count == 2
        finally:
            await client.stop()
            try:
                await asyncio.wait_for(task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def test_manual_stop_during_backoff_exits_cleanly(self, fake_irc):
        # The Autobrr issue #1239 case: stop is signaled while we're
        # waiting in the reconnect backoff. The loop must NOT attempt
        # another connection — it must exit cleanly.
        config = _make_config(
            initial_backoff_seconds=10.0,  # long enough to definitely interrupt
            max_reconnect_attempts=99,
        )
        client = IrcClient(config, _Collector(), connect_fn=fake_irc.connect_fn)
        task = asyncio.create_task(client.run_forever())

        await drive_sasl_handshake(fake_irc, nick=config.nick)
        assert fake_irc.connect_count == 1

        fake_irc.eof()
        # Now the client is in the backoff sleep waiting 10s. Signal
        # stop and verify the task exits in well under that.
        await asyncio.sleep(0.05)  # let the loop hit the backoff wait
        await client.stop()

        await asyncio.wait_for(task, timeout=1.0)
        # Critically: connect_count should still be 1 — the manual
        # stop must have prevented a second connection attempt.
        assert fake_irc.connect_count == 1

    async def test_max_reconnect_attempts_caps_the_loop(self):
        # If reconnect keeps failing, the loop should give up after
        # max_reconnect_attempts back-to-back failures rather than
        # spinning forever. No fake_irc needed — we use a connect_fn
        # that always raises before any IRC traffic happens.
        attempts = {"n": 0}

        async def failing_connect():
            attempts["n"] += 1
            raise ConnectionRefusedError("simulated")

        config = _make_config(
            initial_backoff_seconds=0.01,
            max_backoff_seconds=0.02,
            max_reconnect_attempts=3,
        )
        client = IrcClient(config, _Collector(), connect_fn=failing_connect)
        task = asyncio.create_task(client.run_forever())
        await asyncio.wait_for(task, timeout=2.0)

        # Should have tried initial + max_reconnect_attempts more.
        # The first call counts as attempt 0; failures 1..3 trigger
        # backoffs and retries 2..4 — so total connect calls is at
        # most max_reconnect_attempts + 1.
        assert 1 <= attempts["n"] <= config.max_reconnect_attempts + 1
        assert "ConnectionRefusedError" in client.last_error
