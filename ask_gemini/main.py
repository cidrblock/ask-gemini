import asyncio
import sys
from pathlib import Path

import click
from loguru import logger
from prompt_toolkit.patch_stdout import patch_stdout

from ask_gemini.client import (
    GeminiAuthError,
    GeminiClientWrapper,
    GeminiNetworkError,
    _load_sessions,
    _save_sessions,
)
from ask_gemini.config import GeminiCookies, RateLimitConfig
from ask_gemini.display import (
    VERSION,
    FollowUpPrompt,
    clear_screen,
    echo_complete,
    echo_stream,
    print_header,
    print_notice,
    print_user_bubble,
)

logger.remove()
logger.add(
    sys.stderr,
    level="WARNING",
    format="<level>{message}</level>",
    filter=lambda record: record["extra"].get("name") != "gemini_webapi",
)

_CLIENT_ERRORS = (GeminiAuthError, GeminiNetworkError)

AVAILABLE_MODELS = [
    "gemini-3-pro",
    "gemini-3-flash",
    "gemini-3-flash-thinking",
]

DEFAULT_MODEL = "gemini-3-pro"


@click.command()
@click.argument("prompt", required=False)
@click.option(
    "-m",
    "--model",
    default=None,
    help=f"Model to use. Options: {', '.join(AVAILABLE_MODELS)}",
)
@click.option(
    "-s",
    "--stream",
    is_flag=True,
    default=True,
    help="Stream output as it arrives (default)",
)
@click.option(
    "--no-stream",
    is_flag=True,
    default=False,
    help="Wait for full response before printing",
)
@click.option(
    "--chat",
    is_flag=True,
    default=False,
    help="Enter interactive chat mode (resumes your latest web conversation)",
)
@click.option(
    "--session",
    "session_name",
    default=None,
    help="Use a named conversation session (creates one if it doesn't exist)",
)
@click.option(
    "--sessions",
    is_flag=True,
    default=False,
    help="List all named sessions",
)
@click.option(
    "--rm-session",
    default=None,
    help="Delete a named session",
)
@click.option(
    "--new-chat",
    is_flag=True,
    default=False,
    help="Start a fresh conversation instead of resuming the latest web chat",
)
@click.option(
    "--no-rate-limit",
    is_flag=True,
    default=False,
    help="Disable human-like typing/cooldown delays",
)
@click.option("--cookie-setup", is_flag=True, help="Print cookie setup instructions")
@click.version_option(version=VERSION)
def main(
    prompt,
    model,
    stream,
    no_stream,
    chat,
    session_name,
    sessions,
    rm_session,
    new_chat,
    no_rate_limit,
    cookie_setup,
):
    """Ask Gemini from the terminal."""
    if cookie_setup:
        print(GeminiCookies.setup_instructions())
        return

    if sessions:
        _list_sessions()
        return

    if rm_session:
        asyncio.run(_remove_session(rm_session))
        return

    # Read prompt from stdin if not provided and input is piped
    if prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if no_stream:
        stream = False

    chat, new_chat = _ag_defaults_to_new_chat(prompt, chat, session_name, new_chat)

    model = model or "gemini-3-pro"
    if model not in AVAILABLE_MODELS:
        click.echo(f"Unknown model: {model}")
        click.echo(f"Available models: {', '.join(AVAILABLE_MODELS)}")
        raise SystemExit(1)

    GeminiCookies.try_load_from_browser()

    if not GeminiCookies.is_configured() and not sys.stderr.isatty():
        click.echo("Error: Gemini cookies not found.\n")
        click.echo(GeminiCookies.setup_instructions(), err=True)
        raise SystemExit(1)

    rate_limit = not no_rate_limit
    client = GeminiClientWrapper(rate_limit=rate_limit)

    if rate_limit:
        logger.info(
            f"Rate limit enabled: typing={RateLimitConfig.typing_speed}s/char, "
            f"min={RateLimitConfig.min_delay}s, max={RateLimitConfig.max_delay}s, "
            f"cooldown={RateLimitConfig.cooldown}s"
        )

    if session_name:
        asyncio.run(_run_session(client, session_name, prompt, model, stream))
    elif chat:
        asyncio.run(_run_chat(client, model, stream, new_chat=new_chat))
    elif prompt:
        asyncio.run(_run(client, prompt, model, stream))
    else:
        click.echo("Usage: ask-gemini <your question>")
        click.echo("  ask-gemini --chat              Interactive chat mode")
        click.echo("  ask-gemini --session <name>    Use a named session")
        click.echo("  ask-gemini --sessions          List saved sessions")
        click.echo("  ask-gemini --help              Show all options")
        raise SystemExit(0)


def _chat_command(text: str) -> str:
    """Normalize an in-chat command; optional leading slash is ignored."""
    cmd = text.strip().lower()
    if cmd.startswith("/"):
        cmd = cmd[1:]
    return cmd


def _invoked_name() -> str:
    return Path(sys.argv[0]).name.lower()


def _ag_defaults_to_new_chat(
    prompt,
    chat: bool,
    session_name: str | None,
    new_chat: bool,
    *,
    invoked: str | None = None,
    stdin_tty: bool | None = None,
) -> tuple[bool, bool]:
    """Bare `ag` (no prompt, not piped) starts a fresh chat."""
    if invoked is None:
        invoked = _invoked_name()
    if stdin_tty is None:
        stdin_tty = sys.stdin.isatty()
    if (
        Path(invoked).name in ("ag", "ag.exe")
        and prompt is None
        and not chat
        and not session_name
        and stdin_tty
    ):
        return True, True
    return chat, new_chat


