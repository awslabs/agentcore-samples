"""
Policy in Amazon Bedrock AgentCore Demo — NL2Cedar, Direct Cedar, and Fine-Grained ABAC.

Thin wrapper — delegates to the parent 02-policy/policy_demo.py which contains
the full implementation.

Usage:
    uv run python policy_demo.py [--section A|B|C]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from policy_demo import main

if __name__ == "__main__":
    main()
