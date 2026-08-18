# ask-gemini — Agent Skill

CLI tool to call Gemini's web interface from the command line. Uses browser cookies — no API key needed.

## Quick reference

```bash
# Single question (stateless)
ask-gemini "your prompt here"
# `ag` is a short alias; bare `ag` starts a fresh chat (`--chat --new-chat`)

# With specific model
ask-gemini -m gemini-3-flash "your prompt"

# Interactive chat (resumes latest web conversation)
ask-gemini --chat

# Named session (isolated context, persists across calls)
ask-gemini --session my-task "first prompt"
ask-gemini --session my-task "follow-up — Gemini remembers context"
```

## When to use

Use this when the user needs Gemini's web interface (free) rather than another model. Good for second opinions, content generation, or model comparison.

## Models

| Model | Description |
|---|---|
| `gemini-3-pro` | Most capable (default) |
| `gemini-3-flash` | Fast responses |
| `gemini-3-flash-thinking` | Chain-of-thought reasoning |

## Patterns

### Independent queries

Each call is stateless — no context carried over.

```bash
ask-gemini "What is the difference between X and Y?"
```

### Follow-up with context

Use `--session <name>` for multi-turn conversations across separate calls:

```bash
# First exchange — creates or resumes session
ask-gemini --session review "Analyze this code: $(cat file.py)"

# Follow-up — Gemini remembers the code
ask-gemini --session review "Now fix the issues you found"
```

Manage sessions:

```bash
ask-gemini --sessions                # List all
ask-gemini --rm-session review       # Delete
```

### Interactive chat (web resume)

Use `--chat` to resume the latest conversation from `gemini.google.com`:

```bash
# Pipe messages
printf "Question 1\nQuestion 2\nexit\n" | ask-gemini --chat -m gemini-3-flash

# Start fresh instead of resuming
ask-gemini --chat --new-chat <<< "New topic"
```

In-chat commands: `/quit` (or `quit`/`exit`) to leave, `/new` or `/clear` to start over.

### Isolated multi-agent sessions

When multiple agents need independent conversations:

```bash
# Agent A
ask-gemini --session agent-a "Review this code..."

# Agent B — completely separate context
ask-gemini --session agent-b "Write docs summary..."
```

Agent A and B never share context.

## Rate limiting

Enabled by default — simulates human typing to avoid Google risk control:

- Typing delay: `len(message) * 0.05s`, clamped 1–5s with ±20% jitter
- Cooldown: 2s after response

Disable with `--no-rate-limit` for batch/automated use.

## Cookie setup

Requires Gemini browser cookies. Auto-detected from Chrome/Edge. If not found:

```bash
ask-gemini --cookie-setup
```

Or set `GEMINI_PSID` / `GEMINI_PSIDTS` in `.env`.

## Options

| Flag | Description |
|---|---|
| `-m, --model` | Model: `gemini-3-pro` (default), `gemini-3-flash`, `gemini-3-flash-thinking` |
| `--stream` / `--no-stream` | Stream or wait for full response |
| `--chat` | Resume latest web conversation |
| `--new-chat` | Start fresh (with `--chat`) |
| `--session <name>` | Named isolated session |
| `--sessions` | List sessions |
| `--rm-session <name>` | Delete session |
| `--no-rate-limit` | Disable typing delays |
| `--cookie-setup` | Cookie setup instructions |
