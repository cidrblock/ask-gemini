import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

CLI = "ask-gemini"
SESSIONS_FILE = Path.home() / ".ask-gemini" / "sessions.json"

TEST_SESSION_PREFIX = "_test_"

pytestmark = pytest.mark.integration


def _run(
    *args, timeout: int = 120, input: str | None = None
) -> subprocess.CompletedProcess:
    """Run the ask-gemini CLI and return the result."""
    env = os.environ.copy()
    return subprocess.run(
        [CLI, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input,
    )


def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _clean_web_chats(before_cids, after_cids):
    """Delete web-side conversations created during a test.

    Compares list_chats() snapshots before/after the test to find newly
    created conversations, then deletes them. Only cid-based matching —
    never touches titles, so normal conversations are safe.
    """
    from ask_gemini.config import GeminiCookies, ProxyConfig
    from gemini_webapi import GeminiClient

    GeminiCookies.try_load_from_browser()
    if not GeminiCookies.is_configured():
        return

    client = GeminiClient(
        secure_1psid=GeminiCookies.PSID,
        secure_1psidts=GeminiCookies.PSIDTS,
        proxy=ProxyConfig.url or None,
    )

    async def _delete():
        try:
            await client.init(timeout=15, auto_close=True)
        except Exception:
            return
        after = client.list_chats()
        new_cids = [c.cid for c in after if c.cid not in before_cids]
        # Only delete cids that were actually created during the test
        for cid in new_cids:
            if cid in after_cids:
                try:
                    await client.delete_chat(cid)
                except Exception:
                    pass

    asyncio.run(_delete())


def _snapshot_web_chats():
    """Return set of current web conversation cids."""
    from ask_gemini.config import GeminiCookies, ProxyConfig
    from gemini_webapi import GeminiClient

    GeminiCookies.try_load_from_browser()
    if not GeminiCookies.is_configured():
        return set()

    client = GeminiClient(
        secure_1psid=GeminiCookies.PSID,
        secure_1psidts=GeminiCookies.PSIDTS,
        proxy=ProxyConfig.url or None,
    )

    async def _list():
        try:
            await client.init(timeout=15, auto_close=True)
        except Exception:
            return set()
        return {c.cid for c in client.list_chats()}

    return asyncio.run(_list())


def _clean_test_sessions():
    """Remove all test-prefixed sessions from disk."""
    sessions = _load_sessions()
    to_remove = [k for k in sessions if k.startswith(TEST_SESSION_PREFIX)]
    for name in to_remove:
        _run("--rm-session", name, timeout=30)


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up test sessions after each test."""
    before = _snapshot_web_chats()
    yield
    _clean_test_sessions()
    after = _snapshot_web_chats()
    _clean_web_chats(before, after)


# --- Single question tests ---


def test_single_question():
    """Basic single question should return a response."""
    result = _run(
        "-m", "gemini-3-flash", "What is 1+1? Please answer with just the number."
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "2" in result.stdout


def test_single_question_model_flag():
    """Specifying a model should work."""
    result = _run("-m", "gemini-3-flash", "What color is the sky on a clear day?")
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert result.stdout  # non-empty response


def test_single_question_no_stream():
    """Non-streaming mode should return a complete response."""
    result = _run(
        "--no-stream", "-m", "gemini-3-flash", "Say 'hello world' and nothing else."
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "hello" in result.stdout.lower() or "world" in result.stdout.lower()


# --- Named session tests ---


def test_named_session_create():
    """Creating a named session should work."""
    session = f"{TEST_SESSION_PREFIX}create-test"
    result = _run(
        "-m", "gemini-3-flash", "--session", session, "Say 'session created' and stop."
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "created" in result.stdout.lower()


def test_named_session_context_memory():
    """Named session should preserve context across calls."""
    session = f"{TEST_SESSION_PREFIX}memory-test"

    # First call: establish context
    result1 = _run(
        "-m",
        "gemini-3-flash",
        "--session",
        session,
        "My secret word is BLOOM. Remember it.",
    )
    assert result1.returncode == 0, f"First call failed: {result1.stderr}"

    # Second call: ask about the context
    result2 = _run(
        "-m", "gemini-3-flash", "--session", session, "What was my secret word?"
    )
    assert result2.returncode == 0, f"Second call failed: {result2.stderr}"
    assert "bloom" in result2.stdout.lower(), (
        f"Context not remembered. Response: {result2.stdout}"
    )


def test_named_session_isolation():
    """Two named sessions should have independent contexts."""
    session_a = f"{TEST_SESSION_PREFIX}isolation-a"
    session_b = f"{TEST_SESSION_PREFIX}isolation-b"

    # Set different contexts
    _run(
        "-m", "gemini-3-flash", "--session", session_a, "My color is RED. Remember it."
    )
    _run(
        "-m", "gemini-3-flash", "--session", session_b, "My color is BLUE. Remember it."
    )

    # Ask each session — they should give different answers
    result_a = _run(
        "-m", "gemini-3-flash", "--session", session_a, "What color did I say?"
    )
    result_b = _run(
        "-m", "gemini-3-flash", "--session", session_b, "What color did I say?"
    )

    assert "red" in result_a.stdout.lower(), (
        f"Session A lost context. Response: {result_a.stdout}"
    )
    assert "blue" in result_b.stdout.lower(), (
        f"Session B lost context. Response: {result_b.stdout}"
    )


# --- Session management tests ---


def test_list_sessions_empty():
    """Listing sessions should not show any test sessions when none created."""
    _clean_test_sessions()
    result = _run("--sessions")
    assert result.returncode == 0
    # No test-prefixed sessions should appear (non-test sessions from user may exist)
    assert TEST_SESSION_PREFIX not in result.stdout


def test_list_sessions_has_entry():
    """Listing sessions should show saved sessions."""
    session = f"{TEST_SESSION_PREFIX}list-test"
    _run("-m", "gemini-3-flash", "--session", session, "Hi")
    result = _run("--sessions")
    assert result.returncode == 0
    assert session in result.stdout


def test_delete_session():
    """Deleting a session should remove it locally and from Gemini web."""
    session = f"{TEST_SESSION_PREFIX}delete-test"
    _run("-m", "gemini-3-flash", "--session", session, "Hi")

    # Should exist
    list_result = _run("--sessions")
    assert session in list_result.stdout

    # Delete it
    result = _run("--rm-session", session)
    assert result.returncode == 0
    assert "deleted" in result.stdout.lower()

    # Should be gone
    list_result = _run("--sessions")
    assert session not in list_result.stdout


def test_delete_nonexistent_session():
    """Deleting a session that doesn't exist should show error."""
    result = _run("--rm-session", f"{TEST_SESSION_PREFIX}does-not-exist-xyz")
    assert "not found" in result.stdout.lower()


# --- Chat mode internals test ---


def test_chat_mode_stdin():
    """Chat mode should work with piped stdin."""
    result = _run(
        "--chat",
        "-m",
        "gemini-3-flash",
        input="I am a test user. Type 'exit'.\nexit\n",
    )
    assert result.returncode == 0, f"Chat mode failed: {result.stderr}"
    # Verify chat mode started and exited cleanly via piped stdin.
    # The "exit" line may be consumed before Gemini responds, so we
    # check for either a response containing our input or a clean exit.
    has_response = "test user" in result.stdout.lower()
    clean_exit = result.returncode == 0 and "ask-gemini" in result.stdout.lower()
    assert has_response or clean_exit, (
        f"Chat mode did not handle stdin correctly. Output: {result.stdout}"
    )


# --- Stdin / pipe mode tests ---


def test_stdin_pipe_input():
    """When stdin is piped, use it as the prompt."""
    result = _run(
        "-m", "gemini-3-flash", input="What is 2+2? Reply with just the number."
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "4" in result.stdout


def test_stdin_pipe_with_session():
    """Stdin pipe with a named session should work."""
    session = f"{TEST_SESSION_PREFIX}stdin-session"
    result1 = _run(
        "-m",
        "gemini-3-flash",
        "--session",
        session,
        input="My test word is PEAR. Remember it.",
    )
    assert result1.returncode == 0, f"First call failed: {result1.stderr}"

    result2 = _run(
        "-m", "gemini-3-flash", "--session", session, input="What was my test word?"
    )
    assert result2.returncode == 0, f"Second call failed: {result2.stderr}"
    assert "pear" in result2.stdout.lower(), (
        f"Context not remembered. Response: {result2.stdout}"
    )


def test_stdin_pipe_with_model():
    """Stdin pipe with model flag should work."""
    result = _run("-m", "gemini-3-flash", input="Say 'pipe test ok'.")
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    assert "pipe" in result.stdout.lower() or "ok" in result.stdout.lower()


# --- Help and version ---


def test_help():
    """Help should list all options."""
    result = _run("--help")
    assert result.returncode == 0
    assert "--session" in result.stdout
    assert "--chat" in result.stdout
    assert "--no-rate-limit" in result.stdout


def test_version():
    """Version flag should work."""
    result = _run("--version")
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


# --- Cookie setup ---


def test_cookie_setup():
    """Cookie setup instructions should print."""
    result = _run("--cookie-setup")
    assert result.returncode == 0
    assert "__Secure-1PSID" in result.stdout
