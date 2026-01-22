"""
Health Module Type Definitions

Dataclasses for all health-related data structures.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class SleepData:
    """Sleep metrics from Garmin."""
    date: date
    duration_hours: float
    score: int
    deep_hours: float = 0.0
    light_hours: float = 0.0
    rem_hours: float = 0.0
    awake_hours: float = 0.0

    @property
    def quality(self) -> str:
        """Human-readable sleep quality."""
        if self.score >= 80:
            return "excellent"
        elif self.score >= 60:
            return "good"
        elif self.score >= 40:
            return "fair"
        return "poor"


@dataclass
class HeartRateData:
    """Heart rate metrics from Garmin."""
    date: date
    resting: int
    max: int
    min: int
    zones: dict[str, int] = field(default_factory=dict)  # zone -> minutes


@dataclass
class StressData:
    """Stress metrics from Garmin."""
    date: date
    avg_level: int
    max_level: int
    qualifier: str  # 'calm', 'balanced', 'stressful', 'very stressful'

    @classmethod
    def qualifier_from_level(cls, level: int) -> str:
        """Get qualifier string from stress level."""
        if level < 25:
            return "calm"
        elif level < 50:
            return "balanced"
        elif level < 75:
            return "stressful"
        return "very stressful"


@dataclass
class BodyBatteryData:
    """Body Battery metrics from Garmin."""
    date: date
    start_value: int  # Morning value
    end_value: int    # Current/evening value
    charged: int      # Total charged during day
    drained: int      # Total drained during day

    @property
    def net_change(self) -> int:
        """Net change in body battery."""
        return self.end_value - self.start_value

    @property
    def status(self) -> str:
        """Human-readable status."""
        if self.end_value >= 70:
            return "high"
        elif self.end_value >= 40:
            return "moderate"
        elif self.end_value >= 20:
            return "low"
        return "critical"


@dataclass
class HRVData:
    """Heart Rate Variability metrics from Garmin."""
    date: date
    value: int  # RMSSD in milliseconds
    status: str  # 'balanced', 'low', 'unbalanced'

    @property
    def recovery_indicator(self) -> str:
        """Recovery status based on HRV."""
        if self.status == "balanced" and self.value >= 50:
            return "good"
        elif self.status == "balanced":
            return "moderate"
        return "needs attention"


@dataclass
class SpO2Data:
    """Blood oxygen metrics from Garmin."""
    date: date
    avg: float
    min: float

    @property
    def status(self) -> str:
        """Health status based on SpO2."""
        if self.avg >= 96:
            return "normal"
        elif self.avg >= 94:
            return "low-normal"
        elif self.avg >= 90:
            return "low"
        return "very low"


@dataclass
class ActivityData:
    """Daily activity metrics from Garmin."""
    date: date
    steps: int
    active_minutes: int
    calories_total: int
    floors_climbed: int = 0
    distance_km: float = 0.0

    @property
    def activity_level(self) -> str:
        """Human-readable activity level."""
        if self.steps >= 10000:
            return "very active"
        elif self.steps >= 7000:
            return "active"
        elif self.steps >= 4000:
            return "moderate"
        return "sedentary"


@dataclass
class WorkoutData:
    """Individual workout/activity from Garmin."""
    id: str
    date: date
    activity_type: str  # 'running', 'cycling', 'strength', etc.
    duration_minutes: float
    distance_km: Optional[float] = None
    calories: Optional[int] = None
    avg_hr: Optional[int] = None
    max_hr: Optional[int] = None
    training_effect: Optional[float] = None


@dataclass
class DailyHealthSummary:
    """Complete daily health summary combining all metrics."""
    date: date
    sleep: Optional[SleepData] = None
    heart_rate: Optional[HeartRateData] = None
    stress: Optional[StressData] = None
    body_battery: Optional[BodyBatteryData] = None
    hrv: Optional[HRVData] = None
    spo2: Optional[SpO2Data] = None
    activity: Optional[ActivityData] = None
    workouts: list[WorkoutData] = field(default_factory=list)
    synced_at: Optional[datetime] = None

    @property
    def has_data(self) -> bool:
        """Check if we have any health data for this day."""
        return any([
            self.sleep,
            self.heart_rate,
            self.stress,
            self.body_battery,
            self.hrv,
            self.spo2,
            self.activity,
        ])


@dataclass
class HealthAlert:
    """Health alert to be sent to user."""
    type: str  # 'low_sleep', 'high_stress', etc.
    severity: str  # 'warning', 'critical'
    metric_name: str
    current_value: float | int
    threshold: float | int
    message: str
    recommendation: Optional[str] = None
    triggered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class HealthContext:
    """Generated context block for Claudius prompts."""
    summary_text: str
    alerts: list[HealthAlert] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def has_alerts(self) -> bool:
        """Check if there are any active alerts."""
        return len(self.alerts) > 0

    @property
    def critical_alerts(self) -> list[HealthAlert]:
        """Get only critical severity alerts."""
        return [a for a in self.alerts if a.severity == "critical"]

    def to_prompt_block(self) -> str:
        """Format as a prompt block for Claudius."""
        lines = ["[Health Context]"]
        lines.append(self.summary_text)

        if self.alerts:
            lines.append("")
            for alert in self.alerts[:3]:  # Max 3 alerts
                emoji = "🚨" if alert.severity == "critical" else "⚠️"
                lines.append(f"{emoji} {alert.message}")

        if self.recommendations:
            lines.append("")
            lines.append("💡 " + self.recommendations[0])

        lines.append("[End Health Context]")
        return "\n".join(lines)
