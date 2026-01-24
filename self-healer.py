#!/usr/bin/env python3
"""
Claudius Self-Healing Daemon
============================
Autonomous infrastructure repair system that:
1. Monitors container health
2. Detects known error patterns
3. Applies fixes automatically
4. Learns from new failures via Brain memory

Author: Claudius (the AI, building tools for itself)
Version: 1.0.0
"""

import subprocess
import json
import time
import os
import re
import logging
import socket
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
import requests
from urllib.parse import quote

# Configuration
CHECK_INTERVAL = 60  # seconds between health checks
LOG_FILE = "/opt/claudius/logs/self-healer.log"
LEARNED_PATTERNS_FILE = "/opt/claudius/learned-patterns.json"
BRAIN_API = "http://localhost:3000/api/admin/brain"
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
DOCKER_SOCKET = "/var/run/docker.sock"
# Load .env if token not set
if not TELEGRAM_BOT_TOKEN:
    try:
        with open("/opt/omniops/.env", "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))
        TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    except Exception:
        pass

OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "7070679785")

# Enable autonomous investigation for unknown errors
ENABLE_INVESTIGATION = True
INVESTIGATION_COOLDOWN = 3600  # 1 hour between investigating same error signature

# Containers to monitor
MONITORED_CONTAINERS = [
    "omniops-app-live",
    "omniops-redis",
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("self-healer")


@dataclass
class KnownFix:
    """A known problem and its fix"""
    name: str
    pattern: str  # regex to match in logs
    fix_command: str  # command to run
    description: str
    container: str = "omniops-app-live"
    cooldown: int = 300  # seconds before applying same fix again
    last_applied: float = 0


@dataclass
class HealthStatus:
    """Container health status"""
    container: str
    running: bool
    healthy: bool
    uptime: str
    last_error: Optional[str] = None
    restart_count: int = 0


# Known fixes registry
KNOWN_FIXES: List[KnownFix] = [
    KnownFix(
        name="dangerously-skip-permissions",
        pattern=r"--dangerously-skip-permissions cannot be used with root",
        fix_command="""docker exec omniops-app-live sh -c "sed -i 's/--dangerously-skip-permissions//g' /app/lib/advisor-board-v2/opus-executor.js 2>/dev/null || true" """,
        description="Remove --dangerously-skip-permissions flag from Claude CLI invocations",
        container="omniops-app-live",
        cooldown=600
    ),
    KnownFix(
        name="clode-api-timeout",
        pattern=r"Clode API call failed.*timeout",
        fix_command="docker restart omniops-mcp-server",
        description="Restart MCP server when Clode API times out repeatedly",
        container="omniops-mcp-server",
        cooldown=900
    ),
]


class SelfHealer:
    """Main self-healing daemon"""

    def __init__(self):
        self.fixes = {fix.name: fix for fix in KNOWN_FIXES}
        self.stats = {
            "checks": 0,
            "fixes_applied": 0,
            "errors_detected": 0,
            "investigations_triggered": 0,
            "start_time": datetime.now().isoformat()
        }
        # Track unknown errors we've seen (for investigation)
        self.unknown_errors: Dict[str, datetime] = {}
        self.investigated_signatures: Dict[str, datetime] = {}

        # Error indicators for detecting unknown issues
        self.error_indicators = [
            r"Error:",
            r"Exception:",
            r"FATAL",
            r"panic:",
            r"Unhandled",
            r"ECONNREFUSED",
            r"ETIMEDOUT",
        ]

    def run_command(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run a shell command and return (returncode, stdout, stderr)"""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def docker_api_request(self, path: str, timeout: int = 10) -> Optional[Dict]:
        """Make a request to Docker API via Unix socket"""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(DOCKER_SOCKET)

            request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request.encode())

            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk

            sock.close()

            # Parse HTTP response
            response_str = response.decode('utf-8', errors='replace')
            if '\r\n\r\n' in response_str:
                body = response_str.split('\r\n\r\n', 1)[1]
                # Handle chunked encoding
                if body.startswith(('0\r\n', '[')):
                    if body.startswith('0\r\n'):
                        return None
                    return json.loads(body)
                else:
                    # Try to find JSON in chunked response
                    lines = body.split('\r\n')
                    for line in lines:
                        if line.startswith('[') or line.startswith('{'):
                            return json.loads(line)
            return None
        except Exception as e:
            logger.debug(f"Docker API error: {e}")
            return None

    def get_container_status(self, container: str) -> HealthStatus:
        """Get health status of a container via Docker socket API"""
        data = self.docker_api_request(f"/containers/{container}/json")

        if data is None:
            return HealthStatus(
                container=container,
                running=False,
                healthy=False,
                uptime="",
                restart_count=0
            )

        state = data.get("State", {})
        running = state.get("Running", False)
        health = state.get("Health", {})
        healthy = health.get("Status", "") == "healthy" if health else True
        uptime = state.get("StartedAt", "")
        restart_count = data.get("RestartCount", 0)

        return HealthStatus(
            container=container,
            running=running,
            healthy=healthy if running else False,
            uptime=uptime,
            restart_count=restart_count
        )

    def get_recent_logs(self, container: str, lines: int = 100) -> str:
        """Get recent container logs via Docker socket API"""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(30)
            sock.connect(DOCKER_SOCKET)

            path = f"/containers/{container}/logs?stdout=true&stderr=true&tail={lines}"
            request = f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request.encode())

            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk

            sock.close()

            # Remove null bytes from raw response before decoding
            response = response.replace(b'\x00', b'')

            # Parse HTTP response and extract body
            response_str = response.decode('utf-8', errors='replace')
            if '\r\n\r\n' in response_str:
                body = response_str.split('\r\n\r\n', 1)[1]
                # Docker logs have 8-byte header per line, strip it
                # Also filter out non-printable characters
                cleaned = ""
                i = 0
                while i < len(body):
                    if i + 8 <= len(body):
                        # Skip 8-byte header
                        i += 8
                        # Find newline
                        end = body.find('\n', i)
                        if end == -1:
                            line = body[i:]
                            # Filter line to printable chars
                            cleaned += ''.join(c for c in line if c.isprintable() or c in '\n\r\t')
                            break
                        line = body[i:end+1]
                        cleaned += ''.join(c for c in line if c.isprintable() or c in '\n\r\t')
                        i = end + 1
                    else:
                        break
                return cleaned
            return ""
        except Exception as e:
            logger.debug(f"Error getting logs: {e}")
            return ""

    def check_for_known_issues(self, container: str) -> Tuple[List[KnownFix], List[str]]:
        """Check logs for known issue patterns and unknown errors

        Returns: (known_fixes_to_apply, unknown_error_lines)
        """
        logs = self.get_recent_logs(container)
        triggered_fixes = []
        unknown_errors = []

        # Check for known fixes
        for fix in self.fixes.values():
            if fix.container != container:
                continue

            if re.search(fix.pattern, logs, re.IGNORECASE):
                # Check cooldown
                if time.time() - fix.last_applied > fix.cooldown:
                    triggered_fixes.append(fix)
                else:
                    logger.debug(f"Fix '{fix.name}' in cooldown, skipping")

        # Check for unknown errors (if investigation is enabled)
        if ENABLE_INVESTIGATION and logs:
            for line in logs.split('\n'):
                # Check if line indicates an error
                is_error = any(re.search(pattern, line, re.IGNORECASE)
                              for pattern in self.error_indicators)

                if is_error:
                    # Check if it matches any known fix pattern
                    is_known = any(re.search(fix.pattern, line, re.IGNORECASE)
                                  for fix in self.fixes.values())

                    if not is_known:
                        unknown_errors.append(line)

        return triggered_fixes, unknown_errors

    def compute_error_signature(self, container: str, error_line: str) -> str:
        """Compute a signature for an error to avoid duplicate investigations"""
        sig_input = f"{container}:{error_line[:100]}"
        return hashlib.md5(sig_input.encode()).hexdigest()[:12]

    def should_investigate(self, signature: str) -> bool:
        """Check if we should investigate this error signature"""
        if signature in self.investigated_signatures:
            last_time = self.investigated_signatures[signature]
            if datetime.now() - last_time < timedelta(seconds=INVESTIGATION_COOLDOWN):
                return False
        return True

    def send_telegram(self, message: str) -> bool:
        """Send notification to owner using Telegram Bot API"""
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("No TELEGRAM_BOT_TOKEN, can't send notification")
            return False

        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            response = requests.post(
                url,
                json={
                    "chat_id": OWNER_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
            if response.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram: {e}")
            return False

    def investigate_error(self, container: str, error_context: str, signature: str):
        """Spawn Claude to investigate an unknown error"""
        logger.info(f"Starting autonomous investigation for {container} ({signature})")
        self.stats["investigations_triggered"] += 1
        self.investigated_signatures[signature] = datetime.now()

        prompt = f"""Investigate this error from container '{container}':

```
{error_context[:1500]}
```

Provide a concise analysis:
1. ROOT CAUSE: What's causing this?
2. SEVERITY: Critical/Warning/Info
3. FIX: Specific command or code change
4. PATTERN: Regex to detect this error

Keep response under 500 chars."""

        try:
            # Escape quotes for shell
            escaped_prompt = prompt.replace('"', '\\"').replace('`', '\\`')
            cmd = f'cd /opt/claudius && claude -p "{escaped_prompt}" --max-turns 2 2>&1'

            code, stdout, stderr = self.run_command(cmd, timeout=90)

            if code == 0 and stdout:
                diagnosis = stdout[:1000]
                logger.info(f"Investigation complete for {signature}")

                # Save to memory
                self.save_memory(
                    f"Investigated unknown error in {container}: {error_context[:100]}...",
                    f"When similar error pattern occurs in {container}",
                    "procedural",
                    0.85
                )

                # Notify owner
                self.send_telegram(
                    f"[Self-Healer] Investigated unknown error in {container}\n\n"
                    f"Error: {error_context[:100]}...\n\n"
                    f"Analysis:\n{diagnosis[:400]}"
                )

                # Check if we should add a new fix based on the response
                if "PATTERN:" in diagnosis:
                    pattern_match = re.search(r'PATTERN:\s*`?([^`\n]+)`?', diagnosis)
                    if pattern_match:
                        new_pattern = pattern_match.group(1).strip()
                        logger.info(f"Learned new error pattern: {new_pattern}")
                        # Could dynamically add to KNOWN_FIXES here
            else:
                logger.error(f"Investigation failed for {signature}: {stderr}")

        except Exception as e:
            logger.error(f"Investigation exception for {signature}: {e}")

    def apply_fix(self, fix: KnownFix) -> bool:
        """Apply a known fix"""
        logger.info(f"Applying fix: {fix.name}")
        logger.info(f"Description: {fix.description}")

        code, stdout, stderr = self.run_command(fix.fix_command, timeout=60)

        if code == 0:
            fix.last_applied = time.time()
            self.stats["fixes_applied"] += 1
            logger.info(f"Fix '{fix.name}' applied successfully")
            self.save_memory(
                f"Auto-applied fix '{fix.name}': {fix.description}",
                f"When {fix.pattern} error is detected",
                "procedural",
                0.7
            )
            return True
        else:
            logger.error(f"Fix '{fix.name}' failed: {stderr}")
            self.save_memory(
                f"Fix '{fix.name}' failed with error: {stderr}",
                f"When attempting to fix {fix.pattern}",
                "episodic",
                0.9
            )
            return False

    def save_memory(self, content: str, trigger: str, memory_type: str, salience: float):
        """Save a memory to the Brain"""
        if not ADMIN_SECRET:
            logger.warning("No ADMIN_SECRET set, skipping memory save")
            return

        try:
            response = requests.post(
                BRAIN_API,
                headers={
                    "Authorization": f"Bearer {ADMIN_SECRET}",
                    "Content-Type": "application/json"
                },
                json={
                    "operation": "store_memory",
                    "params": {
                        "content": f"[self-healer] {content}",
                        "triggerSituation": trigger,
                        "memoryType": memory_type,
                        "salienceSignals": {"importance": salience}
                    }
                },
                timeout=10
            )
            if response.status_code == 200:
                logger.debug("Memory saved successfully")
            else:
                logger.warning(f"Failed to save memory: {response.status_code}")
        except Exception as e:
            logger.warning(f"Failed to save memory: {e}")

    def health_check_loop(self):
        """Main health check loop"""
        logger.info("=" * 60)
        logger.info("Claudius Self-Healer starting")
        logger.info(f"Monitoring containers: {MONITORED_CONTAINERS}")
        logger.info(f"Check interval: {CHECK_INTERVAL}s")
        logger.info(f"Known fixes loaded: {len(self.fixes)}")
        logger.info("=" * 60)

        while True:
            try:
                self.stats["checks"] += 1

                for container in MONITORED_CONTAINERS:
                    status = self.get_container_status(container)

                    if not status.running:
                        logger.warning(f"Container {container} is NOT running!")
                        self.stats["errors_detected"] += 1
                        # Could auto-restart here, but that's risky
                        continue

                    if not status.healthy:
                        logger.warning(f"Container {container} is unhealthy")
                        self.stats["errors_detected"] += 1

                    # Check for known issues and unknown errors
                    fixes_needed, unknown_errors = self.check_for_known_issues(container)

                    # Apply known fixes
                    for fix in fixes_needed:
                        logger.info(f"Detected issue: {fix.name} in {container}")
                        self.stats["errors_detected"] += 1
                        self.apply_fix(fix)

                    # Investigate unknown errors (limit to first one per cycle)
                    if unknown_errors and ENABLE_INVESTIGATION:
                        error_line = unknown_errors[0]
                        signature = self.compute_error_signature(container, error_line)

                        if self.should_investigate(signature):
                            # Get more context around the error
                            logs = self.get_recent_logs(container, lines=50)
                            self.investigate_error(container, logs, signature)

                # Log stats periodically
                if self.stats["checks"] % 10 == 0:
                    logger.info(f"Stats: {self.stats}")

            except Exception as e:
                logger.error(f"Health check error: {e}")

            time.sleep(CHECK_INTERVAL)

    def run_once(self):
        """Run a single health check (for testing)"""
        logger.info("Running single health check...")

        for container in MONITORED_CONTAINERS:
            status = self.get_container_status(container)
            logger.info(f"Container: {container}")
            logger.info(f"  Running: {status.running}")
            logger.info(f"  Healthy: {status.healthy}")
            logger.info(f"  Restarts: {status.restart_count}")

            if status.running:
                fixes, unknown = self.check_for_known_issues(container)
                if fixes:
                    for fix in fixes:
                        logger.info(f"  Known issue: {fix.name}")
                        logger.info(f"    Would apply: {fix.fix_command[:50]}...")
                else:
                    logger.info("  No known issues detected")

                if unknown:
                    logger.info(f"  Unknown errors: {len(unknown)}")
                    for err in unknown[:3]:  # Show first 3
                        logger.info(f"    - {err[:80]}...")

        return self.stats


def main():
    import sys

    healer = SelfHealer()

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        # Single check mode for testing
        healer.run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--apply":
        # Check and apply fixes
        for container in MONITORED_CONTAINERS:
            fixes = healer.check_for_known_issues(container)
            for fix in fixes:
                healer.apply_fix(fix)
    else:
        # Daemon mode
        healer.health_check_loop()


if __name__ == "__main__":
    main()
