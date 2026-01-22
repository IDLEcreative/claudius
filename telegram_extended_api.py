#!/usr/bin/env python3
"""
Extended Telegram Bot API Functions

Adds support for:
- Bot management (getMe, commands, description)
- Message operations (delete, pin, forward, copy)
- Media (photo, video, animation, sticker, location)
- Reactions and polls
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")


def telegram_api(method: str, data: dict = None, files: dict = None) -> dict:
    """Call Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Token not configured"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    try:
        if files:
            boundary = "----PythonTelegramBoundary"
            body = b""
            if data:
                for key, value in data.items():
                    body += f"--{boundary}\r\n".encode()
                    body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                    body += f"{value}\r\n".encode()
            for key, (filename, content, content_type) in files.items():
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
                body += f"Content-Type: {content_type}\r\n\r\n".encode()
                body += content
                body += b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        else:
            body = json.dumps(data or {}).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Bot Management
# =============================================================================

def get_me() -> dict:
    """Get bot information."""
    return telegram_api("getMe")


def set_my_commands(commands: List[Dict[str, str]], scope: dict = None) -> dict:
    """Set bot commands."""
    params = {"commands": commands}
    if scope:
        params["scope"] = scope
    return telegram_api("setMyCommands", params)


def get_my_commands(scope: dict = None) -> dict:
    """Get bot commands."""
    params = {}
    if scope:
        params["scope"] = scope
    return telegram_api("getMyCommands", params)


def delete_my_commands(scope: dict = None) -> dict:
    """Delete bot commands."""
    params = {}
    if scope:
        params["scope"] = scope
    return telegram_api("deleteMyCommands", params)


def set_my_description(description: str = None, language_code: str = None) -> dict:
    """Set bot description."""
    params = {}
    if description:
        params["description"] = description
    if language_code:
        params["language_code"] = language_code
    return telegram_api("setMyDescription", params)


def get_my_description(language_code: str = None) -> dict:
    """Get bot description."""
    params = {}
    if language_code:
        params["language_code"] = language_code
    return telegram_api("getMyDescription", params)


def set_my_name(name: str = None, language_code: str = None) -> dict:
    """Set bot name."""
    params = {}
    if name:
        params["name"] = name
    if language_code:
        params["language_code"] = language_code
    return telegram_api("setMyName", params)


# =============================================================================
# Message Operations
# =============================================================================

def delete_message(chat_id: int, message_id: int) -> dict:
    """Delete a message."""
    return telegram_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def delete_messages(chat_id: int, message_ids: List[int]) -> dict:
    """Delete multiple messages."""
    return telegram_api("deleteMessages", {"chat_id": chat_id, "message_ids": message_ids})


def forward_message(chat_id: int, from_chat_id: int, message_id: int) -> dict:
    """Forward a message."""
    return telegram_api("forwardMessage", {
        "chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id
    })


def copy_message(chat_id: int, from_chat_id: int, message_id: int, caption: str = None) -> dict:
    """Copy a message."""
    params = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    if caption:
        params["caption"] = caption
    return telegram_api("copyMessage", params)


def pin_chat_message(chat_id: int, message_id: int, disable_notification: bool = False) -> dict:
    """Pin a message."""
    return telegram_api("pinChatMessage", {
        "chat_id": chat_id, "message_id": message_id, "disable_notification": disable_notification
    })


def unpin_chat_message(chat_id: int, message_id: int = None) -> dict:
    """Unpin a message."""
    params = {"chat_id": chat_id}
    if message_id:
        params["message_id"] = message_id
    return telegram_api("unpinChatMessage", params)


def unpin_all_chat_messages(chat_id: int) -> dict:
    """Unpin all messages."""
    return telegram_api("unpinAllChatMessages", {"chat_id": chat_id})


# =============================================================================
# Media
# =============================================================================

def send_photo(chat_id: int, photo: bytes, caption: str = None, filename: str = "photo.jpg") -> dict:
    """Send a photo."""
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    return telegram_api("sendPhoto", data, files={"photo": (filename, photo, "image/jpeg")})


def send_video(chat_id: int, video: bytes, caption: str = None, filename: str = "video.mp4") -> dict:
    """Send a video."""
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    return telegram_api("sendVideo", data, files={"video": (filename, video, "video/mp4")})


def send_animation(chat_id: int, animation: bytes, caption: str = None, filename: str = "animation.gif") -> dict:
    """Send an animation (GIF)."""
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    return telegram_api("sendAnimation", data, files={"animation": (filename, animation, "image/gif")})


def send_sticker(chat_id: int, sticker: str) -> dict:
    """Send a sticker by file_id or URL."""
    return telegram_api("sendSticker", {"chat_id": chat_id, "sticker": sticker})


def send_audio(chat_id: int, audio: bytes, caption: str = None, filename: str = "audio.mp3") -> dict:
    """Send an audio file."""
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    return telegram_api("sendAudio", data, files={"audio": (filename, audio, "audio/mpeg")})


def send_location(chat_id: int, latitude: float, longitude: float) -> dict:
    """Send a location."""
    return telegram_api("sendLocation", {"chat_id": chat_id, "latitude": latitude, "longitude": longitude})


# =============================================================================
# Reactions & Interactive
# =============================================================================

def set_message_reaction(chat_id: int, message_id: int, reaction: List[dict] = None, is_big: bool = False) -> dict:
    """Set reaction on a message."""
    params = {"chat_id": chat_id, "message_id": message_id, "is_big": is_big}
    if reaction:
        params["reaction"] = reaction
    return telegram_api("setMessageReaction", params)


def add_emoji_reaction(chat_id: int, message_id: int, emoji: str, is_big: bool = False) -> dict:
    """Add an emoji reaction."""
    return set_message_reaction(chat_id, message_id, [{"type": "emoji", "emoji": emoji}], is_big)


def send_poll(chat_id: int, question: str, options: List[str], is_anonymous: bool = True,
              poll_type: str = "regular", allows_multiple: bool = False) -> dict:
    """Send a poll."""
    return telegram_api("sendPoll", {
        "chat_id": chat_id,
        "question": question,
        "options": [{"text": opt} for opt in options],
        "is_anonymous": is_anonymous,
        "type": poll_type,
        "allows_multiple_answers": allows_multiple
    })


def stop_poll(chat_id: int, message_id: int) -> dict:
    """Stop a poll."""
    return telegram_api("stopPoll", {"chat_id": chat_id, "message_id": message_id})


def send_dice(chat_id: int, emoji: str = "🎲") -> dict:
    """Send a dice with random value."""
    return telegram_api("sendDice", {"chat_id": chat_id, "emoji": emoji})


def send_chat_action(chat_id: int, action: str) -> dict:
    """Send chat action (typing, upload_photo, etc)."""
    return telegram_api("sendChatAction", {"chat_id": chat_id, "action": action})


# =============================================================================
# Photo/Vision - Re-exported from telegram_vision.py
# =============================================================================

# Import vision functions from dedicated module
try:
    from telegram_vision import (
        download_file,
        download_photo,
        analyze_image_with_vision,
        process_telegram_photo,
    )
except ImportError:
    # Fallback stubs if module not available
    def download_file(file_id: str) -> Optional[bytes]:
        return None

    def download_photo(photo_sizes: List[dict]) -> Optional[bytes]:
        return None

    def analyze_image_with_vision(image_data: bytes, prompt: str = "") -> Optional[str]:
        return "Vision module not available"

    def process_telegram_photo(photo_sizes: List[dict], caption: str = None) -> str:
        return "Vision module not available"
