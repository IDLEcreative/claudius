#!/usr/bin/env python3
"""
Gmail Smart Labels Module for Claudius

Manages color-coded Gmail labels that the AI applies automatically based on
email content analysis. Uses Gmail's native label coloring for visual triage.

Labels are applied by Claude based on email content - no hardcoded rules.
"""

import json
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple

# Credentials file location
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"

# Smart label definitions with Gmail color codes
# Gmail color format: {"textColor": "#ffffff", "backgroundColor": "#ac2b16"}
# See: https://developers.google.com/gmail/api/reference/rest/v1/users.labels
SMART_LABELS = {
    "Action-Required": {
        "description": "You need to do something",
        "color": {"textColor": "#ffffff", "backgroundColor": "#ac2b16"},  # Red
    },
    "Needs-Reply": {
        "description": "Waiting on your response",
        "color": {"textColor": "#000000", "backgroundColor": "#fce8b3"},  # Yellow
    },
    "Money-In": {
        "description": "Payments coming to you",
        "color": {"textColor": "#ffffff", "backgroundColor": "#16a765"},  # Green
    },
    "Money-Out": {
        "description": "Bills, subscriptions, expenses",
        "color": {"textColor": "#ffffff", "backgroundColor": "#cf8933"},  # Orange (Gmail palette)
    },
    "Creative": {
        "description": "Art, music, projects",
        "color": {"textColor": "#ffffff", "backgroundColor": "#4a86e8"},  # Blue
    },
    "Networking": {
        "description": "People, connections, opportunities",
        "color": {"textColor": "#ffffff", "backgroundColor": "#a479e2"},  # Purple
    },
    "Auto-Handled": {
        "description": "FYI only - no action needed",
        "color": {"textColor": "#ffffff", "backgroundColor": "#999999"},  # Gray
    }
}

# Cache for label IDs (label name -> label ID mapping)
_label_cache: Dict[str, str] = {}
_label_cache_file = "/opt/claudius/gmail_label_cache.json"


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

        creds["token"] = new_token
        creds["expiry"] = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        with open(CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f, indent=2)

        return new_token


def gmail_api_request(endpoint: str, method: str = "GET", body: dict = None) -> dict:
    """Make a Gmail API request."""
    creds = load_credentials()
    token = refresh_token(creds)

    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{endpoint}"

    if body:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)

    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"Gmail API error: {e.code} - {error_body}")
        raise
    except Exception as e:
        print(f"Gmail error: {e}")
        raise


def load_label_cache() -> Dict[str, str]:
    """Load cached label ID mappings."""
    global _label_cache
    if _label_cache:
        return _label_cache

    try:
        if os.path.exists(_label_cache_file):
            with open(_label_cache_file) as f:
                _label_cache = json.load(f)
    except Exception as e:
        print(f"Could not load label cache: {e}")
        _label_cache = {}

    return _label_cache


def save_label_cache():
    """Save label ID mappings to cache file."""
    try:
        with open(_label_cache_file, "w") as f:
            json.dump(_label_cache, f, indent=2)
    except Exception as e:
        print(f"Could not save label cache: {e}")


def get_all_labels() -> List[dict]:
    """Get all labels from Gmail."""
    result = gmail_api_request("labels")
    return result.get("labels", [])


def get_label_id(label_name: str) -> Optional[str]:
    """Get the Gmail ID for a label by name. Uses cache."""
    cache = load_label_cache()

    if label_name in cache:
        return cache[label_name]

    # Not in cache, fetch from Gmail
    labels = get_all_labels()
    for label in labels:
        if label["name"] == label_name:
            _label_cache[label_name] = label["id"]
            save_label_cache()
            return label["id"]

    return None


def create_label(name: str, color: dict = None) -> dict:
    """
    Create a new Gmail label with optional color.

    Args:
        name: Label name
        color: Dict with textColor and backgroundColor (hex codes)

    Returns:
        Created label object from Gmail API
    """
    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show"
    }

    if color:
        body["color"] = color

    result = gmail_api_request("labels", method="POST", body=body)

    # Update cache
    if result.get("id"):
        _label_cache[name] = result["id"]
        save_label_cache()

    return result


