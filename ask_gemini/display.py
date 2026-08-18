"""Terminal chrome — header, user strips, spinner, follow-up prompt."""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.enums import DEFAULT_BUFFER
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VerticalAlign,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import AfterInput, ConditionalProcessor
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

APP_NAME = "ask-gemini"
WAITING = "Waiting for Gemini…"
WAITING_SIGNIN = "Waiting for you to sign in…"
SIGNIN_URL = "https://gemini.google.com"
SIGNIN_POLL_SECONDS = 2.5
SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
HISTORY_FILE = Path.home() / ".ask-gemini" / "history"
PROMPT_BG = "#303030"
USER_STRIP = f"bold white on {PROMPT_BG}"
# Match Cursor Agent user-message-ui: paddingY=1, paddingLeft=1, paddingRight=5, marginX=1
USER_PAD_TOP = 1
USER_PAD_RIGHT = 5
USER_PAD_BOTTOM = 1
USER_PAD_LEFT = 1
USER_MARGIN_X = 1
PROMPT_BAR_ROWS = USER_PAD_TOP + 1 + USER_PAD_BOTTOM

try:
    VERSION = version("ask-gemini")
except PackageNotFoundError:
    VERSION = "0.2.1"

_PROMPT_STYLE = Style.from_dict(
    {
        # Never style "": a default background paints the whole window.
        "prompt-bar": f"bg:{PROMPT_BG} #e6e6e6",
        "prompt": f"bg:{PROMPT_BG} #e6e6e6",
        "placeholder": f"bg:{PROMPT_BG} italic #808080",
        "waiting": "italic #808080",
    }
)


def _stderr_console() -> Console:
    return Console(stderr=True)


def _stdout_console() -> Console:
    return Console()


def clear_screen() -> None:
    """Wipe the terminal so chat starts on a blank canvas."""
    if sys.stdout.isatty():
        click.clear()


def print_header(
    *,
    model: str,
    resumed: bool | None = None,
    console: Console | None = None,
) -> None:
    c = console or _stdout_console()
    c.print(APP_NAME, style="bold")
    c.print(f"v{VERSION}", style="dim")
    if resumed is True:
        tip = "Resuming your latest Gemini web chat. /quit to leave."
    elif resumed is False:
        tip = "Starting a new conversation. /quit to leave."
    else:
        tip = f"Using {model}. Type /quit to leave, /clear for a new chat."
    c.print(f"Tip: {tip}", style="dim")
    c.print()


def _user_bar_row(inner: str, inner_w: int) -> Text:
    """One user-bar row: unstyled left cell, grey body, no right cell.

    Terminals wrap a line that is exactly `width` cells, which eats a trailing
    margin space and makes the bar look full-bleed. Printing `width - 1` cells
    (1-col left margin + `width - 2` grey) leaves the last column as the
    default background — Ink's marginX=1.
    """
    line = Text()
    line.append(" " * USER_MARGIN_X)
    line.append(inner[:inner_w].ljust(inner_w), style=USER_STRIP)
    return line


def print_user_bubble(text: str, console: Console | None = None) -> None:
    """Render a user message as a grey bar with 1-column side margins."""
    c = console or _stdout_console()
    inner_w = max(1, c.size.width - 2 * USER_MARGIN_X)
    text_w = max(1, inner_w - USER_PAD_LEFT - USER_PAD_RIGHT)
    wrapped = textwrap.wrap(text.replace("\t", "    "), width=text_w) or [""]
    blank = " " * inner_w
    left = " " * USER_PAD_LEFT
    kwargs = {"highlight": False, "no_wrap": True, "overflow": "ignore"}
    for _ in range(USER_PAD_TOP):
        c.print(_user_bar_row(blank, inner_w), **kwargs)
    for part in wrapped:
        c.print(_user_bar_row(left + part, inner_w), **kwargs)
    for _ in range(USER_PAD_BOTTOM):
        c.print(_user_bar_row(blank, inner_w), **kwargs)
    c.print()


def print_notice(text: str, console: Console | None = None) -> None:
    c = console or _stdout_console()
    c.print(text, style="dim")
    c.print()


