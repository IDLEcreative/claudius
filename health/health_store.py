"""
Health Data Store

SQLite-based storage for Garmin health metrics.
Handles data persistence, queries, and cleanup.
"""

import sqlite3
import json
import os
from datetime import date, datetime, timedelta
from typing import Optional
from contextlib import contextmanager

from .config import HEALTH_DB_PATH, DATA_RETENTION
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
    HealthAlert,
)


class HealthStore:
    """SQLite-based health data storage."""

    def __init__(self, db_path: str = HEALTH_DB_PATH):
        self.db_path = db_path
        self._ensure_db_dir()
        self._init_schema()

    def _ensure_db_dir(self) -> None:
        """Ensure the database directory exists."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with self._get_connection() as conn:
            conn.executescript("""
                -- Daily health summaries (one per day)
                CREATE TABLE IF NOT EXISTS daily_health (
                    date TEXT PRIMARY KEY,

                    -- Sleep metrics
                    sleep_duration_hours REAL,
                    sleep_score INTEGER,
                    deep_sleep_hours REAL,
                    light_sleep_hours REAL,
                    rem_sleep_hours REAL,
                    awake_hours REAL,

                    -- Heart rate
                    resting_hr INTEGER,
                    max_hr INTEGER,
                    min_hr INTEGER,
                    hr_zones TEXT,  -- JSON

                    -- Stress & Recovery
                    avg_stress INTEGER,
                    max_stress INTEGER,
                    stress_qualifier TEXT,

                    -- Body Battery
                    body_battery_start INTEGER,
                    body_battery_end INTEGER,
                    body_battery_charged INTEGER,
                    body_battery_drained INTEGER,

                    -- HRV
                    hrv_value INTEGER,
                    hrv_status TEXT,

                    -- SpO2
                    spo2_avg REAL,
                    spo2_min REAL,

                    -- Activity
                    steps INTEGER,
                    active_minutes INTEGER,
                    calories_total INTEGER,
                    floors_climbed INTEGER,
                    distance_km REAL,

                    -- Metadata
                    synced_at TEXT,
                    garmin_user_id TEXT
                );

                -- Workout activities
                CREATE TABLE IF NOT EXISTS workouts (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    activity_type TEXT,
                    duration_minutes REAL,
                    distance_km REAL,
                    calories INTEGER,
                    avg_hr INTEGER,
                    max_hr INTEGER,
                    training_effect REAL,
                    synced_at TEXT,
                    FOREIGN KEY (date) REFERENCES daily_health(date)
                );

                -- Alert history (for cooldown tracking)
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    metric_name TEXT,
                    current_value REAL,
                    threshold REAL,
                    message TEXT NOT NULL,
                    recommendation TEXT,
                    triggered_at TEXT NOT NULL,
                    sent_via TEXT,
                    acknowledged_at TEXT
                );

                -- Sync metadata
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    data_types TEXT,  -- JSON array
                    status TEXT,
                    error_message TEXT,
                    records_processed INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_daily_health_date ON daily_health(date);
                CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date);
                CREATE INDEX IF NOT EXISTS idx_alert_history_type ON alert_history(alert_type, triggered_at);
                CREATE INDEX IF NOT EXISTS idx_sync_log_created ON sync_log(created_at);
            """)

    # ============== Daily Health CRUD ==============

    def save_daily_health(self, summary: DailyHealthSummary) -> None:
        """Save or update daily health summary."""
        date_str = summary.date.isoformat()

        # Build the data dict from the summary
        data = {"date": date_str, "synced_at": datetime.utcnow().isoformat()}

        if summary.sleep:
            data.update({
                "sleep_duration_hours": summary.sleep.duration_hours,
                "sleep_score": summary.sleep.score,
                "deep_sleep_hours": summary.sleep.deep_hours,
                "light_sleep_hours": summary.sleep.light_hours,
                "rem_sleep_hours": summary.sleep.rem_hours,
                "awake_hours": summary.sleep.awake_hours,
            })

        if summary.heart_rate:
            data.update({
                "resting_hr": summary.heart_rate.resting,
                "max_hr": summary.heart_rate.max,
                "min_hr": summary.heart_rate.min,
                "hr_zones": json.dumps(summary.heart_rate.zones),
            })

        if summary.stress:
            data.update({
                "avg_stress": summary.stress.avg_level,
                "max_stress": summary.stress.max_level,
                "stress_qualifier": summary.stress.qualifier,
            })

        if summary.body_battery:
            data.update({
                "body_battery_start": summary.body_battery.start_value,
                "body_battery_end": summary.body_battery.end_value,
                "body_battery_charged": summary.body_battery.charged,
                "body_battery_drained": summary.body_battery.drained,
            })

        if summary.hrv:
            data.update({
                "hrv_value": summary.hrv.value,
                "hrv_status": summary.hrv.status,
            })

        if summary.spo2:
            data.update({
                "spo2_avg": summary.spo2.avg,
                "spo2_min": summary.spo2.min,
            })

        if summary.activity:
            data.update({
                "steps": summary.activity.steps,
                "active_minutes": summary.activity.active_minutes,
                "calories_total": summary.activity.calories_total,
                "floors_climbed": summary.activity.floors_climbed,
                "distance_km": summary.activity.distance_km,
            })

        # Upsert
        columns = list(data.keys())
        placeholders = ["?" for _ in columns]
        updates = [f"{col} = excluded.{col}" for col in columns if col != "date"]

        sql = f"""
            INSERT INTO daily_health ({', '.join(columns)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT(date) DO UPDATE SET {', '.join(updates)}
        """

        with self._get_connection() as conn:
            conn.execute(sql, list(data.values()))

            # Save workouts
            for workout in summary.workouts:
                self._save_workout(conn, workout)

    def _save_workout(self, conn: sqlite3.Connection, workout: WorkoutData) -> None:
        """Save a workout record."""
        conn.execute("""
            INSERT OR REPLACE INTO workouts
            (id, date, activity_type, duration_minutes, distance_km, calories, avg_hr, max_hr, training_effect, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            workout.id,
            workout.date.isoformat(),
            workout.activity_type,
            workout.duration_minutes,
            workout.distance_km,
            workout.calories,
            workout.avg_hr,
            workout.max_hr,
            workout.training_effect,
            datetime.utcnow().isoformat(),
        ))

    def get_daily_health(self, target_date: date) -> Optional[DailyHealthSummary]:
        """Get health summary for a specific date."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM daily_health WHERE date = ?",
                (target_date.isoformat(),)
            ).fetchone()

            if not row:
                return None

            return self._row_to_summary(dict(row), target_date)

    def get_recent_health(self, days: int = 7) -> list[DailyHealthSummary]:
        """Get health summaries for the last N days."""
        start_date = date.today() - timedelta(days=days)

        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_health WHERE date >= ? ORDER BY date DESC",
                (start_date.isoformat(),)
            ).fetchall()

            return [
                self._row_to_summary(dict(row), date.fromisoformat(row["date"]))
                for row in rows
            ]

    def _row_to_summary(self, row: dict, target_date: date) -> DailyHealthSummary:
        """Convert database row to DailyHealthSummary."""
        summary = DailyHealthSummary(date=target_date)

        # Sleep
        if row.get("sleep_duration_hours") is not None:
            summary.sleep = SleepData(
                date=target_date,
                duration_hours=row["sleep_duration_hours"],
                score=row["sleep_score"] or 0,
                deep_hours=row.get("deep_sleep_hours", 0) or 0,
                light_hours=row.get("light_sleep_hours", 0) or 0,
                rem_hours=row.get("rem_sleep_hours", 0) or 0,
                awake_hours=row.get("awake_hours", 0) or 0,
            )

        # Heart rate
        if row.get("resting_hr") is not None:
            zones = {}
            if row.get("hr_zones"):
                try:
                    zones = json.loads(row["hr_zones"])
                except json.JSONDecodeError:
                    pass
            summary.heart_rate = HeartRateData(
                date=target_date,
                resting=row["resting_hr"],
                max=row.get("max_hr", 0) or 0,
                min=row.get("min_hr", 0) or 0,
                zones=zones,
            )

        # Stress
        if row.get("avg_stress") is not None:
            summary.stress = StressData(
                date=target_date,
                avg_level=row["avg_stress"],
                max_level=row.get("max_stress", 0) or 0,
                qualifier=row.get("stress_qualifier", "balanced"),
            )

        # Body Battery
        if row.get("body_battery_end") is not None:
            summary.body_battery = BodyBatteryData(
                date=target_date,
                start_value=row.get("body_battery_start", 0) or 0,
                end_value=row["body_battery_end"],
                charged=row.get("body_battery_charged", 0) or 0,
                drained=row.get("body_battery_drained", 0) or 0,
            )

        # HRV
        if row.get("hrv_value") is not None:
            summary.hrv = HRVData(
                date=target_date,
                value=row["hrv_value"],
                status=row.get("hrv_status", "balanced"),
            )

        # SpO2
        if row.get("spo2_avg") is not None:
            summary.spo2 = SpO2Data(
                date=target_date,
                avg=row["spo2_avg"],
                min=row.get("spo2_min", row["spo2_avg"]),
            )

        # Activity
        if row.get("steps") is not None:
            summary.activity = ActivityData(
                date=target_date,
                steps=row["steps"],
                active_minutes=row.get("active_minutes", 0) or 0,
                calories_total=row.get("calories_total", 0) or 0,
                floors_climbed=row.get("floors_climbed", 0) or 0,
                distance_km=row.get("distance_km", 0) or 0,
            )

        # Metadata
        if row.get("synced_at"):
            summary.synced_at = datetime.fromisoformat(row["synced_at"])

        return summary

    # ============== Alert History ==============

    def save_alert(self, alert: HealthAlert) -> int:
        """Save an alert to history. Returns the alert ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO alert_history
                (alert_type, severity, metric_name, current_value, threshold, message, recommendation, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.type,
                alert.severity,
                alert.metric_name,
                alert.current_value,
                alert.threshold,
                alert.message,
                alert.recommendation,
                alert.triggered_at.isoformat(),
            ))
            return cursor.lastrowid

    def get_last_alert_time(self, alert_type: str) -> Optional[datetime]:
        """Get the timestamp of the last alert of a given type."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT triggered_at FROM alert_history WHERE alert_type = ? ORDER BY triggered_at DESC LIMIT 1",
                (alert_type,)
            ).fetchone()

            if row:
                return datetime.fromisoformat(row["triggered_at"])
            return None

    def can_send_alert(self, alert_type: str, cooldown_seconds: int) -> bool:
        """Check if we can send an alert (respecting cooldown)."""
        last_alert = self.get_last_alert_time(alert_type)
        if not last_alert:
            return True

        elapsed = (datetime.utcnow() - last_alert).total_seconds()
        return elapsed >= cooldown_seconds

    # ============== Sync Log ==============

    def log_sync(
        self,
        event_type: str,
        data_types: list[str],
        status: str,
        records_processed: int = 0,
        error_message: Optional[str] = None
    ) -> None:
        """Log a sync event."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sync_log (event_type, data_types, status, records_processed, error_message)
                VALUES (?, ?, ?, ?, ?)
            """, (
                event_type,
                json.dumps(data_types),
                status,
                records_processed,
                error_message,
            ))

    def get_last_sync(self) -> Optional[dict]:
        """Get the most recent sync event."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sync_log ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

            if row:
                return dict(row)
            return None

    # ============== Cleanup ==============

    def cleanup_old_data(self) -> dict[str, int]:
        """Remove data older than retention limits. Returns counts of deleted records."""
        deleted = {}

        with self._get_connection() as conn:
            # Daily health
            cutoff = (date.today() - timedelta(days=DATA_RETENTION["daily_health"])).isoformat()
            result = conn.execute("DELETE FROM daily_health WHERE date < ?", (cutoff,))
            deleted["daily_health"] = result.rowcount

            # Workouts
            cutoff = (date.today() - timedelta(days=DATA_RETENTION["workouts"])).isoformat()
            result = conn.execute("DELETE FROM workouts WHERE date < ?", (cutoff,))
            deleted["workouts"] = result.rowcount

            # Alert history
            cutoff = (datetime.utcnow() - timedelta(days=DATA_RETENTION["alert_history"])).isoformat()
            result = conn.execute("DELETE FROM alert_history WHERE triggered_at < ?", (cutoff,))
            deleted["alert_history"] = result.rowcount

            # Sync log (keep last 30 days)
            cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
            result = conn.execute("DELETE FROM sync_log WHERE created_at < ?", (cutoff,))
            deleted["sync_log"] = result.rowcount

        return deleted

    # ============== Statistics ==============

    def get_stats(self) -> dict:
        """Get storage statistics."""
        with self._get_connection() as conn:
            stats = {}

            # Count records
            for table in ["daily_health", "workouts", "alert_history", "sync_log"]:
                row = conn.execute(f"SELECT COUNT(*) as count FROM {table}").fetchone()
                stats[f"{table}_count"] = row["count"]

            # Date range
            row = conn.execute("SELECT MIN(date) as min, MAX(date) as max FROM daily_health").fetchone()
            stats["date_range"] = {"min": row["min"], "max": row["max"]}

            # Last sync
            row = conn.execute("SELECT MAX(synced_at) as last FROM daily_health").fetchone()
            stats["last_synced"] = row["last"]

            # Database size
            stats["db_size_bytes"] = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0

            return stats
