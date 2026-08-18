"""Unit tests for spinner-backed output helpers."""

import asyncio
import sys
from io import StringIO

from rich.console import Console

from ask_gemini.display import (
    APP_NAME,
    WAITING,
    FollowUpPrompt,
    echo_complete,
    echo_stream,
    print_header,
    print_user_bubble,
)


async def _chunks(*parts: str):
    for part in parts:
        yield part


def test_echo_stream_prints_chunks(capsys):
    console = Console(file=StringIO(), stderr=True, force_terminal=False)
    output = Console(file=StringIO(), force_terminal=False)

    async def _run():
        await echo_stream(
            _chunks("Hel", "lo"),
            prefix="Gemini> ",
            console=console,
            output=output,
        )

    asyncio.run(_run())
    assert capsys.readouterr().out == "Gemini> Hello\n"


def test_echo_stream_skips_prefix_when_empty(capsys):
    console = Console(file=StringIO(), stderr=True, force_terminal=False)
    output = Console(file=StringIO(), force_terminal=False)

    async def _run():
        await echo_stream(_chunks("ok"), console=console, output=output)

    asyncio.run(_run())
    assert capsys.readouterr().out == "ok\n"


def test_echo_complete_prints_after_await(capsys):
    console = Console(file=StringIO(), stderr=True, force_terminal=False)
    output = Console(file=StringIO(), force_terminal=False)

    async def _response():
        return "done"

    async def _run():
        await echo_complete(
            _response(), prefix="Gemini> ", console=console, output=output
        )

    asyncio.run(_run())
    assert capsys.readouterr().out == "Gemini> done\n"


def test_echo_stream_renders_markdown_bold():
    err = Console(file=StringIO(), stderr=True, force_terminal=False)
    out = Console(record=True, file=StringIO(), force_terminal=True, width=80)

    async def _run():
        await echo_stream(
            _chunks("**5:00 AM – 5:55 AM:** standup"),
            console=err,
            output=out,
        )

    asyncio.run(_run())
    text = out.export_text()
    assert "5:00 AM" in text
    assert "standup" in text
    assert "**" not in text


def test_echo_complete_renders_markdown_bold():
    err = Console(file=StringIO(), stderr=True, force_terminal=False)
    out = Console(record=True, file=StringIO(), force_terminal=True, width=80)

    async def _response():
        return "See **bold** text"

    async def _run():
        await echo_complete(_response(), console=err, output=out)

    asyncio.run(_run())
    text = out.export_text()
    assert "bold" in text
    assert "**" not in text


def test_clear_screen_skips_non_tty(monkeypatch):
    from ask_gemini.display import clear_screen

    monkeypatch.setattr(sys, "stdout", StringIO())
    called: list[bool] = []
    monkeypatch.setattr("click.clear", lambda: called.append(True))
    clear_screen()
    assert called == []


def test_print_header():
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80, color_system=None)
    print_header(model="gemini-3-pro", resumed=True, console=console)
    text = buf.getvalue()
    assert APP_NAME in text
    assert "v" in text
    assert "Resuming" in text
    assert "/quit" in text


def test_chat_command_strips_slash():
    from ask_gemini.main import _ag_defaults_to_new_chat, _chat_command

    assert _chat_command("/quit") == "quit"
    assert _chat_command("quit") == "quit"
    assert _chat_command("/Exit") == "exit"
    assert _chat_command("/clear") == "clear"


def test_ag_without_args_starts_new_chat():
    from ask_gemini.main import _ag_defaults_to_new_chat

    assert _ag_defaults_to_new_chat(
        None, False, None, False, invoked="ag", stdin_tty=True
    ) == (True, True)
    assert _ag_defaults_to_new_chat(
        None, False, None, False, invoked="ask-gemini", stdin_tty=True
    ) == (False, False)
    assert _ag_defaults_to_new_chat(
        "hello", False, None, False, invoked="ag", stdin_tty=True
    ) == (False, False)
    assert _ag_defaults_to_new_chat(
        None, True, None, False, invoked="ag", stdin_tty=True
    ) == (True, False)
    assert _ag_defaults_to_new_chat(
        None, False, None, False, invoked="ag", stdin_tty=False
    ) == (False, False)


def test_print_user_bubble():
    from ask_gemini.display import USER_MARGIN_X, _user_bar_row

    row = _user_bar_row(" hello", inner_w=18)
    assert row.plain.startswith(" " * USER_MARGIN_X)
    assert row.plain[USER_MARGIN_X:].startswith(" hello")
    assert len(row.plain) == USER_MARGIN_X + 18
    assert row.spans
    assert row.spans[0].start == USER_MARGIN_X

    buf = StringIO()
    console = Console(
        file=buf, force_terminal=True, width=40, color_system=None, highlight=False
    )
    print_user_bubble("hello", console=console)
    painted = [line for line in buf.getvalue().splitlines() if "hello" in line]
    assert painted
    assert painted[0].startswith(" ")
    assert not painted[0].endswith("hello")  # right padding inside the bar
    assert len(buf.getvalue().splitlines()) >= 4


def test_echo_stream_above_prompt_prints_once():
    err = Console(file=StringIO(), stderr=True, force_terminal=False)
    out = Console(record=True, file=StringIO(), force_terminal=True, width=80)

    async def _run():
        await echo_stream(
            _chunks("**hello**"),
            console=err,
            output=out,
            above_prompt=True,
        )

    asyncio.run(_run())
    text = out.export_text()
    assert "hello" in text
    assert "**" not in text


def test_followup_bar_matches_historical_padding():
    from ask_gemini.display import PROMPT_BAR_ROWS, USER_PAD_BOTTOM, USER_PAD_TOP

    assert PROMPT_BAR_ROWS == USER_PAD_TOP + 1 + USER_PAD_BOTTOM
    assert PROMPT_BAR_ROWS == 3


def test_followup_waiting_sits_above_input():
    prompt = FollowUpPrompt("gemini-3-pro")
    idle = prompt._waiting_fragments()
    assert not any(WAITING in part for _, part in idle)
    prompt.set_waiting(True)
    waiting = prompt._waiting_fragments()
    assert any(WAITING in part for _, part in waiting)
    prompt.set_waiting(False)
    assert prompt._waiting_fragments() == []


def test_wait_for_signin_polls_until_ready():
    from ask_gemini.display import SIGNIN_URL, wait_for_signin

    n = {"i": 0}
    opened: list[str] = []

    async def probe():
        n["i"] += 1
        return n["i"] >= 3

    async def sleeper(_t):
        return

    buf = StringIO()
    console = Console(file=buf, force_terminal=False)
    asyncio.run(
        wait_for_signin(
            probe,
            interval=0,
            console=console,
            sleeper=sleeper,
            browser_open=opened.append,
        )
    )
    assert n["i"] == 3
    assert opened == [SIGNIN_URL]
    assert "gemini.google.com" in buf.getvalue()
