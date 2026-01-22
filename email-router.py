#!/usr/bin/env python3
"""
Email Router v1.0 - Intelligent Real-Time Email Processing

Receives emails via Gmail push notifications (or polling fallback),
classifies them, and routes to appropriate actions:

  Invoice/Receipt  → Save to Drive (quarterly folders)
  Important/Urgent → Alert Claudius via Telegram
  Build Failure    → Log + optional alert
  Newsletter       → Archive silently
  Spam/Marketing   → Skip
  Unknown          → Alert (err on side of caution)

Runs as:
1. Polling mode (cron every 2 min) - reliable fallback
2. Push mode (via webhook) - near real-time [future]

Author: Claudius
"""

import json
import os
import re
import sys
import argparse
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple, Any
from pathlib import Path

# Add parent dir to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.config import AccountConfig
from lib.telegram_sender import send_telegram as _send_telegram

# =============================================================================
# CONFIGURATION (loaded from --account flag or default)
# =============================================================================

def _parse_account() -> str:
    """Parse --account from argv without interfering with other args."""
    for i, arg in enumerate(sys.argv):
        if arg == "--account" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith("--account="):
            return arg.split("=", 1)[1]
    return "idle"  # Default account

_ACCOUNT_NAME = _parse_account()
_ACCOUNT = AccountConfig.load(_ACCOUNT_NAME)

CREDENTIALS_FILE = _ACCOUNT.credentials_file
STATE_FILE = f"/opt/claudius/state/email_router_{_ACCOUNT.state_prefix}_state.json"
LEARNED_SENDERS_FILE = f"/opt/claudius/state/email_learned_senders_{_ACCOUNT.state_prefix}.json"
USER_EMAIL = _ACCOUNT.email

# Telegram config (from .env via lib)
from lib.telegram_sender import _ensure_env
_ensure_env()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Drive folder for invoices
VAT_FOLDER_ID = _ACCOUNT.vat_folder_id

# Allowed attachment types for auto-save
ALLOWED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp']

# =============================================================================
# EMAIL CLASSIFICATION RULES
# =============================================================================

# These senders ALWAYS get immediate alerts
PRIORITY_SENDERS = [
    "art@wolfroom.studio",
    "wolfroom",
    "@anthropic.com",  # Important for business
    "hmrc.gov.uk",     # Tax office!
    "companieshouse",  # Legal requirements
]

# Invoice/Receipt patterns - save to Drive
INVOICE_SENDERS = [
    "receipt", "invoice", "order", "payment", "billing",
    "paypal", "stripe", "square", "shopify", "gocardless",
    "amazon.co.uk", "amazon.com", "apple.com",
    "vercel.com", "github.com", "digitalocean",
    "godaddy", "namecheap", "cloudflare", "netlify",
    "adobe", "microsoft", "google.com", "openai.com",
    "anthropic.com", "notion.so", "figma.com", "canva.com",
    "zoom.us", "slack.com", "dropbox.com",
    "envato", "creative-tim", "gumroad", "paddle.com",
    "xero.com", "quickbooks", "freshbooks",
    "uber.com", "deliveroo", "justeat",
    "vodafone", "ee.co.uk", "three.co.uk", "bt.com",
    "british-gas", "octopus.energy", "bulb.co.uk",
    "hetzner",
]

INVOICE_SUBJECTS = [
    "receipt", "invoice", "order confirmation", "payment",
    "your order", "purchase", "transaction", "statement",
    "billing", "subscription", "renewal", "charge",
    "payment received", "payment confirmation",
]

# Build failure patterns - alert with lower priority
BUILD_FAILURE_SENDERS = [
    "noreply@vercel.com", "vercel.com",
    "notifications@github.com", "github.com",
    "netlify.com", "circleci.com", "gitlab.com"
]

BUILD_FAILURE_SUBJECTS = [
    "failed", "failure", "error", "broken", "build failed",
    "deployment failed", "workflow run failed"
]

