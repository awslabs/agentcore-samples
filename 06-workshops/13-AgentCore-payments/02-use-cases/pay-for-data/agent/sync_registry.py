#!/usr/bin/env python3
"""Fetch the live Heurist catalog and refresh the local cache.

Run this on the host machine (NOT inside the container) before each
`agentcore deploy` so the catalog cache bundled into the image is fresh.

Usage (from pay-for-data/):
    python agent/sync_registry.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make sibling modules importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))

from catalog import fetch_live_catalog, get_tools_for_agents  # noqa: E402
from config import LIVE_CATALOG_CACHE_PATH, get_config  # noqa: E402


def main() -> None:
    cfg = get_config()
    catalog = fetch_live_catalog()
    selected_tools = get_tools_for_agents(cfg.heurist_tool_agent_ids, refresh=False)
    print(f"Saved live catalog cache to {LIVE_CATALOG_CACHE_PATH}")
    print(f"Catalog url:    {cfg.heurist_catalog_url}")
    print(f"Catalog agents: {catalog.get('count', '?')}")
    print(f"Selected agents: {', '.join(cfg.heurist_tool_agent_ids)}")
    print(f"Selected tools: {len(selected_tools)}")


if __name__ == "__main__":
    main()
