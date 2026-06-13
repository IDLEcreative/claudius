#!/usr/bin/env python3
"""
Claudius Autonomous Loop
=========================
The "heartbeat" that gives Claudius proactive agency.

Runs via cron. Each run:
1. Collects system state (fast, no AI needed)
2. Checks for pending tasks or issues
3. Spawns Sonnet sub-agents for routine work
4. Escalates to Telegram only when human judgment needed

Philosophy: Handle silently what can be handled silently.
Only message Jay when something actually needs him.

Usage:
    python3 autonomous-loop.py              # Full run (cron)
    python3 autonomous-loop.py --patrol     # Quick health patrol only
    python3 autonomous-loop.py --status     # Show current state
"""

import json
import os
import subprocess
import sys
import time
import logging
import urllib.request
from datetime import datetime
from typing import Optional

sys.path.insert(0, '/opt/claudius')

from autonomous_health import (
    collect_system_state, detect_issues, auto_resolve, SystemState
)
from curiosity_loop import run_curiosity

# Paths
LOG_FILE = "/opt/claudius/logs/autonomous-loop.log"
STATE_FILE = "/opt/claudius/state/autonomous-loop-state.json"
PATROL_REPORT_FILE = "/opt/claudius/state/patrol-report.json"

# Load environment
ENV_FILE = "/opt/claudius/.env"
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = "7070679785"

# Logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger("autonomous-loop")


def send_telegram(message: str, silent: bool = False) -> bool:
    """Send message to Jay via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("No TELEGRAM_BOT_TOKEN configured")
        return False
    try:
        data = json.dumps({
            "chat_id": OWNER_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": silent,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=data, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def notify_claudius(message: str) -> bool:
    """Route alerts through Claudius API for triage before escalating to Jay."""
    try:
        data = json.dumps({
            "message": message,
            "source": "autonomous-loop"
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:3100/message",
            data=data, method="POST"
        )
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Claudius API failed: {e}")
        return False


def load_state() -> dict:
    """Load persistent state (last run, alert history, etc)."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_run": None, "alert_history": {}, "run_count": 0}


