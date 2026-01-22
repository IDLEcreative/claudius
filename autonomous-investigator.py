#!/usr/bin/env python3
"""
Claudius Autonomous Investigator
=================================
When an unknown error is detected, this spawns a Claude session to diagnose it.

This is the "thinking" layer on top of the self-healer - instead of just pattern
matching known fixes, it can reason about new problems.

Author: Claudius (AI building AI tools)
Version: 1.0.0
"""

import subprocess
import json
import os
import logging
import time
import re
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import requests

# Configuration
LOG_FILE = "/opt/claudius/logs/investigator.log"
BRAIN_API = "http://localhost:3000/api/admin/brain"
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = "7070679785"
DOCKER_SOCKET = "/var/run/docker.sock"

# Load .env if token not set
if not TELEGRAM_BOT_TOKEN:
    try:
        with open("/opt/omniops/.env", "r") as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip().strip('"')
                    break
    except:
        pass

# How long to wait between investigations of the same error signature
INVESTIGATION_COOLDOWN = 3600  # 1 hour

# Maximum concurrent investigations
MAX_CONCURRENT_INVESTIGATIONS = 2

# Setup logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("investigator")


@dataclass
class ErrorSignature:
    """Unique signature for an error pattern"""
    container: str
    error_type: str  # Extracted from error message
    stack_trace_hash: str  # Hash of first 3 lines of stack trace
    first_seen: datetime
    occurrences: int = 1
    last_investigated: Optional[datetime] = None
    resolution: Optional[str] = None


@dataclass
class Investigation:
    """An ongoing or completed investigation"""
    id: str
    error_signature: ErrorSignature
    started_at: datetime
    completed_at: Optional[datetime] = None
    diagnosis: Optional[str] = None
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False
    status: str = "running"  # running, completed, failed


