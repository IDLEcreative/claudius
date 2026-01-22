#!/usr/bin/env python3
"""
Claudius API Server with Telegram Integration
HTTP API for invoking Claudius (bare metal Claude Emperor) on the Hetzner host.

Claudius is the OVERSEER - handles all infrastructure tasks directly.
Now with direct Telegram webhook support for independence from Docker.

Usage:
    python3 claudius-api.py                    # Start on port 3100
    python3 claudius-api.py --port 3100        # Specify port

API Endpoints:
    POST /invoke    - Invoke Claudius with a prompt
                      Body: {"prompt": "your question"}
                      Headers: Authorization: Bearer <CRON_SECRET>
                      Returns: {"success": true, "response": "..."}

    POST /telegram  - Telegram webhook (direct, no Docker dependency)
                      Receives Telegram updates, responds via Telegram API

    GET /health     - Health check (no auth required)
                      Returns: {"status": "ok", "claudius": true}

    GET /memory     - Get Claudius memory
                      Headers: Authorization: Bearer <CRON_SECRET>
                      Returns: {"memory": "..."}

Authentication:
    All endpoints except /health and /telegram require Bearer token.
    Telegram webhook validates via chat_id (owner only).
    CRON_SECRET - Required for API auth
"""

import json
import subprocess
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import argparse
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
import threading
import psutil
import sys
import uuid
import asyncio

# Claude Agent SDK for async, non-blocking agent spawning
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# Add claudius package to path for modular imports
sys.path.insert(0, '/opt/claudius')
from claudius.core.agent import get_agent_pool, invoke_claudius as _invoke_claudius_pooled
from claudius.core.retry import retry_with_backoff
from claudius.memory.unified import get_unified_memory

# Configuration
CLAUDIUS_DIR = "/opt/claudius"
CLAUDE_MD = f"{CLAUDIUS_DIR}/CLAUDE.md"
MEMORY_MD = f"{CLAUDIUS_DIR}/MEMORY.md"
LOG_FILE = "/var/log/claudius-api.log"
MEMORY_MAX_ENTRIES = 100

# Authentication - uses same secret as Docker app
CRON_SECRET = os.environ.get("CRON_SECRET")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID = 7070679785  # Only respond to owner

# Web App Dashboard URL
WEB_APP_DASHBOARD_URL = "https://www.omniops.ai/claudius-dashboard.html"

# OpenAI for voice transcription
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# fal.ai MiniMax for TTS (Speech-02 HD)
FAL_KEY = os.environ.get("FAL_KEY")

# Supabase for conversation history persistence
SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
CONVERSATION_HISTORY_LIMIT = 20  # Last N messages to include as context
CONVERSATION_TIMEOUT_HOURS = 24  # Start fresh conversation after this idle time

# Clode API endpoint (inside Docker)
CLODE_API_URL = "http://localhost:3000/api/admin/claude"

# Special chat_id for API invoke requests (non-Telegram)
API_CHAT_ID = 0  # All API calls share this conversation

# Telegram deduplication (prevents double responses on retries)
processed_updates = {}
DEDUP_EXPIRY = 300  # 5 minutes

# Keywords that indicate codebase tasks (should delegate to Clode)
CODEBASE_KEYWORDS = [
    # Testing
    r"\btest\b", r"\btests\b", r"\btesting\b", r"\bjest\b", r"\bplaywright\b",
    # Build/compile
    r"\bbuild\b", r"\bcompile\b", r"\btypescript\b", r"\btsc\b", r"\bnpm\b", r"\bnode\b",
    # Code operations
    r"\bcode\b", r"\bfunction\b", r"\bcomponent\b", r"\bapi\b", r"\bendpoint\b",
    r"\brefactor\b", r"\blint\b", r"\beslint\b", r"\bformat\b",
    # Review/debug
    r"\breview\b", r"\bdebug\b", r"\bbug\b", r"\bfix\b", r"\berror\b",
    # Files/modules
    r"\.ts\b", r"\.tsx\b", r"\.js\b", r"\.json\b", r"\bpackage\.json\b",
    r"\blib/", r"\bapp/", r"\bcomponents/", r"\bsrc/",
    # Database/schema
    r"\bsupabase\b", r"\bdatabase\b", r"\bschema\b", r"\bmigration\b", r"\bsql\b",
]

# Memory monitoring configuration
MEMORY_CHECK_INTERVAL = 30  # Check every 30 seconds
MEMORY_RESTART_THRESHOLD_MB = 750  # Graceful restart at 750MB (before 800MB soft limit)

# Brain MCP HTTP API configuration
BRAIN_API_URL = "http://localhost:3000/api/admin/brain"  # Docker app

# Global server instance for graceful shutdown
server_instance = None

# Concurrency control - limit simultaneous Claude CLI invocations
MAX_CONCURRENT_CLAUDE_SESSIONS = 2  # Reduced: each Claude session spawns 5+ MCP processes
claude_semaphore = threading.Semaphore(MAX_CONCURRENT_CLAUDE_SESSIONS)
active_claude_sessions = 0
session_lock = threading.Lock()

# Track active session details for status endpoint
active_session_details = {}  # session_id -> {started_at, prompt_preview, chat_id}


# =============================================================================
# Supabase Conversation History Functions
# =============================================================================