def _exit_init(exc: BaseException) -> None:
    if isinstance(exc, GeminiAuthError):
        click.echo(exc.user_message(), err=True)
    else:
        click.echo(f"Error: {exc}", err=True)
    raise SystemExit(1) from exc


def _list_sessions():
    """List all named sessions."""
    sessions = _load_sessions()
    if not sessions:
        click.echo("No named sessions.")
        return
    for name, cid in sessions.items():
        click.echo(f"  {name:20s}  cid: {cid}")


async def _remove_session(name: str):
    """Delete a named session locally and from Gemini web."""
    sessions = _load_sessions()
    if name not in sessions:
        click.echo(f"Session '{name}' not found.")
        return

    cid = sessions[name]
    del sessions[name]
    _save_sessions(sessions)

    # Also delete the conversation from Gemini web
    GeminiCookies.try_load_from_browser()
    client = GeminiClientWrapper(rate_limit=False)
    try:
        await client.init()
        await client.delete_chat(cid)
        click.echo(f"Deleted session '{name}' from disk and Gemini web.")
    except Exception as e:
        click.echo(
            f"Deleted local session '{name}', but failed to delete from Gemini web: {e}",
            err=True,
        )


async def _run(client, prompt, model, stream):
    try:
        await client.init()
    except (RuntimeError, GeminiAuthError) as e:
        _exit_init(e)

    try:
        print_user_bubble(prompt)
        if stream:
            await echo_stream(client.ask_stream(prompt, model))
        else:
            await echo_complete(client.ask(prompt, model))
    except _CLIENT_ERRORS as e:
        click.echo(e.user_message(), err=True)
        raise SystemExit(1) from e


async def _run_session(client, name, prompt, model, stream):
    """Named session: auto-create or resume, then send one message."""
    try:
        await client.init()
    except (RuntimeError, GeminiAuthError) as e:
        _exit_init(e)

    existed = await client.resume_named_session(name, model)
    if existed:
        logger.info(f"Using existing session '{name}'")
    else:
        logger.info(f"Created new session '{name}'")

    try:
        if prompt:
            print_user_bubble(prompt)
            if stream:
                await echo_stream(client.chat_stream(prompt, model))
            else:
                await echo_complete(client.chat(prompt, model))
    except _CLIENT_ERRORS as e:
        click.echo(e.user_message(), err=True)
        raise SystemExit(1) from e

    # Update the stored cid after the conversation was created
    if client._chat and client._chat.session.cid:
        sessions = _load_sessions()
        sessions[name] = client._chat.session.cid
        _save_sessions(sessions)


async def _run_chat(client, model, stream, new_chat=False):
    try:
        await client.init()
    except (RuntimeError, GeminiAuthError) as e:
        _exit_init(e)

    clear_screen()

    if new_chat:
        await client.start_chat(model)
        print_header(model=model, resumed=False)
    else:
        resumed = await client.resume_latest_chat(model)
        print_header(model=model, resumed=bool(resumed))

    followup = FollowUpPrompt(model)

    if sys.stdin.isatty():
        await _run_chat_sticky(client, model, stream, followup)
        return

    while True:
        try:
            text = await followup.read()
        except EOFError:
            click.echo()
            break
        except KeyboardInterrupt:
            click.echo()
            continue

        text = text.strip()
        if not text:
            continue
        cmd = _chat_command(text)
        if cmd in ("exit", "quit"):
            break
        if cmd == "clear":
            await client.start_chat(model)
            print_notice("Started a new conversation.")
            continue
        if cmd == "new" and not new_chat:
            await client.start_chat(model)
            new_chat = True
            print_notice("Started a new conversation.")
            continue

        print_user_bubble(text)

        try:
            if stream:
                await echo_stream(client.chat_stream(text, model))
            else:
                await echo_complete(client.chat(text, model))
            click.echo()
        except _CLIENT_ERRORS as e:
            click.echo(f"\n{e.user_message()}", err=True)
        except Exception as e:  # noqa: BLE001 - keep the chat loop alive
            click.echo(f"\nGemini request failed: {e}", err=True)


async def _run_chat_sticky(client, model, stream, followup: FollowUpPrompt):
    """Keep the follow-up field live while replies insert above it."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def worker():
        while True:
            text = await queue.get()
            try:
                if text is None:
                    return
                try:
                    if stream:
                        await echo_stream(
                            client.chat_stream(text, model), above_prompt=True
                        )
                    else:
                        await echo_complete(client.chat(text, model), above_prompt=True)
                    click.echo()
                except _CLIENT_ERRORS as e:
                    click.echo(f"\n{e.user_message()}", err=True)
                except Exception as e:  # noqa: BLE001 - keep the worker from dying
                    click.echo(f"\nGemini request failed: {e}", err=True)
                finally:
                    followup.set_waiting(False)
            finally:
                queue.task_done()

    worker_task = asyncio.create_task(worker())
    try:
        with patch_stdout(raw=True):
            while True:
                try:
                    text = await followup.read()
                except EOFError:
                    click.echo()
                    break
                except KeyboardInterrupt:
                    click.echo()
                    continue

                text = text.strip()
                if not text:
                    continue
                cmd = _chat_command(text)
                if cmd in ("exit", "quit"):
                    break
                if cmd in ("clear", "new"):
                    await queue.join()
                    await client.start_chat(model)
                    print_notice("Started a new conversation.")
                    continue

                print_user_bubble(text)
                followup.set_waiting(True)
                await queue.put(text)
    finally:
        await queue.join()
        await queue.put(None)
        await worker_task


if __name__ == "__main__":
    main()
