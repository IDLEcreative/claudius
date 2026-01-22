#!/usr/bin/env python3
"""
Invoice Archiver for Claudius

Downloads PDF/image attachments from receipt emails and uploads them
to the appropriate quarterly folder on Google Drive.

Folder structure on Drive:
  VAT INVOICES & RECEIPTS/
    ├── 2024/
    │    └── Invoices and receipts 01.01.24 - 29.02.24
    ├── 2025/
    │    └── Quarterly folders...
    └── 2026/
         └── Invoices and receipts 01.12.2025 – 30.02.2026

Runs alongside email-monitor.py (every 5 mins) or manually.
"""

import json
import os
import re
import sys
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple
from pathlib import Path

# Add parent dir to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.config import AccountConfig
from lib.telegram_sender import send_telegram as _send_telegram, _ensure_env

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
STATE_FILE = f"/opt/claudius/state/invoice_archiver_{_ACCOUNT.state_prefix}_state.json"
USER_EMAIL = _ACCOUNT.email

# Telegram config (from .env)
_ensure_env()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Drive folder IDs
VAT_FOLDER_ID = _ACCOUNT.vat_folder_id

# Quarterly folder mappings (will be populated dynamically)
QUARTERLY_FOLDERS = {
    # Format: (year, quarter) -> folder_id
    # Quarter 1: Jan-Mar, Q2: Apr-Jun, Q3: Jul-Sep, Q4: Oct-Dec
    # But your system uses 3-month rolling: Dec-Feb, Mar-May, Jun-Aug, Sep-Nov
}

# Your quarterly periods (start month -> period name)
# Dec 1 - Feb 28/29
# Mar 1 - May 31
# Jun 1 - Aug 31
# Sep 1 - Nov 30
PERIOD_DEFINITIONS = [
    ((12, 1), (2, 28), "01.12.{y1} – 30.02.{y2}"),   # Dec-Feb (crosses year)
    ((3, 1), (5, 31), "01.03.{y1} – 31.05.{y1}"),    # Mar-May
    ((6, 1), (8, 31), "01.06.{y1} – 31.08.{y1}"),    # Jun-Aug
    ((9, 1), (11, 30), "01.09.{y1} – 30.11.{y1}"),   # Sep-Nov
]

# Receipt patterns for detecting invoice emails
RECEIPT_SENDERS = [
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
    "hmrc.gov.uk", "companieshouse",
]

RECEIPT_SUBJECTS = [
    "receipt", "invoice", "order confirmation", "payment",
    "your order", "purchase", "transaction", "statement",
    "billing", "subscription", "renewal", "charge",
    "payment received", "payment confirmation",
]

# Attachment types to save
ALLOWED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp']


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
        print(f"Gmail API error: {e.code} - {error_body}")
        return {}


def drive_api_request(endpoint: str, method: str = "GET", body: dict = None, raw_data: bytes = None, content_type: str = None) -> dict:
    """Make a Drive API request."""
    token = get_access_token()
    url = f"https://www.googleapis.com/drive/v3/{endpoint}"

    if raw_data:
        req = urllib.request.Request(url, data=raw_data, method=method)
        req.add_header("Content-Type", content_type or "application/octet-stream")
    elif body:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)

    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"Drive API error: {e.code} - {error_body}")
        return {}


def file_exists_in_folder(original_filename: str, folder_id: str) -> bool:
    """
    Check if a file with this original filename already exists in the folder.
    Uses the base filename (without date prefix) to catch duplicates regardless of sender name.
    """
    token = get_access_token()

    # Extract just the original filename part (e.g., "5450626754.pdf" from various formats)
    # Match files that end with this filename
    base_name = os.path.basename(original_filename)

    # Search for files containing this filename in the folder
    query = f"'{folder_id}' in parents and name contains '{base_name}' and trashed = false"
    params = urllib.parse.urlencode({
        "q": query,
        "fields": "files(id,name)"
    })

    url = f"https://www.googleapis.com/drive/v3/files?{params}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            files = result.get("files", [])
            if files:
                return True
    except Exception as e:
        print(f"Error checking for existing file: {e}")

    return False


def upload_to_drive(file_data: bytes, filename: str, folder_id: str, mime_type: str) -> Optional[str]:
    """Upload a file to Google Drive."""
    token = get_access_token()

    # Use multipart upload
    boundary = "----CloudiusBoundary"

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
        error_body = e.read().decode()
        print(f"Drive upload error: {e.code} - {error_body}")
        return None


