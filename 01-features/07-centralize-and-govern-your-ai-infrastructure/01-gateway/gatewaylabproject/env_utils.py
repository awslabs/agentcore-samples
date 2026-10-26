"""Shared environment helpers for tutorial scripts.

Small, dependency-free utilities for loading a tutorial's local ``.env`` file
and reading required variables. Scripts pass their own ``.env`` path so the
helper stays explicit about which file it reads.

Usage from scripts:

    import os
    from env_utils import load_env, get_required_env

    load_env(os.path.join(os.path.dirname(__file__), ".env"))
    gateway_url = get_required_env("GATEWAY_URL")
"""

from __future__ import annotations

import os
import sys


def load_env(env_path: str | None = None) -> None:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ``.

    Uses ``setdefault`` so values already present in the environment win.
    Lines that are blank, commented (``#``), or lack ``=`` are ignored.
    If ``env_path`` is None or the file does not exist, this is a no-op.
    """
    if not env_path or not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def get_required_env(key: str) -> str:
    """Return ``os.environ[key]`` or exit(1) with a clear message."""
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val