def supabase_request(endpoint: str, method: str = "GET", data: dict = None, max_retries: int = 3) -> dict:
    """Make a request to Supabase REST API with exponential backoff retry."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        logger.warning("Supabase not configured - conversation history disabled")
        return {"error": "Supabase not configured"}

    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            headers = {
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }

            if data:
                body = json.dumps(data).encode("utf-8")
                req = urllib.request.Request(url, data=body, headers=headers, method=method)
            else:
                req = urllib.request.Request(url, headers=headers, method=method)

            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                # Exponential backoff with jitter
                import random
                delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random())
                logger.warning(f"Supabase retry {attempt + 1}/{max_retries}: {e} (waiting {delay:.1f}s)")
                time.sleep(delay)
            else:
                logger.error(f"Supabase request failed after {max_retries} retries: {e}")

        except Exception as e:
            logger.error(f"Supabase request failed: {e}")
            return {"error": str(e)}

    return {"error": str(last_error) if last_error else "Unknown error"}


def save_claudius_message(chat_id: int, role: str, content: str, metadata: dict = None) -> bool:
    """Save a message to Supabase conversation history."""
    data = {
        "chat_id": chat_id,
        "role": role,
        "content": content,
        "metadata": metadata or {}
    }

    result = supabase_request("claudius_messages", method="POST", data=data)

    if "error" in result and not isinstance(result, list):
        logger.error(f"Failed to save message: {result}")
        return False

    return True


def get_claudius_history(chat_id: int, limit: int = CONVERSATION_HISTORY_LIMIT) -> list:
    """Get recent conversation history from Supabase.

    Returns messages in chronological order (oldest first).
    Only includes messages from last CONVERSATION_TIMEOUT_HOURS.
    """
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return []

    try:
        # Calculate cutoff time for conversation timeout
        from datetime import timedelta
        cutoff = (datetime.utcnow() - timedelta(hours=CONVERSATION_TIMEOUT_HOURS)).isoformat()

        # Query with filters: chat_id match, recent messages, ordered by time desc, limited
        endpoint = (
            f"claudius_messages?"
            f"chat_id=eq.{chat_id}&"
            f"created_at=gte.{cutoff}&"
            f"order=created_at.desc&"
            f"limit={limit}"
        )

        result = supabase_request(endpoint)

        if isinstance(result, list):
            # Reverse to get chronological order (oldest first)
            return list(reversed(result))
        return []

    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        return []


def format_history_for_context(history: list) -> str:
    """Format conversation history as context for Claude."""
    if not history:
        return ""

    formatted = "\n\n--- CONVERSATION HISTORY ---\n"
    for msg in history:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        formatted += f"\n{role}: {content}\n"
    formatted += "\n--- END HISTORY ---\n"

    return formatted


def clear_old_messages(days: int = 7):
    """Delete messages older than specified days (cleanup utility)."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    try:
        url = f"{SUPABASE_URL}/rest/v1/claudius_messages?created_at=lt.{cutoff}"
        headers = {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        }
        req = urllib.request.Request(url, headers=headers, method="DELETE")

        with urllib.request.urlopen(req, timeout=10) as response:
            logger.info(f"Cleaned up messages older than {days} days")
            return True

    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        return False


# =============================================================================
# Telegram API Functions
# =============================================================================

def is_duplicate_update(update_id: int) -> bool:
    """Check if we've already processed this Telegram update."""
    now = time.time()
    # Clean expired entries
    for uid in list(processed_updates.keys()):
        if now - processed_updates[uid] > DEDUP_EXPIRY:
            del processed_updates[uid]
    return update_id in processed_updates


def mark_update_processed(update_id: int):
    """Mark a Telegram update as processed."""
    processed_updates[update_id] = time.time()


def telegram_api(method: str, data: dict = None, files: dict = None, max_retries: int = 3) -> dict:
    """Call Telegram Bot API with retry logic."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return {"ok": False, "error": "Token not configured"}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            if files:
                # Multipart form data for file uploads
                import io
                boundary = "----PythonBoundary"
                body = b""

                # Add regular fields
                if data:
                    for key, value in data.items():
                        body += f"--{boundary}\r\n".encode()
                        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                        body += f"{value}\r\n".encode()

                # Add files
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
                # JSON request
                body = json.dumps(data or {}).encode("utf-8")
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                import random
                delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random())
                logger.warning(f"Telegram API retry {attempt + 1}/{max_retries}: {e} (waiting {delay:.1f}s)")
                time.sleep(delay)
            else:
                logger.error(f"Telegram API failed after {max_retries} retries: {e}")

        except Exception as e:
            logger.error(f"Telegram API error: {e}")
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": str(last_error) if last_error else "Unknown error"}


def send_telegram_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    """Send a text message to Telegram."""
    # Split long messages (Telegram limit: 4096 chars)
    max_len = 4000
    chunks = []

    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find split point (prefer newline, then space)
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len

        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    for chunk in chunks:
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode
        })


def send_telegram_typing(chat_id: int):
    """Show typing indicator."""
    telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})



def send_web_app_button(chat_id: int, text: str, button_text: str, web_app_url: str) -> dict:
    """Send a message with Web App button."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Token not configured"}
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {
                    "text": button_text,
                    "web_app": {"url": web_app_url}
                }
            ]]
        }
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Web App button send failed: {e}")
        return {"ok": False, "error": str(e)}


def send_telegram_voice(chat_id: int, audio_data: bytes):
    """Send a voice message to Telegram."""
    telegram_api("sendVoice", {"chat_id": chat_id}, files={
        "voice": ("response.ogg", audio_data, "audio/ogg")
    })


