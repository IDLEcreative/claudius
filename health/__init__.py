"""
Claudius Health Module - Garmin Integration

Provides health-aware context for Claudius by integrating Garmin health data.

Features:
- Garmin Connect integration via python-garminconnect
- SQLite storage for health metrics
- Context generation for AI responses
- Proactive health alerts via Telegram

Setup:
    pip install garminconnect
    export GARMIN_EMAIL="your@email.com"
    export GARMIN_PASSWORD="yourpassword"
"""

from .config import (
    ALERT_THRESHOLDS,
    ALERT_COOLDOWNS,
    HEALTH_METRICS,
    CONTEXT_CONFIG,
    GARMIN_CONFIG,
)

from .types import (
    DailyHealthSummary,
    HealthContext,
    HealthAlert,
    SleepData,
    HeartRateData,
    StressData,
    BodyBatteryData,
    HRVData,
    SpO2Data,
    ActivityData,
    WorkoutData,
)

from .health_store import HealthStore

from .health_context import (
    generate_context_block,
    get_health_summary,
    check_for_alerts,
)

from .garmin_auth import (
    GarminAuth,
    GarminAuthError,
    get_garmin_auth,
)

from .garmin_api import (
    GarminAPI,
    GarminAPIError,
    get_garmin_api,
)

from .garmin_sync import (
    sync_health_data,
    sync_today,
    sync_recent,
    backfill,
    manual_sync,
)

from .health_alerts import (
    process_and_send_alerts,
    send_morning_health_summary,
    get_alert_stats,
)

__all__ = [
    # Config
    "ALERT_THRESHOLDS",
    "ALERT_COOLDOWNS",
    "HEALTH_METRICS",
    "CONTEXT_CONFIG",
    "GARMIN_CONFIG",
    # Types
    "DailyHealthSummary",
    "HealthContext",
    "HealthAlert",
    "SleepData",
    "HeartRateData",
    "StressData",
    "BodyBatteryData",
    "HRVData",
    "SpO2Data",
    "ActivityData",
    "WorkoutData",
    # Store
    "HealthStore",
    # Context
    "generate_context_block",
    "get_health_summary",
    "check_for_alerts",
    # Auth
    "GarminAuth",
    "GarminAuthError",
    "get_garmin_auth",
    # API
    "GarminAPI",
    "GarminAPIError",
    "get_garmin_api",
    # Sync
    "sync_health_data",
    "sync_today",
    "sync_recent",
    "backfill",
    "manual_sync",
    # Alerts
    "process_and_send_alerts",
    "send_morning_health_summary",
    "get_alert_stats",
]
