"""
Health Module Configuration

Constants, thresholds, and settings for the Garmin health integration.
"""

import os
from typing import TypedDict

# Paths
CLAUDIUS_DIR = os.environ.get("CLAUDIUS_DIR", "/opt/claudius")
HEALTH_DB_PATH = os.path.join(CLAUDIUS_DIR, "data", "health.db")
GARMIN_TOKENS_PATH = os.path.join(CLAUDIUS_DIR, "secrets", "garmin_tokens.json")

# Garmin OAuth2 Configuration
GARMIN_OAUTH_CONFIG = {
    "client_id": os.environ.get("GARMIN_CLIENT_ID", ""),
    "client_secret": os.environ.get("GARMIN_CLIENT_SECRET", ""),
    "auth_url": "https://connect.garmin.com/oauthConfirm",
    "token_url": "https://connectapi.garmin.com/oauth-service/oauth/access_token",
    "redirect_uri": os.environ.get("GARMIN_REDIRECT_URI", "http://77.42.19.161:3100/health/oauth/callback"),
    "scope": "health_activity health_daily health_sleep health_stress health_hr",
}

# Data retention (days)
DATA_RETENTION = {
    "daily_health": 90,  # 3 months
    "workouts": 365,     # 1 year
    "alert_history": 30,  # 1 month
}

# Sync configuration
SYNC_CONFIG = {
    "poll_interval_minutes": 15,  # How often to check for new data (if not using webhooks)
    "backfill_days": 7,           # How many days to backfill on first sync
    "max_retries": 3,             # Max retries for failed API calls
    "retry_delay_seconds": 30,    # Delay between retries
}


class AlertThreshold(TypedDict):
    """Type definition for alert thresholds."""
    warning: float | int
    critical: float | int


# Alert thresholds - when to notify the user
ALERT_THRESHOLDS: dict[str, AlertThreshold] = {
    # Sleep
    "sleep_duration_hours": {"warning": 6, "critical": 5},
    "sleep_score": {"warning": 60, "critical": 45},

    # Body Battery (lower is worse)
    "body_battery": {"warning": 30, "critical": 15},

    # Stress (higher is worse)
    "avg_stress": {"warning": 60, "critical": 75},

    # Heart rate variability (lower might indicate poor recovery)
    # HRV thresholds are personal - these are conservative defaults
    "hrv_value": {"warning": 30, "critical": 20},

    # SpO2 (lower is worse, medical significance below 94%)
    "spo2_avg": {"warning": 94, "critical": 90},

    # Activity (lower is worse)
    "active_minutes": {"warning": 20, "critical": 10},
    "steps": {"warning": 3000, "critical": 1000},
}

# Alert cooldowns (seconds) - prevent spam
ALERT_COOLDOWNS = {
    "sleep_duration_hours": 24 * 60 * 60,   # 24 hours
    "sleep_score": 24 * 60 * 60,            # 24 hours
    "body_battery": 2 * 60 * 60,            # 2 hours
    "avg_stress": 4 * 60 * 60,              # 4 hours
    "hrv_value": 24 * 60 * 60,              # 24 hours
    "spo2_avg": 30 * 60,                    # 30 minutes (critical health)
    "active_minutes": 8 * 60 * 60,          # 8 hours
    "steps": 8 * 60 * 60,                   # 8 hours
}

# Health metrics to track
HEALTH_METRICS = [
    "sleep_duration_hours",
    "sleep_score",
    "deep_sleep_hours",
    "light_sleep_hours",
    "rem_sleep_hours",
    "awake_hours",
    "resting_hr",
    "max_hr",
    "min_hr",
    "avg_stress",
    "max_stress",
    "stress_qualifier",
    "body_battery_start",
    "body_battery_end",
    "body_battery_charged",
    "body_battery_drained",
    "hrv_value",
    "hrv_status",
    "spo2_avg",
    "spo2_min",
    "steps",
    "active_minutes",
    "calories_total",
    "floors_climbed",
]

# Stress qualifiers based on average stress level
STRESS_QUALIFIERS = {
    (0, 25): "calm",
    (25, 50): "balanced",
    (50, 75): "stressful",
    (75, 100): "very stressful",
}

# Recommendations based on health state
HEALTH_RECOMMENDATIONS = {
    "low_sleep": "Consider an earlier bedtime tonight. Poor sleep affects focus and mood.",
    "low_body_battery": "Your energy reserves are low. Consider rest or light activity only.",
    "high_stress": "Stress levels elevated. Try a short break or breathing exercises.",
    "low_hrv": "Recovery indicators suggest extra rest may help. Consider lighter workload.",
    "low_spo2": "Blood oxygen lower than usual. Monitor this and consult a doctor if persistent.",
    "low_activity": "Movement has been low today. A short walk can help energy and mood.",
    "good_recovery": "Great recovery indicators! You can push harder today if you want.",
    "sleep_debt": "You've had below-target sleep for several days. Prioritize rest.",
}

# Context generation settings
CONTEXT_CONFIG = {
    "include_in_prompt": True,           # Whether to inject health context into prompts
    "max_alerts": 3,                     # Max alerts to show in context
    "show_recommendations": True,        # Include recommendations in context
    "verbose_mode": False,               # Show all metrics (vs. just key ones)
    "sleep_data_from": "yesterday",      # Sleep data comes from previous night
}
