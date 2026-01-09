#!/usr/bin/env python3
"""
Claudius MCP Server

MCP server for Claude Code to communicate with Claudius (bare metal infrastructure agent).

Usage:
    python3 mcp/server.py

Environment:
    CLAUDIUS_URL - Claudius API URL (default: http://localhost:3100)
    CRON_SECRET - Authentication token

Add to Claude Code MCP config:
{
  "mcpServers": {
    "claudius": {
      "command": "python3",
      "args": ["/path/to/claudius/mcp/server.py"],
      "env": {
        "CLAUDIUS_URL": "http://77.42.19.161:3100",
        "CRON_SECRET": "your_secret"
      }
    }
  }
}
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime

# Add parent directory to path for board import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from board.advisor_board import AdvisorBoard, DeliberationResult

# Configuration
CLAUDIUS_URL = os.environ.get("CLAUDIUS_URL", "http://localhost:3100")
CRON_SECRET = os.environ.get("CRON_SECRET")

# Initialize board for deliberation
board = AdvisorBoard()


def call_claudius(prompt: str, timeout: int = 120) -> dict:
    """Call Claudius API with a prompt."""
    if not CRON_SECRET:
        return {"success": False, "error": "CRON_SECRET not configured"}

    try:
        url = f"{CLAUDIUS_URL}/invoke"
        data = json.dumps({"prompt": prompt, "timeout": timeout}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CRON_SECRET}"
        }

        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout + 30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def check_health() -> dict:
    """Check Claudius health status."""
    try:
        url = f"{CLAUDIUS_URL}/health"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


def get_memory() -> dict:
    """Get Claudius memory."""
    if not CRON_SECRET:
        return {"success": False, "error": "CRON_SECRET not configured"}

    try:
        url = f"{CLAUDIUS_URL}/memory"
        headers = {"Authorization": f"Bearer {CRON_SECRET}"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_initialize(request_id: int) -> dict:
    """Handle MCP initialize request."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "claudius",
                "version": "2.0.0"
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
                    "name": "ask_claudius",
                    "description": "Talk to Claudius - the infrastructure specialist running on bare metal (outside Docker). Use for: Docker operations (restart, rebuild, logs), disk space management, memory/CPU monitoring, SSL certificate management, deployment and rollback, server health checks, network diagnostics.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Your server/infrastructure task for Claudius"
                            }
                        },
                        "required": ["prompt"]
                    }
                },
                {
                    "name": "check_claudius_health",
                    "description": "Check the health status of Claudius agent.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                },
                {
                    "name": "deliberate",
                    "description": "Invoke the Board of Advisors for a routing decision. Use when deciding whether a task should go to Claudius (infrastructure) or Clode (codebase), or when facing ambiguous requests.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The decision or action to deliberate on"
                            },
                            "context": {
                                "type": "string",
                                "description": "Additional context about the situation (optional)"
                            },
                            "quickCheck": {
                                "type": "boolean",
                                "description": "Just do a quick keyword-based check (faster, no API cost)"
                            }
                        },
                        "required": ["prompt"]
                    }
                },
                {
                    "name": "get_claudius_memory",
                    "description": "Get Claudius's persistent memory file containing session history, known issues, and server quirks.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {}
                    }
                }
            ]
        }
    }


def handle_tool_call(request_id: int, tool_name: str, arguments: dict) -> dict:
    """Handle tools/call request."""

    if tool_name == "ask_claudius":
        prompt = arguments.get("prompt", "")
        if not prompt:
            return error_response(request_id, "Missing prompt")

        result = call_claudius(prompt)

        if result.get("success"):
            text = result.get("response", "No response")
        else:
            text = f"Error: {result.get('error', 'Unknown error')}"

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                **({"isError": True} if not result.get("success") else {})
            }
        }

    elif tool_name == "check_claudius_health":
        health = check_health()
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(health, indent=2)}]
            }
        }

    elif tool_name == "deliberate":
        prompt = arguments.get("prompt", "")
        context = arguments.get("context")
        quick = arguments.get("quickCheck", False)

        if not prompt:
            return error_response(request_id, "Missing prompt")

        if quick:
            result = board.quick_check(prompt)
        else:
            result = board.deliberate(prompt, context)

        lines = [
            f"**Board Decision: {result.decision.upper()}**",
            f"Confidence: {int(result.confidence * 100)}%",
            "",
            f"**Reasoning:** {result.reasoning}",
            "",
            f"**Summary:** {result.summary}"
        ]

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": "\n".join(lines)}]
            }
        }

    elif tool_name == "get_claudius_memory":
        result = get_memory()

        if result.get("memory"):
            text = result["memory"]
        else:
            text = f"Error: {result.get('error', 'No memory available')}"

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                **({"isError": True} if not result.get("memory") else {})
            }
        }

    else:
        return error_response(request_id, f"Unknown tool: {tool_name}")


def error_response(request_id: int, message: str) -> dict:
    """Create an error response."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": message
        }
    }


def main():
    """Main MCP server loop."""
    print(f"[{datetime.now().isoformat()}] Claudius MCP server starting...", file=sys.stderr)
    print(f"[INFO] CLAUDIUS_URL: {CLAUDIUS_URL}", file=sys.stderr)
    print(f"[INFO] CRON_SECRET: {'configured' if CRON_SECRET else 'NOT SET'}", file=sys.stderr)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            method = request.get("method", "")
            request_id = request.get("id")
            params = request.get("params", {})

            if method == "initialize":
                response = handle_initialize(request_id)
            elif method == "initialized":
                continue  # Notification, no response
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
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }

            print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            if request_id:
                error = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32603, "message": str(e)}
                }
                print(json.dumps(error), flush=True)


if __name__ == "__main__":
    main()
