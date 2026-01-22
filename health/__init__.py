"""
Claudius Health Module - Garmin Integration

Provides health-aware context for Claudius by integrating Garmin health data.

Features:
- Garmin Health API integration (OAuth2 PKCE)
- SQLite storage for health metrics
- Context generation for AI responses
- Proactive health alerts via Telegram
"""

from .config import (
    ALERT_THRESHOLDS,
    ALERT_COOLDOWNS,
    HEALTH_METRICS,
    CONTEXT_CONFIG,
    GARMIN_OAUTH_CONFIG,
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
    GarminWebhookHandler,
    get_webhook_handler,
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
    "GARMIN_OAUTH_CONFIG",
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
    "GarminWebhookHandler",
    "get_webhook_handler",
    "manual_sync",
    # Alerts
    "process_and_send_alerts",
    "send_morning_health_summary",
    "get_alert_stats",
]
