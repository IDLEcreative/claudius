#!/usr/bin/env python3
"""
Email Intelligence Module for Claudius

Provides smart email features:
1. Priority Sender Learning - Track who James responds to
2. Thread Awareness - Recognize active threads
3. Follow-up Nudges - Track unanswered important emails
4. Attachment Detection - Detect and summarize attachments
5. Sender Context - Pull relevant memories from Engram
"""

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple, Any
import base64

# File paths
STATE_FILE = "/opt/claudius/email_intelligence_state.json"
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"

# Engram API config
ENGRAM_URL = os.environ.get("ENGRAM_API_URL", "http://localhost:3201/engram")
ENGRAM_KEY = os.environ.get("ADMIN_SECRET", "")


def load_state() -> dict:
    """Load the intelligence state file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "learned_senders": {},  # email -> {"reply_count": N, "last_reply": ISO, "promoted": bool}
            "active_threads": {},   # thread_id -> {"subject": str, "participants": [], "last_activity": ISO, "message_count": int}
            "pending_followups": {},  # message_id -> {"from": str, "subject": str, "received": ISO, "reminded": bool}
            "last_digest": None,
            "digest_enabled": True
        }


def save_state(state: dict):
    """Save state to file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# =============================================================================
# 1. PRIORITY SENDER LEARNING
# =============================================================================

def record_sent_email(to_email: str, subject: str):
    """Record when James sends/replies to someone - they become more important."""
    state = load_state()

    # Normalize email
    email = extract_email_address(to_email).lower()
    if not email:
        return

    if email not in state["learned_senders"]:
        state["learned_senders"][email] = {
            "reply_count": 0,
            "last_reply": None,
            "promoted": False,
            "name": extract_sender_name(to_email)
        }

    sender = state["learned_senders"][email]
    sender["reply_count"] += 1
    sender["last_reply"] = datetime.now(timezone.utc).isoformat()

    # Auto-promote after 2+ replies
    if sender["reply_count"] >= 2 and not sender["promoted"]:
        sender["promoted"] = True
        print(f"[EmailIntel] Auto-promoted sender: {email} (replied {sender['reply_count']} times)")

    save_state(state)


def is_learned_priority_sender(from_email: str) -> Tuple[bool, Optional[dict]]:
    """Check if sender has been learned as priority based on James's behavior."""
    state = load_state()
    email = extract_email_address(from_email).lower()

    if email in state["learned_senders"]:
        sender = state["learned_senders"][email]
        if sender["promoted"] or sender["reply_count"] >= 2:
            return True, sender

    return False, None


def get_learned_senders_list() -> List[dict]:
    """Get list of all learned priority senders."""
    state = load_state()
    result = []
    for email, data in state["learned_senders"].items():
        result.append({
            "email": email,
            **data
        })
    return sorted(result, key=lambda x: x["reply_count"], reverse=True)


# =============================================================================
# 2. THREAD AWARENESS
# =============================================================================

def update_thread_info(thread_id: str, message: dict, headers: dict):
    """Update thread tracking information."""
    state = load_state()

    from_addr = headers.get("from", "")
    subject = headers.get("subject", "")

    if thread_id not in state["active_threads"]:
        state["active_threads"][thread_id] = {
            "subject": subject.replace("Re: ", "").replace("Fwd: ", ""),
            "participants": [],
            "message_count": 0,
            "last_activity": None,
            "first_seen": datetime.now(timezone.utc).isoformat()
        }

    thread = state["active_threads"][thread_id]
    thread["message_count"] += 1
    thread["last_activity"] = datetime.now(timezone.utc).isoformat()

    # Track participants
    sender_email = extract_email_address(from_addr).lower()
    if sender_email and sender_email not in thread["participants"]:
        thread["participants"].append(sender_email)

    # Clean up old threads (older than 7 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    state["active_threads"] = {
        tid: t for tid, t in state["active_threads"].items()
        if datetime.fromisoformat(t["last_activity"].replace("Z", "+00:00")) > cutoff
    }

    save_state(state)
    return thread