class AutonomousInvestigator:
    """Investigates unknown errors using Claude"""

    def __init__(self):
        self.known_patterns: Dict[str, ErrorSignature] = {}
        self.active_investigations: Dict[str, Investigation] = {}
        self.investigation_history: List[Investigation] = []

        # Error patterns to detect (broader than self-healer's exact matches)
        self.error_indicators = [
            r"Error:",
            r"Exception:",
            r"FATAL",
            r"panic:",
            r"Unhandled",
            r"failed",
            r"ECONNREFUSED",
            r"ETIMEDOUT",
            r"OOM",
            r"killed",
            r"exit code [1-9]",
        ]

        # Patterns we already handle (skip investigation)
        self.known_handled = [
            r"--dangerously-skip-permissions",
            r"Clode API call failed.*timeout",
        ]

    def run_command(self, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
        """Run a shell command"""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except Exception as e:
            return -1, "", str(e)

    def get_container_logs(self, container: str, lines: int = 200) -> str:
        """Get recent logs from a container"""
        code, stdout, stderr = self.run_command(
            f"docker logs --tail {lines} {container} 2>&1"
        )
        return stdout if code == 0 else stderr

    def extract_error_blocks(self, logs: str) -> List[Dict[str, Any]]:
        """Extract error blocks from logs"""
        errors = []
        lines = logs.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if this line indicates an error
            is_error = any(re.search(pattern, line, re.IGNORECASE)
                         for pattern in self.error_indicators)

            if is_error:
                # Check if it's a known/handled error
                is_known = any(re.search(pattern, line, re.IGNORECASE)
                              for pattern in self.known_handled)

                if not is_known:
                    # Collect the error and surrounding context (5 lines before, 10 after)
                    start = max(0, i - 5)
                    end = min(len(lines), i + 10)

                    error_block = {
                        "trigger_line": line,
                        "context": '\n'.join(lines[start:end]),
                        "line_number": i
                    }
                    errors.append(error_block)
                    i = end  # Skip ahead to avoid duplicate captures
                    continue

            i += 1

        return errors

    def compute_error_signature(self, container: str, error_block: Dict) -> str:
        """Compute a unique signature for this error type"""
        # Use first 100 chars of trigger + container for signature
        trigger = error_block["trigger_line"][:100]
        sig_input = f"{container}:{trigger}"
        return hashlib.md5(sig_input.encode()).hexdigest()[:12]

    def should_investigate(self, signature: str) -> bool:
        """Determine if we should investigate this error"""
        # Too many active investigations?
        if len(self.active_investigations) >= MAX_CONCURRENT_INVESTIGATIONS:
            return False

        # Already investigating this exact signature?
        if signature in self.active_investigations:
            return False

        # Recently investigated?
        if signature in self.known_patterns:
            pattern = self.known_patterns[signature]
            if pattern.last_investigated:
                if datetime.now() - pattern.last_investigated < timedelta(seconds=INVESTIGATION_COOLDOWN):
                    return False

        return True

    def send_telegram(self, message: str) -> bool:
        """Send notification to owner using Telegram Bot API"""
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("No TELEGRAM_BOT_TOKEN, can't send Telegram")
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

    def save_memory(self, content: str, trigger: str, resolution: str = None,
                   memory_type: str = "procedural", salience: float = 0.8):
        """Save investigation results to Brain"""
        if not ADMIN_SECRET:
            return

        try:
            requests.post(
                BRAIN_API,
                headers={
                    "Authorization": f"Bearer {ADMIN_SECRET}",
                    "Content-Type": "application/json"
                },
                json={
                    "operation": "store_memory",
                    "params": {
                        "content": f"[investigator] {content}",
                        "triggerSituation": trigger,
                        "resolution": resolution,
                        "memoryType": memory_type,
                        "salienceSignals": {"importance": salience}
                    }
                },
                timeout=10
            )
        except Exception as e:
            logger.warning(f"Failed to save memory: {e}")

    def invoke_claude_investigation(self, container: str, error_block: Dict,
                                    signature: str) -> Investigation:
        """Spawn Claude to investigate an error"""
        investigation = Investigation(
            id=signature,
            error_signature=ErrorSignature(
                container=container,
                error_type=error_block["trigger_line"][:50],
                stack_trace_hash=signature,
                first_seen=datetime.now()
            ),
            started_at=datetime.now()
        )

        self.active_investigations[signature] = investigation

        # Build the investigation prompt
        prompt = f"""Investigate this error from container '{container}':

```
{error_block["context"]}
```

Provide:
1. ROOT CAUSE: What is causing this error?
2. IMPACT: How does this affect the system?
3. FIX: What command or code change would fix it?
4. PREVENTABLE: Can this be auto-fixed? (yes/no)
5. PATTERN: A regex pattern to detect this error

Be concise and actionable. If you need more context, say what you need.
"""

        logger.info(f"Starting investigation {signature} for {container}")
        logger.info(f"Error: {error_block['trigger_line'][:80]}...")

        try:
            # Use the Claude Code CLI to investigate
            # This runs in a subprocess with timeout
            escaped_prompt = prompt.replace('"', '\\"')
            cmd = f'cd /opt/claudius && claude -p "{escaped_prompt}" --max-turns 3 2>&1'

            code, stdout, stderr = self.run_command(cmd, timeout=120)

            if code == 0 and stdout:
                investigation.diagnosis = stdout[:2000]  # Truncate if too long
                investigation.status = "completed"
                investigation.completed_at = datetime.now()

                # Parse the response for auto-fix info
                if "PREVENTABLE: yes" in stdout.lower() or "auto-fixed: yes" in stdout.lower():
                    investigation.auto_fixable = True
                    # Try to extract the fix command
                    fix_match = re.search(r'FIX:\s*`([^`]+)`', stdout)
                    if fix_match:
                        investigation.suggested_fix = fix_match.group(1)

                logger.info(f"Investigation {signature} completed")

                # Save findings to memory
                self.save_memory(
                    f"Error in {container}: {error_block['trigger_line'][:100]}",
                    f"When this error pattern occurs in {container}",
                    investigation.diagnosis[:500] if investigation.diagnosis else None,
                    "procedural",
                    0.85
                )

                # Notify owner of findings
                self.send_telegram(
                    f"[Investigator] Analyzed error in {container}\n\n"
                    f"Error: {error_block['trigger_line'][:80]}...\n\n"
                    f"Findings:\n{investigation.diagnosis[:500] if investigation.diagnosis else 'No diagnosis'}"
                )
            else:
                investigation.status = "failed"
                investigation.diagnosis = f"Investigation failed: {stderr or 'unknown error'}"
                investigation.completed_at = datetime.now()
                logger.error(f"Investigation {signature} failed: {stderr}")

        except Exception as e:
            investigation.status = "failed"
            investigation.diagnosis = f"Investigation exception: {str(e)}"
            investigation.completed_at = datetime.now()
            logger.error(f"Investigation {signature} exception: {e}")

        # Move to history
        del self.active_investigations[signature]
        self.investigation_history.append(investigation)

        # Update known patterns
        if signature not in self.known_patterns:
            self.known_patterns[signature] = investigation.error_signature
        self.known_patterns[signature].last_investigated = datetime.now()

        return investigation

    def scan_containers(self, containers: List[str]) -> List[Investigation]:
        """Scan containers for unknown errors and investigate them"""
        new_investigations = []

        for container in containers:
            logger.debug(f"Scanning {container}...")

            logs = self.get_container_logs(container)
            if not logs:
                continue

            errors = self.extract_error_blocks(logs)

            for error_block in errors:
                signature = self.compute_error_signature(container, error_block)

                if self.should_investigate(signature):
                    logger.info(f"New error detected in {container}, starting investigation")
                    investigation = self.invoke_claude_investigation(
                        container, error_block, signature
                    )
                    new_investigations.append(investigation)

        return new_investigations

    def run_daemon(self, containers: List[str], interval: int = 300):
        """Run as daemon, scanning periodically"""
        logger.info("=" * 60)
        logger.info("Claudius Autonomous Investigator starting")
        logger.info(f"Monitoring: {containers}")
        logger.info(f"Scan interval: {interval}s")
        logger.info("=" * 60)

        while True:
            try:
                investigations = self.scan_containers(containers)
                if investigations:
                    logger.info(f"Completed {len(investigations)} investigation(s)")
            except Exception as e:
                logger.error(f"Scan error: {e}")

            time.sleep(interval)

    def run_once(self, containers: List[str]):
        """Run a single scan"""
        logger.info("Running single scan...")
        investigations = self.scan_containers(containers)

        for inv in investigations:
            print(f"\n{'='*60}")
            print(f"Investigation: {inv.id}")
            print(f"Container: {inv.error_signature.container}")
            print(f"Status: {inv.status}")
            print(f"Auto-fixable: {inv.auto_fixable}")
            print(f"\nDiagnosis:\n{inv.diagnosis}")
            if inv.suggested_fix:
                print(f"\nSuggested fix:\n{inv.suggested_fix}")

        return investigations


def main():
    import sys

    investigator = AutonomousInvestigator()
    containers = ["omniops-app-live", "omniops-redis"]

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--once":
            investigator.run_once(containers)
        elif cmd == "--help":
            print("Claudius Autonomous Investigator")
            print("")
            print("Usage:")
            print("  python3 autonomous-investigator.py          # Daemon mode")
            print("  python3 autonomous-investigator.py --once   # Single scan")
    else:
        investigator.run_daemon(containers)


if __name__ == "__main__":
    main()
