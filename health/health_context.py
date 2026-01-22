"""
Health Context Generator

Generates health-aware context blocks to inject into Claudius prompts.
Provides passive health awareness for AI responses.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from .config import (
    ALERT_THRESHOLDS,
    ALERT_COOLDOWNS,
    HEALTH_RECOMMENDATIONS,
    CONTEXT_CONFIG,
)
from .types import (
    DailyHealthSummary,
    HealthContext,
    HealthAlert,
)
from .health_store import HealthStore


def get_health_store() -> HealthStore:
    """Get or create the health store singleton."""
    if not hasattr(get_health_store, "_instance"):
        get_health_store._instance = HealthStore()
    return get_health_store._instance


def get_todays_health() -> Optional[DailyHealthSummary]:
    """Get today's health summary."""
    store = get_health_store()
    return store.get_daily_health(date.today())


def get_yesterdays_health() -> Optional[DailyHealthSummary]:
    """Get yesterday's health summary (for sleep data)."""
    store = get_health_store()
    return store.get_daily_health(date.today() - timedelta(days=1))


def check_for_alerts(
    today: Optional[DailyHealthSummary],
    yesterday: Optional[DailyHealthSummary]
) -> list[HealthAlert]:
    """Check health data against thresholds and generate alerts."""
    alerts = []

    # Sleep alerts (from yesterday - last night's sleep)
    if yesterday and yesterday.sleep:
        sleep = yesterday.sleep

        # Sleep duration
        if sleep.duration_hours < ALERT_THRESHOLDS["sleep_duration_hours"]["critical"]:
            alerts.append(HealthAlert(
                type="low_sleep",
                severity="critical",
                metric_name="sleep_duration_hours",
                current_value=sleep.duration_hours,
                threshold=ALERT_THRESHOLDS["sleep_duration_hours"]["critical"],
                message=f"Critical: Only {sleep.duration_hours:.1f}h sleep last night",
                recommendation=HEALTH_RECOMMENDATIONS["low_sleep"],
            ))
        elif sleep.duration_hours < ALERT_THRESHOLDS["sleep_duration_hours"]["warning"]:
            alerts.append(HealthAlert(
                type="low_sleep",
                severity="warning",
                metric_name="sleep_duration_hours",
                current_value=sleep.duration_hours,
                threshold=ALERT_THRESHOLDS["sleep_duration_hours"]["warning"],
                message=f"Low sleep: {sleep.duration_hours:.1f}h (recommended 7-9h)",
                recommendation=HEALTH_RECOMMENDATIONS["low_sleep"],
            ))

        # Sleep score
        if sleep.score < ALERT_THRESHOLDS["sleep_score"]["critical"]:
            alerts.append(HealthAlert(
                type="poor_sleep_quality",
                severity="critical",
                metric_name="sleep_score",
                current_value=sleep.score,
                threshold=ALERT_THRESHOLDS["sleep_score"]["critical"],
                message=f"Critical: Sleep quality very poor (score: {sleep.score})",
                recommendation=HEALTH_RECOMMENDATIONS["low_sleep"],
            ))

    # Today's metrics
    if today:
        # Body Battery
        if today.body_battery:
            bb = today.body_battery.end_value
            if bb < ALERT_THRESHOLDS["body_battery"]["critical"]:
                alerts.append(HealthAlert(
                    type="low_body_battery",
                    severity="critical",
                    metric_name="body_battery",
                    current_value=bb,
                    threshold=ALERT_THRESHOLDS["body_battery"]["critical"],
                    message=f"Critical: Body Battery at {bb}% - energy depleted",
                    recommendation=HEALTH_RECOMMENDATIONS["low_body_battery"],
                ))
            elif bb < ALERT_THRESHOLDS["body_battery"]["warning"]:
                alerts.append(HealthAlert(
                    type="low_body_battery",
                    severity="warning",
                    metric_name="body_battery",
                    current_value=bb,
                    threshold=ALERT_THRESHOLDS["body_battery"]["warning"],
                    message=f"Low Body Battery: {bb}% - consider rest",
                    recommendation=HEALTH_RECOMMENDATIONS["low_body_battery"],
                ))

        # Stress
        if today.stress:
            stress = today.stress.avg_level
            if stress > ALERT_THRESHOLDS["avg_stress"]["critical"]:
                alerts.append(HealthAlert(
                    type="high_stress",
                    severity="critical",
                    metric_name="avg_stress",
                    current_value=stress,
                    threshold=ALERT_THRESHOLDS["avg_stress"]["critical"],
                    message=f"Critical: Stress very high today (avg: {stress})",
                    recommendation=HEALTH_RECOMMENDATIONS["high_stress"],
                ))
            elif stress > ALERT_THRESHOLDS["avg_stress"]["warning"]:
                alerts.append(HealthAlert(
                    type="high_stress",
                    severity="warning",
                    metric_name="avg_stress",
                    current_value=stress,
                    threshold=ALERT_THRESHOLDS["avg_stress"]["warning"],
                    message=f"Elevated stress today (avg: {stress})",
                    recommendation=HEALTH_RECOMMENDATIONS["high_stress"],
                ))

        # SpO2 (blood oxygen - critical health metric)
        if today.spo2:
            spo2 = today.spo2.avg
            if spo2 < ALERT_THRESHOLDS["spo2_avg"]["critical"]:
                alerts.append(HealthAlert(
                    type="low_spo2",
                    severity="critical",
                    metric_name="spo2_avg",
                    current_value=spo2,
                    threshold=ALERT_THRESHOLDS["spo2_avg"]["critical"],
                    message=f"CRITICAL: Blood oxygen very low ({spo2:.0f}%) - monitor closely",
                    recommendation=HEALTH_RECOMMENDATIONS["low_spo2"],
                ))
            elif spo2 < ALERT_THRESHOLDS["spo2_avg"]["warning"]:
                alerts.append(HealthAlert(
                    type="low_spo2",
                    severity="warning",
                    metric_name="spo2_avg",
                    current_value=spo2,
                    threshold=ALERT_THRESHOLDS["spo2_avg"]["warning"],
                    message=f"Blood oxygen slightly low ({spo2:.0f}%)",
                    recommendation=HEALTH_RECOMMENDATIONS["low_spo2"],
                ))

        # HRV
        if today.hrv:
            hrv = today.hrv.value
            if hrv < ALERT_THRESHOLDS["hrv_value"]["critical"]:
                alerts.append(HealthAlert(
                    type="low_hrv",
                    severity="critical",
                    metric_name="hrv_value",
                    current_value=hrv,
                    threshold=ALERT_THRESHOLDS["hrv_value"]["critical"],
                    message=f"HRV very low ({hrv}ms) - recovery compromised",
                    recommendation=HEALTH_RECOMMENDATIONS["low_hrv"],
                ))
            elif hrv < ALERT_THRESHOLDS["hrv_value"]["warning"]:
                alerts.append(HealthAlert(
                    type="low_hrv",
                    severity="warning",
                    metric_name="hrv_value",
                    current_value=hrv,
                    threshold=ALERT_THRESHOLDS["hrv_value"]["warning"],
                    message=f"HRV below baseline ({hrv}ms)",
                    recommendation=HEALTH_RECOMMENDATIONS["low_hrv"],
                ))

        # Activity
        if today.activity:
            steps = today.activity.steps
            active_mins = today.activity.active_minutes

            # Only alert if it's past midday (give time to be active)
            if datetime.now().hour >= 14:
                if steps < ALERT_THRESHOLDS["steps"]["critical"]:
                    alerts.append(HealthAlert(
                        type="low_activity",
                        severity="warning",
                        metric_name="steps",
                        current_value=steps,
                        threshold=ALERT_THRESHOLDS["steps"]["critical"],
                        message=f"Very low movement today ({steps:,} steps)",
                        recommendation=HEALTH_RECOMMENDATIONS["low_activity"],
                    ))

    return alerts