# Newsletter patterns - archive silently
NEWSLETTER_SENDERS = [
    "substack.com", "mailchimp.com", "newsletter",
    "digest", "update@", "news@", "noreply@medium.com",
    "marketing@", "promo@", "offers@",
    # Corporate compliance spam
    "uber.com", "uberforbusiness", "uber for business",
]

# Social notification patterns - archive silently
SOCIAL_SENDERS = [
    "linkedin.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com",
    "noreply@discord.com", "slack.com"
]

# Automated patterns (noreply, etc.) - generally lower priority
AUTOMATED_PATTERNS = [
    "noreply", "no-reply", "donotreply",
    "automated", "mailer-daemon", "notifications@"
]

# =============================================================================
# OAUTH & API UTILITIES
# =============================================================================

def load_credentials() -> dict:
    """Load OAuth credentials from file."""
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def refresh_token(creds: dict) -> str:
    """Refresh the OAuth access token if needed."""
    expiry_str = creds["expiry"]
    if "+" not in expiry_str and "Z" not in expiry_str:
        expiry_str += "+00:00"
    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))

    if datetime.now(timezone.utc) < expiry - timedelta(minutes=5):
        return creds["token"]

    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token"
    }).encode("utf-8")

    req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        new_token = result["access_token"]
        expires_in = result.get("expires_in", 3600)

        creds["token"] = new_token
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        fd = os.open(CREDENTIALS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(creds, f, indent=2)

        return new_token


def get_access_token() -> str:
    """Get a valid access token."""
    creds = load_credentials()
    return refresh_token(creds)


def gmail_api_request(endpoint: str, method: str = "GET", body: dict = None) -> dict:
    """Make a Gmail API request."""
    token = get_access_token()
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{endpoint}"

    if body:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)

    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"[Gmail API] Error {e.code}: {error_body[:200]}")
        return {}


def drive_upload_multipart(file_data: bytes, filename: str, folder_id: str, mime_type: str) -> Optional[str]:
    """Upload a file to Google Drive using multipart upload."""
    token = get_access_token()
    boundary = "----EmailRouterBoundary"

    metadata = json.dumps({
        "name": filename,
        "parents": [folder_id]
    })

    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n"
    ).encode("utf-8")

    body += base64.b64encode(file_data)
    body += f"\r\n--{boundary}--".encode("utf-8")

    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result.get("id")
    except urllib.error.HTTPError as e:
        print(f"[Drive] Upload error {e.code}: {e.read().decode()[:200]}")
        return None


# =============================================================================
# EMAIL FETCHING & PARSING
# =============================================================================

def search_messages(query: str, max_results: int = 20) -> List[dict]:
    """Search for messages matching query."""
    params = urllib.parse.urlencode({
        "q": query,
        "maxResults": max_results
    })
    result = gmail_api_request(f"messages?{params}")
    return result.get("messages", [])


def get_message(message_id: str) -> dict:
    """Get full message details."""
    return gmail_api_request(f"messages/{message_id}?format=full")


def get_message_headers(message: dict) -> dict:
    """Extract useful headers from a message."""
    headers = {}
    for header in message.get("payload", {}).get("headers", []):
        name = header.get("name", "").lower()
        if name in ["from", "to", "subject", "date", "message-id"]:
            headers[name] = header.get("value", "")
    return headers


def get_message_body(message: dict) -> str:
    """Extract plain text body from message."""
    payload = message.get("payload", {})

    # Check for simple body
    if "body" in payload and payload["body"].get("data"):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Check parts for text/plain
    def find_text_part(parts):
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            if "parts" in part:
                result = find_text_part(part["parts"])
                if result:
                    return result
        return ""

    if "parts" in payload:
        return find_text_part(payload["parts"])

    return ""


def find_attachments(message: dict) -> List[dict]:
    """Find all downloadable attachments in a message."""
    attachments = []

    def scan_parts(parts: list):
        for part in parts:
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            if filename and attachment_id:
                ext = os.path.splitext(filename.lower())[1]
                if ext in ALLOWED_EXTENSIONS:
                    attachments.append({
                        "filename": filename,
                        "attachment_id": attachment_id,
                        "mime_type": part.get("mimeType", "application/octet-stream"),
                        "size": body.get("size", 0)
                    })

            if "parts" in part:
                scan_parts(part["parts"])

    payload = message.get("payload", {})
    if "parts" in payload:
        scan_parts(payload["parts"])

    return attachments


