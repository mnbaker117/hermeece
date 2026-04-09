"""
Unit tests for the MAM cookie module.

Three layers:

  1. **Pure helpers** — `build_headers` shape, validate's no-token
     short-circuit. No fixture needed.
  2. **Fake-MAM driven** — `register_ip`, `verify_session`, and the
     full `validate` flow against the in-memory `httpx.MockTransport`
     fixture. Real MAM is never contacted.
  3. **Cookie capture** — assertions that the production code is
     attaching the right `mam_id` cookie to outgoing requests.
"""
from app.mam.cookie import (
    build_headers,
    register_ip,
    validate,
    verify_session,
)
from tests.fake_mam import HTML_LOGIN_PAGE


# ─── Pure helper tests (no fixture needed) ───────────────────


class TestBuildHeaders:
    def test_includes_cookie_with_mam_id_prefix(self):
        headers = build_headers("abc123")
        assert headers["Cookie"] == "mam_id=abc123"

    def test_user_agent_is_curl_8(self):
        # The curl/8.0 UA is load-bearing — MAM has been observed to
        # subtly reject other UAs. AthenaScout's mam.py docs the same
        # constraint. Pin the value down so a "tidy up the headers"
        # refactor can't quietly break production.
        headers = build_headers("abc123")
        assert headers["User-Agent"] == "curl/8.0"

    def test_content_type_is_json(self):
        headers = build_headers("abc123")
        assert headers["Content-Type"] == "application/json"

    def test_token_substituted_verbatim(self):
        # No URL-encoding, no quoting, no transformation — the cookie
        # is whatever MAM emitted on the security page.
        headers = build_headers("a:b/c+d=e")
        assert headers["Cookie"] == "mam_id=a:b/c+d=e"


class TestValidateNoToken:
    async def test_empty_token_returns_clear_failure_without_network(self):
        # The validate() function MUST short-circuit on empty token
        # rather than firing an HTTP request.
        result = await validate("")
        assert result["success"] is False
        assert "no mam session" in result["message"].lower()
        assert result["ip_result"] is None
        assert result["search_result"] is None


# ─── verify_session against fake MAM ─────────────────────────


class TestVerifySession:
    async def test_success(self, fake_mam):
        # Default fake response: HTTP 200 with a non-empty JSON body.
        result = await verify_session("good_token")
        assert result["success"] is True
        assert "successful" in result["message"].lower()

    async def test_empty_200_treated_as_invalid(self, fake_mam):
        # MAM returns HTTP 200 with an empty body when the cookie is
        # invalid — exactly the gotcha that motivated the cookie module
        # documentation. Hermeece must catch this.
        fake_mam.search.body = b""
        result = await verify_session("expired_token")
        assert result["success"] is False
        assert "empty" in result["message"].lower() or "invalid" in result["message"].lower()

    async def test_403_treated_as_rejected(self, fake_mam):
        fake_mam.search.status = 403
        result = await verify_session("bad_token")
        assert result["success"] is False
        assert "403" in result["message"] or "rejected" in result["message"].lower()

    async def test_unexpected_status(self, fake_mam):
        fake_mam.search.status = 502
        result = await verify_session("any_token")
        assert result["success"] is False
        assert "502" in result["message"]

    async def test_attaches_cookie_to_request(self, fake_mam):
        await verify_session("my_session_value")
        assert "my_session_value" in fake_mam.cookies_seen()


# ─── register_ip against fake MAM ────────────────────────────


class TestRegisterIp:
    async def test_skip_short_circuits_without_network(self, fake_mam):
        result = await register_ip("any_token", skip_ip_update=True)
        assert result["success"] is True
        assert "asn-locked" in result["message"].lower() or "skipped" in result["message"].lower()
        # Confirm no HTTP request was made
        assert len(fake_mam.requests) == 0

    async def test_success_returns_ip_and_asn(self, fake_mam):
        # Default fake response is the happy-path JSON.
        result = await register_ip("good_token", skip_ip_update=False)
        assert result["success"] is True
        assert result["ip"] == "192.0.2.1"
        assert result["asn"] == 64500

    async def test_html_response_means_token_expired(self, fake_mam):
        fake_mam.dynip.body = HTML_LOGIN_PAGE
        fake_mam.dynip.headers = {"content-type": "text/html"}
        result = await register_ip("expired_token", skip_ip_update=False)
        assert result["success"] is False
        assert "html" in result["message"].lower() or "expired" in result["message"].lower()

    async def test_no_session_cookie_msg(self, fake_mam):
        fake_mam.dynip.body = b'{"Success":false,"msg":"No Session Cookie"}'
        result = await register_ip("bad_token", skip_ip_update=False)
        assert result["success"] is False
        assert "not recognized" in result["message"].lower() or "not recognised" in result["message"].lower()

    async def test_ip_mismatch_msg(self, fake_mam):
        fake_mam.dynip.body = b'{"Success":false,"msg":"Invalid session - IP mismatch"}'
        result = await register_ip("bad_token", skip_ip_update=False)
        assert result["success"] is False
        assert "different network" in result["message"].lower()

    async def test_too_recent_msg(self, fake_mam):
        fake_mam.dynip.body = b'{"Success":false,"msg":"Last Change too recent"}'
        result = await register_ip("good_token", skip_ip_update=False)
        assert result["success"] is False
        assert "rate-limited" in result["message"].lower()

    async def test_asn_locked_session_msg_treated_as_success(self, fake_mam):
        # The "incorrect session type" branch — an ASN-locked session
        # called with skip_ip_update=False. Hermeece treats this as
        # success since the cookie is fine, just doesn't need IP register.
        fake_mam.dynip.body = b'{"Success":false,"msg":"Incorrect session type for this endpoint"}'
        result = await register_ip("good_token", skip_ip_update=False)
        assert result["success"] is True


# ─── Full validate() flow ────────────────────────────────────


class TestValidate:
    async def test_full_happy_path(self, fake_mam):
        result = await validate("good_token", skip_ip_update=True)
        assert result["success"] is True
        assert result["ip_result"] is not None
        assert result["search_result"] is not None
        assert result["search_result"]["success"] is True

    async def test_full_happy_path_with_ip_register(self, fake_mam):
        result = await validate("good_token", skip_ip_update=False)
        assert result["success"] is True
        assert result["ip_result"]["success"] is True
        assert result["search_result"]["success"] is True

    async def test_search_failure_propagates(self, fake_mam):
        fake_mam.simulate_cookie_rejected_403()
        result = await validate("bad_token", skip_ip_update=True)
        assert result["success"] is False
        assert result["search_result"] is not None
        assert result["search_result"]["success"] is False

    async def test_ip_register_failure_short_circuits_search(self, fake_mam):
        # If IP register fails, validate must NOT proceed to search —
        # there's no point and we want a clear "this step failed" UI.
        fake_mam.dynip.body = b'{"Success":false,"msg":"Invalid session - IP mismatch"}'
        result = await validate("bad_token", skip_ip_update=False)
        assert result["success"] is False
        assert result["ip_result"] is not None
        assert result["ip_result"]["success"] is False
        assert result["search_result"] is None  # never called
        # And the fake MAM should never have seen a search request
        assert not any(
            "loadSearchJSONbasic.php" in str(req.url)
            for req in fake_mam.requests
        )

    async def test_empty_token_short_circuits_without_network(self, fake_mam):
        result = await validate("", skip_ip_update=True)
        assert result["success"] is False
        assert len(fake_mam.requests) == 0
