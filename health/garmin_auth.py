"""
Garmin OAuth2 Authentication

Handles OAuth2 PKCE flow for Garmin Connect API authentication.
Tokens are stored encrypted in the secrets directory.

NOTE: This module requires Garmin API credentials. Apply at:
https://developer.garmin.com/gc-developer-program/
"""

import os
import json
import base64
import hashlib
import secrets
import urllib.request
import urllib.parse
import urllib.error
import logging
from datetime import datetime, timedelta
from typing import Optional
from cryptography.fernet import Fernet

from .config import GARMIN_OAUTH_CONFIG, GARMIN_TOKENS_PATH

logger = logging.getLogger("claudius.health.auth")


class GarminAuthError(Exception):
    """Raised when Garmin authentication fails."""
    pass


class GarminAuth:
    """Handles Garmin OAuth2 authentication with PKCE."""

    def __init__(self, tokens_path: str = GARMIN_TOKENS_PATH):
        self.tokens_path = tokens_path
        self._encryption_key = self._get_or_create_key()
        self._fernet = Fernet(self._encryption_key)
        self._tokens: Optional[dict] = None
        self._code_verifier: Optional[str] = None

    def _get_or_create_key(self) -> bytes:
        """Get or create encryption key for token storage."""
        key_path = os.path.join(os.path.dirname(self.tokens_path), ".key")

        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return f.read()

        # Generate new key
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, "wb") as f:
            f.write(key)
        os.chmod(key_path, 0o600)  # Owner read/write only

        return key

    def _save_tokens(self, tokens: dict) -> None:
        """Save tokens encrypted to disk."""
        tokens["saved_at"] = datetime.utcnow().isoformat()
        encrypted = self._fernet.encrypt(json.dumps(tokens).encode())

        os.makedirs(os.path.dirname(self.tokens_path), exist_ok=True)
        with open(self.tokens_path, "wb") as f:
            f.write(encrypted)
        os.chmod(self.tokens_path, 0o600)

        self._tokens = tokens
        logger.info("Saved Garmin tokens")

    def _load_tokens(self) -> Optional[dict]:
        """Load tokens from disk."""
        if self._tokens:
            return self._tokens

        if not os.path.exists(self.tokens_path):
            return None

        try:
            with open(self.tokens_path, "rb") as f:
                encrypted = f.read()
            decrypted = self._fernet.decrypt(encrypted)
            self._tokens = json.loads(decrypted)
            return self._tokens
        except Exception as e:
            logger.error(f"Failed to load tokens: {e}")
            return None

    def is_authenticated(self) -> bool:
        """Check if we have valid tokens."""
        tokens = self._load_tokens()
        if not tokens:
            return False

        # Check if access token is expired
        if "expires_at" in tokens:
            expires_at = datetime.fromisoformat(tokens["expires_at"])
            if datetime.utcnow() >= expires_at:
                # Try to refresh
                return self.refresh_token()

        return "access_token" in tokens

    def get_access_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if needed."""
        if not self.is_authenticated():
            return None
        return self._tokens.get("access_token")

    # ============== PKCE Flow ==============

    def _generate_code_verifier(self) -> str:
        """Generate a code verifier for PKCE."""
        # 43-128 characters, URL-safe
        self._code_verifier = secrets.token_urlsafe(64)[:128]
        return self._code_verifier

    def _generate_code_challenge(self, verifier: str) -> str:
        """Generate code challenge from verifier (S256 method)."""
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def generate_auth_url(self) -> str:
        """
        Generate the OAuth2 authorization URL.
        User should be redirected to this URL to authorize.
        """
        config = GARMIN_OAUTH_CONFIG

        if not config["client_id"]:
            raise GarminAuthError("GARMIN_CLIENT_ID not configured")

        # Generate PKCE codes
        verifier = self._generate_code_verifier()
        challenge = self._generate_code_challenge(verifier)

        params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "redirect_uri": config["redirect_uri"],
            "scope": config["scope"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": secrets.token_urlsafe(16),  # CSRF protection
        }

        url = f"{config['auth_url']}?{urllib.parse.urlencode(params)}"
        logger.info(f"Generated auth URL (state={params['state'][:8]}...)")

        return url

    def handle_callback(self, code: str, state: Optional[str] = None) -> bool:
        """
        Handle the OAuth2 callback with authorization code.
        Exchange code for tokens.
        """
        config = GARMIN_OAUTH_CONFIG

        if not self._code_verifier:
            raise GarminAuthError("No code verifier - call generate_auth_url first")

        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code_verifier": self._code_verifier,
        }).encode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        req = urllib.request.Request(config["token_url"], data=data, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                tokens = json.loads(response.read().decode())

            # Calculate expiry time
            if "expires_in" in tokens:
                expires_at = datetime.utcnow() + timedelta(seconds=tokens["expires_in"])
                tokens["expires_at"] = expires_at.isoformat()

            self._save_tokens(tokens)
            self._code_verifier = None  # Clear verifier
            logger.info("Successfully authenticated with Garmin")
            return True

        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error(f"Token exchange failed: {e.code} - {error_body}")
            raise GarminAuthError(f"Token exchange failed: {e.code}")
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            raise GarminAuthError(f"Token exchange error: {e}")

    def refresh_token(self) -> bool:
        """Refresh the access token using the refresh token."""
        tokens = self._load_tokens()
        if not tokens or "refresh_token" not in tokens:
            logger.warning("No refresh token available")
            return False

        config = GARMIN_OAUTH_CONFIG

        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        }).encode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        req = urllib.request.Request(config["token_url"], data=data, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                new_tokens = json.loads(response.read().decode())

            # Preserve refresh token if not returned
            if "refresh_token" not in new_tokens and "refresh_token" in tokens:
                new_tokens["refresh_token"] = tokens["refresh_token"]

            # Calculate expiry time
            if "expires_in" in new_tokens:
                expires_at = datetime.utcnow() + timedelta(seconds=new_tokens["expires_in"])
                new_tokens["expires_at"] = expires_at.isoformat()

            self._save_tokens(new_tokens)
            logger.info("Successfully refreshed Garmin token")
            return True

        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error(f"Token refresh failed: {e.code} - {error_body}")
            return False
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return False

    def revoke_tokens(self) -> bool:
        """Revoke tokens and clear stored credentials."""
        # Clear local tokens
        if os.path.exists(self.tokens_path):
            os.remove(self.tokens_path)
        self._tokens = None

        logger.info("Revoked Garmin tokens")
        return True

    def get_auth_status(self) -> dict:
        """Get current authentication status."""
        tokens = self._load_tokens()

        if not tokens:
            return {
                "authenticated": False,
                "message": "Not authenticated - run OAuth flow",
            }

        expires_at = None
        if "expires_at" in tokens:
            expires_at = datetime.fromisoformat(tokens["expires_at"])

        return {
            "authenticated": True,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "has_refresh_token": "refresh_token" in tokens,
            "saved_at": tokens.get("saved_at"),
        }


# Singleton instance
_auth_instance: Optional[GarminAuth] = None


def get_garmin_auth() -> GarminAuth:
    """Get the singleton GarminAuth instance."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = GarminAuth()
    return _auth_instance
