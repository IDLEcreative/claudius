"""
State file management for Claudius.

Provides atomic load/save of JSON state files with
automatic directory resolution.
"""

import json
import os
import tempfile
from pathlib import Path

STATE_DIR = Path("/opt/claudius/state")


def state_path(filename: str) -> Path:
    """Resolve a state filename to its full path in the state directory."""
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / filename


def load_state(filename: str, default: dict = None) -> dict:
    """Load a JSON state file. Returns default if file doesn't exist."""
    if default is None:
        default = {}

    path = state_path(filename)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default.copy()


def save_state(filename: str, data: dict):
    """Save state to JSON file atomically (write to temp, then rename)."""
    path = state_path(filename)
    STATE_DIR.mkdir(exist_ok=True)

    # Write to temp file first, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
