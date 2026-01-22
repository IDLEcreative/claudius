#!/usr/bin/env python3
"""
Google OAuth Token Refresher

Proactively refreshes Google OAuth tokens before they expire.
Run via cron every 45 minutes to ensure tokens are always fresh.

Handles multiple credential files (MCP, calendar-nudge, etc.)
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

# All credential files to keep refreshed
CREDENTIAL_FILES = [
    "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json",
    "/opt/claudius/.google_workspace_mcp/credentials/token.json",
]

LOG_FILE = "/opt/claudius/logs/token-refresh.log"


def log(msg: str):
    """Log with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    print(log_msg)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(log_msg + "\n")
    except Exception as e:
        print(f"Could not write to log: {e}")


def refresh_token_for_file(filepath: str) -> bool:
    """Refresh the token in a credential file."""
    if not os.path.exists(filepath):
        log(f"SKIP: {filepath} does not exist")
        return True  # Not an error, just doesn't exist

    try:
        with open(filepath) as f:
            creds = json.load(f)
    except Exception as e:
        log(f"ERROR reading {filepath}: {e}")
        return False

    # Check required fields
    required = ["refresh_token", "client_id", "client_secret", "token_uri"]
    if not all(k in creds for k in required):
        log(f"SKIP: {filepath} missing required fields")
        return True

    # Check expiry - refresh if within 15 minutes of expiry
    expiry_str = creds.get("expiry", "")
    if expiry_str:
        try:
            if "+" not in expiry_str and "Z" not in expiry_str:
                expiry_str += "+00:00"
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            time_left = expiry - datetime.now(timezone.utc)

            if time_left > timedelta(minutes=15):
                log(f"OK: {Path(filepath).name} - {int(time_left.total_seconds() / 60)} mins left, no refresh needed")
                return True
            else:
                log(f"REFRESHING: {Path(filepath).name} - only {int(time_left.total_seconds() / 60)} mins left")
        except Exception as e:
            log(f"Could not parse expiry, will refresh: {e}")

    # Do the refresh
    try:
        data = urllib.parse.urlencode({
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request(creds["token_uri"], data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            new_token = result["access_token"]
            expires_in = result.get("expires_in", 3600)

            # Update the file
            creds["token"] = new_token
            creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            # If we got a new refresh token, save that too
            if "refresh_token" in result:
                creds["refresh_token"] = result["refresh_token"]

            with open(filepath, "w") as f:
                json.dump(creds, f, indent=2)

            log(f"REFRESHED: {Path(filepath).name} - new token valid for {expires_in // 60} mins")
            return True

    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else "no body"
        log(f"ERROR refreshing {Path(filepath).name}: HTTP {e.code} - {error_body}")
        return False
    except Exception as e:
        log(f"ERROR refreshing {Path(filepath).name}: {e}")
        return False


def main():
    log("=== Token Refresh Run ===")

    all_ok = True
    for filepath in CREDENTIAL_FILES:
        if not refresh_token_for_file(filepath):
            all_ok = False

    if all_ok:
        log("All tokens OK")
    else:
        log("WARNING: Some tokens failed to refresh")

    return 0 if all_ok else 1


if __name__ == "__main__":
    exit(main())
