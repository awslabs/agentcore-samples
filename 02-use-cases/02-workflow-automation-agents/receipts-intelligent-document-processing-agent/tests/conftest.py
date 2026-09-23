"""Pytest config: make the agent package and scripts importable without install.

Tests here are hands-free and need no AWS account (moto mocks DynamoDB) and no
AgentCore runtime (we test pure modules + mocked clients).
"""

import os
import sys

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# app/receiptsagent so `import parsing`, `import config` resolve as they do at runtime.
sys.path.insert(0, os.path.join(_ROOT, "app", "receiptsagent"))
# scripts so we can import the seed module.
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
