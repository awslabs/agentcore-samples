#!/usr/bin/env python3
"""Compatibility entrypoint for the strict, target-bound E2E suite.

The old best-effort log-tail demo was not proof of tool execution. It now uses
scripts/test_e2e.py, requiring explicit project/target/account/region. For live
stage visibility, the agent still emits structured Runtime SSE stage events.
"""

from test_e2e import main

if __name__ == "__main__":
    main()
