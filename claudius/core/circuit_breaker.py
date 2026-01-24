"""
Circuit Breaker for external service calls.

Prevents cascade failures by tracking consecutive errors and temporarily
disabling calls to failing services. Automatically recovers after cooldown.

States:
  CLOSED  - Normal operation, requests pass through
  OPEN    - Service is failing, requests short-circuit immediately
  HALF_OPEN - Cooldown elapsed, next request is a probe
"""

import time
import threading
import logging
from typing import Callable, TypeVar, Optional

logger = logging.getLogger('claudius.circuit_breaker')

T = TypeVar('T')

CLOSED = 'closed'
OPEN = 'open'
HALF_OPEN = 'half_open'


class CircuitBreaker:
    """Simple 3-state circuit breaker for external service calls."""

    def __init__(self, name: str = 'name', failure_threshold: int = 3, cooldown_seconds: float = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._state = CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self):
        with self._lock:
            if self._state == OPEN:
                if time.time() - self._last_failure_time >= self.cooldown_seconds:
                    return HALF_OPEN
            return self._state

    def call(self, fn: Callable, *args, fallback=..., **kwargs) -> Optional[T]:
        """Execute fn through the circuit breaker.

        If the breaker is open, returns fallback immediately.
        On success, resets failure count. On exception, records failure.
        """
        current_state = self.state
        if current_state == OPEN:
            logger.debug(f'[CircuitBreaker:{self.name}] OPEN - returning fallback')
            return fallback

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            return fallback

    def _on_success(self):
        with self._lock:
            if self._state == HALF_OPEN:
                logger.info(f'[CircuitBreaker:{self.name}] Recovery confirmed, closing circuit')
                self._failure_count = 0
                self._state = CLOSED

    def _on_failure(self, error):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == HALF_OPEN:
                self._state = OPEN
                logger.warning(f'[CircuitBreaker:{self.name}] Probe failed, reopening: {error}')
            elif self._failure_count >= self.failure_threshold:
                self._state = OPEN
                logger.warning(
                    f'[CircuitBreaker:{self.name}] OPENED after '
                    f'{self._failure_count} failures (cooldown: '
                    f'{self.cooldown_seconds}s): {error}'
                )
            else:
                logger.debug(
                    f'[CircuitBreaker:{self.name}] Failure '
                    f'{self._failure_count}/{self.failure_threshold}: {error}'
                )

    def reset(self):
        """Manually reset the breaker to closed state."""
        with self._lock:
            self._state = CLOSED
            self._failure_count = 0


supabase_breaker = CircuitBreaker('supabase', failure_threshold=3, cooldown_seconds=60)
engram_breaker = CircuitBreaker('engram', failure_threshold=2, cooldown_seconds=30)
learning_memory_breaker = CircuitBreaker('learning_memory')