def generate_summary_text(
    today: Optional[DailyHealthSummary],
    yesterday: Optional[DailyHealthSummary]
) -> str:
    """Generate a concise health summary text."""
    lines = []

    # Sleep (from yesterday - last night)
    if yesterday and yesterday.sleep:
        sleep = yesterday.sleep
        emoji = "😴" if sleep.score >= 70 else "😪" if sleep.score >= 50 else "🥱"
        lines.append(f"{emoji} Last night: {sleep.duration_hours:.1f}h sleep, score {sleep.score}")

    # Body Battery (current)
    if today and today.body_battery:
        bb = today.body_battery
        emoji = "🔋" if bb.end_value >= 50 else "🪫"
        lines.append(f"{emoji} Body Battery: {bb.end_value}%")

    # Stress (today)
    if today and today.stress:
        stress = today.stress
        emoji = "😌" if stress.avg_level < 30 else "😐" if stress.avg_level < 50 else "😰"
        lines.append(f"{emoji} Stress: {stress.qualifier} (avg {stress.avg_level})")

    # HRV (if available)
    if today and today.hrv:
        hrv = today.hrv
        emoji = "💚" if hrv.status == "balanced" else "💛"
        lines.append(f"{emoji} HRV: {hrv.value}ms ({hrv.status})")

    # Activity (today)
    if today and today.activity:
        act = today.activity
        lines.append(f"👟 Steps: {act.steps:,} | Active: {act.active_minutes}min")

    if not lines:
        return "No health data available"

    return "\n".join(lines)


