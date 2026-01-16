#!/usr/bin/env python3
"""
Email Monitor for Claudius

Runs every 5 minutes (alongside calendar-nudge).
Checks for new emails, categorizes them, and either:
  - Alerts James on Telegram if it needs attention
  - Silently organizes it into the right folder

Uses Gmail API directly with stored OAuth credentials.
"""

import json
import os
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple

# Config
TELEGRAM_BOT_TOKEN = "8387428119:AAEGEeSCBSdw7y4SSv9FV_7rDzjDyu-SNmQ"
TELEGRAM_CHAT_ID = "7070679785"
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"
STATE_FILE = "/opt/claudius/email_monitor_state.json"
USER_EMAIL = "james.d.guy@gmail.com"

# Legacy label IDs (still used for backwards compatibility)
LEGACY_LABELS = {
    "build_failures": "Label_4",
    "newsletters": "Label_5",
    "receipts": "Label_6",
    "social": "Label_7",
    "needs_response": "Label_8"
}

# Smart labels - AI-applied, color-coded (imported from gmail_labels.py)
USE_SMART_LABELS = True  # Set to False to use legacy behavior

# Priority senders - always alert for these
PRIORITY_SENDERS = [
    "art@wolfroom.studio",
    "wolfroom",
    # Add more as needed
]

# Build failure patterns
BUILD_FAILURE_SENDERS = [
    "noreply@vercel.com",
    "vercel.com",
    "notifications@github.com",
    "netlify.com",
    "circleci.com",
    "gitlab.com"
]
BUILD_FAILURE_SUBJECTS = [
    "failed", "failure", "error", "broken", "build failed",
    "deployment failed", "workflow run failed"
]

# Newsletter patterns
NEWSLETTER_SENDERS = [
    "substack.com",
    "mailchimp.com",
    "newsletter",
    "digest",
    "update@",
    "news@",
    "noreply@medium.com"
]

# Receipt/transaction patterns
RECEIPT_SENDERS = [
    "receipt", "invoice", "order", "payment",
    "paypal", "stripe", "square", "shopify",
    "amazon.co.uk", "apple.com"
]
RECEIPT_SUBJECTS = [
    "receipt", "invoice", "order confirmation", "payment",
    "your order", "purchase", "transaction"
]

# Social notification patterns
SOCIAL_SENDERS = [
    "linkedin.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "notifications@",
    "noreply@discord.com", "slack.com"
]


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
        return {}
    except Exception as e:
        print(f"Gmail error: {e}")
        return {}


def search_messages(query: str, max_results: int = 10) -> List[dict]:
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


def get_message_body(message: dict) -> str:
    """Extract plain text body from message."""
    import base64

    payload = message.get("payload", {})

    # Check for simple body
    if "body" in payload and payload["body"].get("data"):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Check parts for text/plain
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Fallback: check nested parts
    for part in payload.get("parts", []):
        for subpart in part.get("parts", []):
            if subpart.get("mimeType") == "text/plain":
                data = subpart.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    return ""


def modify_message_labels(message_id: str, add_labels: List[str] = None, remove_labels: List[str] = None):
    """Add or remove labels from a message."""
    body = {}
    if add_labels:
        body["addLabelIds"] = add_labels
    if remove_labels:
        body["removeLabelIds"] = remove_labels

    if body:
        gmail_api_request(f"messages/{message_id}/modify", method="POST", body=body)


