#!/usr/bin/env python3
"""
Email Quick Actions for Telegram

Provides inline keyboard buttons for email alerts:
- Reply Later (snooze)
- Archive
- Show Full Email
- Draft Reply
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# Config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8387428119:AAEGEeSCBSdw7y4SSv9FV_7rDzjDyu-SNmQ")
TELEGRAM_CHAT_ID = "7070679785"
CREDENTIALS_FILE = "/opt/claudius/.google_workspace_mcp/credentials/james.d.guy@gmail.com.json"
ACTION_STATE_FILE = "/opt/claudius/email_actions_state.json"

# Store pending email actions (message_id -> email details)
# This maps Telegram message IDs to email data so we can handle button presses


def load_credentials() -> dict:
    """Load OAuth credentials from file."""
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def refresh_token(creds: dict) -> str:
    """Refresh the OAuth access token if needed."""
    import urllib.parse

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
        return {"error": error_body}
    except Exception as e:
        print(f"Gmail error: {e}")
        return {"error": str(e)}


def telegram_api(method: str, data: dict) -> dict:
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def load_action_state() -> dict:
    """Load pending actions state."""
    try:
        with open(ACTION_STATE_FILE) as f:
            return json.load(f)
    except:
        return {"pending_actions": {}}


def save_action_state(state: dict):
    """Save actions state."""
    # Keep only last 100 entries
    if len(state["pending_actions"]) > 100:
        # Remove oldest entries
        sorted_keys = sorted(
            state["pending_actions"].keys(),
            key=lambda k: state["pending_actions"][k].get("created", "")
        )
        for key in sorted_keys[:-100]:
            del state["pending_actions"][key]

    with open(ACTION_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def register_email_for_actions(telegram_msg_id: int, email_data: dict):
    """Register an email alert for quick actions."""
    state = load_action_state()
    state["pending_actions"][str(telegram_msg_id)] = {
        "message_id": email_data["message_id"],
        "thread_id": email_data.get("thread_id"),
        "from": email_data["from"],
        "from_email": email_data.get("from_email"),
        "subject": email_data["subject"],
        "body": email_data.get("body", "")[:2000],  # Truncate for storage
        "created": datetime.now(timezone.utc).isoformat()
    }
    save_action_state(state)


def get_email_for_action(telegram_msg_id: int) -> Optional[dict]:
    """Get email data for a Telegram message ID."""
    state = load_action_state()
    return state["pending_actions"].get(str(telegram_msg_id))


def build_email_action_keyboard(include_draft: bool = True) -> dict:
    """Build inline keyboard for email actions."""
    keyboard = [
        [
            {"text": "⏰ Reply Later", "callback_data": "email:snooze"},
            {"text": "🗑️ Delete", "callback_data": "email:delete"}
        ],
        [
            {"text": "📖 Show Full", "callback_data": "email:show"}
        ]
    ]

    if include_draft:
        keyboard[1].append({"text": "✍️ Draft Reply", "callback_data": "email:draft"})

    return {"inline_keyboard": keyboard}


def send_email_alert_with_buttons(chat_id: int, alert_text: str, email_data: dict) -> Optional[int]:
    """Send an email alert with action buttons and register it."""
    keyboard = build_email_action_keyboard()

    result = telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": alert_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": keyboard
    })

    if result.get("ok"):
        msg_id = result["result"]["message_id"]
        register_email_for_actions(msg_id, email_data)
        return msg_id

    return None


def handle_email_callback(callback_query: dict) -> bool:
    """
    Handle email action button press.
    Returns True if handled, False if not an email callback.
    """
    callback_id = callback_query.get("id")
    data = callback_query.get("data", "")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    message_id = callback_query.get("message", {}).get("message_id")

    if not data.startswith("email:"):
        return False

    action = data.replace("email:", "")

    # Get the email data
    email_data = get_email_for_action(message_id)
    if not email_data:
        telegram_api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Email data expired. Please check Gmail directly.",
            "show_alert": True
        })
        return True

    # Handle each action
    if action == "snooze":
        handle_snooze(callback_id, chat_id, message_id, email_data)
    elif action == "delete" or action == "archive":
        # "archive" kept for backwards compatibility with old buttons
        handle_delete(callback_id, chat_id, message_id, email_data)
    elif action == "show":
        handle_show_full(callback_id, chat_id, message_id, email_data)
    elif action == "draft":
        handle_draft_reply(callback_id, chat_id, message_id, email_data)
    else:
        telegram_api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": f"Unknown action: {action}"
        })

    return True


def handle_snooze(callback_id: str, chat_id: int, message_id: int, email_data: dict):
    """Handle 'Reply Later' - add to follow-up tracking."""
    from email_intelligence import track_for_followup

    # Track for follow-up (will remind in 24 hours)
    headers = {
        "from": email_data["from"],
        "subject": email_data["subject"]
    }
    track_for_followup(email_data["message_id"], headers, is_priority=True)

    # Acknowledge
    telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": "⏰ Snoozed! I'll remind you in 24 hours if you haven't replied.",
        "show_alert": False
    })

    # Update the message to show it's snoozed
    telegram_api("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": [[
            {"text": "⏰ Snoozed - I'll remind you", "callback_data": "email:snoozed"}
        ]]}
    })


def handle_delete(callback_id: str, chat_id: int, message_id: int, email_data: dict):
    """Handle 'Delete' - move to trash."""
    gmail_message_id = email_data["message_id"]

    # Add TRASH label (moves to trash)
    result = gmail_api_request(
        f"messages/{gmail_message_id}/modify",
        method="POST",
        body={"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}
    )

    if "error" not in result:
        telegram_api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "🗑️ Deleted!",
            "show_alert": False
        })

        # Update the message to show it's deleted
        telegram_api("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": [[
                {"text": "🗑️ Deleted", "callback_data": "email:deleted"}
            ]]}
        })

        # Also remove from pending follow-ups
        from email_intelligence import mark_followup_complete
        if email_data.get("from_email"):
            mark_followup_complete(email_data["from_email"])
    else:
        telegram_api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": f"❌ Delete failed: {result['error'][:50]}",
            "show_alert": True
        })


def handle_show_full(callback_id: str, chat_id: int, message_id: int, email_data: dict):
    """Handle 'Show Full' - display full email body."""
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})

    body = email_data.get("body", "")
    if not body:
        # Fetch the full body from Gmail
        gmail_message_id = email_data["message_id"]
        msg = gmail_api_request(f"messages/{gmail_message_id}?format=full")
        if msg and "payload" in msg:
            import base64
            payload = msg["payload"]

            # Try to extract body
            if "body" in payload and payload["body"].get("data"):
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            else:
                for part in payload.get("parts", []):
                    if part.get("mimeType") == "text/plain":
                        data = part.get("body", {}).get("data", "")
                        if data:
                            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                            break

    if body:
        # Clean up and truncate if needed
        body = body.strip()
        if len(body) > 3500:
            body = body[:3500] + "\n\n... (truncated)"

        # Send as a reply
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": f"📧 *Full Email:*\n\n{body}",
            "parse_mode": "Markdown",
            "reply_to_message_id": message_id
        })
    else:
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ Couldn't retrieve email body. Check Gmail directly.",
            "reply_to_message_id": message_id
        })


def handle_draft_reply(callback_id: str, chat_id: int, message_id: int, email_data: dict):
    """Handle 'Draft Reply' - use Claude to draft a response."""
    telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": "✍️ Drafting reply with Claude Opus...",
        "show_alert": False
    })

    import subprocess

    # Use Claude to draft a reply
    prompt = f"""You are James's personal assistant. Draft a professional but friendly email reply to this message.
