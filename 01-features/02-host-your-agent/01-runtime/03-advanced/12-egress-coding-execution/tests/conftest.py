"""Pytest path setup.

The sample is a multi-image project (not an installed package), so the source
directories are made importable here the same way the containers do it via
PYTHONPATH=/app. This lets the tests import `shared.ipc` and the broker sources.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Sample root (for `shared.ipc`) and the broker package root (for `src.config`).
for path in (ROOT, os.path.join(ROOT, "broker")):
    if path not in sys.path:
        sys.path.insert(0, path)
