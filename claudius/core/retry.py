"""
Retry utilities with exponential backoff.

Provides decorators and helpers for resilient API calls.
"""

import functools
import logging
import random
import time
from typing import Callable, Type, Tuple, Optional

logger = logging.getLogger('claudius.retry')

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_EXPONENTIAL_BASE = 2


def exponential_backoff(attempt: int, base_delay: float, max_delay: float,
                        exponential_base: int, jitter: bool = True) -> float:
    """Calculate delay for exponential backoff.

    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap
        exponential_base: Base for exponential calculation
        jitter: Add random jitter to prevent thundering herd

    Returns:
        Delay in seconds
    """
    delay = min(base_delay * (exponential_base ** attempt), max_delay)
    if jitter:
        delay = delay * (0.5 + random.random())
    return delay


def retry_with_backoff(max_retries: int = DEFAULT_MAX_RETRIES,
                       base_delay: float = DEFAULT_BASE_DELAY,
                       max_delay: float = DEFAULT_MAX_DELAY,
                       exceptions=Exception,
                       on_retry: Optional[Callable] = None) -> Callable:
    """Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries
        max_delay: Maximum delay cap
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback(exception, attempt) called before retry

    Usage:
        @retry_with_backoff(max_retries=3, exceptions=(ConnectionError, TimeoutError))
        def fetch_data():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        if on_retry:
                            on_retry(e, attempt)
                        delay = exponential_backoff(attempt, base_delay, max_delay, DEFAULT_EXPONENTIAL_BASE)
                        logger.warning(
                            f'Retry {attempt + 1}/{max_retries} for '
                            f'{func.__name__}: {type(e).__name__}: '
                            f'{str(e)[:50]}... (waiting {delay:.1f}s)'
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f'All {max_retries} retries failed for '
                            f'{func.__name__}: {str(e)[:100]}'
                        )
            raise last_exception
        return wrapper
    return decorator


class RetryableRequest:
    """Context manager for retryable operations.

    Usage:
        with RetryableRequest(max_retries=3) as retry:
            while retry.attempt():
                try:
                    result = make_request()
                    break
                except ConnectionError as e:
                    retry.handle_error(e)
    """

    def __init__(self, max_retries: int = DEFAULT_MAX_RETRIES,
                 base_delay: float = DEFAULT_BASE_DELAY,
                 max_delay: float = DEFAULT_MAX_DELAY):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.current_attempt = 0
        self.last_error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None

    def attempt(self) -> bool:
        """Check if another attempt should be made."""
        return self.current_attempt <= self.max_retries

    def handle_error(self, error: Exception):
        """Handle an error, sleeping if more retries available."""
        self.last_error = error
        self.current_attempt += 1
        if self.current_attempt <= self.max_retries:
            delay = exponential_backoff(
                self.current_attempt, self.base_delay, self.max_delay,
                DEFAULT_EXPONENTIAL_BASE
            )
            logger.warning(
                f'Retry {self.current_attempt}/{self.max_retries}: '
                f'{type(error).__name__}: {str(error)[:50]}... (waiting {delay:.1f}s)'
            )
            time.sleep(delay)


def with_fallback(primary: Callable, fallback: Callable, exceptions=Exception):
    """Execute primary function, fall back on failure.

    Args:
        primary: Primary function to try
        fallback: Fallback function if primary fails
        exceptions: Exceptions that trigger fallback

    Returns:
        Result from primary or fallback
    """
    try:
        return primary()
    except exceptions as e:
        logger.warning(f'Primary failed ({type(e).__name__}), using fallback')
        return fallback()
