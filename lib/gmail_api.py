"""
Gmail API utilities for Claudius.

Provides thin wrappers around the Gmail REST API using urllib
(no google-api-python-client dependency).
"""

import json
import urllib.request
import urllib.error
from typing import Optional

from lib.google_auth import get_access_token

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def gmail_request(
    endpoint: str,
    account_email: str,
    method: str = "GET",
    body: dict = None
) -> dict:
    """Make a Gmail API request. Returns parsed JSON or empty dict on error."""
    token = get_access_token(account_email)
    url = f"{GMAIL_BASE}/{endpoint}"

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


def list_messages(
    account_email: str,
    query: str = "",
    max_results: int = 50,
    label_ids: list = None
) -> list:
    """List messages matching a query."""
    params = f"maxResults={max_results}"
    if query:
        params += f"&q={urllib.parse.quote(query)}"
    if label_ids:
        for lid in label_ids:
            params += f"&labelIds={lid}"

    result = gmail_request(f"messages?{params}", account_email)
    return result.get("messages", [])


def get_message(account_email: str, msg_id: str, fmt: str = "full") -> dict:
    """Get a single message by ID."""
    return gmail_request(f"messages/{msg_id}?format={fmt}", account_email)


def modify_message(
    account_email: str,
    msg_id: str,
    add_labels: list = None,
    remove_labels: list = None
) -> dict:
    """Add or remove labels from a message."""
    body = {}
    if add_labels:
        body["addLabelIds"] = add_labels
    if remove_labels:
        body["removeLabelIds"] = remove_labels
    return gmail_request(
        f"messages/{msg_id}/modify", account_email, method="POST", body=body
    )


def get_attachment(account_email: str, msg_id: str, attachment_id: str) -> Optional[str]:
    """Get attachment data (base64url encoded)."""
    result = gmail_request(
        f"messages/{msg_id}/attachments/{attachment_id}", account_email
    )
    return result.get("data")


# Need urllib.parse for query encoding
import urllib.parse
