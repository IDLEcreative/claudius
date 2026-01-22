"""
Account configuration management for Claudius.

Loads per-account settings (email, Drive folder IDs, etc.)
from config/accounts/*.json
"""

import json
from pathlib import Path
from dataclasses import dataclass

CONFIG_DIR = Path("/opt/claudius/config/accounts")


@dataclass
class AccountConfig:
    """Configuration for a Google Workspace account."""
    name: str
    email: str
    credentials_file: str
    vat_folder_id: str
    state_prefix: str

    @classmethod
    def load(cls, account_name: str) -> "AccountConfig":
        """Load account config from JSON file."""
        config_file = CONFIG_DIR / f"{account_name}.json"
        with open(config_file) as f:
            data = json.load(f)
        return cls(
            name=account_name,
            email=data["email"],
            credentials_file=data.get(
                "credentials_file",
                f"/opt/claudius/.google_workspace_mcp/credentials/{data['email']}.json"
            ),
            vat_folder_id=data["vat_folder_id"],
            state_prefix=data.get("state_prefix", account_name)
        )


def list_accounts() -> list:
    """List all configured account names."""
    if not CONFIG_DIR.exists():
        return []
    return [f.stem for f in CONFIG_DIR.glob("*.json")]