async def wait_for_signin(
    probe: Callable[[], Awaitable[bool]],
    *,
    interval: float = SIGNIN_POLL_SECONDS,
    url: str = SIGNIN_URL,
    console: Console | None = None,
    open_browser: bool = True,
    sleeper=asyncio.sleep,
    browser_open=None,
) -> None:
    """Poll until probe() is true, after asking the user to sign in."""
    import webbrowser

    c = console or _stderr_console()
    c.print("Gemini is not signed in.", style="bold")
    c.print(f"Sign in at {url}")
    c.print("This command waits until the browser session is ready.", style="dim")
    c.print("Ctrl-C to cancel.", style="dim")
    c.print()
    opener = browser_open or webbrowser.open
    if open_browser:
        try:
            opener(url)
        except Exception:  # noqa: BLE001 - opening a browser is best-effort
            pass
    spinner = _start_spinner(c, WAITING_SIGNIN)
    try:
        while True:
            if await probe():
                return
            await sleeper(interval)
    finally:
        _stop_spinner(spinner)


def _start_spinner(console: Console, message: str = WAITING):
    """Start a Rich status spinner, or None when stderr is not a TTY."""
    if not console.is_terminal:
        return None
    status = console.status(message, spinner="dots")
    status.start()
    return status


def _stop_spinner(status) -> None:
    if status is not None:
        status.stop()


def _markdown(text: str) -> Markdown:
    return Markdown(text, hyperlinks=True)


def _print_reply(text: str, *, prefix: str, output: Console) -> None:
    if prefix:
        click.echo(prefix, nl=False)
    if output.is_terminal and not prefix:
        output.print(_markdown(text))
    else:
        click.echo(text)


async def _echo_above_prompt(
    chunks: AsyncIterator[str],
    *,
    prefix: str,
    output: Console,
) -> None:
    """Insert a finished reply above the live prompt.

    Do not use Rich Live/Status here: they fight prompt_toolkit for the cursor.
    """
    buf: list[str] = []
    async for chunk in chunks:
        buf.append(chunk)
    _print_reply("".join(buf), prefix=prefix, output=output)


async def echo_stream(
    chunks: AsyncIterator[str],
    *,
    prefix: str = "",
    console: Console | None = None,
    output: Console | None = None,
    above_prompt: bool = False,
) -> None:
    """Show a spinner until the first chunk, then print the stream.

    On a TTY, chunks are re-rendered as Markdown so **bold** and lists display.
    Pipes and prefixed output stay as raw text.
    When above_prompt is set, the reply is printed once through stdout so
    prompt_toolkit can keep ownership of the input line.
    """
    output = output or _stdout_console()
    if above_prompt:
        await _echo_above_prompt(chunks, prefix=prefix, output=output)
        return

    console = console or _stderr_console()
    use_markdown = output.is_terminal and not prefix
    spinner = _start_spinner(console)
    live: Live | None = None
    buf: list[str] = []
    first = True
    try:
        async for chunk in chunks:
            if first:
                _stop_spinner(spinner)
                spinner = None
                if prefix:
                    click.echo(prefix, nl=False)
                first = False
            if use_markdown:
                buf.append(chunk)
                rendered = _markdown("".join(buf))
                if live is None:
                    live = Live(
                        rendered,
                        console=output,
                        refresh_per_second=12,
                        vertical_overflow="visible",
                    )
                    live.start()
                else:
                    live.update(rendered)
            else:
                click.echo(chunk, nl=False)
    finally:
        _stop_spinner(spinner)
        if live is not None:
            live.stop()
    if not use_markdown:
        click.echo()


async def echo_complete(
    response: Awaitable[str],
    *,
    prefix: str = "",
    console: Console | None = None,
    output: Console | None = None,
    above_prompt: bool = False,
) -> None:
    """Show a spinner until the full response is ready, then print it."""
    output = output or _stdout_console()
    spinner = None if above_prompt else _start_spinner(console or _stderr_console())
    try:
        text = await response
    finally:
        _stop_spinner(spinner)
    _print_reply(text, prefix=prefix, output=output)