def send_telegram(message: str, email_data: dict = None) -> bool:
    """Send a message via Telegram, with optional email action buttons."""
    # If email_data provided, use the advanced email actions module
    if email_data:
        try:
            from email_actions import send_email_alert_with_buttons
            msg_id = send_email_alert_with_buttons(
                int(TELEGRAM_CHAT_ID),
                message,
                email_data
            )
            return msg_id is not None
        except ImportError:
            print("email_actions module not available, falling back to plain message")
        except Exception as e:
            print(f"Email actions error: {e}, falling back to plain message")

    # Fallback: plain message without buttons
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
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def load_state() -> dict:
    """Load the state file (last check time, processed message IDs)."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {
            "last_check": None,
            "processed_ids": []
        }


def save_state(state: dict):
    """Save state to file."""
    # Keep only last 500 processed IDs to prevent unbounded growth
    state["processed_ids"] = state["processed_ids"][-500:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def matches_pattern(text: str, patterns: List[str]) -> bool:
    """Check if text matches any pattern (case insensitive)."""
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in patterns)


def is_priority_sender(from_addr: str) -> bool:
    """Check if sender is a priority contact."""
    return matches_pattern(from_addr, PRIORITY_SENDERS)


def is_build_failure(from_addr: str, subject: str) -> bool:
    """Check if this is a build failure notification."""
    if matches_pattern(from_addr, BUILD_FAILURE_SENDERS):
        if matches_pattern(subject, BUILD_FAILURE_SUBJECTS):
            return True
    return False


def is_newsletter(from_addr: str) -> bool:
    """Check if this is a newsletter."""
    return matches_pattern(from_addr, NEWSLETTER_SENDERS)


def is_receipt(from_addr: str, subject: str) -> bool:
    """Check if this is a receipt/transaction email."""
    return matches_pattern(from_addr, RECEIPT_SENDERS) or matches_pattern(subject, RECEIPT_SUBJECTS)


def is_social(from_addr: str) -> bool:
    """Check if this is a social media notification."""
    return matches_pattern(from_addr, SOCIAL_SENDERS)


def is_automated_email(from_addr: str) -> bool:
    """Check if email is from an automated system (noreply, etc)."""
    automated_patterns = ["noreply", "no-reply", "donotreply", "automated", "mailer-daemon"]
    return matches_pattern(from_addr, automated_patterns)


def is_reply_to_me(headers: dict) -> bool:
    """Check if this is a reply to an email I sent."""
    subject = headers.get("subject", "")
    return subject.lower().startswith("re:") or subject.lower().startswith("fw:")


def categorize_email(headers: dict, body: str) -> Tuple[str, Optional[str]]:
    """
    Categorize an email and decide what to do (legacy mode).

    Returns: (action, label_id)
    - action: "alert" (notify user), "organize" (just label), "ignore" (already read/handled)
    - label_id: which label to apply (if any)
    """
    from_addr = headers.get("from", "")
    subject = headers.get("subject", "")

    # Priority senders always get alerts
    if is_priority_sender(from_addr):
        return ("alert", LEGACY_LABELS["needs_response"])

    # Build failures - organize but don't alert (noise)
    if is_build_failure(from_addr, subject):
        return ("organize", LEGACY_LABELS["build_failures"])

    # Newsletters - organize silently
    if is_newsletter(from_addr):
        return ("organize", LEGACY_LABELS["newsletters"])

    # Receipts - organize silently
    if is_receipt(from_addr, subject):
        return ("organize", LEGACY_LABELS["receipts"])

    # Social notifications - organize silently
    if is_social(from_addr):
        return ("organize", LEGACY_LABELS["social"])

    # Replies to threads I'm in - probably needs attention
    if is_reply_to_me(headers) and not is_automated_email(from_addr):
        return ("alert", LEGACY_LABELS["needs_response"])

    # Real person email (not automated) - alert
    if not is_automated_email(from_addr):
        # Looks like a real person wrote this
        return ("alert", LEGACY_LABELS["needs_response"])

    # Default: automated stuff we haven't categorized
    return ("ignore", None)


def categorize_email_smart(headers: dict, body: str) -> Tuple[str, List[str]]:
    """
    AI-powered email categorization using smart labels.

    Returns: (action, smart_labels_list)
    - action: "alert" (notify user), "organize" (just label)
    - smart_labels_list: List of smart label names to apply
    """
    from gmail_labels import categorize_email_with_ai

    from_addr = headers.get("from", "")

    # Get AI categorization
    smart_labels = categorize_email_with_ai(headers, body)

    # Determine action based on labels
    # Alert for labels that need attention
    alert_labels = {"Action-Required", "Needs-Reply", "Creative", "Networking"}
    silent_labels = {"Auto-Handled", "Money-Out", "Money-In"}

    # Priority senders always get alerts (override AI decision)
    if is_priority_sender(from_addr):
        if "Action-Required" not in smart_labels:
            smart_labels.insert(0, "Action-Required")
        return ("alert", smart_labels)

    # Check if any alert-worthy label
    if any(label in alert_labels for label in smart_labels):
        # Check if also automated
        if not is_automated_email(from_addr):
            return ("alert", smart_labels)

    # Money labels - don't alert, just organize
    if any(label in silent_labels for label in smart_labels):
        return ("organize", smart_labels)

    # Default: if AI assigned labels, organize; otherwise ignore
    if smart_labels:
        # If only Auto-Handled, no alert
        if smart_labels == ["Auto-Handled"]:
            return ("organize", smart_labels)
        # Otherwise alert (be safe, notify for unknown stuff)
        return ("alert", smart_labels)

    return ("organize", ["Auto-Handled"])


def generate_email_summary_with_ai(headers: dict, body: str) -> tuple:
    """
    Use Claude CLI (--print mode) to generate an intelligent summary and actions from email content.
    Uses Claude Sonnet for speed via the local Claude Code installation.
    Falls back to basic summary if CLI fails.
    Returns: (summary, actions_list)
    """
    import subprocess

    from_addr = headers.get("from", "Unknown")
    subject = headers.get("subject", "No subject")

    # Clean the body - remove excessive whitespace, signatures, quoted text
    clean_body = body.strip()
    # Remove common email footer patterns
    for marker in ["\n--\n", "\n___", "\nSent from", "\nGet Outlook", "\n>", "________________________________"]:
        if marker in clean_body:
            clean_body = clean_body.split(marker)[0]

    # Truncate if too long (save tokens)
    if len(clean_body) > 2000:
        clean_body = clean_body[:2000] + "..."

    # Build prompt for Claude
    prompt = f"""You are James's personal assistant. Analyze this email and provide:
