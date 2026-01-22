"""
Telegram message sending for Claudius.

Single source of truth for sending Telegram messages.
"""

import json
import os
import urllib.request
from pathlib import Path

# Load from environment or .env file
_env_loaded = False


def _ensure_env():
    """Load .env if not already in environment."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True

    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        env_file = Path("/opt/claudius/.env")
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    os.environ.setdefault(key.strip(), value)


def send_telegram(
    message: str,
    parse_mode: str = "Markdown",
    disable_preview: bool = True,
    chat_id: str = None
) -> bool:
    """Send a message via Telegram. Returns True on success."""
    _ensure_env()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        print("[Telegram] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Telegram] Error sending message: {e}")
        return False


def send_typing(chat_id: str = None) -> bool:
    """Send typing action indicator."""
    _ensure_env()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
    payload = {"chat_id": chat_id, "action": "typing"}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
