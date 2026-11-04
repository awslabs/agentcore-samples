"""
Import helper for Strands implementation.
"""

import sys
from pathlib import Path


def setup_interactive_tools_import():
    """Add paths for interactive_tools and shared config."""
    current_file = Path(__file__).resolve()

    # Navigate to enterprise-web-intelligence-agent folder
    agent_root = current_file.parent.parent.parent

    # Define paths
    shared_path = agent_root / "shared"

    # Add to sys.path
    paths_to_add = [
        str(agent_root),  # For the vendored interactive_tools package
        str(shared_path),  # Only for config.py and cleanup_resources.py
    ]

    for path in paths_to_add:
        if path not in sys.path:
            sys.path.insert(0, path)

    return {
        "agent_root": str(agent_root),
        "shared_path": str(shared_path),
    }
