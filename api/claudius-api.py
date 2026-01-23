#!/usr/bin/env python3
"""
Claudius API Server
HTTP API for invoking Claudius (bare metal Claude Emperor) on the Hetzner host.

Claudius is the OVERSEER - handles all infrastructure tasks directly.

Usage:
    python3 claudius-api.py                    # Start on port 3100
    python3 claudius-api.py --port 3100        # Specify port

API Endpoints:
    POST /invoke    - Invoke Claudius with a prompt
                      Body: {
                        "prompt": "your question",
                        "model": "sonnet",      # Optional: opus, sonnet, haiku
                        "timeout": 120,         # Optional: 30-300 seconds
                        "session_id": "uuid"    # Optional: for session continuity
                      }
                      Headers: Authorization: Bearer <CRON_SECRET>
                      Returns: {
                        "success": true,
                        "response": "...",
                        "model": "sonnet",
                        "session_id": "uuid",
                        "cost_usd": 0.02,       # If available
                        "duration_ms": 3400     # If available
                      }

    GET /health     - Health check (no auth required)
                      Returns: {"status": "ok", "claudius": true}

    GET /memory     - Get Claudius memory
                      Headers: Authorization: Bearer <CRON_SECRET>
                      Returns: {"memory": "..."}

    Health Module Endpoints:
    GET  /health/status         - Get current health summary
    GET  /health/auth/status    - Check Garmin authentication status
    POST /health/login          - Login to Garmin (credentials from env vars)
    POST /health/sync           - Trigger manual health data sync

Authentication:
    All endpoints except /health require Bearer token authentication.
    CRON_SECRET - Required for Claudius auth
"""

import json
import subprocess
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import uuid
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import logging
from logging.handlers import RotatingFileHandler

# Add parent directory to path for health module import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Health module imports (lazy loaded to avoid startup failures)
_health_module_loaded = False
_health_context_generator = None
_health_summary_getter = None
_garmin_auth = None
_manual_sync_func = None

# Configuration - updated paths for standalone repo
CLAUDIUS_DIR = os.environ.get("CLAUDIUS_DIR", "/opt/claudius")
CLAUDE_MD = f"{CLAUDIUS_DIR}/CLAUDE.md"
MEMORY_MD = f"{CLAUDIUS_DIR}/MEMORY.md"
PLANNING_MD = f"{CLAUDIUS_DIR}/PLANNING.md"
LOG_FILE = "/var/log/claudius-api.log"
MEMORY_MAX_ENTRIES = 100
DEFAULT_MODEL = "opus"
FALLBACK_MODEL = "sonnet"  # Must be different from default
DEFAULT_TIMEOUT = 120
VALID_MODELS = ["opus", "sonnet", "haiku"]

# Telegram progress reporting
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "7070679785")

# Additional directories Claudius can access
ADDITIONAL_DIRS = [
    "/opt/omniops",      # Main application directory
    "/var/log",          # System logs
    "/etc/caddy",        # Caddy web server config
]

# MCP server for Telegram progress notifications
TELEGRAM_MCP_SERVER = f"{CLAUDIUS_DIR}/mcp/telegram-progress.py"

# System prompt for Claudius identity (appended to Claude's default)
CLAUDIUS_SYSTEM_PROMPT = f"""You are Claudius, the bare metal server Claude (Emperor/Overseer).
Read your instructions from {CLAUDE_MD}.
Read your memory from {MEMORY_MD} for context from previous sessions.
Read your planning document from {PLANNING_MD} for ongoing tasks and improvement ideas.
You have full access to infrastructure tools. Execute tasks autonomously.

IMPORTANT: You have a telegram_progress tool. Use it to send REAL-TIME progress updates:
- Call telegram_progress when STARTING a task (status: "started")
- Call telegram_progress when task is IN PROGRESS with updates
- Call telegram_progress when COMPLETING a task (status: "completed")
- Call telegram_progress if something FAILS (status: "failed")
This keeps the owner informed of your progress in real-time via Telegram."""

# Authentication - uses same secret as Docker app
CRON_SECRET = os.environ.get("CRON_SECRET")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET")

# Clode API endpoint (inside Docker)
CLODE_API_URL = os.environ.get("CLODE_API_URL", "http://localhost:3000/api/admin/claude")

