"""
Garmin Health API Client

Fetches health data from Garmin Connect using OAuth2 tokens.
Handles rate limiting and error recovery.

API Docs: https://developer.garmin.com/gc-developer-program/health-api/
"""

import json
import urllib.request
import urllib.parse
import urllib.error
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from .config import GARMIN_OAUTH_CONFIG
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

# Garmin Health API Base URL
API_BASE = "https://apis.garmin.com/wellness-api/rest"


class GarminAPIError(Exception):
    """Raised when Garmin API request fails."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GarminAPI:
    """Client for Garmin Health API."""

    def __init__(self):
        self._auth = get_garmin_auth()

    def _make_request(
        self,
        endpoint: str,
        params: Optional[dict] = None,
        method: str = "GET"
    ) -> dict:
        """Make authenticated request to Garmin API."""
        token = self._auth.get_access_token()
        if not token:
            raise GarminAPIError("Not authenticated - complete OAuth flow first")

        url = f"{API_BASE}{endpoint}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        req = urllib.request.Request(url, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode())

        except urllib.error.HTTPError as e:
            if e.code == 401:
                # Token expired, try refresh
                logger.info("Token expired, attempting refresh...")
                if self._auth.refresh_token():
                    return self._make_request(endpoint, params, method)
                raise GarminAPIError("Authentication failed - re-authenticate required", 401)

            error_body = e.read().decode() if e.fp else ""
            logger.error(f"Garmin API error: {e.code} - {error_body}")
            raise GarminAPIError(f"API request failed: {e.code}", e.code)

        except urllib.error.URLError as e:
            logger.error(f"Network error: {e}")
            raise GarminAPIError(f"Network error: {e}")

    # ============== Data Fetchers ==============

    def get_sleep(self, target_date: date) -> Optional[SleepData]:
        """Fetch sleep data for a specific date."""
        try:
            data = self._make_request("/sleeps", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            sleep = data[0]  # Most recent sleep for that day
            duration_seconds = sleep.get("durationInSeconds", 0)

            return SleepData(
                date=target_date,
                duration_hours=duration_seconds / 3600,
                score=sleep.get("overallSleepScore", {}).get("value", 0),
                deep_hours=sleep.get("deepSleepDurationInSeconds", 0) / 3600,
                light_hours=sleep.get("lightSleepDurationInSeconds", 0) / 3600,
                rem_hours=sleep.get("remSleepInSeconds", 0) / 3600,
                awake_hours=sleep.get("awakeDurationInSeconds", 0) / 3600,
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch sleep: {e}")
            return None

    def get_heart_rate(self, target_date: date) -> Optional[HeartRateData]:
        """Fetch heart rate data for a specific date."""
        try:
            data = self._make_request("/dailies", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            daily = data[0]

            return HeartRateData(
                date=target_date,
                resting=daily.get("restingHeartRateInBeatsPerMinute", 0),
                max=daily.get("maxHeartRateInBeatsPerMinute", 0),
                min=daily.get("minHeartRateInBeatsPerMinute", 0),
                zones={},  # Heart rate zones not in daily summary
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch heart rate: {e}")
            return None

    def get_stress(self, target_date: date) -> Optional[StressData]:
        """Fetch stress data for a specific date."""
        try:
            data = self._make_request("/dailies", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            daily = data[0]
            avg_stress = daily.get("averageStressLevel", 0)
            max_stress = daily.get("maxStressLevel", 0)

            return StressData(
                date=target_date,
                avg_level=avg_stress,
                max_level=max_stress,
                qualifier=StressData.qualifier_from_level(avg_stress),
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch stress: {e}")
            return None

    def get_body_battery(self, target_date: date) -> Optional[BodyBatteryData]:
        """Fetch Body Battery data for a specific date."""
        try:
            data = self._make_request("/dailies", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            daily = data[0]

            # Body Battery is in bodyBatteryChargedValue and bodyBatteryDrainedValue
            charged = daily.get("bodyBatteryChargedValue", 0)
            drained = daily.get("bodyBatteryDrainedValue", 0)

            # Garmin may also provide bodyBatteryHighestValue and bodyBatteryLowestValue
            start_value = daily.get("bodyBatteryHighestValue", 100) - charged
            end_value = daily.get("bodyBatteryLowestValue", start_value + charged - drained)

            return BodyBatteryData(
                date=target_date,
                start_value=max(0, min(100, start_value)),
                end_value=max(0, min(100, end_value)),
                charged=charged,
                drained=drained,
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch body battery: {e}")
            return None

    def get_hrv(self, target_date: date) -> Optional[HRVData]:
        """Fetch HRV Status data for a specific date."""
        try:
            # HRV uses a dedicated endpoint
            data = self._make_request("/hrv", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            hrv = data[0]

            # HRV value is typically in weekly average or current
            hrv_value = hrv.get("hrvValue", hrv.get("weeklyAvg", 0))
            status = hrv.get("status", "balanced").lower()

            return HRVData(
                date=target_date,
                value=int(hrv_value),
                status=status if status in ["balanced", "low", "unbalanced"] else "balanced",
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch HRV: {e}")
            return None

    def get_spo2(self, target_date: date) -> Optional[SpO2Data]:
        """Fetch SpO2 (Pulse Ox) data for a specific date."""
        try:
            data = self._make_request("/pulseox", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            spo2 = data[0]

            return SpO2Data(
                date=target_date,
                avg=spo2.get("averageSpO2", 0),
                min=spo2.get("lowestSpO2", spo2.get("averageSpO2", 0)),
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch SpO2: {e}")
            return None

    def get_activity(self, target_date: date) -> Optional[ActivityData]:
        """Fetch activity summary for a specific date."""
        try:
            data = self._make_request("/dailies", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list) or len(data) == 0:
                return None

            daily = data[0]

            return ActivityData(
                date=target_date,
                steps=daily.get("steps", 0),
                active_minutes=daily.get("moderateIntensityDurationInSeconds", 0) // 60 +
                              daily.get("vigorousIntensityDurationInSeconds", 0) // 60,
                calories_total=daily.get("activeKilocalories", 0) +
                              daily.get("bmrKilocalories", 0),
                floors_climbed=daily.get("floorsClimbed", 0),
                distance_km=daily.get("distanceInMeters", 0) / 1000,
            )

        except GarminAPIError as e:
            logger.error(f"Failed to fetch activity: {e}")
            return None

    def get_workouts(self, target_date: date) -> list[WorkoutData]:
        """Fetch workouts/activities for a specific date."""
        try:
            data = self._make_request("/activities", {
                "uploadStartTimeInSeconds": int(datetime.combine(
                    target_date, datetime.min.time()
                ).timestamp()),
                "uploadEndTimeInSeconds": int(datetime.combine(
                    target_date + timedelta(days=1), datetime.min.time()
                ).timestamp()),
            })

            if not data or not isinstance(data, list):
                return []

            workouts = []
            for activity in data:
                workouts.append(WorkoutData(
                    id=str(activity.get("activityId", activity.get("summaryId", ""))),
                    date=target_date,
                    activity_type=activity.get("activityType", "unknown"),
                    duration_minutes=activity.get("durationInSeconds", 0) / 60,
                    distance_km=activity.get("distanceInMeters", 0) / 1000 if activity.get("distanceInMeters") else None,
                    calories=activity.get("activeKilocalories"),
                    avg_hr=activity.get("averageHeartRateInBeatsPerMinute"),
                    max_hr=activity.get("maxHeartRateInBeatsPerMinute"),
                    training_effect=activity.get("aerobicTrainingEffect"),
                ))

            return workouts

        except GarminAPIError as e:
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

        logger.info(f"Fetched health summary for {target_date}: {summary.has_data}")
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
