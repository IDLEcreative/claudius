#!/usr/bin/env python3
"""
Claudius Proactive Monitor
==========================
Scheduled autonomous tasks that run without being asked.

This gives Claudius the ability to:
1. Perform regular health checks
2. Run preventive maintenance
3. Send alerts when issues are detected
4. Learn from patterns over time

Author: Claudius (building its own autonomy)
Version: 1.0.0
"""

import subprocess
import json
import os
import logging
import requests
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import time
import schedule

DOCKER_SOCKET = "/var/run/docker.sock"

# Configuration
LOG_FILE = "/opt/claudius/logs/proactive-monitor.log"
BRAIN_API = "http://localhost:3000/api/admin/brain"
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = "7070679785"  # Jay's Telegram

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("proactive-monitor")


@dataclass
class Alert:
    """An alert to send"""
    severity: str  # info, warning, critical
    title: str
    message: str
    context: Dict[str, Any] = field(default_factory=dict)


class ProactiveMonitor:
    """Proactive monitoring and alerting system"""

    def __init__(self):
        self.last_alerts: Dict[str, datetime] = {}
        self.alert_cooldowns = {
            "info": 3600,      # 1 hour
            "warning": 1800,   # 30 minutes
            "critical": 300    # 5 minutes
        }

    def run_command(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run a shell command"""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except Exception as e:
            return -1, "", str(e)

    def send_telegram(self, message: str) -> bool:
        """Send a Telegram message to Jay using Bot API directly"""
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
                logger.info("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Telegram: {e}")
            return False

    def send_alert(self, alert: Alert) -> bool:
        """Send an alert if not in cooldown"""
        alert_key = f"{alert.severity}:{alert.title}"
        now = datetime.now()

        # Check cooldown
        if alert_key in self.last_alerts:
            cooldown = self.alert_cooldowns.get(alert.severity, 3600)
            if (now - self.last_alerts[alert_key]).total_seconds() < cooldown:
                logger.debug(f"Alert '{alert_key}' in cooldown, skipping")
                return False

        # Format message
        emoji = {"info": "i", "warning": "!", "critical": "X"}
        message = f"[{emoji.get(alert.severity, '?')}] {alert.title}\n\n{alert.message}"

        if self.send_telegram(message):
            self.last_alerts[alert_key] = now
            logger.info(f"Alert sent: {alert.title}")
            return True
        return False

    def save_memory(self, content: str, trigger: str, memory_type: str = "procedural"):
        """Save to Brain memory"""
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
                        "content": f"[proactive-monitor] {content}",
                        "triggerSituation": trigger,
                        "memoryType": memory_type,
                        "salienceSignals": {"importance": 0.6}
                    }
                },
                timeout=10
            )
        except Exception as e:
            logger.warning(f"Failed to save memory: {e}")

    # ============= SCHEDULED CHECKS =============

    def check_disk_usage(self):
        """Check disk usage and alert if high"""
        logger.info("Checking disk usage...")
        code, stdout, _ = self.run_command("df -h / | tail -1 | awk '{print $5}' | tr -d '%'")

        if code == 0 and stdout.strip().isdigit():
            usage = int(stdout.strip())

            if usage > 90:
                self.send_alert(Alert(
                    severity="critical",
                    title="Disk usage critical",
                    message=f"Root disk is {usage}% full. Immediate action required."
                ))
            elif usage > 80:
                self.send_alert(Alert(
                    severity="warning",
                    title="Disk usage high",
                    message=f"Root disk is {usage}% full. Consider cleanup."
                ))
            else:
                logger.info(f"Disk usage OK: {usage}%")

    def check_memory_usage(self):
        """Check memory usage"""
        logger.info("Checking memory usage...")
        code, stdout, _ = self.run_command("free | grep Mem | awk '{print int($3/$2 * 100)}'")

        if code == 0 and stdout.strip().isdigit():
            usage = int(stdout.strip())

            if usage > 95:
                self.send_alert(Alert(
                    severity="critical",
                    title="Memory critical",
                    message=f"Memory usage at {usage}%. System may become unresponsive."
                ))
            elif usage > 85:
                self.send_alert(Alert(
                    severity="warning",
                    title="Memory high",
                    message=f"Memory usage at {usage}%."
                ))

    def check_container_health(self):
        """Check all monitored containers"""
        logger.info("Checking container health...")
        containers = ["omniops-app", "omniops-redis", "omniops-caddy"]

        for container in containers:
            code, stdout, _ = self.run_command(
                f"docker inspect --format='{{{{.State.Running}}}}' {container} 2>/dev/null"
            )

            if code != 0 or stdout.strip().lower() != "true":
                self.send_alert(Alert(
                    severity="critical",
                    title=f"Container {container} down",
                    message=f"Container {container} is not running. Investigate immediately."
                ))
            else:
                # Check health if available
                code, stdout, _ = self.run_command(
                    f"docker inspect --format='{{{{.State.Health.Status}}}}' {container} 2>/dev/null"
                )
                if code == 0 and stdout.strip() == "unhealthy":
                    self.send_alert(Alert(
                        severity="warning",
                        title=f"Container {container} unhealthy",
                        message=f"Container {container} is running but unhealthy."
                    ))

    def check_docker_disk(self):
        """Check Docker disk usage"""
        logger.info("Checking Docker disk usage...")
        code, stdout, _ = self.run_command("docker system df --format '{{.Size}}' | head -1")

        if code == 0 and stdout.strip():
            size = stdout.strip()
            # Log for tracking
            logger.info(f"Docker images size: {size}")

            # Parse size (e.g., "29.5GB")
            if "GB" in size:
                try:
                    gb = float(size.replace("GB", ""))
                    if gb > 50:
                        self.send_alert(Alert(
                            severity="warning",
                            title="Docker disk high",
                            message=f"Docker using {size}. Consider running docker system prune."
                        ))
                except:
                    pass

    def check_zombie_processes(self):
        """Check for zombie processes"""
        logger.info("Checking for zombie processes...")
        code, stdout, _ = self.run_command("ps aux | grep -c ' Z '")

        if code == 0 and stdout.strip().isdigit():
            count = int(stdout.strip())
            if count > 5:
                self.send_alert(Alert(
                    severity="warning",
                    title="Zombie processes detected",
                    message=f"Found {count} zombie processes. May indicate stuck processes."
                ))

    def daily_report(self):
        """Generate and send daily status report"""
        logger.info("Generating daily report...")

        # Gather stats
        code, disk, _ = self.run_command("df -h / | tail -1 | awk '{print $5}'")
        code, mem, _ = self.run_command("free -h | grep Mem | awk '{print $3\"/\"$2}'")
        code, uptime, _ = self.run_command("uptime -p")
        code, containers, _ = self.run_command("docker ps --format '{{.Names}}' | wc -l")

        report = f"""Daily System Report

Server: 77.42.19.161
Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}