class FollowUpPrompt:
    """Follow-up field that sits in the transcript, not the screen bottom.

    PromptSession fills every row below the cursor and pins a bottom_toolbar
    to the last line — that put the spinner at the window bottom with a gap
    above it. This layout is top-aligned and only as tall as waiting + input,
    with 1-column side margins on the grey bar.
    """

    def __init__(self, model: str):
        self.model = model
        self._waiting = 0
        self._app: Application[str] | None = None
        self._buffer: Buffer | None = None
        if sys.stdin.isatty():
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._buffer, self._app = self._build_app()

    def _waiting_fragments(self):
        if not self._waiting:
            return []
        frame = SPIN_FRAMES[int(time.monotonic() * 10) % len(SPIN_FRAMES)]
        return [("class:waiting", f" {frame} {WAITING}")]

    def _build_app(self) -> tuple[Buffer, Application[str]]:
        def accept(buff: Buffer) -> bool:
            app = get_app_or_none()
            if app is not None:
                app.exit(result=buff.text)
            return True

        buffer = Buffer(
            name=DEFAULT_BUFFER,
            history=FileHistory(str(HISTORY_FILE)),
            enable_history_search=True,
            multiline=False,
            accept_handler=accept,
        )

        kb = KeyBindings()

        @kb.add("enter")
        def _accept(event) -> None:
            event.app.current_buffer.validate_and_handle()

        @kb.add("c-c")
        def _interrupt(event) -> None:
            event.app.exit(exception=KeyboardInterrupt)

        @kb.add("c-d", filter=Condition(lambda: not buffer.text))
        def _eof(event) -> None:
            event.app.exit(exception=EOFError)

        waiting_window = ConditionalContainer(
            Window(
                FormattedTextControl(self._waiting_fragments),
                height=1,
                dont_extend_height=True,
            ),
            filter=Condition(lambda: self._waiting > 0),
        )
        waiting_gap = ConditionalContainer(
            Window(height=1, dont_extend_height=True),
            filter=Condition(lambda: self._waiting > 0),
        )
        input_window = Window(
            BufferControl(
                buffer=buffer,
                input_processors=[
                    ConditionalProcessor(
                        AfterInput("Add a follow-up", style="class:placeholder"),
                        filter=Condition(lambda: buffer.text == ""),
                    )
                ],
                include_default_input_processors=True,
            ),
            height=1,
            dont_extend_height=True,
            wrap_lines=False,
            style="class:prompt-bar",
            get_line_prefix=lambda _line, _wrap: [("class:prompt", " → ")],
        )

        def _bar_pad() -> Window:
            return Window(
                char=" ",
                height=1,
                dont_extend_height=True,
                style="class:prompt-bar",
            )

        bar = HSplit(
            [_bar_pad(), input_window, _bar_pad()],
            height=PROMPT_BAR_ROWS,
        )
        input_row = VSplit(
            [
                Window(width=USER_MARGIN_X, char=" ", dont_extend_width=True),
                bar,
                Window(width=USER_MARGIN_X, char=" ", dont_extend_width=True),
            ],
            height=PROMPT_BAR_ROWS,
        )
        root = HSplit(
            [waiting_window, waiting_gap, input_row],
            align=VerticalAlign.TOP,
            padding=0,
        )
        app: Application[str] = Application(
            layout=Layout(root, focused_element=input_window),
            style=_PROMPT_STYLE,
            key_bindings=kb,
            erase_when_done=True,
            refresh_interval=0.08,
            full_screen=False,
            mouse_support=False,
        )
        return buffer, app

    def set_waiting(self, waiting: bool) -> None:
        self._waiting += 1 if waiting else -1
        self._waiting = max(0, self._waiting)
        app = get_app_or_none()
        if app is not None:
            app.invalidate()

    async def read(self) -> str:
        if self._app is None or self._buffer is None:
            line = sys.stdin.readline()
            if line == "":
                raise EOFError
            return line.rstrip("\n\r")
        self._buffer.reset()
        result = await self._app.run_async()
        return result or ""
