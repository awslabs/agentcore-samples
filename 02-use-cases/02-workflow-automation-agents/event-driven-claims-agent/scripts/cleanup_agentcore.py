#!/usr/bin/env python3
"""Compatibility entrypoint; no orphan sweeps or state deletion."""

import sys

from lifecycle import main

if __name__ == "__main__":
    sys.argv.insert(1, "cleanup")
    main()