def update_label_color(label_id: str, color: dict) -> dict:
    """Update a label's color."""
    body = {"color": color}
    return gmail_api_request(f"labels/{label_id}", method="PATCH", body=body)


def ensure_smart_labels_exist() -> Dict[str, str]:
    """
    Ensure all smart labels exist with correct colors.
    Creates them if missing, updates colors if changed.

    Returns:
        Dict mapping label name to label ID
    """
    existing_labels = {label["name"]: label for label in get_all_labels()}
    label_ids = {}

    for label_name, label_config in SMART_LABELS.items():
        if label_name in existing_labels:
            # Label exists - check if color needs updating
            existing = existing_labels[label_name]
            label_id = existing["id"]
            label_ids[label_name] = label_id

            # Update cache
            _label_cache[label_name] = label_id

            # Check if color matches
            existing_color = existing.get("color", {})
            desired_color = label_config["color"]

            if existing_color.get("backgroundColor") != desired_color.get("backgroundColor"):
                print(f"Updating color for {label_name}...")
                try:
                    update_label_color(label_id, desired_color)
                    print(f"  Color updated!")
                except Exception as e:
                    print(f"  Could not update color: {e}")
        else:
            # Create new label
            print(f"Creating label: {label_name}...")
            try:
                result = create_label(label_name, label_config["color"])
                label_id = result["id"]
                label_ids[label_name] = label_id
                print(f"  Created with ID: {label_id}")
            except Exception as e:
                print(f"  Error creating label: {e}")

    save_label_cache()
    return label_ids


def apply_label_to_message(message_id: str, label_name: str) -> bool:
    """
    Apply a smart label to a message.

    Args:
        message_id: Gmail message ID
        label_name: Name of the smart label (e.g., "Action-Required")

    Returns:
        True if successful, False otherwise
    """
    label_id = get_label_id(label_name)

    if not label_id:
        print(f"Label '{label_name}' not found")
        return False

    body = {"addLabelIds": [label_id]}

    try:
        gmail_api_request(f"messages/{message_id}/modify", method="POST", body=body)
        return True
    except Exception as e:
        print(f"Error applying label: {e}")
        return False


def apply_labels_to_message(message_id: str, label_names: List[str]) -> bool:
    """
    Apply multiple smart labels to a message.

    Args:
        message_id: Gmail message ID
        label_names: List of label names (e.g., ["Action-Required", "Money-In"])

    Returns:
        True if all successful, False if any failed
    """
    label_ids = []

    for name in label_names:
        label_id = get_label_id(name)
        if label_id:
            label_ids.append(label_id)
        else:
            print(f"Warning: Label '{name}' not found, skipping")

    if not label_ids:
        return False

    body = {"addLabelIds": label_ids}

    try:
        gmail_api_request(f"messages/{message_id}/modify", method="POST", body=body)
        return True
    except Exception as e:
        print(f"Error applying labels: {e}")
        return False


def remove_label_from_message(message_id: str, label_name: str) -> bool:
    """Remove a smart label from a message."""
    label_id = get_label_id(label_name)

    if not label_id:
        print(f"Label '{label_name}' not found")
        return False

    body = {"removeLabelIds": [label_id]}

    try:
        gmail_api_request(f"messages/{message_id}/modify", method="POST", body=body)
        return True
    except Exception as e:
        print(f"Error removing label: {e}")
        return False


def get_available_labels() -> List[str]:
    """Get list of available smart label names."""
    return list(SMART_LABELS.keys())


def get_label_description(label_name: str) -> Optional[str]:
    """Get the description for a smart label."""
    if label_name in SMART_LABELS:
        return SMART_LABELS[label_name]["description"]
    return None


