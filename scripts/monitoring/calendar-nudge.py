#!/usr/bin/env python3
"""
Calendar Nudge System for Claudius

Two modes:
  --briefing  : Morning summary of today's events (run at 8am)
  --nudge     : Check for events starting in ~30 mins (run every 5 mins)

Uses Google Calendar API directly with stored OAuth credentials.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict

# Config
TELEGRAM_BOT_TOKEN = "8387428119:AAEGEeSCBSdw7y4SSv9FV_7rDzjDyu-SNmQ"
TELEGRAM_CHAT_ID = "7070679785"
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"
NUDGE_WINDOW_MIN = 28  # minutes before event
NUDGE_WINDOW_MAX = 33  # minutes before event (5 min cron window)
NUDGE_SENT_FILE = "/tmp/calendar_nudges_sent.json"


def load_credentials() -> dict:
    """Load OAuth credentials from file."""
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def refresh_token(creds: dict) -> str:
    """Refresh the OAuth access token if needed."""
    # Check if token is expired
    expiry_str = creds["expiry"]
    if "+" not in expiry_str and "Z" not in expiry_str:
        expiry_str += "+00:00"
    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) < expiry - timedelta(minutes=5):
        return creds["token"]

    # Refresh the token
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

        # Update credentials file
        creds["token"] = new_token
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f, indent=2)

        return new_token


def get_calendar_events(time_min: str, time_max: str, max_results: int = 25) -> List[Dict]:
    """Fetch events from Google Calendar API."""
    import urllib.parse

    creds = load_credentials()
    token = refresh_token(creds)

    params = urllib.parse.urlencode({
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime"
    })

    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            events = []
            for item in result.get("items", []):
                start = item.get("start", {})
                end = item.get("end", {})
                events.append({
                    "id": item.get("id", ""),
                    "summary": item.get("summary", "Untitled"),
                    "start": start.get("dateTime", start.get("date", "")),
                    "end": end.get("dateTime", end.get("date", "")),
                    "location": item.get("location", ""),
                    "hangoutLink": item.get("hangoutLink", "")
                })
            return events
    except urllib.error.HTTPError as e:
        print(f"Calendar API error: {e.code} - {e.read().decode()}")
        return []
    except Exception as e:
        print(f"Calendar error: {e}")
        return []


def send_telegram(message: str) -> bool:
    """Send a message via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def get_nudges_sent() -> dict:
    """Load record of which nudges we've already sent today."""
    try:
        with open(NUDGE_SENT_FILE) as f:
            data = json.load(f)
            if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
                return {"date": datetime.now().strftime("%Y-%m-%d"), "events": []}
            return data
    except:
        return {"date": datetime.now().strftime("%Y-%m-%d"), "events": []}


def save_nudge_sent(event_id: str):
    """Mark an event as nudged."""
    data = get_nudges_sent()
    if event_id not in data["events"]:
        data["events"].append(event_id)
    with open(NUDGE_SENT_FILE, "w") as f:
        json.dump(data, f)


def format_time(iso_str: str) -> str:
    """Format ISO datetime to human readable time."""
    try:
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            # Convert to local time (UK)
            local_dt = dt.astimezone()
            return local_dt.strftime("%-I:%M%p").lower()
        else:
            return "all day"
    except:
        return iso_str


def morning_briefing():
    """Send morning summary of today's events."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    events = get_calendar_events(
        today_start.isoformat(),
        today_end.isoformat()
    )

    if not events:
        msg = "Good morning!\n\nYour calendar is clear today. A rare gift - use it wisely."
    else:
        msg = "Good morning! Here's your day:\n\n"
        for e in events:
            time_str = format_time(e.get("start", ""))
            summary = e.get("summary", "Untitled")
            msg += f"- {time_str} - {summary}\n"

            # Add location or meet link if present
            if e.get("hangoutLink"):
                msg += f"  (Google Meet available)\n"
            elif e.get("location"):
                loc = e["location"][:40] + "..." if len(e.get("location", "")) > 40 else e.get("location", "")
                msg += f"  @ {loc}\n"

        msg += "\n"
        if len(events) == 1:
            msg += "Just the one thing. Focus time."
        elif len(events) > 4:
            msg += f"{len(events)} meetings. Pace yourself."
        else:
            msg += "Have a good one."

    if send_telegram(msg):
        print(f"Sent morning briefing: {len(events)} events")
    else:
        print("Failed to send morning briefing")


def check_nudges():
    """Check for events starting in ~30 mins and send nudge."""
    now = datetime.now(timezone.utc)

    # Look for events starting between 28-33 mins from now
    window_start = now + timedelta(minutes=NUDGE_WINDOW_MIN)
    window_end = now + timedelta(minutes=NUDGE_WINDOW_MAX)

    events = get_calendar_events(
        window_start.isoformat(),
        window_end.isoformat()
    )

    sent_data = get_nudges_sent()

    for e in events:
        event_id = e.get("id", e.get("summary", ""))

        if event_id in sent_data.get("events", []):
            continue

        summary = e.get("summary", "Untitled event")
        start_time = format_time(e.get("start", ""))

        msg = f"30 minute heads up\n\n{summary}\nStarts at {start_time}\n\n"

        if e.get("hangoutLink"):
            msg += "Google Meet link ready when you are."
        else:
            msg += "Time for a brew or bathroom break."

        if send_telegram(msg):
            save_nudge_sent(event_id)
            print(f"Sent nudge for: {summary}")
        else:
            print(f"Failed to send nudge for: {summary}")


def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: calendar-nudge.py [--briefing|--nudge]")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--briefing":
        morning_briefing()
    elif mode == "--nudge":
        check_nudges()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