def get_thread_context(thread_id: str) -> Optional[dict]:
    """Get context about an existing thread."""
    state = load_state()
    return state["active_threads"].get(thread_id)


def is_active_thread(thread_id: str) -> bool:
    """Check if this is part of an active conversation thread."""
    state = load_state()
    thread = state["active_threads"].get(thread_id)
    if not thread:
        return False

    # Active = multiple messages in last 48 hours
    last_activity = datetime.fromisoformat(thread["last_activity"].replace("Z", "+00:00"))
    return thread["message_count"] > 1 and last_activity > datetime.now(timezone.utc) - timedelta(hours=48)


# =============================================================================
# 3. FOLLOW-UP NUDGES
# =============================================================================

def track_for_followup(message_id: str, headers: dict, is_priority: bool = False):
    """Track an important email that may need follow-up."""
    if not is_priority:
        return  # Only track priority emails

    state = load_state()

    from_addr = headers.get("from", "")
    subject = headers.get("subject", "")

    state["pending_followups"][message_id] = {
        "from": from_addr,
        "from_email": extract_email_address(from_addr).lower(),
        "subject": subject,
        "received": datetime.now(timezone.utc).isoformat(),
        "reminded": False,
        "reminder_count": 0
    }

    # Clean up old entries (older than 14 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    state["pending_followups"] = {
        mid: p for mid, p in state["pending_followups"].items()
        if datetime.fromisoformat(p["received"].replace("Z", "+00:00")) > cutoff
    }

    save_state(state)


def mark_followup_complete(from_email: str):
    """Mark follow-ups as complete when James replies to someone."""
    state = load_state()
    email = extract_email_address(from_email).lower()

    # Remove any pending follow-ups for this sender
    to_remove = [
        mid for mid, p in state["pending_followups"].items()
        if p["from_email"] == email
    ]

    for mid in to_remove:
        del state["pending_followups"][mid]

    if to_remove:
        print(f"[EmailIntel] Cleared {len(to_remove)} follow-up(s) for {email}")
        save_state(state)


def get_pending_followups(min_hours: int = 24) -> List[dict]:
    """Get emails that haven't been responded to after X hours."""
    state = load_state()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_hours)

    pending = []
    for mid, p in state["pending_followups"].items():
        received = datetime.fromisoformat(p["received"].replace("Z", "+00:00"))
        if received < cutoff:
            hours_ago = int((datetime.now(timezone.utc) - received).total_seconds() / 3600)
            pending.append({
                "message_id": mid,
                "hours_ago": hours_ago,
                **p
            })

    return sorted(pending, key=lambda x: x["hours_ago"], reverse=True)


def mark_reminder_sent(message_id: str):
    """Mark that we've sent a reminder for this email."""
    state = load_state()
    if message_id in state["pending_followups"]:
        state["pending_followups"][message_id]["reminded"] = True
        state["pending_followups"][message_id]["reminder_count"] += 1
        save_state(state)


# =============================================================================
# 4. ATTACHMENT DETECTION
# =============================================================================

def detect_attachments(message: dict) -> List[dict]:
    """Detect attachments in an email message."""
    attachments = []
    payload = message.get("payload", {})

    def scan_parts(parts):
        for part in parts:
            filename = part.get("filename", "")
            if filename:
                body = part.get("body", {})
                attachments.append({
                    "filename": filename,
                    "mime_type": part.get("mimeType", "unknown"),
                    "size": body.get("size", 0),
                    "attachment_id": body.get("attachmentId", "")
                })
            # Recurse into nested parts
            if "parts" in part:
                scan_parts(part["parts"])

    if "parts" in payload:
        scan_parts(payload["parts"])

    return attachments