1. A concise summary (1-2 sentences) of what this email is about and what the sender wants
2. 2-4 specific action items James should take

Be specific and practical. Don't be vague.

FROM: {from_addr}
SUBJECT: {subject}
BODY:
{clean_body}

Respond in this exact format:
SUMMARY: [your summary here]
ACTIONS:
- [action 1]
- [action 2]
- [action 3]"""

    try:
        # Use Claude CLI with --print mode (non-interactive)
        # Using Opus 4.5 for best quality analysis
        result = subprocess.run(
            ["claude", "--print", "--model", "opus"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/opt/claudius"
        )

        if result.returncode != 0:
            print(f"Claude CLI error: {result.stderr}")
            return generate_email_summary_basic(headers, body)

        content = result.stdout.strip()

        # Parse the response
        summary = ""
        actions = []

        lines = content.strip().split("\n")
        in_actions = False

        for line in lines:
            line = line.strip()
            if line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
            elif line.startswith("ACTIONS:"):
                in_actions = True
            elif in_actions and line.startswith("-"):
                actions.append(line[1:].strip())

        if summary and actions:
            return summary, actions
        else:
            # Parsing failed, use fallback
            print(f"Claude response parsing failed, content was: {content[:200]}")
            return generate_email_summary_basic(headers, body)

    except subprocess.TimeoutExpired:
        print("Claude CLI timed out")
        return generate_email_summary_basic(headers, body)
    except Exception as e:
        print(f"Claude summary failed: {e}")
        return generate_email_summary_basic(headers, body)


def generate_email_summary_basic(headers: dict, body: str) -> tuple:
    """
    Basic keyword-based summary as fallback.
    Returns: (summary, actions_list)
    """
    subject = headers.get("subject", "").lower()
    from_addr = headers.get("from", "").lower()
    body_lower = body.lower()

    summary = ""
    actions = []

    # Meeting invites
    if "meeting" in subject or "calendar" in subject or "teams" in body_lower or "zoom" in body_lower:
        summary = "Meeting invitation or calendar request"
        actions = ["Check meeting time/date", "Add to calendar", "Confirm attendance"]

    # Job/business opportunities
    elif "introduction" in subject or "introductory" in subject or "opportunity" in body_lower:
        summary = "Introduction or potential opportunity"
        actions = ["Review the proposal", "Research the sender/company", "Schedule a call if interested"]

    # Replies/follow-ups
    elif subject.startswith("re:") or "following up" in body_lower:
        summary = "Reply to previous conversation"
        actions = ["Read the context", "Respond if needed"]

    # Questions
    elif "?" in subject or "question" in body_lower or "help" in body_lower:
        summary = "Someone has a question for you"
        actions = ["Read and respond", "Provide the requested info"]

    # Invoices/payments
    elif "invoice" in subject or "payment" in subject or "receipt" in subject:
        summary = "Financial document - invoice, payment, or receipt"
        actions = ["Review the amount", "Save for records", "Pay if required"]

    # Urgent
    elif "urgent" in subject or "asap" in body_lower or "immediately" in body_lower:
        summary = "Marked as urgent"
        actions = ["Read immediately", "Take prompt action"]

    # Default for real person emails
    else:
        summary = "Personal email requiring attention"
        actions = ["Read and assess", "Reply if needed"]

    return summary, actions


def generate_email_summary(headers: dict, body: str) -> tuple:
    """
    Generate a summary and actions - tries AI first, falls back to basic.
    """
    return generate_email_summary_with_ai(headers, body)


def format_email_alert(headers: dict, body: str, message: dict = None) -> tuple:
    """Format an email for Telegram notification with summary and actions.

    Returns: (alert_text, email_data_for_buttons)
    """
    from_addr = headers.get("from", "Unknown")
    subject = headers.get("subject", "No subject")

    # Clean up from address for display
    from_display = from_addr
    from_email = ""
    if "<" in from_addr:
        match = re.match(r'"?([^"<]+)"?\s*<([^>]+)>', from_addr)
        if match:
            from_display = f"{match.group(1).strip()} ({match.group(2)})"
            from_email = match.group(2)
    elif "@" in from_addr:
        from_email = from_addr

    # Generate summary and actions
    summary, actions = generate_email_summary(headers, body)

    # Build the message
    msg = f"📧 *New Email*\n\n"
    msg += f"*From:* {from_display}\n"
    msg += f"*Subject:* {subject}\n\n"

    # Add sender context from Engram (if available)
    try:
        from email_intelligence import get_sender_context, is_learned_priority_sender

        # Check if this is a learned sender
        is_learned, sender_data = is_learned_priority_sender(from_addr)
        if is_learned and sender_data:
            msg += f"🌟 _Frequent contact ({sender_data.get('reply_count', 0)} replies)_\n\n"

        # Get any memory context
        context = get_sender_context(from_addr)
        if context:
            msg += f"🧠 _Context: {context}_\n\n"
    except Exception as e:
        print(f"Sender context error: {e}")

    # Add thread context if applicable
    if message:
        try:
            from email_intelligence import get_thread_context, is_active_thread, update_thread_info

            thread_id = message.get("threadId")
            if thread_id:
                # Update thread tracking
                thread_info = update_thread_info(thread_id, message, headers)

                if is_active_thread(thread_id):
                    msg_count = thread_info.get("message_count", 0)
                    msg += f"🔗 _Active thread ({msg_count} messages)_\n\n"
        except Exception as e:
            print(f"Thread context error: {e}")

    # Add attachment info
    if message:
        try:
            from email_intelligence import detect_attachments, format_attachment_info

            attachments = detect_attachments(message)
            if attachments:
                att_info = format_attachment_info(attachments)
                msg += f"{att_info}\n\n"
        except Exception as e:
            print(f"Attachment detection error: {e}")

    # Add summary
    msg += f"*Summary:* {summary}\n\n"

    # Add suggested actions
    if actions:
        msg += "*Actions:*\n"
        for action in actions:
            msg += f"• {action}\n"

    # Build email_data for action buttons
    email_data = {
        "message_id": message.get("id") if message else None,
        "thread_id": message.get("threadId") if message else None,
        "from": from_display,
        "from_email": from_email,
        "subject": subject,
        "body": body[:2000]  # Truncate for storage
    }

    return msg, email_data


def check_new_emails():
    """Main function - check for new emails and process them."""
    state = load_state()
    processed_ids = set(state.get("processed_ids", []))

    # Search for unread emails from the last hour
    # This catches anything that came in since last check, with buffer
    query = "is:unread newer_than:1h"
    messages = search_messages(query, max_results=20)

    if not messages:
        print("No new unread emails")
        return

    alerts_sent = 0
    organized = 0

    # Import smart labels if enabled
    if USE_SMART_LABELS:
        try:
            from gmail_labels import apply_labels_to_message, get_label_id
            smart_labels_available = True
            print("Using smart labels (AI-powered)")
        except ImportError as e:
            print(f"Smart labels not available: {e}")
            smart_labels_available = False
    else:
        smart_labels_available = False

    for msg_ref in messages:
        msg_id = msg_ref.get("id")

        # Skip if we've already processed this
        if msg_id in processed_ids:
            continue

        # Get full message details
        message = get_message(msg_id)
        if not message:
            continue

        headers = get_message_headers(message)
        body = get_message_body(message)

        # Use smart labels if available, otherwise legacy
        if USE_SMART_LABELS and smart_labels_available:
            action, smart_labels = categorize_email_smart(headers, body)
            label_names_str = ", ".join(smart_labels) if smart_labels else "none"

            if action == "alert":
                # Send Telegram notification with full context and buttons
                alert_msg, email_data = format_email_alert(headers, body, message)

                # Add labels to alert message
                if smart_labels:
                    label_emoji_map = {
                        "Action-Required": "🔴",
                        "Needs-Reply": "🟡",
                        "Money-In": "🟢",
                        "Money-Out": "🟠",
                        "Creative": "🔵",
                        "Networking": "🟣",
                        "Auto-Handled": "⚫"
                    }
                    label_tags = " ".join([label_emoji_map.get(l, "🏷️") + l for l in smart_labels])
                    alert_msg = f"{label_tags}\n\n{alert_msg}"

                if send_telegram(alert_msg, email_data=email_data):
                    alerts_sent += 1
                    print(f"Alerted: {headers.get('subject', 'No subject')[:50]} [{label_names_str}]")

                    # Track for follow-up nudges
                    try:
                        from email_intelligence import track_for_followup, is_learned_priority_sender
                        is_priority = is_priority_sender(headers.get("from", "")) or \
                                      is_learned_priority_sender(headers.get("from", ""))[0]
                        track_for_followup(msg_id, headers, is_priority=is_priority)
                    except Exception as e:
                        print(f"Follow-up tracking error: {e}")

                # Apply smart labels
                if smart_labels:
                    apply_labels_to_message(msg_id, smart_labels)

            elif action == "organize" and smart_labels:
                # Just apply labels, no alert
                apply_labels_to_message(msg_id, smart_labels)
                organized += 1
                print(f"Organized: {headers.get('subject', 'No subject')[:50]} [{label_names_str}]")

        else:
            # Legacy mode
            action, label_id = categorize_email(headers, body)

            if action == "alert":
                # Send Telegram notification with full context and buttons
                alert_msg, email_data = format_email_alert(headers, body, message)
                if send_telegram(alert_msg, email_data=email_data):
                    alerts_sent += 1
                    print(f"Alerted: {headers.get('subject', 'No subject')[:50]}")

                    # Track for follow-up nudges
                    try:
                        from email_intelligence import track_for_followup, is_learned_priority_sender
                        is_priority = is_priority_sender(headers.get("from", "")) or \
                                      is_learned_priority_sender(headers.get("from", ""))[0]
                        track_for_followup(msg_id, headers, is_priority=is_priority)
                    except Exception as e:
                        print(f"Follow-up tracking error: {e}")

                # Apply label if specified
                if label_id:
                    modify_message_labels(msg_id, add_labels=[label_id])

            elif action == "organize" and label_id:
                # Just apply label, no alert
                modify_message_labels(msg_id, add_labels=[label_id])
                organized += 1
                print(f"Organized: {headers.get('subject', 'No subject')[:50]} -> {label_id}")

        # Mark as processed
        processed_ids.add(msg_id)

    # Save state
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state["processed_ids"] = list(processed_ids)
    save_state(state)

    print(f"Done. Alerts: {alerts_sent}, Organized: {organized}")


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Test mode: just print what we'd do
        print("Running in test mode...")
        check_new_emails()
    else:
        check_new_emails()


if __name__ == "__main__":
    main()