def save_state(state: dict):
    """Save persistent state."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def should_alert(alert_key: str, cooldown_seconds: int, persistent_state: dict) -> bool:
    """Check if we should send an alert (respects cooldown)."""
    last_sent = persistent_state.get("alert_history", {}).get(alert_key)
    if last_sent and (time.time() - last_sent) < cooldown_seconds:
        return False
    return True


def mark_alerted(alert_key: str, persistent_state: dict):
    """Mark an alert as sent."""
    persistent_state.setdefault("alert_history", {})[alert_key] = time.time()


def spawn_sonnet_agent(task: str, working_dir: str = "/opt/claudius") -> Optional[str]:
    """Spawn a Sonnet sub-agent for routine work."""
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", "sonnet", "--dangerously-skip-permissions", "-p", task],
            cwd=working_dir, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.error(f"Sub-agent failed: {result.stderr[:200]}")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Sub-agent timed out (120s)")
        return None
    except Exception as e:
        logger.error(f"Sub-agent spawn failed: {e}")
        return None


def run_patrol():
    """Quick health patrol. Fast, no AI, just checks and alerts."""
    logger.info("=== Patrol Starting ===")
    state = collect_system_state()
    issues = detect_issues(state)
    persistent = load_state()

    actions_taken = []
    notifications = []
    cooldowns = {"critical": 300, "warning": 1800, "info": 3600}

    for issue in issues:
        severity = issue["severity"]
        issue_key = f"{severity}:{issue['issue'][:30]}"

        if issue["action"] != "notify":
            result = auto_resolve(issue, state)
            if result:
                actions_taken.append(f"[AUTO] {result}")
                logger.info(f"Auto-resolved: {result}")
                continue

        if should_alert(issue_key, cooldowns.get(severity, 3600), persistent):
            emoji = {"critical": "\U0001f534", "warning": "\u26a0\ufe0f", "info": "\u2139\ufe0f"}.get(severity, "")
            notifications.append(f"{emoji} {issue['issue']}")
            mark_alerted(issue_key, persistent)

    if notifications or actions_taken:
        msg_parts = []
        if notifications:
            msg_parts.append("Issues detected:\n" + "\n".join(f"  {n}" for n in notifications))
        if actions_taken:
            msg_parts.append("Auto-resolved:\n" + "\n".join(f"  {a}" for a in actions_taken))

        message = "\n\n".join(msg_parts)
        has_critical = any("\U0001f534" in n for n in notifications)

        # Route through Claudius for triage - only escalate to Jay if Claudius can't handle it
        # or if it's a critical issue that persists after Claudius's assessment
        notify_claudius(message)
        logger.info(f"Routed alert to Claudius API: {message[:100]}...")

    persistent["last_run"] = datetime.now().isoformat()
    persistent["run_count"] = persistent.get("run_count", 0) + 1
    persistent["last_state"] = {
        "disk": state.disk_percent, "mem": state.mem_percent,
        "load": state.load_1m, "claude_procs": state.claude_procs,
        "containers": len(state.containers), "issues": len(issues),
    }
    save_state(persistent)

    report = {
        "timestamp": state.timestamp, "healthy": len(issues) == 0,
        "issues": issues, "actions_taken": actions_taken,
        "state": {
            "disk": state.disk_percent, "mem": state.mem_percent,
            "load": state.load_1m, "claude_procs": state.claude_procs,
            "containers": state.containers, "http_checks": state.http_checks,
        }
    }
    os.makedirs(os.path.dirname(PATROL_REPORT_FILE), exist_ok=True)
    with open(PATROL_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    if issues:
        logger.info(f"Patrol complete: {len(issues)} issues, {len(actions_taken)} auto-resolved")
    else:
        logger.info("Patrol complete: all clear")
    return report


def check_pending_tasks() -> list[str]:
    """Check for pending tasks needing attention."""
    tasks = []
    try:
        with open(PATROL_REPORT_FILE) as f:
            report = json.load(f)
        for issue in report.get("issues", []):
            if issue["severity"] == "critical" and issue["action"] == "notify":
                tasks.append(f"Investigate critical issue: {issue['issue']}. Check logs and report.")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return tasks


def run_full():
    """Full orchestration run. Patrol + pending tasks + curiosity + sub-agents."""
    logger.info("=== Full Orchestration Run ===")
    patrol = run_patrol()
    pending_tasks = check_pending_tasks()

    if pending_tasks:
        for task in pending_tasks[:2]:
            logger.info(f"Spawning sub-agent for: {task[:50]}...")
            result = spawn_sonnet_agent(task)
            if result:
                logger.info(f"Sub-agent completed: {result[:100]}")

    # Curiosity cycle — only when healthy (don't wonder while the house is on fire)
    if patrol["healthy"]:
        try:
            thought = run_curiosity()
            if thought:
                logger.info(f"Curiosity: {thought[:80]}")
        except Exception as e:
            logger.warning(f"Curiosity loop error: {e}")

    persistent = load_state()
    logger.info(
        f"Run #{persistent.get('run_count', 0)} complete. "
        f"Health: {'OK' if patrol['healthy'] else 'ISSUES'}. "
        f"Pending tasks: {len(pending_tasks)}"
    )


def show_status():
    """Show current autonomous loop status."""
    persistent = load_state()
    print(json.dumps(persistent, indent=2))
    if os.path.exists(PATROL_REPORT_FILE):
        with open(PATROL_REPORT_FILE) as f:
            report = json.load(f)
        print(f"\nLast patrol: {report['timestamp']}")
        print(f"Healthy: {report['healthy']}")
        if report['issues']:
            print(f"Issues: {len(report['issues'])}")
            for i in report['issues']:
                print(f"  [{i['severity']}] {i['issue']}")


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--patrol":
            run_patrol()
        elif cmd == "--status":
            show_status()
        elif cmd == "--full":
            run_full()
        else:
            print(f"Unknown: {cmd}. Use --patrol|--full|--status")
    else:
        run_full()


if __name__ == "__main__":
    main()
