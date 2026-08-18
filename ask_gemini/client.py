import asyncio
import json
import random
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from gemini_webapi import GeminiClient
from loguru import logger

from ask_gemini.config import GeminiCookies, ProxyConfig, RateLimitConfig

SESSIONS_FILE = Path.home() / ".ask-gemini" / "sessions.json"

# Patterns that indicate a network-level failure (not an API/logic error)
_NETWORK_ERROR_PATTERNS = [
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"recv failure", re.IGNORECASE),
    re.compile(r"ssl", re.IGNORECASE),
    re.compile(r"broken pipe", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
]

_AUTH_ERROR_PATTERNS = [
    re.compile(r"unauthenticated", re.IGNORECASE),
    re.compile(r"cookies have expired", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"account status", re.IGNORECASE),
    re.compile(r"\b401\b"),
    re.compile(r"\b403\b"),
]


class GeminiNetworkError(Exception):
    """Network-level error with user-friendly guidance."""

    def __init__(self, original_error: Exception, context: str = ""):
        self.original_error = original_error
        self.context = context
        super().__init__(self.user_message())

    def user_message(self) -> str:
        parts = []
        parts.append("Connection to Gemini failed.")
        parts.append("")
        if not ProxyConfig.url:
            parts.append("PROXY_URL is not configured.")
            parts.append(
                "  If you are in a region where Gemini is blocked, add PROXY_URL to your .env file."
            )
        else:
            parts.append(f"PROXY_URL is set to: {ProxyConfig.url}")
            parts.append("  Verify that the proxy is running and accessible.")
        parts.append("")
        parts.append("Other possible causes:")
        parts.append("  - The message may be too large (try splitting it)")
        parts.append("  - Cookies may have expired (refresh Gemini in your browser)")
        if self.context:
            parts.append("")
            parts.append(self.context)
        return "\n".join(parts)


class GeminiAuthError(Exception):
    """Expired or rejected Gemini web session."""

    def __init__(self, original_error: Exception | None = None):
        self.original_error = original_error
        super().__init__(self.user_message())

    def user_message(self) -> str:
        return (
            "Gemini session expired or is not signed in.\n"
            "\n"
            "Open https://gemini.google.com in your browser and sign in.\n"
            "On a terminal, ask-gemini waits for that session automatically."
        )


def _is_network_error(exc: Exception) -> bool:
    err_str = str(exc)
    return any(p.search(err_str) for p in _NETWORK_ERROR_PATTERNS)


def _is_auth_error(exc: Exception) -> bool:
    err_str = str(exc)
    return any(p.search(err_str) for p in _AUTH_ERROR_PATTERNS)


class _ChatSession:
    """Wraps a gemini-webapi ChatSession with model tracking."""

    def __init__(self, session, model: str):
        self.session = session
        self.model = model


def _load_sessions() -> dict[str, str]:
    """Load named sessions from disk. Returns {name: cid}."""
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_sessions(sessions: dict[str, str]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2) + "\n")