def get_telegram_file(file_id: str) -> Optional[bytes]:
    """Download a file from Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return None

    try:
        # Get file path
        result = telegram_api("getFile", {"file_id": file_id})
        if not result.get("ok"):
            return None

        file_path = result["result"]["file_path"]

        # Download file
        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        with urllib.request.urlopen(download_url, timeout=30) as response:
            return response.read()

    except Exception as e:
        logger.error(f"Failed to download Telegram file: {e}")
        return None


def transcribe_voice(file_id: str) -> Optional[str]:
    """Transcribe voice message using OpenAI Whisper."""
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY not configured for transcription")
        return None

    audio_data = get_telegram_file(file_id)
    if not audio_data:
        return None

    try:
        # Build multipart form data for OpenAI
        boundary = "----WhisperBoundary"
        body = b""

        # Add model field
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="model"\r\n\r\n'
        body += b"whisper-1\r\n"

        # Add file
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n'
        body += b"Content-Type: audio/ogg\r\n\r\n"
        body += audio_data
        body += b"\r\n"

        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            method="POST"
        )
        req.add_header("Authorization", f"Bearer {OPENAI_API_KEY}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("text")

    except Exception as e:
        logger.error(f"Voice transcription failed: {e}")
        return None


def clean_text_for_speech(text: str) -> str:
    """Strip markdown formatting for natural TTS."""
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '... code block ...', text)
    # Remove inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove bold/italic
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    # Remove URLs
    text = re.sub(r'https?://[^\s]+', 'link', text)
    # Remove headers
    text = re.sub(r'^#{1,3}\s+', '', text, flags=re.MULTILINE)
    # Remove bullet points
    text = re.sub(r'^[-*•]\s+', '', text, flags=re.MULTILINE)
    # Clean whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def add_natural_speech_markers(text: str) -> str:
    """Add natural speech markers for more realistic TTS.

    MiniMax supports:
    - <#0.5#> for pauses (in seconds)
    - emotion parameter for overall tone
    - Natural punctuation is interpreted well
    """
    # Add slight pauses after commas for natural rhythm
    text = re.sub(r',\s*', ', <#0.2#> ', text)

    # Add pauses after sentence endings
    text = re.sub(r'\.\s+', '. <#0.4#> ', text)
    text = re.sub(r'\?\s+', '? <#0.3#> ', text)
    text = re.sub(r'!\s+', '! <#0.3#> ', text)

    # Add pause before "but", "however", "although" for emphasis
    text = re.sub(r'\s+(but|however|although|though)\s+', r' <#0.3#> \1 ', text, flags=re.IGNORECASE)

    # Add slight pause after "well", "so", "now" at start of sentences
    text = re.sub(r'^(Well|So|Now|Okay|Right|Alright)\s+', r'\1 <#0.2#> ', text, flags=re.IGNORECASE)
    text = re.sub(r'\.\s+(Well|So|Now|Okay|Right|Alright)\s+', r'. <#0.4#> \1 <#0.2#> ', text, flags=re.IGNORECASE)

    # Dramatic pause before important words
    text = re.sub(r'\s+(actually|absolutely|definitely|certainly)\s+', r' <#0.2#> \1 ', text, flags=re.IGNORECASE)

    # Add pause for ellipsis (thinking/trailing off)
    text = re.sub(r'\.\.\.', ' <#0.6#> ', text)

    # Pause for em-dashes (interjections)
    text = re.sub(r'\s*—\s*', ' <#0.3#> ', text)
    text = re.sub(r'\s*--\s*', ' <#0.3#> ', text)

    # Clean up any double pauses
    text = re.sub(r'(<#[\d.]+#>\s*)+', lambda m: m.group(0).split()[-1] + ' ', text)

    return text.strip()


def text_to_speech(text: str) -> Optional[bytes]:
    """Convert text to speech using fal.ai MiniMax Speech-02 Turbo.

    MiniMax Turbo is faster than HD, supports up to 5,000 characters realtime.
    Better for chat interfaces where speed matters.
    """
    if not FAL_KEY:
        logger.warning("FAL_KEY not configured for TTS")
        return None

    try:
        # Clean markdown, then add natural speech markers
        speech_text = clean_text_for_speech(text)
        speech_text = add_natural_speech_markers(speech_text)

        # MiniMax handles up to 5000 chars realtime
        if len(speech_text) > 5000:
            speech_text = speech_text[:5000] + "... and more."

        payload = json.dumps({
            "text": speech_text,
            "voice_setting": {
                "voice_id": "Imposing_Manner",  # Male voice with gravitas
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0
            },
            "emotion": "auto"  # Let MiniMax detect emotion from context
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://fal.run/fal-ai/minimax/speech-02-turbo",
            data=payload,
            method="POST"
        )
        req.add_header("Authorization", f"Key {FAL_KEY}")
        req.add_header("Content-Type", "application/json")

        # Longer timeout for longer text
        timeout = 120 if len(speech_text) > 2000 else 60
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))

        # Download the audio file
        audio_url = result.get("audio", {}).get("url")
        if not audio_url:
            logger.error("No audio URL in fal.ai MiniMax response")
            return None

        with urllib.request.urlopen(audio_url, timeout=60) as audio_response:
            return audio_response.read()

    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return None


# =============================================================================
# Brain MCP HTTP Client (Access Brain via Docker HTTP API)
# =============================================================================

def call_brain_api(operation: str, params: dict = None, max_retries: int = 3) -> dict:
    """Call Brain MCP via HTTP API with retry logic."""
    if not ADMIN_SECRET:
        logger.warning("ADMIN_SECRET not configured - Brain API unavailable")
        return {"success": False, "error": "ADMIN_SECRET not configured"}

    data = json.dumps({
        "operation": operation,
        "params": params or {}
    }).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {ADMIN_SECRET}",
        "Content-Type": "application/json"
    }

    last_error = None

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(BRAIN_API_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                import random
                delay = min(1.0 * (2 ** attempt), 10.0) * (0.5 + random.random())
                logger.warning(f"Brain API retry {attempt + 1}/{max_retries}: {e} (waiting {delay:.1f}s)")
                time.sleep(delay)
            else:
                logger.error(f"Brain API failed after {max_retries} retries: {e}")

        except Exception as e:
            logger.error(f"Brain API error: {str(e)}")
            return {"success": False, "error": str(e)}

    error_msg = str(last_error) if last_error else "Unknown error"
    return {"success": False, "error": error_msg}


def store_memory_via_http(content: str, trigger: str, resolution: str = None) -> bool:
    """Store a memory via Brain HTTP API."""
    result = call_brain_api("store_memory", {
        "content": content,
        "triggerSituation": trigger,
        "resolution": resolution,
        "memoryType": "procedural",  # Claudius stores procedural memories (how-to)
        "salienceSignals": {"sourceAgent": "claudius"}
    })

    if result.get("success"):
        memory_id = result.get("result", {}).get("memoryId", "unknown")
        logger.info(f"[Brain] Memory stored: {memory_id}")
        return True
    else:
        logger.error(f"[Brain] Failed to store memory: {result.get('error')}")
        return False


def recall_memories_via_http(query: str, limit: int = 5) -> list:
    """Recall memories via Brain HTTP API."""
    result = call_brain_api("recall_memories", {
        "query": query,
        "limit": limit
    })

    if result.get("success"):
        memories = result.get("result", {}).get("memories", [])
        logger.info(f"[Brain] Recalled {len(memories)} memories")
        return memories
    else:
        logger.error(f"[Brain] Failed to recall memories: {result.get('error')}")
        return []


def get_brain_stats_via_http() -> dict:
    """Get Brain MCP stats via HTTP API."""
    result = call_brain_api("get_stats")

    if result.get("success"):
        return result.get("result", {})
    else:
        logger.error(f"[Brain] Failed to get stats: {result.get('error')}")
        return {}


# =============================================================================
# Core Claudius Functions
# =============================================================================

def is_codebase_task(prompt: str) -> bool:
    """Detect if a prompt is about codebase operations (should delegate to Clode)."""
    prompt_lower = prompt.lower()
    for pattern in CODEBASE_KEYWORDS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def call_clode(prompt: str, model: str = "sonnet") -> dict:
    """Delegate codebase operations to Clode (inside Docker container)."""
    if not ADMIN_SECRET:
        return {"success": False, "error": "ADMIN_SECRET not configured for Clode delegation"}

    data = json.dumps({"prompt": prompt, "model": model}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {ADMIN_SECRET}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(CLODE_API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
            output = result.get("output", result.get("response", ""))
            return {"success": True, "response": output, "delegated_to": "clode"}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"Clode API error: {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Clode connection error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": f"Clode delegation failed: {str(e)}"}


def invoke_claudius(prompt: str, conversation_history: list = None, session_id: str = None) -> dict:
    """Invoke Claudius CLI with a prompt and optional session resumption.

    Now uses AgentPool for proper queueing and resource management.

    Args:
        prompt: The current user message
        conversation_history: List of previous messages [{role, content}, ...] (fallback context)
        session_id: Optional Claude CLI session ID to resume

    Returns:
        dict with 'response' and 'session_id' keys
    """
    # Delegate to the AgentPool for proper queueing and concurrency control
    return _invoke_claudius_pooled(prompt, conversation_history, session_id)


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging():
    logger = logging.getLogger("claudius")
    logger.setLevel(logging.INFO)

    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s'
        ))
        logger.addHandler(file_handler)
    except PermissionError:
        pass

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(message)s', datefmt='%Y-%m-%dT%H:%M:%SZ'
    ))
    logger.addHandler(console_handler)

    return logger


logger = setup_logging()


# =============================================================================
# Memory Monitoring (Graceful Restart Before Hitting Limits)
# =============================================================================

def get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception as e:
        logger.error(f"Failed to get memory usage: {e}")
        return 0.0


def check_resources_available() -> tuple[bool, str]:
    """Check if system has enough resources to spawn a new Claude CLI process.

    Returns:
        (ok, message) - ok=True if resources available, False otherwise with reason
    """
    try:
        # Check available memory (need at least 500MB free)
        mem = psutil.virtual_memory()
        available_mb = mem.available / (1024 * 1024)
        if available_mb < 500:
            return False, f"Low memory: {available_mb:.0f}MB available (need 500MB)"

        # Check number of running processes (soft limit to prevent fork bombs)
        # Claude CLI + MCP processes can spawn many children
        claude_procs = len([p for p in psutil.process_iter(['name', 'cmdline'])
                          if 'claude' in str(p.info.get('cmdline', [])).lower()])
        if claude_procs > 10:
            return False, f"Too many Claude processes running: {claude_procs}"

        # Check overall process count
        total_procs = len(list(psutil.process_iter()))
        if total_procs > 500:
            return False, f"System overloaded: {total_procs} processes running"

        # Check CPU load (1-minute average)
        load_avg = os.getloadavg()[0]
        cpu_count = os.cpu_count() or 1
        if load_avg > cpu_count * 2:
            return False, f"High load: {load_avg:.1f} (threshold: {cpu_count * 2})"

        return True, "Resources OK"

    except Exception as e:
        logger.error(f"Resource check failed: {e}")
        # Fail closed - don't spawn if we can't verify resources
        return False, f"Resource check unavailable: {e}"


def memory_monitor_thread():
    """Background thread that monitors memory usage and triggers graceful restart.

    Runs every MEMORY_CHECK_INTERVAL seconds.
    If memory exceeds MEMORY_RESTART_THRESHOLD_MB, initiates graceful shutdown.
    Systemd will automatically restart the service with fresh memory.
    """
    global server_instance

    while True:
        try:
            time.sleep(MEMORY_CHECK_INTERVAL)

            memory_mb = get_memory_usage_mb()

            # Log memory usage every 5 minutes (10 checks)
            if int(time.time()) % (MEMORY_CHECK_INTERVAL * 10) == 0:
                logger.info(f"Memory usage: {memory_mb:.1f} MB (threshold: {MEMORY_RESTART_THRESHOLD_MB} MB)")

            # Check if we're approaching the systemd soft limit
            if memory_mb > MEMORY_RESTART_THRESHOLD_MB:
                logger.warning(
                    f"Memory usage {memory_mb:.1f} MB exceeds threshold {MEMORY_RESTART_THRESHOLD_MB} MB. "
                    f"Initiating graceful restart to prevent OOM..."
                )

                if server_instance:
                    # Graceful shutdown - systemd will restart us automatically
                    threading.Thread(target=server_instance.shutdown, daemon=True).start()
                    logger.info("Graceful shutdown initiated. Systemd will restart the service.")
                else:
                    # Fallback: exit process (systemd will restart)
                    logger.warning("Server instance not available, exiting process for restart...")
                    sys.exit(0)

                break  # Exit monitoring thread after triggering restart

        except Exception as e:
            logger.error(f"Memory monitor error: {e}", exc_info=True)
            # Don't crash the monitor thread - keep monitoring


# =============================================================================
# HTTP Request Handler
# =============================================================================

class ClaudiusHandler(BaseHTTPRequestHandler):
    # Upgrade to HTTP/1.1 for keep-alive connections (reduces connection overhead)
    protocol_version = "HTTP/1.1"

    ALLOWED_ORIGINS = ["http://172.18.0.1", "http://localhost", "http://127.0.0.1"]

    def log_message(self, format, *args):
        logger.info(args[0])

    def validate_auth(self):
        """Validate Bearer token authentication."""
        if not CRON_SECRET:
            logger.error("CRON_SECRET not configured - rejecting request")
            return False

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False

        token = auth_header[7:]
        return token == CRON_SECRET

    def get_cors_origin(self):
        origin = self.headers.get("Origin", "")
        for allowed in self.ALLOWED_ORIGINS:
            if origin.startswith(allowed):
                return origin
        return "http://172.18.0.1"

    def send_json(self, data, status=200):
        response_body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", self.get_cors_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(response_body)

    def do_OPTIONS(self):
        self.send_json({})

    def log_to_memory(self, action, content):
        """Log to memory file with automatic rotation."""
        if not os.path.exists(MEMORY_MD):
            return

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        new_line = f"| {timestamp} | {action} | {content[:50]}... |\n"

        try:
            with open(MEMORY_MD, "r") as f:
                lines = f.readlines()

            header_lines = []
            table_lines = []

            for line in lines:
                if line.strip().startswith("|") and "Date" not in line and "---" not in line:
                    table_lines.append(line)
                else:
                    header_lines.append(line)

            table_lines.append(new_line)
            if len(table_lines) > MEMORY_MAX_ENTRIES:
                table_lines = table_lines[-MEMORY_MAX_ENTRIES:]

            with open(MEMORY_MD, "w") as f:
                f.writelines(header_lines)
                f.writelines(table_lines)

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")

    def do_GET(self):
        if self.path == "/health":
            claudius_ready = os.path.exists(CLAUDE_MD)
            memory_mb = get_memory_usage_mb()
            self.send_json({
                "status": "ok",
                "claudius": claudius_ready,
                "memory_exists": os.path.exists(MEMORY_MD),
                "auth_configured": bool(CRON_SECRET),
                "telegram_configured": bool(TELEGRAM_BOT_TOKEN),
                "tts_configured": bool(FAL_KEY),
                "transcription_configured": bool(OPENAI_API_KEY),
                "supabase_configured": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
                "conversation_history_enabled": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
                "memory_mb": round(memory_mb, 1),
                "memory_threshold_mb": MEMORY_RESTART_THRESHOLD_MB
            })
        elif self.path == "/status":
            # Show active sessions - no auth needed for quick checks
            pool_status = get_agent_pool().get_status()
            pool_status["status"] = "busy" if pool_status["active_sessions"] > 0 else "idle"
            self.send_json(pool_status)
        elif self.path == "/metrics":
            # Metrics endpoint - auth required to protect system info
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return
            try:
                memory_mb = get_memory_usage_mb()
                mem = psutil.virtual_memory()
                cpu_percent = psutil.cpu_percent(interval=0.1)
                load_avg = os.getloadavg()

                # Count claude-related processes
                claude_procs = len([
                    p for p in psutil.process_iter(['cmdline'])
                    if 'claude' in str(p.info.get('cmdline', [])).lower()
                ])

                # Get uptime
                import time
                boot_time = psutil.boot_time()
                uptime_seconds = int(time.time() - boot_time)

                pool_status = get_agent_pool().get_status()
                self.send_json({
                    "timestamp": datetime.utcnow().isoformat(),
                    "active_sessions": pool_status["active_sessions"],
                    "max_sessions": pool_status["max_sessions"],
                    "queue_depth": pool_status["queue_size"],
                        "process_memory_mb": round(memory_mb, 1),
                        "system_memory": {
                            "total_mb": round(mem.total / (1024 * 1024), 0),
                            "available_mb": round(mem.available / (1024 * 1024), 0),
                            "percent_used": mem.percent
                        },
                        "cpu_percent": cpu_percent,
                        "load_avg": {
                            "1min": round(load_avg[0], 2),
                            "5min": round(load_avg[1], 2),
                            "15min": round(load_avg[2], 2)
                        },
                        "claude_processes": claude_procs,
                        "uptime_seconds": uptime_seconds,
                        "status": "healthy" if memory_mb < MEMORY_RESTART_THRESHOLD_MB else "warning"
                    })
            except Exception as e:
                logger.error(f"Metrics error: {e}")
                self.send_json({"error": str(e)}, 500)
        elif self.path == "/memory":
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if os.path.exists(MEMORY_MD):
                with open(MEMORY_MD, "r") as f:
                    self.send_json({"memory": f.read()})
            else:
                self.send_json({"memory": None, "error": "No memory file"}, 404)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        # Route to appropriate handler
        if self.path == "/telegram":
            self.handle_telegram(data)
        elif self.path == "/invoke":
            self.handle_invoke(data)
        elif self.path == "/email/action":
            self.handle_email_action(data)
        elif self.path == "/spawn":
            self.handle_spawn(data)
        elif self.path == "/spawn/status":
            self.handle_spawn_status(data)
        else:
            self.send_json({"error": "Not found"}, 404)

    def handle_telegram(self, data: dict):
        """Handle Telegram webhook - no Docker dependency!

        CRITICAL: This entire handler is wrapped in try/except to prevent
        any exception from crashing the server. Always return {"ok": True}
        to Telegram to prevent retries.
        """
        try:
            update_id = data.get("update_id")

            # Deduplication
            if update_id and is_duplicate_update(update_id):
                logger.info(f"[Telegram] Skipping duplicate update_id: {update_id}")
                self.send_json({"ok": True})
                return

            if update_id:
                mark_update_processed(update_id)

            # ============ SECURITY: Handle all update types with owner check ============

            # Handle callback queries (button clicks) - with security
            callback_query = data.get("callback_query")
            if callback_query:
                cb_user_id = callback_query.get("from", {}).get("id")
                if cb_user_id != OWNER_CHAT_ID:
                    logger.warning(f"[SECURITY] Callback from non-owner BLOCKED: {cb_user_id}")
                    self.send_json({"ok": True})
                    return
                
                # Process callback (e.g., escalation responses)
                callback_data = callback_query.get("data", "")
                callback_id = callback_query.get("id")
                cb_chat_id = callback_query.get("message", {}).get("chat", {}).get("id", OWNER_CHAT_ID)
                logger.info(f"[Telegram] Callback from owner: {callback_data}")
                
                # Acknowledge the callback
                telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})
                
                # Handle escalation responses (esc:ESC-123:approve)
                if callback_data.startswith("esc:"):
                    parts = callback_data.split(":")
                    if len(parts) >= 3:
                        esc_id, action = parts[1], parts[2]
                        send_telegram_message(cb_chat_id, f"✅ Escalation {esc_id}: {action}")

                # Handle email action buttons (email:snooze, email:archive, etc.)
                elif callback_data.startswith("email:"):
                    try:
                        from email_actions import handle_email_callback
                        handled = handle_email_callback(callback_query)
                        if handled:
                            logger.info(f"[Telegram] Email action handled: {callback_data}")
                        else:
                            logger.warning(f"[Telegram] Email action not handled: {callback_data}")
                    except ImportError:
                        logger.error("[Telegram] email_actions module not available")
                        send_telegram_message(cb_chat_id, "Email actions module not available")
                    except Exception as e:
                        logger.error(f"[Telegram] Email action error: {e}")
                        send_telegram_message(cb_chat_id, f"Error handling email action: {str(e)[:50]}")

                self.send_json({"ok": True})
                return

            # Handle inline queries - BLOCKED for security (owner-only bot)
            inline_query = data.get("inline_query")
            if inline_query:
                iq_user_id = inline_query.get("from", {}).get("id")
                if iq_user_id != OWNER_CHAT_ID:
                    logger.warning(f"[SECURITY] Inline query from non-owner BLOCKED: {iq_user_id}")
                else:
                    logger.info(f"[Telegram] Inline query from owner (not implemented): {inline_query.get('query', '')}")
                self.send_json({"ok": True})
                return

            # Handle channel posts - BLOCKED
            if data.get("channel_post") or data.get("edited_channel_post"):
                logger.warning("[SECURITY] Channel post BLOCKED - owner-only bot")
                self.send_json({"ok": True})
                return

            # Handle edited messages - apply same owner check
            message = data.get("message") or data.get("edited_message", {})
            if not message:
                logger.warning("[SECURITY] Update with no message BLOCKED")
                self.send_json({"ok": True})
                return

            chat_id = message.get("chat", {}).get("id")

            # Security: Reject if no chat_id
            if not chat_id:
                logger.warning("[SECURITY] Message with no chat_id BLOCKED")
                self.send_json({"ok": True})
                return

            # Security: Reject if not owner
            if chat_id != OWNER_CHAT_ID:
                logger.warning(f"[SECURITY] Message from non-owner BLOCKED: {chat_id}")
                self.send_json({"ok": True})
                return

            # ============ END SECURITY - Owner verified ============

            # Extract message content
            respond_with_voice = False
            text = message.get("text")

            # Handle voice messages
            if not text and message.get("voice"):
                file_id = message["voice"]["file_id"]
                logger.info(f"[Telegram] Voice message received, transcribing...")
                text = transcribe_voice(file_id)
                respond_with_voice = True

                if text:
                    send_telegram_message(chat_id, f'🎤 "{text}"')
                else:
                    send_telegram_message(chat_id, "Sorry, couldn't transcribe that voice message.")
                    self.send_json({"ok": True})
                    return

            # Handle photo messages
            if not text and message.get("photo"):
                logger.info("[Telegram] Photo received, analyzing with vision...")
                send_telegram_typing(chat_id)

                try:
                    from telegram_vision import process_telegram_photo
                    photo_sizes = message["photo"]
                    caption = message.get("caption", "")

                    # Use caption as prompt if provided, otherwise describe image
                    analysis = process_telegram_photo(photo_sizes, caption)

                    if analysis:
                        send_telegram_message(chat_id, f"📷 **Image Analysis:**\n\n{analysis}", parse_mode="Markdown")
                    else:
                        send_telegram_message(chat_id, "Sorry, couldn't analyze that image.")
                except ImportError:
                    send_telegram_message(chat_id, "Photo analysis not available - telegram_extended_api.py not found")
                except Exception as e:
                    logger.error(f"[Telegram] Photo processing failed: {e}")
                    send_telegram_message(chat_id, f"Photo analysis failed: {str(e)[:100]}")

                self.send_json({"ok": True})
                return

            if not text:
                self.send_json({"ok": True})
                return

            logger.info(f"[Telegram] Message: {text[:50]}...")

            # Show typing indicator
            send_telegram_typing(chat_id)

            # Load conversation history from Supabase
            conversation_history = get_claudius_history(chat_id)
            if conversation_history:
                logger.info(f"[Telegram] Loaded {len(conversation_history)} messages of history")

            # Save user message to Supabase (before invoking, so it's in history)
            save_claudius_message(
                chat_id=chat_id,
                role="user",
                content=text,
                metadata={"voice": respond_with_voice}
            )

            # Log to memory file (legacy)
            self.log_to_memory("Telegram", text[:50])

            # Handle /dashboard command - send Web App button
            if text.strip().lower() == '/dashboard':
                send_web_app_button(
                    chat_id=chat_id,
                    text="📊 Open the Claudius Dashboard to view server status, memory timeline, and more.",
                    button_text="🖥️ Open Dashboard",
                    web_app_url=WEB_APP_DASHBOARD_URL
                )
                self.send_json({"ok": True})
                return

            # Invoke Claudius WITH conversation history (no session for Telegram - uses Supabase history)
            result = invoke_claudius(text, conversation_history=conversation_history)
            response = result.get("response", "No response")
            logger.info(f"[Telegram] Response: {response[:50]}...")

            # Save assistant response to Supabase
            save_claudius_message(
                chat_id=chat_id,
                role="assistant",
                content=response
            )

            # Log response to memory file (legacy)
            self.log_to_memory("Response", response[:50])

            # Send response - voice if input was voice
            if respond_with_voice and FAL_KEY:
                try:
                    audio = text_to_speech(response)
                    if audio:
                        send_telegram_voice(chat_id, audio)
                        logger.info("[Telegram] Sent voice response")
                    else:
                        # TTS failed, fall back to text
                        send_telegram_message(chat_id, response)
                except Exception as e:
                    logger.error(f"[Telegram] TTS failed: {e}")
                    send_telegram_message(chat_id, response)
            else:
                send_telegram_message(chat_id, response)

            self.send_json({"ok": True})

        except Exception as e:
            # CRITICAL: Catch ALL exceptions to prevent server crash
            logger.error(f"[Telegram] CRITICAL ERROR in handler: {e}", exc_info=True)
            try:
                # Try to notify owner about the error
                send_telegram_message(OWNER_CHAT_ID, f"⚠️ Error processing request: {str(e)[:100]}")
            except Exception:
                pass  # Don't let notification failure crash us either
            # Always return OK to Telegram to prevent infinite retries
            self.send_json({"ok": True})

    def handle_invoke(self, data: dict):
        """Handle API invoke request with conversation history and session support."""
        if not self.validate_auth():
            logger.warning(f"Unauthorized /invoke request from {self.client_address[0]}")
            self.send_json({"error": "Unauthorized"}, 401)
            return

        prompt = data.get("prompt", "").strip()
        if not prompt:
            self.send_json({"success": False, "error": "No prompt provided"}, 400)
            return

        # Get session ID from request (for --resume support)
        session_id = data.get("sessionId") or data.get("session_id")

        logger.info(f"Processing request: {prompt[:50]}..." + (f" (session: {session_id})" if session_id else ""))

        # Load conversation history from Supabase (shared across all API calls)
        # Used as fallback context if no session_id provided
        conversation_history = get_claudius_history(API_CHAT_ID)
        if conversation_history:
            logger.info(f"[API] Loaded {len(conversation_history)} messages of history")

        # Save user message to Supabase
        save_claudius_message(
            chat_id=API_CHAT_ID,
            role="user",
            content=prompt,
            metadata={"source": "api", "session_id": session_id}
        )

        # Log to memory file (legacy)
        self.log_to_memory("API Request", prompt[:50])

        # Invoke Claudius with conversation history and optional session resumption
        result = invoke_claudius(
            prompt,
            conversation_history=conversation_history,
            session_id=session_id
        )

        response_text = result.get("response", "No response")
        new_session_id = result.get("session_id")

        # Save assistant response to Supabase
        save_claudius_message(
            chat_id=API_CHAT_ID,
            role="assistant",
            content=response_text,
            metadata={"source": "api", "session_id": new_session_id}
        )

        self.log_to_memory("Response", response_text[:50])
        logger.info(f"Request completed successfully" + (f" (session: {new_session_id})" if new_session_id else ""))

        # Return response with session ID for future --resume calls
        response_data = {"success": True, "response": response_text}
        if new_session_id:
            response_data["sessionId"] = new_session_id
        self.send_json(response_data)

    def handle_email_action(self, data: dict):
        """Handle direct email action via HTTP API.

        POST /email/action
        Body: {"telegram_message_id": 12345, "action": "delete"}
        Actions: snooze, delete, show, draft

        This allows triggering email actions programmatically without
        going through Telegram button callbacks.
        """
        # Require authentication
        if not self.validate_auth():
            logger.warning(f"Unauthorized /email/action request from {self.client_address[0]}")
            self.send_json({"success": False, "error": "Unauthorized"}, 401)
            return

        telegram_message_id = data.get("telegram_message_id")
        action = data.get("action", "").strip().lower()

        if not telegram_message_id:
            self.send_json({"success": False, "error": "Missing telegram_message_id"}, 400)
            return

        if not action:
            self.send_json({"success": False, "error": "Missing action"}, 400)
            return

        try:
            telegram_message_id = int(telegram_message_id)
        except (TypeError, ValueError):
            self.send_json({"success": False, "error": "telegram_message_id must be an integer"}, 400)
            return

        logger.info(f"[Email Action] Processing {action} for message {telegram_message_id}")

        try:
            from email_actions import handle_direct_action
            result = handle_direct_action(telegram_message_id, action)

            if result.get("success"):
                logger.info(f"[Email Action] Success: {action} for message {telegram_message_id}")
                self.send_json(result)
            else:
                logger.warning(f"[Email Action] Failed: {result.get('error')}")
                self.send_json(result, 400)

        except ImportError:
            logger.error("[Email Action] email_actions module not available")
            self.send_json({"success": False, "error": "email_actions module not available"}, 500)
        except Exception as e:
            logger.error(f"[Email Action] Error: {e}")
            self.send_json({"success": False, "error": str(e)}, 500)

    def handle_spawn(self, data: dict):
        """Spawn a background agent - returns immediately, agent runs async.

        POST /spawn
        Body: {"prompt": "Fix LOC violations", "working_dir": "/opt/omniops"}
        Headers: Authorization: Bearer <CRON_SECRET>
        Returns: {"success": true, "task_id": "abc123"}

        The spawned agent:
        - Uses Max plan tokens (not API credits)
        - Runs completely async via Claude SDK
        - Notifies via Telegram when complete (optional)
        """
        if not self.validate_auth():
            logger.warning(f"Unauthorized /spawn request from {self.client_address[0]}")
            self.send_json({"error": "Unauthorized"}, 401)
            return

        prompt = data.get("prompt", "").strip()
        if not prompt:
            self.send_json({"success": False, "error": "No prompt provided"}, 400)
            return

        working_dir = data.get("working_dir", "/opt/omniops")
        notify = data.get("notify", True)  # Send Telegram notification on completion

        try:
            import asyncio
            import sys
            sys.path.insert(0, '/opt/claudius')
            from lib.async_agent_spawner import spawn_agent, get_queue_status

            # Define notification callback
            async def notify_completion(task):
                if notify:
                    status_emoji = "✅" if task.status.value == "completed" else "❌"
                    message = f"{status_emoji} Background agent finished\n\nTask: {task.prompt[:100]}...\nStatus: {task.status.value}"
                    if task.error:
                        message += f"\nError: {task.error[:200]}"
                    send_telegram_message(OWNER_CHAT_ID, message)

            # Spawn async (non-blocking)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            task_id = loop.run_until_complete(
                spawn_agent(prompt, callback=notify_completion, working_dir=working_dir)
            )

            queue_status = get_queue_status()
            logger.info(f"[Spawn] Created task {task_id}: {prompt[:50]}...")

            self.send_json({
                "success": True,
                "task_id": task_id,
                "queue_status": queue_status
            })

        except Exception as e:
            logger.error(f"[Spawn] Error: {e}")
            self.send_json({"success": False, "error": str(e)}, 500)

    def handle_spawn_status(self, data: dict):
        """Check status of spawned agents.

        POST /spawn/status
        Body: {"task_id": "abc123"} or {} for queue status
        Headers: Authorization: Bearer <CRON_SECRET>
        """
        if not self.validate_auth():
            self.send_json({"error": "Unauthorized"}, 401)
            return

        task_id = data.get("task_id")

        try:
            import asyncio
            import sys
            sys.path.insert(0, '/opt/claudius')
            from lib.async_agent_spawner import check_agent_status, get_queue_status, get_agent_result

            if task_id:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                status = loop.run_until_complete(check_agent_status(task_id))

                # Include result if completed
                if status.get("status") == "completed":
                    result = loop.run_until_complete(get_agent_result(task_id))
                    status["result_preview"] = result[:500] if result else None

                self.send_json(status)
            else:
                self.send_json(get_queue_status())

        except Exception as e:
            logger.error(f"[Spawn Status] Error: {e}")
            self.send_json({"error": str(e)}, 500)


# =============================================================================
# Threading HTTP Server (allows health checks while Claude CLI is running)
# =============================================================================

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server - handles concurrent requests.

    CRITICAL: This allows /health to respond even while /telegram or /invoke
    are blocked waiting for Claude CLI to complete (which can take minutes).
    """
    daemon_threads = True  # Clean shutdown of worker threads