def format_attachment_info(attachments: List[dict]) -> str:
    """Format attachment info for display."""
    if not attachments:
        return ""

    lines = ["📎 *Attachments:*"]
    for att in attachments:
        size_kb = att["size"] / 1024
        if size_kb > 1024:
            size_str = f"{size_kb/1024:.1f} MB"
        else:
            size_str = f"{size_kb:.0f} KB"

        # Emoji based on type
        mime = att["mime_type"].lower()
        if "pdf" in mime:
            emoji = "📄"
        elif "image" in mime:
            emoji = "🖼"
        elif "spreadsheet" in mime or "excel" in mime:
            emoji = "📊"
        elif "document" in mime or "word" in mime:
            emoji = "📝"
        elif "zip" in mime or "archive" in mime:
            emoji = "📦"
        else:
            emoji = "📎"

        lines.append(f"  {emoji} {att['filename']} ({size_str})")

    return "\n".join(lines)


# =============================================================================
# 5. SENDER CONTEXT FROM ENGRAM
# =============================================================================

def get_sender_context(from_email: str) -> Optional[str]:
    """Query Engram for any memories about this sender."""
    email = extract_email_address(from_email).lower()
    if not email:
        return None

    # Also try getting the sender name/domain for context
    name = extract_sender_name(from_email)
    domain = email.split("@")[1] if "@" in email else ""

    # Build search query
    search_terms = [email]
    if name and len(name) > 2:
        search_terms.append(name)
    if domain and domain not in ["gmail.com", "outlook.com", "yahoo.com", "hotmail.com"]:
        search_terms.append(domain.replace(".com", "").replace(".co.uk", ""))

    try:
        # Use semantic search
        data = json.dumps({
            "query": f"emails from {' or '.join(search_terms)} context history",
            "limit": 3,
            "threshold": 0.65
        }).encode("utf-8")

        req = urllib.request.Request(f"{ENGRAM_URL}/recall", data=data, method="POST")
        req.add_header("Authorization", f"Bearer {ENGRAM_KEY}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            memories = result.get("memories", [])

            if memories:
                # Format the most relevant memory
                relevant = []
                for mem in memories[:2]:
                    content = mem.get("content", "")
                    if len(content) > 100:
                        content = content[:100] + "..."
                    relevant.append(content)

                return " | ".join(relevant)

    except Exception as e:
        print(f"[EmailIntel] Engram lookup failed: {e}")

    return None


# =============================================================================
# 6. DAILY DIGEST
# =============================================================================

def should_send_digest() -> bool:
    """Check if it's time to send the daily digest."""
    state = load_state()

    if not state.get("digest_enabled", True):
        return False

    now = datetime.now(timezone.utc)

    # Send digest at 8:00 AM UTC (9:00 AM London winter, 10:00 AM summer)
    if now.hour != 8:
        return False

    # Check if we already sent today
    last = state.get("last_digest")
    if last:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.date() == now.date():
            return False

    return True


def mark_digest_sent():
    """Mark that we've sent today's digest."""
    state = load_state()
    state["last_digest"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


def set_digest_enabled(enabled: bool):
    """Enable or disable daily digest."""
    state = load_state()
    state["digest_enabled"] = enabled
    save_state(state)


# =============================================================================
# HELPERS
# =============================================================================

def extract_email_address(from_str: str) -> str:
    """Extract email address from 'Name <email>' format."""
    match = re.search(r'<([^>]+)>', from_str)
    if match:
        return match.group(1)
    # Maybe it's just a plain email
    if "@" in from_str:
        return from_str.strip()
    return ""


def extract_sender_name(from_str: str) -> str:
    """Extract sender name from 'Name <email>' format."""
    match = re.match(r'"?([^"<]+)"?\s*<', from_str)
    if match:
        return match.group(1).strip()
    return ""


# =============================================================================
# SCAN SENT EMAILS (for learning)
# =============================================================================

def scan_recent_sent_emails(gmail_api_func, days: int = 7):
    """
    Scan sent emails to learn who James responds to.
    Call this periodically to update learned senders.
    """
    from datetime import datetime, timedelta

    # This would need to be called from the main email monitor
    # with access to the Gmail API
    pass


# Quick test
if __name__ == "__main__":
    print("Email Intelligence Module")
    print("State file:", STATE_FILE)

    state = load_state()
    print(f"Learned senders: {len(state.get('learned_senders', {}))}")
    print(f"Active threads: {len(state.get('active_threads', {}))}")
    print(f"Pending follow-ups: {len(state.get('pending_followups', {}))}")
