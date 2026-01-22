#!/usr/bin/env python3
"""
Advanced Telegram Bot API Functions

Adds support for:
- Live location
- Inline queries
- Sticker management
- Video notes
- Contacts and venues
"""

import json
import os
import urllib.request
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
# Live Location
# =============================================================================

def send_live_location(chat_id: int, lat: float, lon: float, live_period: int = 3600) -> dict:
    """Send a live location that updates for live_period seconds (max 86400)."""
    return telegram_api("sendLocation", {
        "chat_id": chat_id, "latitude": lat, "longitude": lon, "live_period": live_period
    })


def edit_live_location(chat_id: int, message_id: int, lat: float, lon: float) -> dict:
    """Update a live location message."""
    return telegram_api("editMessageLiveLocation", {
        "chat_id": chat_id, "message_id": message_id, "latitude": lat, "longitude": lon
    })


def stop_live_location(chat_id: int, message_id: int) -> dict:
    """Stop a live location message."""
    return telegram_api("stopMessageLiveLocation", {"chat_id": chat_id, "message_id": message_id})


# =============================================================================
# Inline Queries
# =============================================================================

def answer_inline_query(
    query_id: str,
    results: List[dict],
    cache_time: int = 300,
    is_personal: bool = False
) -> dict:
    """Answer an inline query with results."""
    return telegram_api("answerInlineQuery", {
        "inline_query_id": query_id,
        "results": results,
        "cache_time": cache_time,
        "is_personal": is_personal
    })


def create_article_result(id: str, title: str, text: str, description: str = None) -> dict:
    """Create an article result for inline query."""
    result = {
        "type": "article",
        "id": id,
        "title": title,
        "input_message_content": {"message_text": text}
    }
    if description:
        result["description"] = description
    return result


# =============================================================================
# Sticker Management
# =============================================================================

def get_sticker_set(name: str) -> dict:
    """Get a sticker set by name."""
    return telegram_api("getStickerSet", {"name": name})


def upload_sticker_file(user_id: int, sticker: bytes, sticker_format: str = "static") -> dict:
    """Upload a sticker file for later use."""
    return telegram_api(
        "uploadStickerFile",
        {"user_id": user_id, "sticker_format": sticker_format},
        files={"sticker": ("sticker.webp", sticker, "image/webp")}
    )


def set_sticker_position(sticker: str, position: int) -> dict:
    """Set sticker position in set."""
    return telegram_api("setStickerPositionInSet", {"sticker": sticker, "position": position})


def delete_sticker(sticker: str) -> dict:
    """Delete a sticker from a set."""
    return telegram_api("deleteStickerFromSet", {"sticker": sticker})


# =============================================================================
# Video Notes (Circular Videos)
# =============================================================================

def send_video_note(chat_id: int, video: bytes, duration: int = None, length: int = None) -> dict:
    """Send a circular video note."""
    data = {"chat_id": chat_id}
    if duration:
        data["duration"] = duration
    if length:
        data["length"] = length
    return telegram_api("sendVideoNote", data, files={"video_note": ("video.mp4", video, "video/mp4")})


# =============================================================================
# Contacts & Venues
# =============================================================================

def send_contact(chat_id: int, phone: str, first_name: str, last_name: str = None) -> dict:
    """Send a contact."""
    params = {"chat_id": chat_id, "phone_number": phone, "first_name": first_name}
    if last_name:
        params["last_name"] = last_name
    return telegram_api("sendContact", params)


def send_venue(
    chat_id: int,
    lat: float,
    lon: float,
    title: str,
    address: str,
    foursquare_id: str = None
) -> dict:
    """Send a venue."""
    params = {
        "chat_id": chat_id,
        "latitude": lat,
        "longitude": lon,
        "title": title,
        "address": address
    }
    if foursquare_id:
        params["foursquare_id"] = foursquare_id
    return telegram_api("sendVenue", params)


# =============================================================================
# Custom Emoji
# =============================================================================

def get_custom_emoji_stickers(emoji_ids: List[str]) -> dict:
    """Get custom emoji stickers by IDs."""
    return telegram_api("getCustomEmojiStickers", {"custom_emoji_ids": emoji_ids})
