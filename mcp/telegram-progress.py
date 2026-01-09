#!/usr/bin/env python3
"""
Claudius Telegram MCP Server

A simple MCP server that provides a telegram_progress tool for real-time
progress updates. When Claudius uses TodoWrite, it can also call this
tool to send instant notifications.

Usage:
    python3 telegram-progress.py

The server communicates via stdio (stdin/stdout) as per MCP protocol.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "7070679785")


def send_telegram(text: str, parse_mode: str = "HTML") -> dict:
    """Send a message to Telegram and return result."""
    if not TELEGRAM_BOT_TOKEN:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN not configured"}

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_OWNER_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            return {"success": True, "message_id": result.get("result", {}).get("message_id")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_initialize(request_id: int) -> dict:
    """Handle MCP initialize request."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "claudius-telegram",
                "version": "1.0.0"
            }
        }
    }


def handle_tools_list(request_id: int) -> dict:
    """Handle tools/list request."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "tools": [
                {
                    "name": "telegram_progress",
                    "description": "Send a real-time progress update to the owner via Telegram. Use this when starting a task, completing a task, or reporting status changes. The message is sent immediately.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "The progress message to send. Use emojis for status: 🔄 for in-progress, ✅ for completed, ⏳ for pending, ❌ for failed. Example: '🔄 Starting database backup...'"
                            },
                            "task_name": {
                                "type": "string",
                                "description": "Optional: Short name of the task (shown in bold)"
                            },
                            "status": {
                                "type": "string",
                                "enum": ["started", "in_progress", "completed", "failed", "info"],
                                "description": "Optional: Task status for automatic emoji selection"
                            }
                        },
                        "required": ["message"]
                    }
                },
                {
                    "name": "telegram_todo_update",
                    "description": "Send a formatted todo list update to Telegram. Use this after updating your todo list to show the owner current progress on all tasks.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "todos": {
                                "type": "array",
                                "description": "Array of todo items with content and status",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "content": {"type": "string"},
                                        "status": {
                                            "type": "string",
                                            "enum": ["pending", "in_progress", "completed"]
                                        }
                                    },
                                    "required": ["content", "status"]
                                }
                            }
                        },
                        "required": ["todos"]
                    }
                }
            ]
        }
    }


def handle_tool_call(request_id: int, tool_name: str, arguments: dict) -> dict:
    """Handle tools/call request."""
    if tool_name == "telegram_progress":
        message = arguments.get("message", "")
        task_name = arguments.get("task_name")
        status = arguments.get("status")

        # Auto-add emoji based on status if not already present
        status_emojis = {
            "started": "🚀",
            "in_progress": "🔄",
            "completed": "✅",
            "failed": "❌",
            "info": "ℹ️"
        }

        # Format message
        if task_name:
            formatted = f"<b>{task_name}</b>\n{message}"
        else:
            formatted = message

        # Add emoji prefix if status provided and message doesn't start with emoji
        if status and status in status_emojis:
            if not any(formatted.startswith(e) for e in status_emojis.values()):
                formatted = f"{status_emojis[status]} {formatted}"

        result = send_telegram(formatted)

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Telegram notification sent: {result}"
                    }
                ]
            }
        }

    elif tool_name == "telegram_todo_update":
        todos = arguments.get("todos", [])

        if not todos:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "No todos provided"}]
                }
            }

        # Format todo list
        lines = ["<b>📋 Claudius Progress:</b>"]
        for todo in todos:
            content = todo.get("content", "")
            status = todo.get("status", "pending")

            emoji = {"completed": "✅", "in_progress": "🔄", "pending": "⏳"}.get(status, "⏳")
            lines.append(f"{emoji} {content}")

        message = "\n".join(lines)
        result = send_telegram(message)

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Todo update sent to Telegram: {len(todos)} items"
                    }
                ]
            }
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Unknown tool: {tool_name}"
            }
        }


def main():
    """Main MCP server loop - reads JSON-RPC from stdin, writes to stdout."""
    # Log to stderr (visible in Claude CLI logs)
    print(f"[{datetime.now().isoformat()}] Claudius Telegram MCP starting...", file=sys.stderr)

    if not TELEGRAM_BOT_TOKEN:
        print("[WARNING] TELEGRAM_BOT_TOKEN not set - notifications will fail", file=sys.stderr)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            method = request.get("method", "")
            request_id = request.get("id")
            params = request.get("params", {})

            # Handle different MCP methods
            if method == "initialize":
                response = handle_initialize(request_id)
            elif method == "initialized":
                # Notification, no response needed
                continue
            elif method == "tools/list":
                response = handle_tools_list(request_id)
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                response = handle_tool_call(request_id, tool_name, arguments)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }

            # Write response
            print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            if request_id:
                error_response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }
                print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()
