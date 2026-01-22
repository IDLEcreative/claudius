#!/usr/bin/env python3
"""
Deployment Watcher - Claudius Infrastructure Agent

Watches Docker events for OmniOps container lifecycle changes and sends
notifications via Telegram. This replaces the in-app notification system
to maintain separation of concerns.

Features:
- Monitors Docker events in real-time
- Detects successful deployments (healthy containers)
- Detects failed deployments (unhealthy/crashed)
- Configurable notification levels (failures only, all, none)

Configuration (via env or /opt/claudius/.env):
- TELEGRAM_BOT_TOKEN: Bot token for notifications
- TELEGRAM_CHAT_ID: Owner chat ID
- DEPLOYMENT_NOTIFY_SUCCESS: "true" to notify on success (default: false)
- DEPLOYMENT_NOTIFY_FAILURES: "true" to notify on failures (default: true)
"""

import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Load env from file if not in environment
ENV_FILE = Path("/opt/claudius/.env")
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if key not in os.environ:
                os.environ[key] = value.strip('"').strip("'")

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTIFY_SUCCESS = os.environ.get("DEPLOYMENT_NOTIFY_SUCCESS", "false").lower() == "true"
NOTIFY_FAILURES = os.environ.get("DEPLOYMENT_NOTIFY_FAILURES", "true").lower() == "true"

# Container patterns to watch
WATCHED_PATTERNS = ["omniops-app"]


def send_telegram(message: str) -> bool:
    """Send a message via Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] Not configured, would send: {message[:100]}...")
        return False

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
            return resp.status == 200
    except Exception as e:
        print(f"[Telegram] Error: {e}")
        return False


def get_container_info(container_name: str) -> dict:
    """Get container details via docker inspect."""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if data:
                return data[0]
    except Exception as e:
        print(f"[Docker] Inspect error: {e}")
    return {}


def get_git_info() -> dict:
    """Get current git commit info from OmniOps."""
    info = {"commit": "unknown", "branch": "unknown"}
    try:
        result = subprocess.run(
            ["git", "-C", "/opt/omniops", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["commit"] = result.stdout.strip()

        result = subprocess.run(
            ["git", "-C", "/opt/omniops", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            info["branch"] = result.stdout.strip()
    except Exception:
        pass
    return info


def is_watched_container(name: str) -> bool:
    """Check if container name matches our watch patterns."""
    return any(pattern in name for pattern in WATCHED_PATTERNS)


def notify_deployment_success(container_name: str):
    """Send success notification."""
    if not NOTIFY_SUCCESS:
        print(f"[Watcher] Success notification disabled, skipping")
        return

    git = get_git_info()
    msg = (
        f"✅ *Deployment Successful*\n\n"
        f"📦 Container: `{container_name}`\n"
        f"📝 Commit: `{git['commit']}`\n"
        f"🌿 Branch: `{git['branch']}`\n"
        f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)


def notify_deployment_failure(container_name: str, reason: str):
    """Send failure notification."""
    if not NOTIFY_FAILURES:
        print(f"[Watcher] Failure notification disabled, skipping")
        return

    git = get_git_info()
    msg = (
        f"❌ *Deployment Failed*\n\n"
        f"📦 Container: `{container_name}`\n"
        f"📝 Commit: `{git['commit']}`\n"
        f"🌿 Branch: `{git['branch']}`\n"
        f"⚠️ Reason: {reason}\n"
        f"⏰ Time: {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)


def watch_docker_events():
    """Watch Docker events stream for container lifecycle changes."""
    print(f"[Watcher] Starting deployment watcher...")
    print(f"[Watcher] Notify success: {NOTIFY_SUCCESS}")
    print(f"[Watcher] Notify failures: {NOTIFY_FAILURES}")
    print(f"[Watcher] Watching patterns: {WATCHED_PATTERNS}")

    # Track containers we've already notified about
    notified_healthy = set()
    notified_unhealthy = set()

    cmd = [
        "docker", "events",
        "--filter", "type=container",
        "--format", "{{json .}}"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)

    for line in proc.stdout:
        try:
            event = json.loads(line.strip())
            container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
            action = event.get("Action", "")

            if not is_watched_container(container_name):
                continue

            # Health status changes
            if action == "health_status: healthy":
                if container_name not in notified_healthy:
                    print(f"[Watcher] {container_name} became healthy")
                    notify_deployment_success(container_name)
                    notified_healthy.add(container_name)
                    notified_unhealthy.discard(container_name)

            elif action in ["health_status: unhealthy", "die", "kill", "oom"]:
                if container_name not in notified_unhealthy:
                    print(f"[Watcher] {container_name} {action}")
                    notify_deployment_failure(container_name, action)
                    notified_unhealthy.add(container_name)
                    notified_healthy.discard(container_name)

            # Container start/stop (for tracking)
            elif action == "start":
                print(f"[Watcher] {container_name} started")
                # Reset notification tracking for fresh container
                notified_healthy.discard(container_name)
                notified_unhealthy.discard(container_name)

            elif action == "stop":
                print(f"[Watcher] {container_name} stopped")

        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(f"[Watcher] Error processing event: {e}")


def main():
    """Main entry point."""
    while True:
        try:
            watch_docker_events()
        except KeyboardInterrupt:
            print("\n[Watcher] Shutting down...")
            break
        except Exception as e:
            print(f"[Watcher] Error: {e}, restarting in 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