def get_attachment(message_id: str, attachment_id: str) -> Optional[bytes]:
    """Download an attachment."""
    result = gmail_api_request(f"messages/{message_id}/attachments/{attachment_id}")
    if result and "data" in result:
        return base64.urlsafe_b64decode(result["data"])
    return None


def parse_email_date(date_str: str) -> datetime:
    """Parse email date header to datetime."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ]

    date_str = re.sub(r'\s+', ' ', date_str.strip())
    date_str = re.sub(r'\s*\([^)]+\)\s*$', '', date_str)

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    return datetime.now(timezone.utc)


# =============================================================================
# CLASSIFICATION ENGINE
# =============================================================================

def matches_pattern(text: str, patterns: List[str]) -> bool:
    """Check if text matches any pattern (case insensitive)."""
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in patterns)


class EmailClassification:
    """Result of email classification."""

    PRIORITY_ALERT = "priority_alert"      # VIP sender, alert immediately
    INVOICE = "invoice"                     # Receipt/invoice, save to Drive
    BUILD_FAILURE = "build_failure"         # CI/CD failure, log + optional alert
    NEWSLETTER = "newsletter"               # Marketing, archive silently
    SOCIAL = "social"                       # Social notifications, archive
    PERSONAL = "personal"                   # Real person, alert
    AUTOMATED = "automated"                 # Generic automated, low priority
    UNKNOWN = "unknown"                     # Can't classify, alert to be safe

    def __init__(self, category: str, confidence: float, reason: str):
        self.category = category
        self.confidence = confidence
        self.reason = reason

    def __repr__(self):
        return f"<{self.category} ({self.confidence:.0%}): {self.reason}>"


def classify_email(headers: dict, body: str, attachments: List[dict]) -> EmailClassification:
    """
    Classify an email into a routing category.

    Uses a rule-based approach with fallback to "alert" for safety.
    """
    from_addr = headers.get("from", "").lower()
    subject = headers.get("subject", "").lower()

    # 1. Priority senders - ALWAYS alert (both hardcoded and learned)
    if matches_pattern(from_addr, PRIORITY_SENDERS):
        return EmailClassification(
            EmailClassification.PRIORITY_ALERT,
            1.0,
            f"Priority sender"
        )

    # 1b. Learned priority senders (people you reply to frequently)
    if is_learned_priority_sender(from_addr):
        return EmailClassification(
            EmailClassification.PRIORITY_ALERT,
            0.9,
            f"Learned priority (frequent replies)"
        )

    # 2. Invoice/Receipt detection
    is_invoice_sender = matches_pattern(from_addr, INVOICE_SENDERS)
    is_invoice_subject = matches_pattern(subject, INVOICE_SUBJECTS)
    has_pdf = any(a["filename"].lower().endswith(".pdf") for a in attachments)

    if is_invoice_sender and (is_invoice_subject or has_pdf):
        return EmailClassification(
            EmailClassification.INVOICE,
            0.95,
            f"Invoice from {from_addr.split('@')[0] if '@' in from_addr else from_addr[:20]}"
        )

    if is_invoice_subject and has_pdf:
        return EmailClassification(
            EmailClassification.INVOICE,
            0.85,
            "Invoice subject + PDF attachment"
        )

    # 3. Build failures
    if matches_pattern(from_addr, BUILD_FAILURE_SENDERS):
        if matches_pattern(subject, BUILD_FAILURE_SUBJECTS):
            return EmailClassification(
                EmailClassification.BUILD_FAILURE,
                0.95,
                "Build/deploy failure notification"
            )

    # 4. Newsletters
    if matches_pattern(from_addr, NEWSLETTER_SENDERS):
        return EmailClassification(
            EmailClassification.NEWSLETTER,
            0.9,
            "Newsletter/marketing"
        )

    # 5. Social notifications
    if matches_pattern(from_addr, SOCIAL_SENDERS):
        return EmailClassification(
            EmailClassification.SOCIAL,
            0.9,
            "Social notification"
        )

    # 6. Automated emails (noreply, etc.)
    if matches_pattern(from_addr, AUTOMATED_PATTERNS):
        # Check if it's something important despite being automated
        urgent_keywords = ["urgent", "action required", "verify", "confirm", "security"]
        if any(kw in subject for kw in urgent_keywords):
            return EmailClassification(
                EmailClassification.PERSONAL,  # Treat as personal to alert
                0.7,
                "Automated but urgent-sounding"
            )
        return EmailClassification(
            EmailClassification.AUTOMATED,
            0.8,
            "Automated notification"
        )

    # 7. Replies to existing threads
    if subject.startswith("re:") or subject.startswith("fw:"):
        return EmailClassification(
            EmailClassification.PERSONAL,
            0.85,
            "Reply to conversation"
        )

    # 8. Default: Treat as personal (safer to alert)
    return EmailClassification(
        EmailClassification.PERSONAL,
        0.6,
        "Unknown sender - treating as personal"
    )


# =============================================================================
# ACTION HANDLERS
# =============================================================================

def send_telegram_alert(message: str, parse_mode: str = "Markdown") -> bool:
    """Send a message via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True
    except Exception as e:
        print(f"[Telegram] Error: {e}")
        return False


