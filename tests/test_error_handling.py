"""Unit tests for error handling — no network required."""

import asyncio

import pytest

from ask_gemini.client import (
    GeminiAuthError,
    GeminiNetworkError,
    ProxyConfig,
    _is_auth_error,
    _is_network_error,
)


class TestIsNetworkError:
    @pytest.mark.parametrize(
        "err_text",
        [
            "curl_cffi.curl.CurlError: Connection reset by peer",
            "Recv failure: Connection reset",
            "ssl.SSLError: certificate verify failed",
            "broken pipe",
            "timed out after 30 seconds",
            "CURL (35) RECV FAILURE",
        ],
    )
    def test_detects_network(self, err_text):
        assert _is_network_error(Exception(err_text)) is True

    @pytest.mark.parametrize(
        "err_text",
        [
            "Gemini cookies not configured",
            "Unknown model: gemini-4-ultra",
            "Session not found",
            "KeyError: 'text_delta'",
        ],
    )
    def test_non_network_errors(self, err_text):
        assert _is_network_error(Exception(err_text)) is False


class TestIsAuthError:
    @pytest.mark.parametrize(
        "err_text",
        [
            "Account status: UNAUTHENTICATED - Session is not authenticated or cookies have expired. Please check your cookies.",
            "RPC request GPRiHf failed: Permission denied or unauthenticated.",
            "gemini-pro is not available for use. Account status: UNAUTHENTICATED",
            "401 Unauthorized",
            "403 Forbidden",
        ],
    )
    def test_detects_auth(self, err_text):
        assert _is_auth_error(Exception(err_text)) is True

    def test_ignores_unrelated(self):
        assert _is_auth_error(Exception("Unknown model: gemini-4-ultra")) is False


class TestGeminiNetworkError:
    def test_user_message_includes_proxy_hint_when_unset(self):
        # Save and restore
        original = ProxyConfig.url
        try:
            ProxyConfig.url = ""
            err = GeminiNetworkError(Exception("connection reset"))
            msg = err.user_message()
            assert "Connection to Gemini failed" in msg
            assert "PROXY_URL is not configured" in msg
            assert "message may be too large" in msg
        finally:
            ProxyConfig.url = original

    def test_user_message_shows_proxy_when_set(self):
        original = ProxyConfig.url
        try:
            ProxyConfig.url = "http://127.0.0.1:7890"
            err = GeminiNetworkError(Exception("connection reset"))
            msg = err.user_message()
            assert "PROXY_URL is set to: http://127.0.0.1:7890" in msg
            assert "proxy is running" in msg
        finally:
            ProxyConfig.url = original

    def test_context_is_included(self):
        err = GeminiNetworkError(
            Exception("ssl error"),
            context="Session cid=abc123 may be out of sync.",
        )
        msg = err.user_message()
        assert "abc123" in msg
        assert "out of sync" in msg


class TestGeminiAuthError:
    def test_user_message_tells_how_to_refresh(self):
        err = GeminiAuthError(
            Exception(
                "Account status: UNAUTHENTICATED - Session is not authenticated "
                "or cookies have expired."
            )
        )
        msg = err.user_message()
        assert "gemini.google.com" in msg
        assert "waits" in msg
        assert "GPRiHf" not in msg
        assert "UNAUTHENTICATED" not in msg


class TestWrapError:
    def test_auth_becomes_gemini_auth_error(self):
        from ask_gemini.client import GeminiClientWrapper

        client = GeminiClientWrapper(rate_limit=False)
        wrapped = client._wrap_error(
            Exception(
                "gemini-pro is not available for use. Account status: "
                "UNAUTHENTICATED - Session is not authenticated or cookies have expired."
            )
        )
        assert isinstance(wrapped, GeminiAuthError)

    def test_network_stays_network(self):
        from ask_gemini.client import GeminiClientWrapper

        client = GeminiClientWrapper(rate_limit=False)
        wrapped = client._wrap_error(Exception("connection reset by peer"))
        assert isinstance(wrapped, GeminiNetworkError)


def test_apply_cookiejar_reads_secure_cookies():
    from types import SimpleNamespace

    from ask_gemini.config import GeminiCookies

    old = (GeminiCookies.PSID, GeminiCookies.PSIDTS)
    try:
        GeminiCookies.PSID = ""
        GeminiCookies.PSIDTS = ""
        cookies = [
            SimpleNamespace(name="__Secure-1PSID", value="abc"),
            SimpleNamespace(name="__Secure-1PSIDTS", value="xyz"),
        ]
        assert GeminiCookies._apply_cookiejar(cookies) is True
        assert GeminiCookies.PSID == "abc"
        assert GeminiCookies.PSIDTS == "xyz"
    finally:
        GeminiCookies.PSID, GeminiCookies.PSIDTS = old


def test_init_raises_auth_error_when_not_waiting(monkeypatch):
    from ask_gemini.client import GeminiClientWrapper
    from ask_gemini.config import GeminiCookies

    async def fail():
        return False

    old = (GeminiCookies.PSID, GeminiCookies.PSIDTS)
    client = GeminiClientWrapper(rate_limit=False)
    monkeypatch.setattr(client, "_try_connect", fail)
    GeminiCookies.PSID = "x"
    GeminiCookies.PSIDTS = "y"
    try:
        with pytest.raises(GeminiAuthError):
            asyncio.run(client.init(wait=False))
    finally:
        GeminiCookies.PSID, GeminiCookies.PSIDTS = old
