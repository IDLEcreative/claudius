"""
Garmin Health Data Sync

Handles polling-based synchronization of Garmin health data.
Since the unofficial API doesn't support webhooks, this module
provides manual and cron-based sync functionality.

Usage:
    # Manual sync via API endpoint
    POST /health/sync {"days": 7}

    # Cron job (every 15 minutes)
    */15 * * * * cd /opt/claudius && python3 -c "from health import sync_health_data; sync_health_data()"
"""

import logging
from datetime import date, timedelta
from typing import Optional

from .config import SYNC_CONFIG
from .garmin_api import get_garmin_api, GarminAPIError
from .health_store import HealthStore
from .health_alerts import process_and_send_alerts

logger = logging.getLogger("claudius.health.sync")


def sync_health_data(
    days_back: int = 1,
    store: Optional[HealthStore] = None,
    send_alerts: bool = True
) -> dict:
    """
    Sync health data for the last N days.

    This is the main entry point for both manual and cron-based sync.

    Args:
        days_back: Number of days to sync (default: 1 for just today/yesterday)
        store: HealthStore instance (creates one if not provided)
        send_alerts: Whether to check and send alerts after sync

    Returns:
        Dict with sync results
    """
    if store is None:
        store = HealthStore()

    api = get_garmin_api()

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    results = {
        "success": True,
        "synced_days": 0,
        "data_points": 0,
        "alerts_sent": 0,
        "errors": [],
    }

    try:
        logger.info(f"Starting health sync: {start_date} to {end_date}")

        summaries = api.sync_date_range(start_date, end_date)

        for summary in summaries:
            store.save_daily_health(summary)
            results["synced_days"] += 1
            results["data_points"] += sum([
                1 if summary.sleep else 0,
                1 if summary.heart_rate else 0,
                1 if summary.stress else 0,
                1 if summary.body_battery else 0,
                1 if summary.hrv else 0,
                1 if summary.spo2 else 0,
                1 if summary.activity else 0,
                len(summary.workouts),
            ])

        # Log sync event
        store.log_sync(
            event_type="poll",
            data_types=["all"],
            status="success",
            records_processed=results["data_points"],
        )

        # Check for alerts if enabled
        if send_alerts and results["synced_days"] > 0:
            alerts = process_and_send_alerts(store)
            results["alerts_sent"] = len(alerts)

        logger.info(f"Health sync completed: {results['synced_days']} days, {results['data_points']} data points")

    except GarminAPIError as e:
        results["success"] = False
        results["errors"].append(f"API error: {e}")
        store.log_sync(
            event_type="poll",
            data_types=["all"],
            status="error",
            error_message=str(e),
        )
        logger.error(f"Health sync failed: {e}")

    except Exception as e:
        results["success"] = False
        results["errors"].append(str(e))
        store.log_sync(
            event_type="poll",
            data_types=["all"],
            status="error",
            error_message=str(e),
        )
        logger.error(f"Health sync error: {e}")

    return results


def sync_today(store: Optional[HealthStore] = None) -> dict:
    """Quick sync for today only."""
    return sync_health_data(days_back=0, store=store)


def sync_recent(store: Optional[HealthStore] = None) -> dict:
    """Sync last 2 days (today + yesterday for sleep data)."""
    return sync_health_data(days_back=1, store=store)


def backfill(days: int = 7, store: Optional[HealthStore] = None) -> dict:
    """Backfill historical data."""
    return sync_health_data(days_back=days, store=store, send_alerts=False)


# Alias for backward compatibility
manual_sync = sync_health_data


# For cron job execution
if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = sync_health_data(days_back=days)

    if result["success"]:
        print(f"✓ Synced {result['synced_days']} days, {result['data_points']} data points")
        if result["alerts_sent"]:
            print(f"  Sent {result['alerts_sent']} alerts")
    else:
        print(f"✗ Sync failed: {result['errors']}")
        sys.exit(1)
