"""
Clean up all AWS resources created by deploy.py.

Thin wrapper — delegates to the parent 02-policy/cleanup.py which contains
the full implementation.

Usage:
    uv run python cleanup.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cleanup import main

if __name__ == "__main__":
    main()