def generate_recommendations(
    today: Optional[DailyHealthSummary],
    yesterday: Optional[DailyHealthSummary],
    alerts: list[HealthAlert]
) -> list[str]:
    """Generate health recommendations based on data."""
    recommendations = []

    # Collect unique recommendations from alerts
    seen = set()
    for alert in alerts:
        if alert.recommendation and alert.recommendation not in seen:
            recommendations.append(alert.recommendation)
            seen.add(alert.recommendation)

    # Add positive recommendations if recovery looks good
    if today and today.body_battery and today.hrv:
        if today.body_battery.end_value >= 70 and today.hrv.status == "balanced":
            recommendations.append(HEALTH_RECOMMENDATIONS["good_recovery"])

    # Check for sleep debt (multiple low-sleep days)
    store = get_health_store()
    recent = store.get_recent_health(days=3)
    low_sleep_days = sum(
        1 for day in recent
        if day.sleep and day.sleep.duration_hours < 6
    )
    if low_sleep_days >= 2:
        recommendations.append(HEALTH_RECOMMENDATIONS["sleep_debt"])

    return recommendations[:3]  # Max 3 recommendations


def generate_context_block() -> str:
    """
    Generate a health context block to prepend to Claudius prompts.
    This is the main entry point for context injection.
    """
    if not CONTEXT_CONFIG["include_in_prompt"]:
        return ""

    today = get_todays_health()
    yesterday = get_yesterdays_health()

    # If no data at all, return empty
    if not today and not yesterday:
        return ""

    # Generate context components
    summary_text = generate_summary_text(today, yesterday)
    alerts = check_for_alerts(today, yesterday)
    recommendations = generate_recommendations(today, yesterday, alerts)

    # Build the context object
    context = HealthContext(
        summary_text=summary_text,
        alerts=alerts[:CONTEXT_CONFIG["max_alerts"]],
        recommendations=recommendations if CONTEXT_CONFIG["show_recommendations"] else [],
    )

    return context.to_prompt_block()


def get_health_summary() -> dict:
    """
    Get a health summary as a dictionary.
    Used by the /health/status API endpoint.
    """
    today = get_todays_health()
    yesterday = get_yesterdays_health()

    summary = {
        "date": date.today().isoformat(),
        "has_data": False,
        "sleep": None,
        "body_battery": None,
        "stress": None,
        "hrv": None,
        "activity": None,
        "alerts": [],
        "recommendations": [],
    }

    # Sleep from yesterday
    if yesterday and yesterday.sleep:
        summary["has_data"] = True
        summary["sleep"] = {
            "hours": yesterday.sleep.duration_hours,
            "score": yesterday.sleep.score,
            "quality": yesterday.sleep.quality,
        }

    # Today's metrics
    if today:
        summary["has_data"] = True

        if today.body_battery:
            summary["body_battery"] = {
                "current": today.body_battery.end_value,
                "status": today.body_battery.status,
            }

        if today.stress:
            summary["stress"] = {
                "average": today.stress.avg_level,
                "qualifier": today.stress.qualifier,
            }

        if today.hrv:
            summary["hrv"] = {
                "value": today.hrv.value,
                "status": today.hrv.status,
            }

        if today.activity:
            summary["activity"] = {
                "steps": today.activity.steps,
                "active_minutes": today.activity.active_minutes,
                "level": today.activity.activity_level,
            }

    # Alerts and recommendations
    alerts = check_for_alerts(today, yesterday)
    summary["alerts"] = [
        {
            "type": a.type,
            "severity": a.severity,
            "message": a.message,
        }
        for a in alerts
    ]

    recommendations = generate_recommendations(today, yesterday, alerts)
    summary["recommendations"] = recommendations

    return summary
