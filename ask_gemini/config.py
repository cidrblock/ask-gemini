import os
from pathlib import Path

import browser_cookie3
from dotenv import load_dotenv
from loguru import logger

# Load .env from project dir or ~/.ask-gemini/
_env_paths = [
    Path(__file__).parent.parent / ".env",
    Path.home() / ".ask-gemini" / ".env",
]
for _p in _env_paths:
    if _p.exists():
        load_dotenv(_p)
        break


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class GeminiCookies:
    PSID: str = _get("GEMINI_PSID")
    PSIDTS: str = _get("GEMINI_PSIDTS")

    @classmethod
    def _apply_cookiejar(cls, cj) -> bool:
        psid = psidts = ""
        for c in cj:
            if c.name == "__Secure-1PSID":
                psid = c.value
            elif c.name == "__Secure-1PSIDTS":
                psidts = c.value
        if psid and psidts:
            cls.PSID = psid
            cls.PSIDTS = psidts
            logger.debug("Loaded cookies from browser")
            return True
        return False

    @classmethod
    def try_load_from_browser(cls) -> bool:
        """Auto-detect cookies from Chrome, Chromium, Edge, Brave, or Firefox."""
        loaders = []
        for name in ("chrome", "chromium", "brave", "edge", "firefox"):
            loader = getattr(browser_cookie3, name, None)
            if loader is not None:
                loaders.append(loader)
        load = getattr(browser_cookie3, "load", None)
        if load is not None:
            loaders.append(load)

        for loader in loaders:
            try:
                cj = loader(domain_name=".google.com")
            except Exception:
                continue
            if cj and cls._apply_cookiejar(cj):
                return True

        return cls.is_configured()

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls.PSID and cls.PSIDTS)

    @classmethod
    def setup_instructions(cls) -> str:
        return """\
Gemini cookies not found. To set them up:

1. Open Chrome/Edge and go to https://gemini.google.com (make sure you're logged in)
2. Open DevTools (F12 or Cmd+Option+I on Mac)
3. Go to Application tab → Cookies → https://gemini.google.com
4. Copy the value of __Secure-1PSID and __Secure-1PSIDTS
5. Create a .env file in ~/.ask-gemini/ or this project's root:

   GEMINI_PSID=<paste __Secure-1PSID value>
   GEMINI_PSIDTS=<paste __Secure-1PSIDTS value>

Alternatively, ask-gemini can auto-detect cookies from your browser.
Make sure you're logged into Gemini in Chrome or Edge.
"""


class ProxyConfig:
    url: str = _get("PROXY_URL")


class RateLimitConfig:
    """Human-like typing/cooldown delays to avoid triggering Google risk control."""

    # Seconds per character of typed message (simulates human typing speed)
    typing_speed: float = float(_get("TYPING_SPEED", "0.05"))  # ~20 chars/sec

    # Min delay before sending (seconds), even for short messages
    min_delay: float = float(_get("MIN_DELAY", "1.0"))

    # Max delay before sending (seconds), caps the typing delay for long messages
    max_delay: float = float(_get("MAX_DELAY", "5.0"))

    # Cooldown after receiving a response before next request (seconds)
    cooldown: float = float(_get("COOLDOWN", "2.0"))

    # Whether rate limiting is enabled
    enabled: bool = _get("RATE_LIMIT", "1") == "1"
