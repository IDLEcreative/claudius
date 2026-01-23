"""
Garmin Connect Authentication

Handles authentication via python-garminconnect library.
Sessions are stored locally to avoid repeated logins.

Install: pip install garminconnect
"""

import os
import json
import logging
from typing import Optional
from datetime import datetime

from .config import GARMIN_CONFIG

logger = logging.getLogger("claudius.health.auth")

# Try to import garminconnect
try:
    from garminconnect import Garmin, GarminConnectAuthenticationError
    GARMIN_AVAILABLE = True
except ImportError:
    GARMIN_AVAILABLE = False
    logger.warning("garminconnect not installed. Run: pip install garminconnect")


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails."""
    pass


class GarminAuth:
    """Handles Garmin Connect authentication with session persistence."""

    def __init__(self):
        self._client: Optional["Garmin"] = None
        self._session_path = GARMIN_CONFIG["session_path"]

    def _ensure_dirs(self) -> None:
        """Ensure session directory exists."""
        session_dir = os.path.dirname(self._session_path)
        if session_dir and not os.path.exists(session_dir):
            os.makedirs(session_dir, exist_ok=True)

    def _load_session(self) -> Optional[dict]:
        """Load saved session from disk."""
        if not os.path.exists(self._session_path):
            return None

        try:
            with open(self._session_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load session: {e}")
            return None

    def _save_session(self, session_data: dict) -> None:
        """Save session to disk for reuse."""
        self._ensure_dirs()
        try:
            with open(self._session_path, "w") as f:
                json.dump(session_data, f)
            os.chmod(self._session_path, 0o600)  # Owner read/write only
            logger.info("Garmin session saved")
        except Exception as e:
            logger.error(f"Failed to save session: {e}")

    def _create_client(self) -> "Garmin":
        """Create a new Garmin client instance."""
        if not GARMIN_AVAILABLE:
            raise GarminAuthError("garminconnect not installed. Run: pip install garminconnect")

        email = GARMIN_CONFIG["email"]
        password = GARMIN_CONFIG["password"]

        if not email or not password:
            raise GarminAuthError(
                "Garmin credentials not configured. "
                "Set GARMIN_EMAIL and GARMIN_PASSWORD environment variables."
            )

        return Garmin(email, password)

    def login(self, force_new: bool = False) -> bool:
        """
        Login to Garmin Connect.

        Tries to restore session first, falls back to fresh login.
        Returns True if successful.
        """
        if not GARMIN_AVAILABLE:
            raise GarminAuthError("garminconnect not installed")

        self._client = self._create_client()

        # Try to restore existing session
        if not force_new:
            session = self._load_session()
            if session:
                try:
                    self._client.login(session)
                    logger.info("Restored Garmin session from disk")
                    return True
                except Exception as e:
                    logger.info(f"Session restore failed, doing fresh login: {e}")

        # Fresh login required
        try:
            self._client.login()
            # Save session for future use
            self._save_session(self._client.session_data)
            logger.info("Fresh Garmin login successful")
            return True

        except GarminConnectAuthenticationError as e:
            logger.error(f"Garmin authentication failed: {e}")
            raise GarminAuthError(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Garmin login error: {e}")
            raise GarminAuthError(f"Login error: {e}")

    def get_client(self) -> "Garmin":
        """
        Get authenticated Garmin client.
        Auto-logs in if needed.
        """
        if self._client is None:
            self.login()
        return self._client

    def is_authenticated(self) -> bool:
        """Check if we have a valid session."""
        if self._client is not None:
            return True

        # Check if we have a saved session
        return self._load_session() is not None

    def logout(self) -> None:
        """Clear session and logout."""
        self._client = None
        if os.path.exists(self._session_path):
            os.remove(self._session_path)
        logger.info("Garmin session cleared")

    def get_auth_status(self) -> dict:
        """Get current authentication status."""
        session = self._load_session()
        has_credentials = bool(GARMIN_CONFIG["email"] and GARMIN_CONFIG["password"])

        return {
            "method": "garminconnect",
            "has_credentials": has_credentials,
            "has_session": session is not None,
            "library_installed": GARMIN_AVAILABLE,
            "email": GARMIN_CONFIG["email"][:3] + "***" if GARMIN_CONFIG["email"] else None,
        }


# Singleton instance
_auth_instance: Optional[GarminAuth] = None


def get_garmin_auth() -> GarminAuth:
    """Get the singleton GarminAuth instance."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = GarminAuth()
    return _auth_instance