# =============================================================================
# Main
# =============================================================================

def main():
    global server_instance

    parser = argparse.ArgumentParser(description="Claudius API Server with Telegram")
    parser.add_argument("--port", type=int, default=3100, help="Port to listen on")
    args = parser.parse_args()

    if not os.path.exists(CLAUDE_MD):
        logger.warning(f"{CLAUDE_MD} not found")

    # Log configuration status
    logger.info("Configuration Status:")
    logger.info(f"  CRON_SECRET: {'✓' if CRON_SECRET else '✗'}")
    logger.info(f"  TELEGRAM_BOT_TOKEN: {'✓' if TELEGRAM_BOT_TOKEN else '✗'}")
    logger.info(f"  OPENAI_API_KEY: {'✓' if OPENAI_API_KEY else '✗'}")
    logger.info(f"  FAL_KEY: {'✓' if FAL_KEY else '✗'}")
    logger.info(f"  SUPABASE (conversation history): {'✓' if SUPABASE_URL and SUPABASE_ANON_KEY else '✗'}")

    # Start memory monitoring thread
    monitor = threading.Thread(target=memory_monitor_thread, daemon=True, name="MemoryMonitor")
    monitor.start()
    logger.info(f"Memory monitoring started (check interval: {MEMORY_CHECK_INTERVAL}s, restart threshold: {MEMORY_RESTART_THRESHOLD_MB} MB)")

    # Use ThreadingHTTPServer for concurrent request handling
    server = ThreadingHTTPServer(("0.0.0.0", args.port), ClaudiusHandler)
    server_instance = server  # Set global for memory monitor shutdown

    logger.info(f"Claudius API starting on port {args.port} (multi-threaded)")
    logger.info("Endpoints:")
    logger.info("  POST /invoke    - Invoke Claudius (auth required)")
    logger.info("  POST /telegram  - Telegram webhook (direct, no Docker)")
    logger.info("  GET  /health    - Health check (includes memory usage)")
    logger.info("  GET  /memory    - View memory (auth required)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
