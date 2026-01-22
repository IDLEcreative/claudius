"""
Health Alerts Module

Handles proactive health notifications via Telegram.
Respects cooldown periods to prevent alert fatigue.
"""

import os
import json
import urllib.request
import urllib.error
import logging
from datetime import datetime
from typing import Optional

from .config import ALERT_COOLDOWNS
from .types import HealthAlert, DailyHealthSummary
from .health_store import HealthStore
from .health_context import check_for_alerts, get_todays_health, get_yesterdays_health

logger = logging.getLogger("claudius.health")

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "7070679785")


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the owner via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set - cannot send health alert")
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
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_health_alert(alert: HealthAlert) -> str:
    """Format a health alert for Telegram display."""
    emoji = "🚨" if alert.severity == "critical" else "⚠️"
    severity_text = "CRITICAL" if alert.severity == "critical" else "Warning"

    lines = [
        f"{emoji} <b>Health Alert: {severity_text}</b>",
        "",
        alert.message,
    ]

    if alert.recommendation:
        lines.append("")
        lines.append(f"💡 {alert.recommendation}")

    lines.append("")
    lines.append(f"<i>Triggered at {alert.triggered_at.strftime('%H:%M')}</i>")

    return "\n".join(lines)


def can_send_alert(store: HealthStore, alert_type: str) -> bool:
    """Check if we can send an alert (respecting cooldown)."""
    cooldown = ALERT_COOLDOWNS.get(alert_type, 3600)  # Default 1 hour
    return store.can_send_alert(alert_type, cooldown)


def process_and_send_alerts(
    store: Optional[HealthStore] = None,
    today: Optional[DailyHealthSummary] = None,
    yesterday: Optional[DailyHealthSummary] = None,
) -> list[HealthAlert]:
    """
    Check for alerts and send them via Telegram.
    Returns list of alerts that were sent.

    This is the main entry point for the alert system.
    """
    if store is None:
        store = HealthStore()

    if today is None:
        today = get_todays_health()

    if yesterday is None:
        yesterday = get_yesterdays_health()

    # Get all alerts based on current health data
    alerts = check_for_alerts(today, yesterday)

    if not alerts:
        logger.debug("No health alerts to send")
        return []

    sent_alerts = []

    for alert in alerts:
        # Check cooldown
        if not can_send_alert(store, alert.type):
            logger.debug(f"Alert {alert.type} in cooldown - skipping")
            continue

        # Format and send
        message = format_health_alert(alert)
        success = send_telegram_message(message)

        if success:
            # Record the alert
            store.save_alert(alert)
            sent_alerts.append(alert)
            logger.info(f"Sent health alert: {alert.type} ({alert.severity})")
        else:
            logger.error(f"Failed to send health alert: {alert.type}")

    return sent_alerts


def send_morning_health_summary() -> bool:
    """
    Send a morning health summary via Telegram.
    Called by the morning briefing script.
    """
    from .health_context import get_health_summary

    summary = get_health_summary()

    if not summary["has_data"]:
        logger.info("No health data for morning summary")
        return False

    lines = ["🌅 <b>Morning Health Summary</b>", ""]

    # Sleep
    if summary["sleep"]:
        s = summary["sleep"]
        emoji = "😴" if s["score"] >= 70 else "😪" if s["score"] >= 50 else "🥱"
        lines.append(f"{emoji} Sleep: {s['hours']:.1f}h (score: {s['score']}, {s['quality']})")

    # Body Battery
    if summary["body_battery"]:
        bb = summary["body_battery"]
        emoji = "🔋" if bb["current"] >= 50 else "🪫"
        lines.append(f"{emoji} Body Battery: {bb['current']}% ({bb['status']})")

    # Stress
    if summary["stress"]:
        st = summary["stress"]
        emoji = "😌" if st["average"] < 30 else "😐" if st["average"] < 50 else "😰"
        lines.append(f"{emoji} Stress baseline: {st['qualifier']}")

    # HRV
    if summary["hrv"]:
        hrv = summary["hrv"]
        emoji = "💚" if hrv["status"] == "balanced" else "💛"
        lines.append(f"{emoji} HRV: {hrv['value']}ms ({hrv['status']})")

    # Yesterday's activity
    if summary["activity"]:
        act = summary["activity"]
        lines.append(f"👟 Yesterday: {act['steps']:,} steps, {act['active_minutes']}min active")

    # Recommendations
    if summary["recommendations"]:
        lines.append("")
        lines.append("💡 " + summary["recommendations"][0])

    # Alerts
    critical_alerts = [a for a in summary["alerts"] if a["severity"] == "critical"]
    if critical_alerts:
        lines.append("")
        lines.append("🚨 <b>Attention needed:</b>")
        for alert in critical_alerts[:2]:
            lines.append(f"  • {alert['message']}")

    message = "\n".join(lines)
    return send_telegram_message(message)


def get_alert_stats(store: Optional[HealthStore] = None) -> dict:
    """Get statistics about health alerts."""
    if store is None:
        store = HealthStore()

    stats = store.get_stats()

    # Get recent alerts
    from datetime import timedelta

    # Count alerts by type in last 7 days
    # (This would require a more complex query, simplified for now)

    return {
        "total_alerts": stats.get("alert_history_count", 0),
        "last_synced": stats.get("last_synced"),
        "db_size_bytes": stats.get("db_size_bytes", 0),
    }