# Keywords that indicate codebase tasks (should delegate to Clode)
CODEBASE_KEYWORDS = [
    # Testing
    r"\btest\b", r"\btests\b", r"\btesting\b", r"\bjest\b", r"\bplaywright\b",
    # Build/compile
    r"\bbuild\b", r"\bcompile\b", r"\btypescript\b", r"\btsc\b", r"\bnpm\b", r"\bnode\b",
    # Code operations
    r"\bcode\b", r"\bfunction\b", r"\bcomponent\b", r"\bapi\b", r"\bendpoint\b",
    r"\brefactor\b", r"\blint\b", r"\beslint\b", r"\bformat\b",
    # Review/debug
    r"\breview\b", r"\bdebug\b", r"\bbug\b", r"\bfix\b", r"\berror\b",
    # Files/modules
    r"\.ts\b", r"\.tsx\b", r"\.js\b", r"\.json\b", r"\bpackage\.json\b",
    r"\blib/", r"\bapp/", r"\bcomponents/", r"\bsrc/",
    # Database/schema
    r"\bsupabase\b", r"\bdatabase\b", r"\bschema\b", r"\bmigration\b", r"\bsql\b",
]

def is_codebase_task(prompt: str) -> bool:
    """Detect if a prompt is about codebase operations (should delegate to Clode)."""
    prompt_lower = prompt.lower()
    for pattern in CODEBASE_KEYWORDS:
        if re.search(pattern, prompt_lower):
            return True
    return False


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the owner via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({
            "chat_id": TELEGRAM_OWNER_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        logger.warning(f"Failed to send Telegram message: {e}")
        return False


def format_todo_update(todos: list) -> str:
    """Format todo list for Telegram display."""
    if not todos:
        return ""

    lines = ["<b>📋 Claudius Progress:</b>"]

    for todo in todos:
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        active_form = todo.get("activeForm", content)

        if status == "completed":
            emoji = "✅"
            display = content
        elif status == "in_progress":
            emoji = "🔄"
            display = active_form
        else:
            emoji = "⏳"
            display = content

        lines.append(f"{emoji} {display}")

    return "\n".join(lines)


def extract_todos_from_text(text: str) -> list:
    """Parse todo-like patterns from response text."""
    todos = []

    # Look for common todo patterns in the result text
    patterns = [
        # Checkbox patterns: - [ ] Task or - [x] Task
        r'[-*]\s*\[([ xX✓✔])\]\s*(.+?)(?:\n|$)',
        # Emoji patterns: ✅ Task or ⏳ Task or 🔄 Task
        r'([✅⏳🔄✓])\s*\*?\*?(.+?)\*?\*?(?:\s*[-–—]\s*.+)?(?:\n|$)',
        # Numbered with status: 1. ~~Task~~ ✓ (completed)
        r'\d+\.\s*~~(.+?)~~.*?(?:completed|done)',
        # Numbered with status: 2. **Task** (in progress)
        r'\d+\.\s*\*\*(.+?)\*\*.*?(?:in progress|working)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                status_char, content = match[0], match[1] if len(match) > 1 else match[0]
                if status_char in ['x', 'X', '✓', '✔', '✅']:
                    status = 'completed'
                elif status_char in ['🔄']:
                    status = 'in_progress'
                else:
                    status = 'pending'
                content = content.strip()
            else:
                content = match.strip()
                status = 'completed' if '~~' in text else 'pending'

            if content and len(content) > 2:
                todos.append({'content': content, 'status': status, 'activeForm': content})

    return todos


def report_todo_progress(cli_response: dict, result_text: str = "") -> None:
    """Extract todo patterns from CLI response and send to Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    try:
        # Use result text from CLI output
        text = result_text or cli_response.get("result", "")

        if not text:
            return

        # Check if the response mentions todos/tasks
        todo_keywords = ['todo', 'task', 'completed', 'in progress', 'pending', '✅', '⏳', '🔄', '[ ]', '[x]']
        has_todo_content = any(kw in text.lower() for kw in todo_keywords)

        if not has_todo_content:
            return

        # Extract todos from text
        todos = extract_todos_from_text(text)

        if todos:
            logger.info(f"Found {len(todos)} todos in response text")
            message = format_todo_update(todos)
            if message:
                send_telegram_message(message)

    except Exception as e:
        logger.warning(f"Failed to report todo progress: {e}")


def call_clode(prompt: str, model: str = "sonnet") -> dict:
    """Delegate codebase operations to Clode (inside Docker container)."""
    if not ADMIN_SECRET:
        return {"success": False, "error": "ADMIN_SECRET not configured for Clode delegation"}

    data = json.dumps({"prompt": prompt, "model": model}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {ADMIN_SECRET}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(CLODE_API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read().decode("utf-8"))
            # Clode returns 'output' not 'response'
            output = result.get("output", result.get("response", ""))
            return {"success": True, "response": output, "delegated_to": "clode"}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"Clode API error: {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Clode connection error: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": f"Clode delegation failed: {str(e)}"}

# Setup logging with rotation
def setup_logging():
    logger = logging.getLogger("claudius")
    logger.setLevel(logging.INFO)

    # Rotating file handler (10MB max, keep 5 backups)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s'
        ))
        logger.addHandler(file_handler)
    except PermissionError:
        pass  # Fall back to console only

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(message)s', datefmt='%Y-%m-%dT%H:%M:%SZ'
    ))
    logger.addHandler(console_handler)

    return logger

logger = setup_logging()


def load_health_module():
    """Lazy-load health module to avoid startup failures if deps missing."""
    global _health_module_loaded, _health_context_generator, _health_summary_getter
    global _garmin_auth, _manual_sync_func

    if _health_module_loaded:
        return True

    try:
        from health import (
            generate_context_block,
            get_health_summary,
            get_garmin_auth,
            manual_sync,
        )
        _health_context_generator = generate_context_block
        _health_summary_getter = get_health_summary
        _garmin_auth = get_garmin_auth()
        _manual_sync_func = manual_sync
        _health_module_loaded = True
        logger.info("Health module loaded successfully")
        return True
    except ImportError as e:
        logger.warning(f"Health module not available: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to load health module: {e}")
        return False


def get_health_context() -> str:
    """Get health context block to prepend to prompts."""
    if not load_health_module() or not _health_context_generator:
        return ""
    try:
        return _health_context_generator()
    except Exception as e:
        logger.warning(f"Failed to generate health context: {e}")
        return ""


class ClaudiusHandler(BaseHTTPRequestHandler):
    # Allow connections from anywhere (standalone mode)
    ALLOWED_ORIGINS = ["*"]

    def log_message(self, format, *args):
        logger.info(args[0])

    def validate_auth(self):
        """Validate Bearer token authentication. Returns True if valid."""
        # Fail-closed: if secret not configured, reject all requests
        if not CRON_SECRET:
            logger.error("CRON_SECRET not configured - rejecting request")
            return False

        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False

        token = auth_header[7:]  # Remove "Bearer " prefix
        return token == CRON_SECRET

    def get_cors_origin(self):
        """Return appropriate CORS origin."""
        origin = self.headers.get("Origin", "")
        # In standalone mode, allow configured origins or return the request origin
        if origin:
            return origin
        return "*"

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", self.get_cors_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_json({})

    def log_to_memory(self, action, content):
        """Log to memory file with automatic rotation (keeps last N entries)."""
        if not os.path.exists(MEMORY_MD):
            return

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        new_line = f"| {timestamp} | {action} | {content}... |\n"

        try:
            with open(MEMORY_MD, "r") as f:
                lines = f.readlines()

            # Find the table rows (lines starting with |)
            header_lines = []
            table_lines = []

            for line in lines:
                if line.strip().startswith("|") and "Date" not in line and "---" not in line:
                    table_lines.append(line)
                else:
                    header_lines.append(line)

            # Add new entry and keep only last N entries
            table_lines.append(new_line)
            if len(table_lines) > MEMORY_MAX_ENTRIES:
                table_lines = table_lines[-MEMORY_MAX_ENTRIES:]

            # Write back
            with open(MEMORY_MD, "w") as f:
                f.writelines(header_lines)
                f.writelines(table_lines)

        except Exception as e:
            logger.error(f"Failed to update memory: {e}")

    def do_GET(self):
        # Parse path and query string
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            # Health check - no auth required
            claudius_ready = os.path.exists(CLAUDE_MD)
            auth_configured = bool(CRON_SECRET)
            clode_delegation_enabled = bool(ADMIN_SECRET)
            health_module_ready = load_health_module()
            self.send_json({
                "status": "ok",
                "claudius": claudius_ready,
                "memory_exists": os.path.exists(MEMORY_MD),
                "auth_configured": auth_configured,
                "clode_delegation": clode_delegation_enabled,
                "health_module": health_module_ready
            })

        elif path == "/health/status":
            # Health status endpoint - requires auth
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if not load_health_module() or not _health_summary_getter:
                self.send_json({"error": "Health module not available"}, 503)
                return

            try:
                summary = _health_summary_getter()
                self.send_json(summary)
            except Exception as e:
                logger.error(f"Failed to get health status: {e}")
                self.send_json({"error": str(e)}, 500)

        elif path == "/health/auth/status":
            # Check Garmin auth status - requires auth
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if not load_health_module() or not _garmin_auth:
                self.send_json({"error": "Health module not available"}, 503)
                return

            try:
                status = _garmin_auth.get_auth_status()
                self.send_json(status)
            except Exception as e:
                logger.error(f"Failed to get auth status: {e}")
                self.send_json({"error": str(e)}, 500)

        elif path == "/memory":
            # Memory endpoint requires auth
            if not self.validate_auth():
                logger.warning(f"Unauthorized /memory request from {self.client_address[0]}")
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if os.path.exists(MEMORY_MD):
                with open(MEMORY_MD, "r") as f:
                    self.send_json({"memory": f.read()})
            else:
                self.send_json({"memory": None, "error": "No memory file"}, 404)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        # Parse path
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Garmin login endpoint (credential-based auth via python-garminconnect)
        if path == "/health/login":
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if not load_health_module() or not _garmin_auth:
                self.send_json({"error": "Health module not available"}, 503)
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"

            try:
                data = json.loads(body) if body else {}
                force_new = data.get("force_new", False)
                success = _garmin_auth.login(force_new=force_new)
                if success:
                    self.send_json({
                        "success": True,
                        "message": "Garmin login successful! Health data sync enabled."
                    })
                else:
                    self.send_json({"error": "Login failed"}, 400)
            except Exception as e:
                logger.error(f"Garmin login failed: {e}")
                self.send_json({"error": str(e)}, 500)
            return

        # Manual health sync endpoint
        if path == "/health/sync":
            if not self.validate_auth():
                self.send_json({"error": "Unauthorized"}, 401)
                return

            if not load_health_module() or not _manual_sync_func:
                self.send_json({"error": "Health module not available"}, 503)
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"

            try:
                data = json.loads(body) if body else {}
                days_back = data.get("days", 7)
                result = _manual_sync_func(days_back=days_back)
                logger.info(f"Manual health sync completed: {result}")
                self.send_json(result)
            except Exception as e:
                logger.error(f"Manual sync failed: {e}")
                self.send_json({"error": str(e)}, 500)
            return

        if path != "/invoke":
            self.send_json({"error": "Not found"}, 404)
            return

        # Require authentication for invoke
        if not self.validate_auth():
            logger.warning(f"Unauthorized /invoke request from {self.client_address[0]}")
            self.send_json({"error": "Unauthorized"}, 401)
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()

        try:
            data = json.loads(body)
            prompt = data.get("prompt", "").strip()
            model = data.get("model", DEFAULT_MODEL)
            timeout = data.get("timeout", DEFAULT_TIMEOUT)
            session_id = data.get("session_id")
            include_health = data.get("include_health", True)  # Health context enabled by default
        except json.JSONDecodeError:
            self.send_json({"success": False, "error": "Invalid JSON"}, 400)
            return

        if not prompt:
            self.send_json({"success": False, "error": "No prompt provided"}, 400)
            return

        # Prepend health context to prompt if enabled and available
        health_context = ""
        if include_health:
            health_context = get_health_context()
            if health_context:
                prompt = f"{health_context}\n\n{prompt}"
                logger.info("Health context injected into prompt")

        # Validate model
        if model not in VALID_MODELS:
            model = DEFAULT_MODEL

        # Clamp timeout to reasonable bounds (30s - 300s)
        timeout = max(30, min(300, int(timeout)))

        # Generate session ID if not provided (enables session tracking)
        if not session_id:
            session_id = str(uuid.uuid4())

        logger.info(f"Processing request (model={model}, session={session_id[:8]}): {prompt[:50]}...")

        # Log to memory with rotation
        self.log_to_memory("API Request", prompt[:50])

        try:
            # Build Claude CLI command with all features enabled
            cmd = [
                "claude", "--print",
                "--model", model,
                "--fallback-model", FALLBACK_MODEL,
                "--tools", "default",
                "--output-format", "json",
                "--append-system-prompt", CLAUDIUS_SYSTEM_PROMPT,
                "--session-id", session_id,
                "--dangerously-skip-permissions"
            ]

            # Add additional directories for tool access
            for dir_path in ADDITIONAL_DIRS:
                if os.path.exists(dir_path):
                    cmd.extend(["--add-dir", dir_path])

            # Add MCP server for Telegram progress notifications
            if os.path.exists(TELEGRAM_MCP_SERVER) and TELEGRAM_BOT_TOKEN:
                # MCP server config: name and command
                mcp_config = json.dumps({
                    "mcpServers": {
                        "telegram": {
                            "command": "python3",
                            "args": [TELEGRAM_MCP_SERVER],
                            "env": {
                                "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
                                "TELEGRAM_OWNER_CHAT_ID": TELEGRAM_OWNER_CHAT_ID
                            }
                        }
                    }
                })
                cmd.extend(["--mcp-config", mcp_config])

            # Invoke Claude CLI
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=CLAUDIUS_DIR
            )

            # Parse JSON output from Claude CLI
            raw_output = result.stdout.strip() or result.stderr.strip()
            response_text = raw_output
            cost_usd = None
            duration_ms = None
            session_id = None

            try:
                cli_response = json.loads(raw_output)
                response_text = cli_response.get("result", raw_output)
                cost_usd = cli_response.get("cost_usd")
                duration_ms = cli_response.get("duration_ms")
                session_id = cli_response.get("session_id")

                # Report todo progress to Telegram
                report_todo_progress(cli_response, response_text)
            except json.JSONDecodeError:
                # Fallback to raw output if not valid JSON
                pass

            # Log response to memory with rotation
            self.log_to_memory("Response", response_text[:50] if response_text else "empty")
            logger.info(f"Request completed successfully (model={model})")

            response_data = {
                "success": True,
                "response": response_text,
                "model": model,
                "health_context_included": bool(health_context)
            }

            # Include metadata if available
            if cost_usd is not None:
                response_data["cost_usd"] = cost_usd
            if duration_ms is not None:
                response_data["duration_ms"] = duration_ms
            if session_id:
                response_data["session_id"] = session_id

            self.send_json(response_data)

        except subprocess.TimeoutExpired:
            logger.error(f"Request timed out after {timeout}s")
            self.send_json({"success": False, "error": f"Timeout after {timeout}s"}, 504)
        except FileNotFoundError:
            logger.error("Claude CLI not found - is it installed?")
            self.send_json({"success": False, "error": "Claude CLI not installed"}, 500)
        except Exception as e:
            logger.error(f"Request failed: {e}")
            self.send_json({"success": False, "error": str(e)}, 500)


def main():
    parser = argparse.ArgumentParser(description="Claudius API Server")
    parser.add_argument("--port", type=int, default=3100, help="Port to listen on")
    args = parser.parse_args()

    # Verify Claudius is set up
    if not os.path.exists(CLAUDE_MD):
        logger.warning(f"{CLAUDE_MD} not found")

    # Check authentication configuration
    if not CRON_SECRET:
        logger.error("CRON_SECRET not set - API will reject all authenticated requests!")
        logger.error("Set CRON_SECRET environment variable to enable authentication")
    else:
        logger.info("Authentication configured (CRON_SECRET set)")

    # Try to load health module at startup
    health_available = load_health_module()

    server = HTTPServer(("0.0.0.0", args.port), ClaudiusHandler)
    logger.info(f"Claudius API starting on port {args.port}")
    logger.info("Endpoints:")
    logger.info("  POST /invoke          - Invoke Claudius (auth required)")
    logger.info("  GET  /health          - Health check (no auth)")
    logger.info("  GET  /memory          - View memory (auth required)")
    if health_available:
        logger.info("Health Module Endpoints:")
        logger.info("  GET  /health/status       - Health summary (auth required)")
        logger.info("  GET  /health/auth/status  - Garmin auth status (auth required)")
        logger.info("  POST /health/login        - Garmin login (auth required)")
        logger.info("  POST /health/sync         - Manual sync (auth required)")
    else:
        logger.warning("Health module not available - health endpoints disabled")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
