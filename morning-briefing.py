#!/usr/bin/env python3
"""
Claudius Morning Briefing
=========================
Sends Jay a morning summary via Telegram:
- Today's calendar events
- Unread/important emails
- Weather (optional)
- Any overnight alerts

Run at 7:30 AM daily.
"""

import json
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict

# Load .env if not already in environment
if not os.environ.get("TELEGRAM_BOT_TOKEN"):
    for _env_path in ["/opt/claudius/.env", "/opt/omniops/.env"]:
        if os.path.exists(_env_path):
            with open(_env_path) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _, _v = _line.partition("=")
                        os.environ.setdefault(_k.strip(), _v.strip().strip('"'))
            break

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "7070679785")
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"


def load_credentials() -> dict:
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def refresh_token(creds: dict) -> str:
    expiry_str = creds.get("expiry", "")
    if expiry_str:
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
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f)
        return new_token

    return creds["token"]


def api_request(url: str, token: str) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_todays_events(token: str) -> List[Dict]:
    """Get today's calendar events."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1)
    
    time_min = start_of_day.isoformat()
    time_max = end_of_day.isoformat()
    
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events"
        f"?timeMin={urllib.parse.quote(time_min)}"
        f"&timeMax={urllib.parse.quote(time_max)}"
        f"&singleEvents=true"
        f"&orderBy=startTime"
    )
    
    try:
        result = api_request(url, token)
        return result.get("items", [])
    except Exception as e:
        print(f"Calendar error: {e}")
        return []


def get_unread_emails(token: str, max_results: int = 10) -> List[Dict]:
    """Get recent unread emails."""
    url = (
        f"https://www.googleapis.com/gmail/v1/users/me/messages"
        f"?q=is:unread+is:inbox"
        f"&maxResults={max_results}"
    )
    
    try:
        result = api_request(url, token)
        messages = result.get("messages", [])
        
        emails = []
        for msg in messages[:5]:  # Limit detail fetch to 5
            msg_url = f"https://www.googleapis.com/gmail/v1/users/me/messages/{msg['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
            try:
                detail = api_request(msg_url, token)
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                emails.append({
                    "from": headers.get("From", "Unknown"),
                    "subject": headers.get("Subject", "(no subject)")
                })
            except:
                pass
        return emails
    except Exception as e:
        print(f"Gmail error: {e}")
        return []


def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def format_time(event: Dict) -> str:
    """Format event time nicely."""
    start = event.get("start", {})
    if "dateTime" in start:
        dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    elif "date" in start:
        return "All day"
    return "?"


def generate_briefing() -> str:
    """Generate the morning briefing message."""
    try:
        creds = load_credentials()
        token = refresh_token(creds)
    except Exception as e:
        return f"⚠️ Could not load credentials: {e}"
    
    now = datetime.now()
    greeting = "Good morning" if now.hour < 12 else "Good afternoon"
    day_name = now.strftime("%A")
    date_str = now.strftime("%d %B")
    
    lines = [f"☀️ *{greeting}!*", f"_{day_name}, {date_str}_", ""]
    
    # Calendar
    events = get_todays_events(token)
    if events:
        lines.append("📅 *Today's Schedule:*")
        for event in events:
            time_str = format_time(event)
            summary = event.get("summary", "Untitled")
            lines.append(f"  • {time_str} - {summary}")
        lines.append("")
    else:
        lines.append("📅 *Calendar:* Clear day - no meetings!")
        lines.append("")
    
    # Emails
    emails = get_unread_emails(token)
    if emails:
        lines.append(f"📧 *Unread Emails ({len(emails)}):*")
        for email in emails[:5]:
            sender = email["from"].split("<")[0].strip()[:25]
            subject = email["subject"][:40]
            lines.append(f"  • {sender}: {subject}")
        lines.append("")
    else:
        lines.append("📧 *Inbox:* All clear!")
        lines.append("")
    
    lines.append("_Have a great day!_ 🚀")
    
    return "\n".join(lines)


def main():
    print(f"[{datetime.now()}] Generating morning briefing...")
    briefing = generate_briefing()
    print(briefing)
    print()
    
    if send_telegram(briefing):
        print("✅ Sent to Telegram")
    else:
        print("❌ Failed to send")


if __name__ == "__main__":
    main()