def get_quarterly_folder(date: datetime) -> str:
    """Get the appropriate quarterly folder ID for a date."""
    token = get_access_token()
    month = date.month
    year = date.year

    # Determine period name based on date
    if month in [12]:
        period_name = f"Invoices and receipts 01.12.{year} – 28.02.{year + 1}"
        year_for_folder = year + 1
    elif month in [1, 2]:
        period_name = f"Invoices and receipts 01.12.{year - 1} – 28.02.{year}"
        year_for_folder = year
    elif month in [3, 4, 5]:
        period_name = f"Invoices and receipts 01.03.{year} – 31.05.{year}"
        year_for_folder = year
    elif month in [6, 7, 8]:
        period_name = f"Invoices and receipts 01.06.{year} – 31.08.{year}"
        year_for_folder = year
    else:
        period_name = f"Invoices and receipts 01.09.{year} – 30.11.{year}"
        year_for_folder = year

    # Find or create year folder
    def find_folder(name: str, parent_id: str) -> Optional[str]:
        query = f"name = '{name}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        params = urllib.parse.urlencode({"q": query, "fields": "files(id,name)"})
        url = f"https://www.googleapis.com/drive/v3/files?{params}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                files = result.get("files", [])
                if files:
                    return files[0]["id"]
        except:
            pass
        return None

    def create_folder(name: str, parent_id: str) -> Optional[str]:
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id]
        }
        data = json.dumps(metadata).encode("utf-8")
        url = "https://www.googleapis.com/drive/v3/files"
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                return result.get("id")
        except:
            pass
        return None

    # Find/create year folder
    year_folder_id = find_folder(str(year_for_folder), VAT_FOLDER_ID)
    if not year_folder_id:
        year_folder_id = create_folder(str(year_for_folder), VAT_FOLDER_ID)

    # Find/create quarterly folder
    quarter_folder_id = find_folder(period_name, year_folder_id)
    if not quarter_folder_id:
        quarter_folder_id = create_folder(period_name, year_folder_id)

    return quarter_folder_id


def action_save_to_drive(message: dict, headers: dict, attachments: List[dict]) -> dict:
    """Save invoice/receipt attachments to Drive."""
    results = {"saved": [], "failed": []}

    if not attachments:
        return results

    # Get email date for folder selection
    email_date = parse_email_date(headers.get("date", ""))
    folder_id = get_quarterly_folder(email_date)

    if not folder_id:
        results["failed"] = [a["filename"] for a in attachments]
        return results

    # Clean sender name for filename
    from_addr = headers.get("from", "")
    sender_clean = re.sub(r'[<>@"\']', '', from_addr.split('<')[0]).strip()[:30]
    sender_clean = re.sub(r'[^\w\s-]', '', sender_clean).strip()

    date_prefix = email_date.strftime("%Y-%m-%d")

    for att in attachments:
        filename = att["filename"]

        # Build new filename
        base, ext = os.path.splitext(filename)
        new_filename = f"{date_prefix} - {sender_clean} - {base}{ext}"
        new_filename = re.sub(r'\s+', ' ', new_filename)

        # Download and upload
        file_data = get_attachment(message.get("id"), att["attachment_id"])
        if not file_data:
            results["failed"].append(filename)
            continue

        file_id = drive_upload_multipart(file_data, new_filename, folder_id, att["mime_type"])
        if file_id:
            results["saved"].append(new_filename)
        else:
            results["failed"].append(filename)

    return results


