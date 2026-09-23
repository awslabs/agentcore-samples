"""
Import helper for LangGraph implementation.
"""

import sys
from pathlib import Path


def setup_interactive_tools_import():
    """Add paths for interactive_tools and shared config."""
    current_file = Path(__file__).resolve()

    # Navigate to competitive-intelligence-agent folder
    agent_root = current_file.parent.parent.parent

    # Go up to repo root
    use_cases_dir = agent_root.parent
    repo_root = use_cases_dir.parent

    # Define paths
    tutorials_path = repo_root / "06-workshops"
    browser_tool_path = tutorials_path / "05-AgentCore-tools" / "02-Agent-Core-browser-tool"
    shared_path = agent_root / "shared"

    # Add to sys.path
    paths_to_add = [
        str(tutorials_path),
        str(browser_tool_path),
        str(shared_path),  # Only for config.py and cleanup_resources.py
    ]

    for path in paths_to_add:
        if path not in sys.path:
            sys.path.insert(0, path)

    return {
        "tutorials_path": str(tutorials_path),
        "browser_tool_path": str(browser_tool_path),
        "shared_path": str(shared_path),
    }
