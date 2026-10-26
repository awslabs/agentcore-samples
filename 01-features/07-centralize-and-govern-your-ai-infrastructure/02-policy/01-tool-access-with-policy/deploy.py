"""
Deploy all resources for the policy in Amazon Bedrock AgentCore demo.

Thin wrapper — delegates to the parent 02-policy/deploy.py which contains
the full implementation.

Usage:
    uv run python deploy.py [--region REGION]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy import main

if __name__ == "__main__":
    main()
