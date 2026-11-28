#!/usr/bin/env python3
"""Optional exact-ID Runtime/Gateway identity deliveries, with no account changes.

Normal Runtime/Gateway/Memory logs/traces are already CloudFormation-owned.
"""

import sys

from lifecycle import main

if __name__ == "__main__":
    sys.argv.insert(1, "identity-tracing")
    main()