Keep it concise and natural. Don't be overly formal.

FROM: {email_data['from']}
SUBJECT: {email_data['subject']}
BODY:
{email_data.get('body', '')[:1500]}

Write ONLY the email reply body, nothing else. No "Subject:" line. Start with an appropriate greeting."""

    try:
        result = subprocess.run(
            ["claude", "--print", "--model", "opus"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=45,
            cwd="/opt/claudius"
        )

        if result.returncode == 0 and result.stdout.strip():
            draft = result.stdout.strip()

            # Send the draft
            telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": f"✍️ *Draft Reply:*\n\n{draft}\n\n_Edit this and send via Gmail, or tell me to adjust it._",
                "parse_mode": "Markdown",
                "reply_to_message_id": message_id
            })
        else:
            telegram_api("sendMessage", {
                "chat_id": chat_id,
                "text": "❌ Couldn't generate draft. Claude CLI error.",
                "reply_to_message_id": message_id
            })

    except subprocess.TimeoutExpired:
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": "❌ Draft generation timed out. Try again later.",
            "reply_to_message_id": message_id
        })
    except Exception as e:
        telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": f"❌ Draft error: {str(e)[:50]}",
            "reply_to_message_id": message_id
        })


def handle_direct_action(telegram_message_id: int, action: str) -> dict:
    """
    Handle email action directly via HTTP API (no Telegram callback).

    Args:
        telegram_message_id: The Telegram message ID associated with the email
        action: One of "snooze", "delete", "show", "draft"

    Returns:
        dict with "success" and "message" or "error"
    """
    # Validate action
    valid_actions = ["snooze", "delete", "show", "draft", "archive"]
    if action not in valid_actions:
        return {"success": False, "error": f"Invalid action: {action}. Valid: {valid_actions}"}

    # Get the email data
    email_data = get_email_for_action(telegram_message_id)
    if not email_data:
        return {"success": False, "error": "Email data not found or expired"}

    # Map archive to delete (backwards compat)
    if action == "archive":
        action = "delete"

    try:
        if action == "snooze":
            from email_intelligence import track_for_followup
            headers = {
                "from": email_data["from"],
                "subject": email_data["subject"]
            }
            track_for_followup(email_data["message_id"], headers, is_priority=True)

            # Update Telegram message
            telegram_api("editMessageReplyMarkup", {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": telegram_message_id,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "⏰ Snoozed - I'll remind you", "callback_data": "email:snoozed"}
                ]]}
            })
            return {"success": True, "message": "Email snoozed. Reminder in 24 hours."}

        elif action == "delete":
            gmail_message_id = email_data["message_id"]
            result = gmail_api_request(
                f"messages/{gmail_message_id}/modify",
                method="POST",
                body={"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}
            )

            if "error" in result:
                return {"success": False, "error": f"Gmail API error: {result['error']}"}

            # Update Telegram message
            telegram_api("editMessageReplyMarkup", {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": telegram_message_id,
                "reply_markup": {"inline_keyboard": [[
                    {"text": "🗑️ Deleted", "callback_data": "email:deleted"}
                ]]}
            })

            # Remove from follow-ups
            from email_intelligence import mark_followup_complete
            if email_data.get("from_email"):
                mark_followup_complete(email_data["from_email"])

            return {"success": True, "message": "Email deleted."}

        elif action == "show":
            body = email_data.get("body", "")
            if not body:
                gmail_message_id = email_data["message_id"]
                msg = gmail_api_request(f"messages/{gmail_message_id}?format=full")
                if msg and "payload" in msg:
                    import base64
                    payload = msg["payload"]
                    if "body" in payload and payload["body"].get("data"):
                        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
                    else:
                        for part in payload.get("parts", []):
                            if part.get("mimeType") == "text/plain":
                                data = part.get("body", {}).get("data", "")
                                if data:
                                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                                    break

            if body:
                body = body.strip()
                if len(body) > 3500:
                    body = body[:3500] + "\n\n... (truncated)"
                return {"success": True, "body": body, "subject": email_data["subject"], "from": email_data["from"]}
            else:
                return {"success": False, "error": "Could not retrieve email body"}

        elif action == "draft":
            import subprocess
            prompt = f"""You are James's personal assistant. Draft a professional but friendly email reply to this message.
Keep it concise and natural. Don't be overly formal.

FROM: {email_data['from']}
SUBJECT: {email_data['subject']}
BODY:
{email_data.get('body', '')[:1500]}

Write ONLY the email reply body, nothing else. No "Subject:" line. Start with an appropriate greeting."""

            result = subprocess.run(
                ["claude", "--print", "--model", "opus"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=45,
                cwd="/opt/claudius"
            )

            if result.returncode == 0 and result.stdout.strip():
                draft = result.stdout.strip()
                return {"success": True, "draft": draft, "to": email_data.get("from_email", email_data["from"]), "subject": f"Re: {email_data['subject']}"}
            else:
                return {"success": False, "error": "Draft generation failed"}

    except Exception as e:
        return {"success": False, "error": str(e)}


# Test
if __name__ == "__main__":
    print("Email Actions Module")
    keyboard = build_email_action_keyboard()
    print("Keyboard:", json.dumps(keyboard, indent=2))