class GeminiClientWrapper:
    def __init__(self, rate_limit: bool = True):
        self._client: GeminiClient | None = None
        self._chat: _ChatSession | None = None
        self._rate_limit = rate_limit

    @staticmethod
    def _typing_delay(message: str) -> float:
        """Calculate a human-like delay based on message length."""
        if not RateLimitConfig.enabled:
            return 0
        delay = len(message) * RateLimitConfig.typing_speed
        delay = max(RateLimitConfig.min_delay, min(delay, RateLimitConfig.max_delay))
        # Add +/- 20% jitter so delays aren't perfectly uniform
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return round(delay + jitter, 2)

    async def _wait_before_send(self, message: str) -> None:
        delay = self._typing_delay(message)
        if delay > 0:
            logger.debug(
                f"Rate limit: waiting {delay}s before sending ({len(message)} chars)"
            )
            await asyncio.sleep(delay)

    async def _wait_after_receive(self) -> None:
        if not RateLimitConfig.enabled:
            return
        if RateLimitConfig.cooldown > 0:
            await asyncio.sleep(RateLimitConfig.cooldown)

    async def init(self, *, wait: bool | None = None) -> None:
        if wait is None:
            wait = sys.stderr.isatty()
        if await self._try_connect():
            return
        if wait:
            from ask_gemini.display import wait_for_signin

            await wait_for_signin(self._try_connect)
            return
        if not GeminiCookies.is_configured():
            raise RuntimeError(
                "Gemini cookies not configured. Run `ask-gemini --cookie-setup` for instructions."
            )
        raise GeminiAuthError()

    async def _try_connect(self) -> bool:
        """Load cookies and open a Gemini session. Returns True when signed in."""
        GeminiCookies.try_load_from_browser()
        if not GeminiCookies.is_configured():
            return False
        try:
            self._client = GeminiClient(
                secure_1psid=GeminiCookies.PSID,
                secure_1psidts=GeminiCookies.PSIDTS,
                proxy=ProxyConfig.url or None,
            )
            await self._client.init(timeout=30, auto_close=False, auto_refresh=True)
        except Exception as e:
            logger.debug(f"Gemini connect failed: {e}")
            return False
        if self._account_available():
            logger.debug("Gemini client initialized")
            return True
        return False

    async def start_chat(self, model: str) -> None:
        """Start a fresh chat session."""
        chat = self._client.start_chat(model=model)
        self._chat = _ChatSession(chat, model)
        logger.debug(f"New chat session started with model={model}")

    async def resume_chat(self, cid: str, model: str) -> None:
        """Resume a specific web-side conversation by its cid.

        Fetches history from the server to recover rid/rcid, which are
        required for the server to correctly continue the conversation.
        Without rid/rcid, only cid is sent and the server may treat the
        next message as a new conversation.
        """
        chat = self._client.start_chat(model=model, cid=cid)
        # Fetch history to recover rid/rcid
        history = await chat.read_history(limit=5)
        if history and history.turns:
            for turn in history.turns:  # newest-first
                if turn.role == "model" and turn.model_output:
                    if turn.model_output.metadata:
                        chat.metadata = turn.model_output.metadata
                    if turn.model_output.rcid:
                        chat.rcid = turn.model_output.rcid
                    break
            logger.debug(f"Recovered rid/rcid for cid={cid}")
        else:
            logger.debug(f"No history turns for cid={cid}, resuming with cid only")
        self._chat = _ChatSession(chat, model)
        logger.info(f"Resumed web chat cid={cid}")

    async def resume_latest_chat(self, model: str) -> bool:
        """Resume the most recent web-side conversation. Returns False if no existing chat found."""
        recent = self._client.list_chats()
        if not recent or len(recent) == 0:
            logger.debug("No existing web chats found, starting fresh")
            await self.start_chat(model)
            return False

        latest = recent[0]
        chat = self._client.start_chat(model=model, cid=latest.cid)
        # Fetch history to recover rid/rcid
        history = await chat.read_history(limit=5)
        if history and history.turns:
            for turn in history.turns:  # newest-first
                if turn.role == "model" and turn.model_output:
                    if turn.model_output.metadata:
                        chat.metadata = turn.model_output.metadata
                    if turn.model_output.rcid:
                        chat.rcid = turn.model_output.rcid
                    break
            logger.debug(f"Recovered rid/rcid for cid={latest.cid}")
        self._chat = _ChatSession(chat, model)
        logger.info(f"Resumed web chat '{latest.title}' (cid={latest.cid})")
        return True

    async def resume_named_session(self, name: str, model: str) -> bool:
        """Resume a named session. Creates a new one if not found.

        Returns True if resumed existing, False if created new.
        """
        sessions = _load_sessions()
        if name in sessions:
            cid = sessions[name]
            await self.resume_chat(cid, model)
            logger.info(f"Resumed named session '{name}' (cid={cid})")
            return True
        else:
            await self.start_chat(model)
            sessions[name] = self._chat.session.cid
            _save_sessions(sessions)
            logger.info(
                f"Created new named session '{name}' (cid={self._chat.session.cid})"
            )
            return False

    def has_chat(self) -> bool:
        return self._chat is not None

    def _account_available(self) -> bool:
        if self._client is None:
            return False
        status = getattr(self._client, "account_status", None)
        if status is None:
            return True
        return getattr(status, "name", str(status)) == "AVAILABLE"

    def _wrap_error(self, exc: Exception) -> Exception:
        """Turn library exceptions into user-facing errors."""
        if _is_auth_error(exc):
            return GeminiAuthError(exc)
        if _is_network_error(exc):
            context = ""
            if self._chat and self._chat.session.cid:
                context = (
                    f"Session cid={self._chat.session.cid} may be out of sync. "
                    "If retry fails, try starting fresh."
                )
            return GeminiNetworkError(exc, context)
        raise exc

    async def chat(self, message: str, model: str) -> str:
        """Send a message in the active chat session."""
        if not self._chat:
            await self.start_chat(model)
        elif self._chat.model != model:
            await self.start_chat(model)

        await self._wait_before_send(message)
        try:
            resp = await self._chat.session.send_message(message)
        except Exception as e:
            if await self._refresh_and_retry(e):
                try:
                    resp = await self._chat.session.send_message(message)
                except Exception as e2:
                    raise self._wrap_error(e2)
            else:
                raise self._wrap_error(e)
        # Save cid after first message so the session is registered on the web
        self._save_named_cid()
        await self._wait_after_receive()
        return resp.text

    async def chat_stream(self, message: str, model: str) -> AsyncIterator[str]:
        """Send a message with streaming in the active chat session."""
        if not self._chat:
            await self.start_chat(model)
        elif self._chat.model != model:
            await self.start_chat(model)

        await self._wait_before_send(message)
        try:
            session = self._chat.session
            first = True
            async for chunk in session.send_message_stream(message):
                if chunk.text_delta:
                    if first:
                        # Save cid after first chunk
                        self._save_named_cid()
                        first = False
                    yield chunk.text_delta
        except Exception as e:
            if await self._refresh_and_retry(e):
                try:
                    session = self._chat.session
                    first = True
                    async for chunk in session.send_message_stream(message):
                        if chunk.text_delta:
                            if first:
                                self._save_named_cid()
                                first = False
                            yield chunk.text_delta
                except Exception as e2:
                    raise self._wrap_error(e2)
            else:
                raise self._wrap_error(e)
        await self._wait_after_receive()

    def _save_named_cid(self) -> None:
        """Update the cid of the active chat in named sessions (if any)."""
        if not self._chat or not self._chat.session.cid:
            return
        sessions = _load_sessions()
        # Find and update matching cid entry
        for name, cid in sessions.items():
            if cid == self._chat.session.cid:
                return  # Already saved
        # If this cid isn't tracked yet, it means the session was started
        # without a name — nothing to save

    async def _refresh_and_retry(self, error: Exception) -> bool:
        """On auth error, reload cookies, or wait for a browser sign-in on a TTY."""
        if not _is_auth_error(error):
            return False
        logger.debug(f"Auth error detected, refreshing cookies: {error}")
        if await self._try_connect():
            await self._rebind_chat_after_refresh()
            return True
        if not sys.stderr.isatty():
            return False
        from ask_gemini.display import wait_for_signin

        await wait_for_signin(self._try_connect)
        await self._rebind_chat_after_refresh()
        return self._account_available()

    async def _rebind_chat_after_refresh(self) -> None:
        """Rebuild the chat session on the new client after a cookie refresh."""
        if not self._chat:
            return
        model = self._chat.model
        cid = getattr(self._chat.session, "cid", None)
        if cid:
            try:
                await self.resume_chat(cid, model)
                return
            except Exception:
                logger.debug(
                    "Could not resume chat after cookie refresh; starting fresh"
                )
        await self.start_chat(model)

    async def delete_chat(self, cid: str) -> None:
        """Delete a conversation from Gemini web (both local and server-side)."""
        if not self._client:
            raise RuntimeError("GeminiProvider not initialized")
        await self._client.delete_chat(cid)
        logger.info(f"Deleted web chat cid={cid}")

    async def ask(self, message: str, model: str) -> str:
        await self._wait_before_send(message)
        try:
            resp = await self._client.generate_content(message, model=model)
            await self._wait_after_receive()
            return resp.text
        except Exception as e:
            if await self._refresh_and_retry(e):
                try:
                    resp = await self._client.generate_content(message, model=model)
                    await self._wait_after_receive()
                    return resp.text
                except Exception as e2:
                    raise self._wrap_error(e2)
            raise self._wrap_error(e)

    async def ask_stream(self, message: str, model: str) -> AsyncIterator[str]:
        await self._wait_before_send(message)
        try:
            async for chunk in self._client.generate_content_stream(
                message, model=model
            ):
                if chunk.text_delta:
                    yield chunk.text_delta
            await self._wait_after_receive()
        except Exception as e:
            if await self._refresh_and_retry(e):
                try:
                    async for chunk in self._client.generate_content_stream(
                        message, model=model
                    ):
                        if chunk.text_delta:
                            yield chunk.text_delta
                    await self._wait_after_receive()
                except Exception as e2:
                    raise self._wrap_error(e2)
            else:
                raise self._wrap_error(e)