def action_quick_invoice_alert(headers: dict, body: str, save_result: dict) -> bool:
    """Send a quick confirmation that an invoice was saved."""
    from_addr = headers.get("from", "Unknown")

    # Extract sender name
    sender = from_addr
    if "<" in from_addr:
        match = re.match(r'"?([^"<]+)"?\s*<', from_addr)
        if match:
            sender = match.group(1).strip()
    # Simplify common sender names
    sender_lower = sender.lower()
    if "anthropic" in sender_lower:
        sender = "Anthropic"
    elif "hetzner" in sender_lower:
        sender = "Hetzner"
    elif "stripe" in sender_lower:
        sender = "Stripe"
    elif "paypal" in sender_lower:
        sender = "PayPal"
    elif "amazon" in sender_lower:
        sender = "Amazon"
    elif "google" in sender_lower:
        sender = "Google"
    elif "vercel" in sender_lower:
        sender = "Vercel"
    elif "github" in sender_lower:
        sender = "GitHub"
    else:
        # Just take first word if it's a company name
        sender = sender.split()[0] if sender else "Unknown"

    # Try to extract amount from body
    amount = None
    amount_patterns = [
        r'[£$€][\d,]+\.?\d*',  # £180.00, $50, €100
        r'[\d,]+\.?\d*\s*(?:GBP|USD|EUR)',  # 180.00 GBP
        r'Total[:\s]+[£$€]?[\d,]+\.?\d*',  # Total: £180
    ]
    for pattern in amount_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            amount = match.group(0).strip()
            # Clean up
            amount = re.sub(r'^Total[:\s]+', '', amount, flags=re.IGNORECASE)
            break

    # Build quick message
    saved_count = len(save_result.get("saved", []))
    if amount:
        msg = f"🧾 {amount} {sender} saved to Drive"
    else:
        msg = f"🧾 {sender} invoice saved to Drive"

    if saved_count > 1:
        msg += f" ({saved_count} files)"

    return send_telegram_alert(msg, parse_mode="Markdown")


def generate_email_summary(body: str, subject: str) -> str:
    """Generate a clean summary of an email's content (no truncation)."""
    body_clean = body.strip()

    # Cut at signature/footer markers
    for marker in ["\n--\n", "\n___", "\nSent from", "\nUnsubscribe", "\nView in browser",
                   "\nClick here to", "\nManage your", "\nUpdate your preferences"]:
        if marker in body_clean:
            body_clean = body_clean.split(marker)[0]

    # Clean up whitespace but preserve paragraph breaks
    body_clean = re.sub(r'[ \t]+', ' ', body_clean)  # Collapse spaces/tabs
    body_clean = re.sub(r'\n{3,}', '\n\n', body_clean)  # Max 2 newlines
    body_clean = body_clean.strip()

    # Cap at reasonable length for Telegram (but much more generous than before)
    max_len = 800
    if len(body_clean) > max_len:
        # Find a good break point (sentence or paragraph)
        truncate_at = body_clean.rfind('. ', 0, max_len)
        if truncate_at == -1:
            truncate_at = body_clean.rfind('\n', 0, max_len)
        if truncate_at == -1:
            truncate_at = max_len
        body_clean = body_clean[:truncate_at + 1].strip()
        if not body_clean.endswith('.'):
            body_clean += "..."

    return body_clean