def categorize_email_with_ai(headers: dict, body: str, max_retries: int = 3) -> List[str]:
    """
    Use Claude to categorize an email and return appropriate labels.
    Pure AI classification with retry logic - no keyword fallback.

    Args:
        headers: Email headers (from, subject, etc.)
        body: Email body text
        max_retries: Number of retry attempts (default 3)

    Returns:
        List of label names to apply, or empty list if classification fails
    """
    import subprocess
    import time

    from_addr = headers.get("from", "Unknown")
    subject = headers.get("subject", "No subject")

    # Clean the body - remove excessive content
    clean_body = body.strip()
    for marker in ["\n--\n", "\n___", "\nSent from", "\nGet Outlook", "\n>", "________________________________"]:
        if marker in clean_body:
            clean_body = clean_body.split(marker)[0]

    # Truncate for token efficiency
    if len(clean_body) > 1500:
        clean_body = clean_body[:1500] + "..."

    prompt = f"""Analyze this email and assign appropriate labels. An email can have MULTIPLE labels.

LABELS:
- Action-Required: You need to take action (respond, pay, sign, approve something)
- Needs-Reply: Someone is specifically waiting for your response
- Money-In: Payments, refunds, or money coming TO you
- Money-Out: Bills, invoices, subscriptions - actual money going FROM you (NOT security warnings that mention billing)
- Creative: Art, music, design, creative projects
- Networking: People connections, introductions, professional opportunities
- Auto-Handled: Automated notifications requiring no action (CI/CD alerts, security advisories, system notifications)

IMPORTANT RULES:
- Security advisories (even if they mention "billing") are NOT Money-Out - they are Auto-Handled or Action-Required
- GitHub/Vercel deployment failures are Auto-Handled + Action-Required, NOT financial
- Only use Money-Out for ACTUAL financial transactions where money leaves your account
- Newsletters and marketing emails are Auto-Handled
- If someone asks you a question directly, use Needs-Reply

EMAIL:
From: {from_addr}
Subject: {subject}
Body:
{clean_body}

Return ONLY the label names, comma-separated on a single line. Example: "Action-Required, Needs-Reply"
Your response:"""

    for attempt in range(max_retries):
        try:
            # Use Claude CLI with Sonnet 4.5 for classification (explicit version for consistency)
            result = subprocess.run(
                ["claude", "--print", "--model", "claude-sonnet-4-5-20241022"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=60,
                cwd="/opt/claudius"
            )

            if result.returncode != 0:
                _log_classification(from_addr, subject, f"CLI error (attempt {attempt + 1}): {result.stderr}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                    continue
                return []

            content = result.stdout.strip()

            # Parse response - handle both comma-separated and newline formats
            labels = []
            valid_labels = set(SMART_LABELS.keys())

            # First try comma-separated format
            if "," in content:
                parts = [p.strip() for p in content.split(",")]
            else:
                parts = content.split("\n")

            for part in parts:
                line = part.strip()
                # Handle potential bullet points or dashes
                if line.startswith("-"):
                    line = line[1:].strip()
                if line.startswith("*"):
                    line = line[1:].strip()
                # Remove quotes if present
                line = line.strip('"\'')

                if line in valid_labels:
                    labels.append(line)

            if labels:
                _log_classification(from_addr, subject, f"SUCCESS: {labels}")
                return labels
            else:
                _log_classification(from_addr, subject, f"No valid labels parsed (attempt {attempt + 1}): {content}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return []

        except subprocess.TimeoutExpired:
            _log_classification(from_addr, subject, f"Timeout (attempt {attempt + 1})")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return []
        except Exception as e:
            _log_classification(from_addr, subject, f"Error (attempt {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return []

    return []


def _log_classification(from_addr: str, subject: str, result: str):
    """Log classification results for debugging."""
    import datetime
    log_file = "/opt/claudius/logs/email_classification.log"

    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        timestamp = datetime.datetime.now().isoformat()
        log_entry = f"[{timestamp}] From: {from_addr[:50]} | Subject: {subject[:50]} | {result}\n"

        with open(log_file, "a") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Could not write classification log: {e}")


# Entry point for setup
if __name__ == "__main__":
    print("Gmail Smart Labels Setup")
    print("=" * 40)
    print("\nAvailable labels:")
    for name, config in SMART_LABELS.items():
        print(f"  {name}: {config['description']}")

    print("\nChecking/creating labels in Gmail...")
    label_ids = ensure_smart_labels_exist()

    print("\nSetup complete! Label IDs:")
    for name, label_id in label_ids.items():
        print(f"  {name}: {label_id}")