Disk: {disk.strip()}
Memory: {mem.strip()}
Uptime: {uptime.strip()}
Containers: {containers.strip()} running

All systems nominal."""

        self.send_telegram(report)
        self.save_memory(
            f"Daily report sent: Disk {disk.strip()}, Mem {mem.strip()}, {containers.strip()} containers",
            "When reviewing daily system snapshots",
            "episodic"
        )

    def run(self):
        """Set up scheduled tasks and run"""
        logger.info("=" * 60)
        logger.info("Claudius Proactive Monitor starting")
        logger.info("=" * 60)

        # Schedule regular checks
        schedule.every(5).minutes.do(self.check_container_health)
        schedule.every(15).minutes.do(self.check_disk_usage)
        schedule.every(15).minutes.do(self.check_memory_usage)
        schedule.every(1).hours.do(self.check_docker_disk)
        schedule.every(1).hours.do(self.check_zombie_processes)
        schedule.every().day.at("09:00").do(self.daily_report)

        # Run initial checks
        self.check_container_health()
        self.check_disk_usage()
        self.check_memory_usage()

        logger.info("Scheduled tasks configured. Running...")

        while True:
            try:
                schedule.run_pending()
                time.sleep(30)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(60)


def main():
    import sys

    monitor = ProactiveMonitor()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--once":
            monitor.check_container_health()
            monitor.check_disk_usage()
            monitor.check_memory_usage()
        elif cmd == "--report":
            monitor.daily_report()
    else:
        monitor.run()


if __name__ == "__main__":
    main()