def action_telegram_alert(headers: dict, body: str, classification: EmailClassification, attachments: List[dict] = None) -> bool:
    """Send a clean, minimal alert to Telegram about this email."""
    from_addr = headers.get("from", "Unknown")
    subject = headers.get("subject", "No subject")

    # Clean up from address for display - just the name
    from_display = from_addr
    if "<" in from_addr:
        match = re.match(r'"?([^"<]+)"?\s*<([^>]+)>', from_addr)
        if match:
            from_display = match.group(1).strip()

    # Further clean common noise
    from_display = from_display.replace('"', '').strip()
    if not from_display or from_display == from_addr:
        # Extract from email address
        if "@" in from_addr:
            from_display = from_addr.split("@")[0].split("<")[-1].replace(".", " ").title()

    # Build clean summary message (Option 2 format)
    msg = f"📧 {from_display}\n"
    msg += f"{subject}\n\n"

    # Add brief summary
    summary = generate_email_summary(body, subject)
    if summary:
        msg += summary

    return send_telegram_alert(msg, parse_mode="HTML")


# =============================================================================
# MAIN ROUTER
# =============================================================================

import fcntl
_state_lock_fd = None


def acquire_state_lock():
    """Acquire exclusive file lock to prevent concurrent runs."""
    global _state_lock_fd
    lock_file = STATE_FILE + ".lock"
    _state_lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(_state_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another instance is already running. Exiting.")
        sys.exit(0)


def release_state_lock():
    """Release the state file lock."""
    global _state_lock_fd
    if _state_lock_fd:
        fcntl.flock(_state_lock_fd, fcntl.LOCK_UN)
        _state_lock_fd.close()
        _state_lock_fd = None


def load_state() -> dict:
    """Load state file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return {
            "last_check": None,
            "processed_ids": [],
            "history_id": None
        }


def save_state(state: dict):
    """Save state to file atomically."""
    import tempfile
    state["processed_ids"] = state["processed_ids"][-1000:]  # Keep last 1000
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(STATE_FILE), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_learned_senders() -> dict:
    """Load learned sender priorities."""
    try:
        with open(LEARNED_SENDERS_FILE) as f:
            return json.load(f)
    except:
        return {"priority_senders": {}, "last_scan": None}


def save_learned_senders(data: dict):
    """Save learned sender priorities."""
    with open(LEARNED_SENDERS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_learned_priority_sender(email: str) -> bool:
    """Check if a sender has been learned as priority based on reply patterns."""
    learned = load_learned_senders()
    email_lower = email.lower()

    for sender, data in learned.get("priority_senders", {}).items():
        if sender.lower() in email_lower:
            # Require at least 3 replies to consider someone priority
            if data.get("reply_count", 0) >= 3:
                return True
    return False


def scan_sent_folder_for_patterns():
    """
    Scan sent folder to learn who you frequently reply to.
    Run this periodically (daily) to update learned senders.
    """
    learned = load_learned_senders()
    priority_senders = learned.get("priority_senders", {})

    # Get sent emails from last 30 days
    query = "in:sent newer_than:30d"
    messages = search_messages(query, max_results=100)

    reply_counts = {}

    for msg_ref in messages:
        message = get_message(msg_ref.get("id"))
        if not message:
            continue

        headers = get_message_headers(message)
        to_addr = headers.get("to", "")

        # Extract email address
        if "<" in to_addr:
            match = re.search(r'<([^>]+)>', to_addr)
            if match:
                to_addr = match.group(1)

        to_addr = to_addr.lower().strip()
        if to_addr and "@" in to_addr:
            reply_counts[to_addr] = reply_counts.get(to_addr, 0) + 1

    # Update learned senders
    for email, count in reply_counts.items():
        if email not in priority_senders:
            priority_senders[email] = {"reply_count": 0, "first_seen": datetime.now(timezone.utc).isoformat()}
        priority_senders[email]["reply_count"] = count
        priority_senders[email]["last_updated"] = datetime.now(timezone.utc).isoformat()

    learned["priority_senders"] = priority_senders
    learned["last_scan"] = datetime.now(timezone.utc).isoformat()
    save_learned_senders(learned)

    # Report new priority senders
    new_priority = [e for e, d in priority_senders.items() if d.get("reply_count", 0) >= 3]
    print(f"[Learn] Scanned sent folder. {len(new_priority)} learned priority senders.")


def route_email(message: dict) -> dict:
    """
    Route a single email through the classification and action pipeline.

    Returns a summary of actions taken.
    """
    headers = get_message_headers(message)
    body = get_message_body(message)
    attachments = find_attachments(message)

    # Classify
    classification = classify_email(headers, body, attachments)

    result = {
        "message_id": message.get("id"),
        "subject": headers.get("subject", "")[:50],
        "classification": classification.category,
        "confidence": classification.confidence,
        "actions_taken": []
    }

    # Route based on classification
    if classification.category == EmailClassification.INVOICE:
        # Save to Drive
        save_result = action_save_to_drive(message, headers, attachments)
        if save_result["saved"]:
            result["actions_taken"].append(f"Saved {len(save_result['saved'])} files to Drive")
            # Quick confirmation: "🧾 £180 Anthropic saved to Drive"
            if action_quick_invoice_alert(headers, body, save_result):
                result["actions_taken"].append("Quick invoice alert sent")
        if save_result["failed"]:
            result["actions_taken"].append(f"Failed to save {len(save_result['failed'])} files")

    elif classification.category == EmailClassification.PRIORITY_ALERT:
        # Immediate alert for VIP senders
        if action_telegram_alert(headers, body, classification, attachments):
            result["actions_taken"].append("Priority Telegram alert sent")

        # Also save any attachments
        if attachments:
            save_result = action_save_to_drive(message, headers, attachments)
            if save_result["saved"]:
                result["actions_taken"].append(f"Saved {len(save_result['saved'])} files to Drive")

    elif classification.category == EmailClassification.PERSONAL:
        # Real person email - alert
        if action_telegram_alert(headers, body, classification, attachments):
            result["actions_taken"].append("Telegram alert sent")

    elif classification.category == EmailClassification.BUILD_FAILURE:
        # Log but don't alert (too noisy)
        result["actions_taken"].append("Logged (no alert - build failure)")

    elif classification.category in [EmailClassification.NEWSLETTER, EmailClassification.SOCIAL]:
        # Silent archive
        result["actions_taken"].append("Archived silently")

    elif classification.category == EmailClassification.AUTOMATED:
        # Low priority automated - log only
        result["actions_taken"].append("Logged (automated)")

    else:
        # Unknown - alert to be safe
        if action_telegram_alert(headers, body, classification, attachments):
            result["actions_taken"].append("Telegram alert sent (unknown category)")

    return result


def process_new_emails(verbose: bool = False):
    """Main function - find and route new emails."""
    state = load_state()
    processed_ids = set(state.get("processed_ids", []))

    # Search for unread emails from last 30 minutes
    query = "is:unread newer_than:30m"
    messages = search_messages(query, max_results=30)

    if not messages:
        if verbose:
            print("[Router] No new unread emails")
        return

    results = []

    for msg_ref in messages:
        msg_id = msg_ref.get("id")

        if msg_id in processed_ids:
            continue

        # Get full message
        message = get_message(msg_id)
        if not message:
            continue

        # Route it
        result = route_email(message)
        results.append(result)

        if verbose:
            print(f"[Router] {result['classification']}: {result['subject']}")
            for action in result['actions_taken']:
                print(f"         -> {action}")

        # Mark as processed
        processed_ids.add(msg_id)

    # Save state
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state["processed_ids"] = list(processed_ids)
    save_state(state)

    # Summary
    categories = {}
    for r in results:
        cat = r["classification"]
        categories[cat] = categories.get(cat, 0) + 1

    if results:
        print(f"[Router] Processed {len(results)} emails: {categories}")
    elif verbose:
        print("[Router] No new emails to process")


# =============================================================================
# GMAIL WATCH API (for push notifications)
# =============================================================================

def setup_gmail_watch(topic_name: str = None) -> dict:
    """
    Set up Gmail watch for push notifications.

    Requires a Google Cloud Pub/Sub topic with Gmail granted publish permission.
    Watch expires after 7 days - must renew daily.

    Returns watch response with historyId and expiration.
    """
    if not topic_name:
        # Default topic (needs to be created in GCP console)
        topic_name = "projects/claudius-email-router/topics/gmail-push"

    body = {
        "topicName": topic_name,
        "labelIds": ["INBOX"],  # Only watch inbox
        "labelFilterAction": "include"
    }

    result = gmail_api_request("watch", method="POST", body=body)

    if result:
        print(f"[Watch] Gmail watch set up successfully")
        print(f"[Watch] History ID: {result.get('historyId')}")
        print(f"[Watch] Expires: {result.get('expiration')}")
    else:
        print("[Watch] Failed to set up Gmail watch")

    return result


def stop_gmail_watch() -> bool:
    """Stop the current Gmail watch."""
    result = gmail_api_request("stop", method="POST")
    return bool(result)


def get_history_since(history_id: str) -> List[dict]:
    """
    Get mailbox history since a given history ID.

    Used to process changes when receiving push notifications.
    """
    params = urllib.parse.urlencode({
        "startHistoryId": history_id,
        "historyTypes": ["messageAdded"],
        "labelId": "INBOX"
    })

    result = gmail_api_request(f"history?{params}")
    return result.get("history", [])


def process_push_notification(data: dict):
    """
    Process a push notification from Gmail.

    The notification contains a historyId - we need to fetch
    the actual changes using the history API.
    """
    state = load_state()
    last_history_id = state.get("history_id")

    # Decode the notification
    if "message" in data and "data" in data["message"]:
        notification = json.loads(base64.urlsafe_b64decode(data["message"]["data"]))
        new_history_id = notification.get("historyId")
    else:
        print("[Push] Invalid notification format")
        return

    if not last_history_id:
        # First notification - just save the history ID
        state["history_id"] = new_history_id
        save_state(state)
        print(f"[Push] Initialized history ID: {new_history_id}")
        return

    # Get changes since last history ID
    history = get_history_since(last_history_id)

    message_ids = set()
    for entry in history:
        for added in entry.get("messagesAdded", []):
            msg_id = added.get("message", {}).get("id")
            if msg_id:
                message_ids.add(msg_id)

    if message_ids:
        print(f"[Push] Processing {len(message_ids)} new messages")
        processed_ids = set(state.get("processed_ids", []))

        for msg_id in message_ids:
            if msg_id in processed_ids:
                continue

            message = get_message(msg_id)
            if message:
                result = route_email(message)
                print(f"[Push] Routed: {result['classification']} - {result['subject']}")
                processed_ids.add(msg_id)

        state["processed_ids"] = list(processed_ids)

    # Update history ID
    state["history_id"] = new_history_id
    save_state(state)


def main():
    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    # Acquire lock to prevent concurrent runs
    acquire_state_lock()

    if "--test" in sys.argv:
        print("[Router] Test mode - checking recent emails...")
        process_new_emails(verbose=True)
    elif "--learn" in sys.argv:
        # Scan sent folder to learn priority senders
        print("[Router] Learning from your reply patterns...")
        scan_sent_folder_for_patterns()
    elif "--show-learned" in sys.argv:
        # Show learned priority senders
        learned = load_learned_senders()
        priority = [(e, d) for e, d in learned.get("priority_senders", {}).items()
                    if d.get("reply_count", 0) >= 3]
        priority.sort(key=lambda x: x[1].get("reply_count", 0), reverse=True)
        print(f"[Router] Learned priority senders ({len(priority)}):")
        for email, data in priority[:20]:
            print(f"  {email}: {data.get('reply_count', 0)} replies")
    elif "--setup-watch" in sys.argv:
        # Set up Gmail watch (requires Pub/Sub topic)
        topic = None
        for arg in sys.argv:
            if arg.startswith("--topic="):
                topic = arg.split("=")[1]
        setup_gmail_watch(topic)
    elif "--stop-watch" in sys.argv:
        stop_gmail_watch()
        print("[Router] Gmail watch stopped")
    else:
        process_new_emails(verbose=verbose)


if __name__ == "__main__":
    main()
