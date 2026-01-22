"""
Garmin Webhook Sync Handler

Processes incoming webhooks from Garmin Health API.
Handles push notifications for new health data.

Webhook Types:
- dailies: Daily summaries (steps, calories, stress, body battery)
- sleeps: Sleep data
- activities: Workouts/exercises
- pulseox: SpO2 readings
- hrv: HRV status

Reference: https://developer.garmin.com/gc-developer-program/health-api/
"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from .config import GARMIN_OAUTH_CONFIG
from .garmin_api import get_garmin_api, GarminAPIError
from .health_store import HealthStore
from .health_alerts import process_and_send_alerts
from .types import (
    DailyHealthSummary,
    SleepData,
    HeartRateData,
    StressData,
    BodyBatteryData,
    HRVData,
    SpO2Data,
    ActivityData,
    WorkoutData,
)

logger = logging.getLogger("claudius.health.sync")


class GarminWebhookHandler:
    """Handles incoming Garmin Health API webhooks."""

    def __init__(self, store: Optional[HealthStore] = None):
        self.store = store or HealthStore()
        self._api = None  # Lazy load

    @property
    def api(self):
        """Lazy load API client."""
        if self._api is None:
            self._api = get_garmin_api()
        return self._api

    def verify_webhook(self, payload: dict, signature: Optional[str] = None) -> bool:
        """
        Verify webhook authenticity.

        Note: Garmin may provide HMAC signature for verification.
        Implementation depends on their webhook security model.
        """
        # Basic validation
        if not payload:
            logger.warning("Empty webhook payload")
            return False

        # Check for expected structure
        if not any(key in payload for key in ["dailies", "sleeps", "activities", "pulseox", "hrv", "epochs"]):
            logger.warning(f"Unexpected webhook structure: {list(payload.keys())}")
            return False

        # TODO: Implement HMAC signature verification when Garmin provides it
        return True

    def process_webhook(self, payload: dict) -> dict:
        """
        Process incoming webhook payload.
        Returns summary of what was processed.
        """
        if not self.verify_webhook(payload):
            return {"error": "Invalid webhook payload", "processed": False}

        results = {
            "processed": True,
            "data_types": [],
            "records": 0,
            "alerts_sent": 0,
        }

        try:
            # Process each data type if present
            if "dailies" in payload:
                count = self._process_dailies(payload["dailies"])
                results["data_types"].append("dailies")
                results["records"] += count

            if "sleeps" in payload:
                count = self._process_sleeps(payload["sleeps"])
                results["data_types"].append("sleeps")
                results["records"] += count

            if "activities" in payload:
                count = self._process_activities(payload["activities"])
                results["data_types"].append("activities")
                results["records"] += count

            if "pulseox" in payload:
                count = self._process_pulseox(payload["pulseox"])
                results["data_types"].append("pulseox")
                results["records"] += count

            if "hrv" in payload:
                count = self._process_hrv(payload["hrv"])
                results["data_types"].append("hrv")
                results["records"] += count

            # Log the sync
            self.store.log_sync(
                event_type="webhook",
                data_types=results["data_types"],
                status="success",
                records_processed=results["records"],
            )

            # Check for alerts after processing new data
            alerts_sent = process_and_send_alerts(self.store)
            results["alerts_sent"] = len(alerts_sent)

            logger.info(f"Webhook processed: {results}")
            return results

        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
            self.store.log_sync(
                event_type="webhook",
                data_types=list(payload.keys()),
                status="error",
                error_message=str(e),
            )
            return {"error": str(e), "processed": False}

    def _process_dailies(self, dailies: list) -> int:
        """Process daily summary records."""
        count = 0
        for daily in dailies:
            try:
                # Parse date from start time
                start_time = daily.get("startTimeInSeconds", 0)
                target_date = datetime.utcfromtimestamp(start_time).date()

                # Get or create summary for this date
                summary = self.store.get_daily_health(target_date) or DailyHealthSummary(date=target_date)

                # Update with daily data
                if daily.get("restingHeartRateInBeatsPerMinute"):
                    summary.heart_rate = HeartRateData(
                        date=target_date,
                        resting=daily.get("restingHeartRateInBeatsPerMinute", 0),
                        max=daily.get("maxHeartRateInBeatsPerMinute", 0),
                        min=daily.get("minHeartRateInBeatsPerMinute", 0),
                        zones={},
                    )

                if daily.get("averageStressLevel") is not None:
                    avg_stress = daily.get("averageStressLevel", 0)
                    summary.stress = StressData(
                        date=target_date,
                        avg_level=avg_stress,
                        max_level=daily.get("maxStressLevel", avg_stress),
                        qualifier=StressData.qualifier_from_level(avg_stress),
                    )

                if daily.get("bodyBatteryChargedValue") is not None:
                    summary.body_battery = BodyBatteryData(
                        date=target_date,
                        start_value=daily.get("bodyBatteryHighestValue", 100),
                        end_value=daily.get("bodyBatteryLowestValue", 50),
                        charged=daily.get("bodyBatteryChargedValue", 0),
                        drained=daily.get("bodyBatteryDrainedValue", 0),
                    )

                if daily.get("steps") is not None:
                    summary.activity = ActivityData(
                        date=target_date,
                        steps=daily.get("steps", 0),
                        active_minutes=(
                            daily.get("moderateIntensityDurationInSeconds", 0) +
                            daily.get("vigorousIntensityDurationInSeconds", 0)
                        ) // 60,
                        calories_total=(
                            daily.get("activeKilocalories", 0) +
                            daily.get("bmrKilocalories", 0)
                        ),
                        floors_climbed=daily.get("floorsClimbed", 0),
                        distance_km=daily.get("distanceInMeters", 0) / 1000,
                    )

                summary.synced_at = datetime.utcnow()
                self.store.save_daily_health(summary)
                count += 1

            except Exception as e:
                logger.error(f"Error processing daily record: {e}")

        return count

    def _process_sleeps(self, sleeps: list) -> int:
        """Process sleep records."""
        count = 0
        for sleep in sleeps:
            try:
                # Parse date
                start_time = sleep.get("startTimeInSeconds", 0)
                # Sleep belongs to the night before (date of waking)
                target_date = datetime.utcfromtimestamp(start_time).date()

                summary = self.store.get_daily_health(target_date) or DailyHealthSummary(date=target_date)

                duration_seconds = sleep.get("durationInSeconds", 0)
                summary.sleep = SleepData(
                    date=target_date,
                    duration_hours=duration_seconds / 3600,
                    score=sleep.get("overallSleepScore", {}).get("value", 0) if isinstance(
                        sleep.get("overallSleepScore"), dict
                    ) else sleep.get("overallSleepScore", 0),
                    deep_hours=sleep.get("deepSleepDurationInSeconds", 0) / 3600,
                    light_hours=sleep.get("lightSleepDurationInSeconds", 0) / 3600,
                    rem_hours=sleep.get("remSleepInSeconds", 0) / 3600,
                    awake_hours=sleep.get("awakeDurationInSeconds", 0) / 3600,
                )

                summary.synced_at = datetime.utcnow()
                self.store.save_daily_health(summary)
                count += 1

            except Exception as e:
                logger.error(f"Error processing sleep record: {e}")

        return count

    def _process_activities(self, activities: list) -> int:
        """Process activity/workout records."""
        count = 0
        for activity in activities:
            try:
                start_time = activity.get("startTimeInSeconds", 0)
                target_date = datetime.utcfromtimestamp(start_time).date()

                workout = WorkoutData(
                    id=str(activity.get("activityId", activity.get("summaryId", f"act_{start_time}"))),
                    date=target_date,
                    activity_type=activity.get("activityType", "unknown"),
                    duration_minutes=activity.get("durationInSeconds", 0) / 60,
                    distance_km=activity.get("distanceInMeters", 0) / 1000 if activity.get("distanceInMeters") else None,
                    calories=activity.get("activeKilocalories"),
                    avg_hr=activity.get("averageHeartRateInBeatsPerMinute"),
                    max_hr=activity.get("maxHeartRateInBeatsPerMinute"),
                    training_effect=activity.get("aerobicTrainingEffect"),
                )

                # Get or create summary for this date
                summary = self.store.get_daily_health(target_date) or DailyHealthSummary(date=target_date)

                # Add workout if not already present
                if not any(w.id == workout.id for w in summary.workouts):
                    summary.workouts.append(workout)
                    summary.synced_at = datetime.utcnow()
                    self.store.save_daily_health(summary)

                count += 1

            except Exception as e:
                logger.error(f"Error processing activity record: {e}")

        return count

    def _process_pulseox(self, pulseox_records: list) -> int:
        """Process SpO2/Pulse Ox records."""
        count = 0
        for record in pulseox_records:
            try:
                start_time = record.get("startTimeInSeconds", 0)
                target_date = datetime.utcfromtimestamp(start_time).date()

                summary = self.store.get_daily_health(target_date) or DailyHealthSummary(date=target_date)

                summary.spo2 = SpO2Data(
                    date=target_date,
                    avg=record.get("averageSpO2", record.get("spo2Value", 0)),
                    min=record.get("lowestSpO2", record.get("averageSpO2", 0)),
                )

                summary.synced_at = datetime.utcnow()
                self.store.save_daily_health(summary)
                count += 1

            except Exception as e:
                logger.error(f"Error processing pulse ox record: {e}")

        return count

    def _process_hrv(self, hrv_records: list) -> int:
        """Process HRV status records."""
        count = 0
        for record in hrv_records:
            try:
                start_time = record.get("startTimeInSeconds", 0)
                target_date = datetime.utcfromtimestamp(start_time).date()

                summary = self.store.get_daily_health(target_date) or DailyHealthSummary(date=target_date)

                hrv_value = record.get("hrvValue", record.get("weeklyAvg", 0))
                status = record.get("status", "balanced").lower()

                summary.hrv = HRVData(
                    date=target_date,
                    value=int(hrv_value) if hrv_value else 0,
                    status=status if status in ["balanced", "low", "unbalanced"] else "balanced",
                )

                summary.synced_at = datetime.utcnow()
                self.store.save_daily_health(summary)
                count += 1

            except Exception as e:
                logger.error(f"Error processing HRV record: {e}")

        return count


def manual_sync(days_back: int = 7) -> dict:
    """
    Manually trigger a sync for the last N days.
    Useful for initial data backfill or recovery.
    """
    api = get_garmin_api()
    store = HealthStore()

    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    results = {
        "synced_days": 0,
        "total_records": 0,
        "errors": [],
    }

    try:
        summaries = api.sync_date_range(start_date, end_date)

        for summary in summaries:
            store.save_daily_health(summary)
            results["synced_days"] += 1
            results["total_records"] += sum([
                1 if summary.sleep else 0,
                1 if summary.heart_rate else 0,
                1 if summary.stress else 0,
                1 if summary.body_battery else 0,
                1 if summary.hrv else 0,
                1 if summary.spo2 else 0,
                1 if summary.activity else 0,
                len(summary.workouts),
            ])

        store.log_sync(
            event_type="manual",
            data_types=["all"],
            status="success",
            records_processed=results["total_records"],
        )

        # Check for alerts after sync
        alerts = process_and_send_alerts(store)
        results["alerts_sent"] = len(alerts)

        logger.info(f"Manual sync completed: {results}")

    except GarminAPIError as e:
        results["errors"].append(str(e))
        store.log_sync(
            event_type="manual",
            data_types=["all"],
            status="error",
            error_message=str(e),
        )

    return results


# Import timedelta at module level
from datetime import timedelta


# Singleton handler
_handler_instance: Optional[GarminWebhookHandler] = None


def get_webhook_handler() -> GarminWebhookHandler:
    """Get the singleton webhook handler."""
    global _handler_instance
    if _handler_instance is None:
        _handler_instance = GarminWebhookHandler()
    return _handler_instance
