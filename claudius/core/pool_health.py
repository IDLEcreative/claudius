"""
Pool Health - Resource checking and request metrics for AgentPool.

Provides:
- System resource validation before spawning agents
- Request duration/success metrics ring buffer
- Aggregated stats (p95, avg, max)
"""

import logging
import os
import threading
from collections import deque
from datetime import datetime

import psutil

logger = logging.getLogger('claudius.pool_health')

MIN_AVAILABLE_MEMORY_MB = 500
MAX_CLAUDE_PROCESSES = 10
METRICS_BUFFER_SIZE = 100


def check_resources() -> tuple[bool, str]:
    """Check if system has resources to spawn a new agent."""
    try:
        # Check available memory
        mem = psutil.virtual_memory()
        available_mb = mem.available / 1048576
        if available_mb < MIN_AVAILABLE_MEMORY_MB:
            return False, f'Low memory: {available_mb:.0f}MB'

        # Check Claude process count
        claude_procs = [
            p for p in psutil.process_iter(['cmdline'])
            if 'claude' in str(p.info.get('cmdline', [])).lower()
        ]
        if len(claude_procs) > MAX_CLAUDE_PROCESSES:
            return False, f'Too many Claude processes: {len(claude_procs)}'

        # Check CPU load
        load_avg = os.getloadavg()[0]
        cpu_count = os.cpu_count() or 1
        if load_avg > cpu_count * 2:
            return False, f'High load: {load_avg:.1f}'

        return True, 'OK'

    except Exception as e:
        logger.warning(f'Resource check failed: {e}')
        return True, 'Check failed (allowing)'


class RequestMetrics:
    """Thread-safe ring buffer of request metrics with aggregation."""

    def __init__(self, maxlen: int = METRICS_BUFFER_SIZE):
        self._lock = threading.Lock()
        self._buffer = deque(maxlen=maxlen)

    def record(self, duration_s: float, success: bool, sources_failed: list = None):
        """Record a request metric entry."""
        with self._lock:
            self._buffer.append({
                'timestamp': datetime.now().isoformat(),
                'duration_s': round(duration_s, 2),
                'success': success,
                'sources_failed': sources_failed,
            })

    def get_summary(self) -> dict:
        """Get aggregated request metrics."""
        with self._lock:
            if not self._buffer:
                return {'total_requests': 0}

            metrics = list(self._buffer)

        durations = [m['duration_s'] for m in metrics]
        successes = sum(1 for m in metrics if m['success'])

        durations_sorted = sorted(durations)
        p95_idx = int(len(durations_sorted) * 0.95)

        return {
            'total_requests': len(metrics),
            'success_count': successes,
            'failure_count': len(metrics) - successes,
            'avg_duration_s': round(sum(durations) / len(durations), 2),
            'max_duration_s': max(durations),
            'p95_duration_s': round(durations_sorted[min(p95_idx, len(durations_sorted) - 1)], 2),
            'recent': metrics[-5:],
        }