def search_messages(query: str, max_results: int = 50) -> List[dict]:
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
        if name in ["from", "to", "subject", "date"]:
            headers[name] = header.get("value", "")
    return headers


def get_attachment(message_id: str, attachment_id: str) -> Optional[bytes]:
    """Download an attachment."""
    result = gmail_api_request(f"messages/{message_id}/attachments/{attachment_id}")
    if result and "data" in result:
        return base64.urlsafe_b64decode(result["data"])
    return None


def find_attachments(message: dict) -> List[dict]:
    """Find all attachments in a message."""
    attachments = []

    def scan_parts(parts: list, depth=0):
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

            # Recurse into nested parts
            if "parts" in part:
                scan_parts(part["parts"], depth + 1)

    payload = message.get("payload", {})
    if "parts" in payload:
        scan_parts(payload["parts"])

    return attachments


def matches_pattern(text: str, patterns: List[str]) -> bool:
    """Check if text matches any pattern (case insensitive)."""
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in patterns)


def is_receipt_email(from_addr: str, subject: str) -> bool:
    """Check if this is a receipt/invoice email."""
    return matches_pattern(from_addr, RECEIPT_SENDERS) or matches_pattern(subject, RECEIPT_SUBJECTS)


def get_quarterly_folder(date: datetime) -> Tuple[str, str]:
    """
    Determine which quarterly folder a date belongs to.
    Returns (year_folder_id, quarter_folder_id) or creates them if needed.
    """
    month = date.month
    year = date.year

    # Determine which period this date falls into
    # Dec-Feb, Mar-May, Jun-Aug, Sep-Nov
    if month in [12]:
        period_name = f"Invoices and receipts 01.12.{year} – 28.02.{year + 1}"
        year_for_folder = year + 1  # Dec 2025 goes into 2026 folder
    elif month in [1, 2]:
        period_name = f"Invoices and receipts 01.12.{year - 1} – 28.02.{year}"
        year_for_folder = year
    elif month in [3, 4, 5]:
        period_name = f"Invoices and receipts 01.03.{year} – 31.05.{year}"
        year_for_folder = year
    elif month in [6, 7, 8]:
        period_name = f"Invoices and receipts 01.06.{year} – 31.08.{year}"
        year_for_folder = year
    else:  # 9, 10, 11
        period_name = f"Invoices and receipts 01.09.{year} – 30.11.{year}"
        year_for_folder = year

    # Find or create year folder
    year_folder_id = find_or_create_folder(str(year_for_folder), VAT_FOLDER_ID)

    # Find or create quarterly folder
    quarter_folder_id = find_or_create_folder(period_name, year_folder_id)

    return year_folder_id, quarter_folder_id


def find_folder(name: str, parent_id: str) -> Optional[str]:
    """Find a folder by name in a parent folder."""
    token = get_access_token()

    query = f"name = '{name}' and '{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    params = urllib.parse.urlencode({
        "q": query,
        "fields": "files(id,name)"
    })

    url = f"https://www.googleapis.com/drive/v3/files?{params}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            files = result.get("files", [])
            if files:
                return files[0]["id"]
    except Exception as e:
        print(f"Error finding folder: {e}")

    return None


def create_folder(name: str, parent_id: str) -> Optional[str]:
    """Create a folder in Drive."""
    token = get_access_token()

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
    except Exception as e:
        print(f"Error creating folder: {e}")

    return None


def find_or_create_folder(name: str, parent_id: str) -> str:
    """Find a folder by name, or create it if it doesn't exist."""
    folder_id = find_folder(name, parent_id)
    if folder_id:
        return folder_id

    print(f"Creating folder: {name}")
    return create_folder(name, parent_id)


def parse_email_date(date_str: str) -> datetime:
    """Parse email date header to datetime."""
    # Common formats in email headers
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ]

    # Clean up the date string
    date_str = re.sub(r'\s+', ' ', date_str.strip())
    # Remove parenthetical timezone names like (GMT)
    date_str = re.sub(r'\s*\([^)]+\)\s*$', '', date_str)

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    # Fallback to now
    return datetime.now(timezone.utc)


