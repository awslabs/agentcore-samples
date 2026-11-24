#!/usr/bin/env python3
"""Remove only journaled optional identity deliveries, never regional resources.

CFN-owned Runtime/Gateway/Memory deliveries are removed with the stack.
"""

import sys

from lifecycle import main

if __name__ == "__main__":
    sys.argv.insert(1, "disable-identity-tracing")
    main()
