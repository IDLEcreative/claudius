"""
Garmin Connect API Client

Fetches health data using the python-garminconnect library.
This uses the unofficial Garmin Connect API (same as the mobile app).

Install: pip install garminconnect
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from .garmin_auth import get_garmin_auth, GarminAuthError
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

logger = logging.getLogger("claudius.health.api")


class GarminAPIError(Exception):
    """Raised when Garmin API request fails."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GarminAPI:
    """Client for Garmin Connect using python-garminconnect."""

    def __init__(self):
        self._auth = get_garmin_auth()

    def _get_client(self):
        """Get authenticated Garmin client."""
        try:
            return self._auth.get_client()
        except GarminAuthError as e:
            raise GarminAPIError(f"Authentication failed: {e}")

    def get_sleep(self, target_date: date) -> Optional[SleepData]:
        """Fetch sleep data for a specific date."""
        try:
            client = self._get_client()
            data = client.get_sleep_data(target_date.isoformat())

            if not data:
                return None

            # Extract sleep data from response
            daily_sleep = data.get("dailySleepDTO", {})
            if not daily_sleep:
                return None

            duration_seconds = daily_sleep.get("sleepTimeSeconds", 0)
            if duration_seconds == 0:
                return None

            # Sleep levels breakdown
            levels = data.get("sleepLevels", {})

            return SleepData(
                date=target_date,
                duration_hours=duration_seconds / 3600,
                score=daily_sleep.get("sleepScores", {}).get("overall", {}).get("value", 0),
                deep_hours=levels.get("deepSleepSeconds", 0) / 3600,
                light_hours=levels.get("lightSleepSeconds", 0) / 3600,
                rem_hours=levels.get("remSleepSeconds", 0) / 3600,
                awake_hours=levels.get("awakeSleepSeconds", 0) / 3600,
            )

        except GarminAPIError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch sleep: {e}")
            return None

    def get_stats(self, target_date: date) -> dict:
        """Fetch daily stats (steps, stress, body battery, etc.)."""
        try:
            client = self._get_client()
            return client.get_stats(target_date.isoformat()) or {}
        except Exception as e:
            logger.error(f"Failed to fetch stats: {e}")
            return {}

    def get_heart_rate(self, target_date: date) -> Optional[HeartRateData]:
        """Fetch heart rate data for a specific date."""
        try:
            client = self._get_client()
            data = client.get_heart_rates(target_date.isoformat())

            if not data:
                return None

            return HeartRateData(
                date=target_date,
                resting=data.get("restingHeartRate", 0),
                max=data.get("maxHeartRate", 0),
                min=data.get("minHeartRate", 0),
                zones={},
            )

        except Exception as e:
            logger.error(f"Failed to fetch heart rate: {e}")
            return None

    def get_stress(self, target_date: date) -> Optional[StressData]:
        """Fetch stress data for a specific date."""
        try:
            stats = self.get_stats(target_date)
            if not stats:
                return None

            avg_stress = stats.get("averageStressLevel", -1)
            if avg_stress < 0:
                return None

            return StressData(
                date=target_date,
                avg_level=avg_stress,
                max_level=stats.get("maxStressLevel", avg_stress),
                qualifier=StressData.qualifier_from_level(avg_stress),
            )

        except Exception as e:
            logger.error(f"Failed to fetch stress: {e}")
            return None

    def get_body_battery(self, target_date: date) -> Optional[BodyBatteryData]:
        """Fetch Body Battery data for a specific date."""
        try:
            stats = self.get_stats(target_date)
            if not stats:
                return None

            # Body battery data from stats
            bb_data = stats.get("bodyBatteryChargedValue")
            if bb_data is None:
                return None

            return BodyBatteryData(
                date=target_date,
                start_value=stats.get("bodyBatteryHighestValue", 100),
                end_value=stats.get("bodyBatteryLowestValue", 50),
                charged=stats.get("bodyBatteryChargedValue", 0),
                drained=stats.get("bodyBatteryDrainedValue", 0),
            )

        except Exception as e:
            logger.error(f"Failed to fetch body battery: {e}")
            return None

    def get_hrv(self, target_date: date) -> Optional[HRVData]:
        """Fetch HRV data for a specific date."""
        try:
            client = self._get_client()
            data = client.get_hrv_data(target_date.isoformat())

            if not data:
                return None

            # HRV summary
            summary = data.get("hrvSummary", {})
            if not summary:
                # Try alternative structure
                hrv_value = data.get("lastNightAvg", data.get("weeklyAvg", 0))
                status = data.get("status", "BALANCED")
            else:
                hrv_value = summary.get("lastNightAvg", summary.get("weeklyAvg", 0))
                status = summary.get("status", "BALANCED")

            if not hrv_value:
                return None

            # Normalize status
            status_map = {
                "BALANCED": "balanced",
                "LOW": "low",
                "UNBALANCED": "unbalanced",
            }

            return HRVData(
                date=target_date,
                value=int(hrv_value),
                status=status_map.get(status.upper(), "balanced"),
            )

        except Exception as e:
            logger.error(f"Failed to fetch HRV: {e}")
            return None

    def get_spo2(self, target_date: date) -> Optional[SpO2Data]:
        """Fetch SpO2 (Pulse Ox) data for a specific date."""
        try:
            client = self._get_client()
            data = client.get_spo2_data(target_date.isoformat())

            if not data:
                return None

            # Get averages from the data
            avg = data.get("averageSpO2", data.get("avgValue"))
            min_val = data.get("lowestSpO2", data.get("minValue", avg))

            if not avg:
                return None

            return SpO2Data(
                date=target_date,
                avg=float(avg),
                min=float(min_val) if min_val else float(avg),
            )

        except Exception as e:
            logger.error(f"Failed to fetch SpO2: {e}")
            return None

    def get_activity(self, target_date: date) -> Optional[ActivityData]:
        """Fetch activity summary for a specific date."""
        try:
            stats = self.get_stats(target_date)
            if not stats:
                return None

            steps = stats.get("totalSteps", 0)
            if steps == 0:
                return None

            # Calculate active minutes
            moderate = stats.get("moderateIntensityMinutes", 0)
            vigorous = stats.get("vigorousIntensityMinutes", 0)

            return ActivityData(
                date=target_date,
                steps=steps,
                active_minutes=moderate + vigorous,
                calories_total=stats.get("totalKilocalories", 0),
                floors_climbed=stats.get("floorsAscended", 0),
                distance_km=stats.get("totalDistanceMeters", 0) / 1000,
            )

        except Exception as e:
            logger.error(f"Failed to fetch activity: {e}")
            return None

    def get_workouts(self, target_date: date) -> list[WorkoutData]:
        """Fetch workouts/activities for a specific date."""
        try:
            client = self._get_client()
            # Get activities for the date range
            start = datetime.combine(target_date, datetime.min.time())
            end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

            activities = client.get_activities_by_date(
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d")
            )

            if not activities:
                return []

            workouts = []
            for activity in activities:
                workouts.append(WorkoutData(
                    id=str(activity.get("activityId", "")),
                    date=target_date,
                    activity_type=activity.get("activityType", {}).get("typeKey", "unknown"),
                    duration_minutes=activity.get("duration", 0) / 60,
                    distance_km=activity.get("distance", 0) / 1000 if activity.get("distance") else None,
                    calories=activity.get("calories"),
                    avg_hr=activity.get("averageHR"),
                    max_hr=activity.get("maxHR"),
                    training_effect=activity.get("aerobicTrainingEffect"),
                ))

            return workouts

        except Exception as e:
            logger.error(f"Failed to fetch workouts: {e}")
            return []

    def get_daily_summary(self, target_date: date) -> DailyHealthSummary:
        """
        Fetch complete health summary for a date.
        Aggregates all available data types.
        """
        summary = DailyHealthSummary(date=target_date)

        # Fetch all data types
        summary.sleep = self.get_sleep(target_date)
        summary.heart_rate = self.get_heart_rate(target_date)
        summary.stress = self.get_stress(target_date)
        summary.body_battery = self.get_body_battery(target_date)
        summary.hrv = self.get_hrv(target_date)
        summary.spo2 = self.get_spo2(target_date)
        summary.activity = self.get_activity(target_date)
        summary.workouts = self.get_workouts(target_date)
        summary.synced_at = datetime.utcnow()

        logger.info(f"Fetched health summary for {target_date}: has_data={summary.has_data}")
        return summary

    def sync_date_range(self, start_date: date, end_date: date) -> list[DailyHealthSummary]:
        """
        Sync health data for a date range.
        Returns list of summaries.
        """
        summaries = []
        current = start_date

        while current <= end_date:
            summary = self.get_daily_summary(current)
            if summary.has_data:
                summaries.append(summary)
            current += timedelta(days=1)

        logger.info(f"Synced {len(summaries)} days from {start_date} to {end_date}")
        return summaries


# Singleton instance
_api_instance: Optional[GarminAPI] = None


def get_garmin_api() -> GarminAPI:
    """Get the singleton GarminAPI instance."""
    global _api_instance
    if _api_instance is None:
        _api_instance = GarminAPI()
    return _api_instance
