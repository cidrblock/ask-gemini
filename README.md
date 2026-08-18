# ask-gemini

[![CI](https://github.com/Guitenbay/ask-gemini/actions/workflows/ci.yml/badge.svg)](https://github.com/Guitenbay/ask-gemini/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/Guitenbay/ask-gemini?style=social)](https://github.com/Guitenbay/ask-gemini)

CLI tool to chat with Gemini's web interface from the terminal. No API key needed — uses your browser cookies.

> **For agents**: See [SKILL.md](SKILL.md) for agent usage patterns and best practices.
> Quick install: `pip install ask-gemini` (PyPI) or download from [GitHub Releases](https://github.com/Guitenbay/ask-gemini/releases).

## Setup

### Option A: Install via pip (recommended)

```bash
pip install ask-gemini
```

Available on [PyPI](https://pypi.org/project/ask-gemini/).

### Option B: Download standalone executable

Download the latest macOS executable from [GitHub Releases](https://github.com/Guitenbay/ask-gemini/releases):

```bash
# Download from the latest release asset at https://github.com/Guitenbay/ask-gemini/releases
chmod +x ask-gemini
./ask-gemini "Hello!"
```

No Python installation required.

### Option C: Build from source

```bash
git clone https://github.com/Guitenbay/ask-gemini.git
cd ask-gemini
pip install pyinstaller
bash build.sh
```

The executable is at `dist/ask-gemini` (~21MB). Or install as a Python package:

```bash
pip install -e .
```

## Configure cookies

**Option A: Auto-detect from browser**

If you're logged into Gemini in Chrome or Edge, ask-gemini will auto-detect your cookies. Just run:

```bash
ask-gemini "Hello!"
```

**Option B: Manual setup**

```bash
ask-gemini --cookie-setup
```

Follow the instructions to copy `__Secure-1PSID` and `__Secure-1PSIDTS` from your browser, then save them in a `.env` file.

## Usage

`ag` is a short alias for `ask-gemini`. Running `ag` with no arguments starts a fresh chat (`--chat --new-chat`).

### Single question

```bash
ask-gemini "What is Python?"
ask-gemini --model gemini-3-flash "Explain async/await"
ask-gemini --no-stream "Write a haiku about coding"
```

### Chat mode

Enter an interactive conversation that resumes your latest web-side chat:

```bash
ask-gemini --chat
```

This automatically picks up where you left off on [gemini.google.com](https://gemini.google.com).
On a TTY, chat mode keeps the follow-up field in the transcript (not pinned to the bottom of the window). While Gemini runs, a spinner sits *between* the last prompt and the follow-up field. Replies insert above the follow-up field. User prompts and the live follow-up are grey bars with a one-column side margin.

Useful commands while in chat:

| Command | Action |
|---|---|
| `/quit` / `quit` / `exit` | Leave chat mode |
| `/new` / `new` | Start a fresh conversation |
| `/clear` / `clear` | Same as `/new` |

To always start a fresh conversation instead of resuming:

```bash
ask-gemini --chat --new-chat
```

### Chat mode internals

- The same `cid` (conversation ID) is used as the web interface, so history is shared between CLI and browser
- Multiple CLI sessions in a row will keep using the same web conversation
- Switching models in an existing chat starts a new session on the web side

### Named sessions

Create isolated conversations that don't interfere with each other. Perfect for multiple agents working on different tasks:

```bash
# Create or resume a named session
ask-gemini --session code-review "Analyze this code: $(cat app.py)"

# Follow-up — Gemini remembers the code review context
ask-gemini --session code-review "Now rewrite it with better error handling"

# A completely separate session
ask-gemini --session data-analysis "What is SQL?"
```

Manage your sessions:

```bash
ask-gemini --sessions                # List all saved sessions
ask-gemini --rm-session code-review  # Delete a session
```

Sessions are stored in `~/.ask-gemini/sessions.json` and map directly to conversations on `gemini.google.com`.

## Options

| Option | Description |
|---|---|
| `-m, --model` | Model to use (default: `gemini-3-pro`) |
| `-s, --stream` | Stream output as it arrives (default) |
| `--no-stream` | Wait for full response before printing |
| `--chat` | Enter interactive chat mode (resumes web conversation) |
| `--new-chat` | Start a fresh conversation instead of resuming |
| `--session <name>` | Use a named, isolated conversation session |
| `--sessions` | List all saved named sessions |
| `--rm-session <name>` | Delete a named session |
| `--no-rate-limit` | Disable human-like typing/cooldown delays |
| `--cookie-setup` | Print cookie setup instructions |
| `--version` | Print version |

## Available Models

| Model | Description |
|---|---|
| `gemini-3-pro` | Most capable (default) |
| `gemini-3-flash` | Fast |
| `gemini-3-flash-thinking` | Flash with chain-of-thought |

## Configuration

All configuration via environment variables in `.env` (project root or `~/.ask-gemini/.env`):

| Variable | Description | Default |
|---|---|---|
| `GEMINI_PSID` | `__Secure-1PSID` cookie | (auto-detect from browser) |
| `GEMINI_PSIDTS` | `__Secure-1PSIDTS` cookie | (auto-detect from browser) |
| `PROXY_URL` | HTTP proxy URL | (empty) |
| `MODEL` | Default model | `gemini-3-pro` |

## Rate limiting

By default, ask-gemini simulates human typing speed to avoid triggering Google's risk control:

- **Typing delay**: `message_length × TYPING_SPEED` seconds, clamped between `MIN_DELAY` and `MAX_DELAY`
- **Cooldown**: `COOLDOWN` seconds after receiving a response before the next request
- **Jitter**: +/- 20% random variation so delays aren't perfectly uniform

Configure in `.env`:

| Variable | Description | Default |
|---|---|---|
| `RATE_LIMIT` | Enable/disable rate limiting (`1` or `0`) | `1` |
| `TYPING_SPEED` | Seconds per character (≈ 20 chars/sec) | `0.05` |
| `MIN_DELAY` | Minimum delay before sending (seconds) | `1.0` |
| `MAX_DELAY` | Maximum delay cap (seconds) | `5.0` |
| `COOLDOWN` | Seconds to wait after receiving a response | `2.0` |

Or disable per-call with `--no-rate-limit`.

## Notes

- Uses the free Gemini web interface, not the paid API.
- Cookies expire. If requests fail, refresh Gemini in your browser or update cookies manually.
- Automated access may violate Google's Terms of Service. Use at your own risk.
