# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Simple file-based PKCE state store for OAuth flow.
Stores code_verifier by state parameter for retrieval after redirect.
"""

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Optional

# Store file location
STORE_DIR = Path(__file__).parent / ".oauth_state"
STORE_FILE = STORE_DIR / "pkce_store.json"
EXPIRY_SECONDS = 600  # 10 minutes


def _ensure_store_dir():
    """Ensure store directory exists with secure permissions (0o700)."""
    if not STORE_DIR.exists():
        STORE_DIR.mkdir(mode=0o700, exist_ok=True)
    elif sys.platform != "win32":
        # Check and fix permissions on non-Windows systems
        current_mode = STORE_DIR.stat().st_mode & 0o777
        if current_mode != 0o700:
            os.chmod(STORE_DIR, 0o700)


def _load_store() -> dict:
    """Load store from file."""
    _ensure_store_dir()
    if STORE_FILE.exists():
        try:
            with open(STORE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_store(store: dict):
    """Save store to file with secure permissions (0o600)."""
    _ensure_store_dir()
    with open(STORE_FILE, "w") as f:
        json.dump(store, f)
    # Set restrictive file permissions (owner read/write only) on non-Windows
    if sys.platform != "win32":
        os.chmod(STORE_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def _cleanup_expired(store: dict) -> dict:
    """Remove expired entries."""
    now = time.time()
    return {k: v for k, v in store.items() if v.get("expires_at", 0) > now}


def store_pkce_state(state: str, code_verifier: str):
    """
    Store PKCE code_verifier for a given state.

    Args:
        state: OAuth state parameter
        code_verifier: PKCE code verifier
    """
    store = _load_store()
    store = _cleanup_expired(store)
    store[state] = {
        "code_verifier": code_verifier,
        "expires_at": time.time() + EXPIRY_SECONDS,
    }
    _save_store(store)


def get_pkce_state(state: str) -> Optional[str]:
    """
    Get PKCE code_verifier for a given state.

    Args:
        state: OAuth state parameter

    Returns:
        code_verifier if found and not expired, None otherwise
    """
    store = _load_store()
    entry = store.get(state)
    if entry and entry.get("expires_at", 0) > time.time():
        return entry.get("code_verifier")
    return None


def remove_pkce_state(state: str):
    """
    Remove PKCE state after use.

    Args:
        state: OAuth state parameter to remove
    """
    store = _load_store()
    if state in store:
        del store[state]
        _save_store(store)
