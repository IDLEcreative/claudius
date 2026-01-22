"""
Google OAuth token management for Claudius.

Handles loading credentials and refreshing access tokens
for Gmail and Drive APIs.
"""

import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

CREDENTIALS_BASE = Path("/opt/claudius/.google_workspace_mcp/credentials")


def load_credentials(account_email: str) -> dict:
    """Load OAuth credentials for a given account email."""
    cred_file = CREDENTIALS_BASE / f"{account_email}.json"
    with open(cred_file) as f:
        return json.load(f)


def refresh_token(creds: dict, cred_file: Path = None) -> str:
    """Refresh the OAuth access token if needed. Returns valid access token."""
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
        creds["expiry"] = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()

        if cred_file:
            with open(cred_file, "w") as f:
                json.dump(creds, f, indent=2)

        return new_token


def get_access_token(account_email: str) -> str:
    """Get a valid access token for the given account."""
    cred_file = CREDENTIALS_BASE / f"{account_email}.json"
    creds = load_credentials(account_email)
    return refresh_token(creds, cred_file)
