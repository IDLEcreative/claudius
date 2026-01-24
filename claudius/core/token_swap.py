"""
Token Swap - Automatic OAuth token rotation for Claude CLI.

When one account hits its usage limit, swap to the backup account and retry.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger('claudius.token_swap')

CREDENTIALS_PATH = '/opt/claudius/.claude/.credentials.json'

LIMIT_PATTERNS = (
    'usage limit',
    'limit reached',
    'rate limit exceeded',
    'quota exceeded',
    'exceeded.*limit',
    'run out of.*usage',
    'no.*capacity',
    'temporarily unavailable.*usage',
)


class TokenManager:
    """Manages OAuth token rotation between primary and backup accounts."""

    def __init__(self):
        self.credentials_path = CREDENTIALS_PATH
        self._current_account = 'primary'
        self._swap_count = 0
        self._last_swap = None

    def _load_credentials(self) -> dict:
        """Load credentials from file."""
        try:
            with open(self.credentials_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'Failed to load credentials: {e}')
            return None

    def _save_credentials(self, creds: dict) -> bool:
        """Save credentials to file."""
        try:
            backup_path = Path(self.credentials_path).with_suffix('.json.pre-swap')
            # Save backup of current credentials
            with open(backup_path, 'w') as f:
                json.dump(self._load_credentials(), f, indent=2)

            # Write new credentials
            with open(self.credentials_path, 'w') as f:
                json.dump(creds, f, indent=2)

            os.chmod(self.credentials_path, 0o600)
            return True
        except Exception as e:
            logger.error(f'Failed to save credentials: {e}')
            return False

    def is_limit_error(self, response: str) -> bool:
        """Check if response indicates account limit reached."""
        response_lower = response.lower()
        for pattern in LIMIT_PATTERNS:
            if re.search(pattern, response_lower):
                logger.info(f'Detected limit error matching pattern: {pattern}')
                return True
        return False

    def has_backup(self) -> bool:
        """Check if backup credentials are available."""
        creds = self._load_credentials()
        return bool(creds and creds.get('backupOauth', {}).get('accessToken'))

    def swap_tokens(self) -> Tuple[bool, str]:
        """Swap primary and backup OAuth tokens.

        Returns:
            Tuple of (success, message)
        """
        creds = self._load_credentials()
        primary = creds.get('claudeAiOauth')
        backup = creds.get('backupOauth')

        if not primary or not backup:
            return False, 'Missing primary or backup credentials'

        if not backup.get('accessToken'):
            return False, 'Backup token is empty'

        old_primary_label = primary.get('label', 'unknown')
        old_backup_label = backup.get('label', 'unknown')

        # Swap
        creds['claudeAiOauth'] = backup
        creds['backupOauth'] = primary

        if not self._save_credentials(creds):
            return False, 'Failed to save swapped credentials'

        self._swap_count += 1
        self._last_swap = datetime.now().isoformat()
        self._current_account = old_backup_label

        msg = f'Swapped tokens: {old_primary_label} -> {old_backup_label}'
        logger.info(f'[TokenSwap] {msg}')
        return True, msg

    def get_status(self) -> dict:
        """Get current token status."""
        creds = self._load_credentials()
        primary = creds.get('claudeAiOauth', {})
        backup = creds.get('backupOauth', {})

        return {
            'current_account': primary.get('label', 'unknown'),
            'backup_account': backup.get('label', 'unknown'),
            'has_backup': bool(backup.get('accessToken')),
            'swap_count': self._swap_count,
            'last_swap': self._last_swap,
        }


_token_manager: Optional[TokenManager] = None


def get_token_manager() -> TokenManager:
    """Get singleton TokenManager instance."""
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager


def check_and_swap_if_limited(response: str) -> bool:
    """Check if response indicates limit and swap if possible.

    Returns:
        Tuple of (swapped, message)
    """
    manager = get_token_manager()
    if not manager.is_limit_error(response):
        return False, 'No limit detected'
    if not manager.has_backup():
        return False, 'No backup token available'
    return manager.swap_tokens()