def load_state() -> dict:
    """Load the state file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "last_run": None,
            "archived_message_ids": []
        }


def save_state(state: dict):
    """Save state to file."""
    # Keep only last 1000 archived IDs
    state["archived_message_ids"] = state["archived_message_ids"][-1000:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_telegram(message: str) -> bool:
    """Send a message via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def archive_receipt_emails(days_back: int = 7, verbose: bool = False):
    """Main function - find receipt emails and archive their attachments."""
    state = load_state()
    archived_ids = set(state.get("archived_message_ids", []))

    # Search for receipt emails with attachments
    query = f"has:attachment (subject:(receipt OR invoice OR order OR payment OR billing) OR from:(receipt OR invoice OR paypal OR stripe OR amazon)) newer_than:{days_back}d"

    if verbose:
        print(f"Searching: {query}")

    messages = search_messages(query, max_results=100)

    if not messages:
        print("No receipt emails with attachments found")
        return

    uploaded_count = 0
    skipped_count = 0

    for msg_ref in messages:
        msg_id = msg_ref.get("id")

        # Skip if already archived
        if msg_id in archived_ids:
            skipped_count += 1
            continue

        # Get full message
        message = get_message(msg_id)
        if not message:
            continue

        headers = get_message_headers(message)
        from_addr = headers.get("from", "")
        subject = headers.get("subject", "")
        date_str = headers.get("date", "")

        # Verify it's a receipt email
        if not is_receipt_email(from_addr, subject):
            continue

        # Find attachments
        attachments = find_attachments(message)
        if not attachments:
            continue

        # Parse date for folder selection
        email_date = parse_email_date(date_str)

        # Get the appropriate quarterly folder
        _, quarter_folder_id = get_quarterly_folder(email_date)

        if verbose:
            print(f"\nProcessing: {subject[:50]}...")
            print(f"  From: {from_addr[:50]}")
            print(f"  Date: {email_date.strftime('%Y-%m-%d')}")
            print(f"  Attachments: {len(attachments)}")

        # Upload each attachment
        for att in attachments:
            filename = att["filename"]

            # Check if this file already exists in the target folder (by original filename)
            # This prevents duplicates even if sender name parsing differs
            if file_exists_in_folder(filename, quarter_folder_id):
                if verbose:
                    print(f"  ⏭ Skipping (already in Drive): {filename}")
                continue

            # Prefix with date for easy sorting
            date_prefix = email_date.strftime("%Y-%m-%d")

            # Clean the sender for filename
            sender_clean = re.sub(r'[<>@"\']', '', from_addr.split('<')[0]).strip()[:30]
            sender_clean = re.sub(r'[^\w\s-]', '', sender_clean).strip()

            # Build new filename
            base, ext = os.path.splitext(filename)
            new_filename = f"{date_prefix} - {sender_clean} - {base}{ext}"
            new_filename = re.sub(r'\s+', ' ', new_filename)  # Clean whitespace

            if verbose:
                print(f"  Uploading: {new_filename}")

            # Download attachment
            file_data = get_attachment(msg_id, att["attachment_id"])
            if not file_data:
                print(f"  Failed to download: {filename}")
                continue

            # Upload to Drive
            file_id = upload_to_drive(file_data, new_filename, quarter_folder_id, att["mime_type"])
            if file_id:
                uploaded_count += 1
                if verbose:
                    print(f"  ✓ Uploaded: {new_filename}")
            else:
                print(f"  ✗ Failed to upload: {new_filename}")

        # Mark as archived
        archived_ids.add(msg_id)

    # Save state
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["archived_message_ids"] = list(archived_ids)
    save_state(state)

    print(f"\nDone! Uploaded: {uploaded_count}, Skipped (already archived): {skipped_count}")

    # Send summary to Telegram if we uploaded anything
    if uploaded_count > 0:
        send_telegram(f"📁 Archived {uploaded_count} invoice/receipt attachments to Drive")


def main():
    import sys

    verbose = "--verbose" in sys.argv or "-v" in sys.argv

    # Check for days argument
    days = 7
    for arg in sys.argv[1:]:
        if arg.startswith("--days="):
            try:
                days = int(arg.split("=")[1])
            except:
                pass

    if "--backfill" in sys.argv:
        # Backfill mode - go back 90 days
        print("Backfill mode: scanning last 90 days")
        archive_receipt_emails(days_back=90, verbose=verbose)
    else:
        archive_receipt_emails(days_back=days, verbose=verbose)


if __name__ == "__main__":
    main()
