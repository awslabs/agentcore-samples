#!/usr/bin/env python3
"""Run only offline tests, with fake credentials and network/SDK calls blocked.

From the sample root: python3 tests/run_offline.py
Requires the agent's Python dependencies. Never invokes scripts/test_*.py.
"""

import os
import socket
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def forbidden(*args, **kwargs):
    raise AssertionError("Offline test attempted network access or an unstubbed AWS API call")


def main():
    # Override (do not read) credential configuration before any SDK imports.
    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "AWS_ACCESS_KEY_ID": "offline-test-key",
                    "AWS_SECRET_ACCESS_KEY": "offline-test-secret",
                    "AWS_SESSION_TOKEN": "offline-test-token",
                    "AWS_REGION": "us-west-2",
                    "AWS_DEFAULT_REGION": "us-west-2",
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "AWS_CONFIG_FILE": os.devnull,
                    "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
                    "OTEL_SDK_DISABLED": "true",
                    "AUTO_APPROVE_THRESHOLD": "80",
                    "AGENTCORE_GATEWAY_URL": "https://gateway.example.invalid/mcp",
                    "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER": "Claimsoffline01_dev_cognito-gateway-m2m",
                    "AGENTCORE_GATEWAY_OAUTH_SCOPES": "agentcore/invoke",
                    "AGENT_MODEL_ID": "global.anthropic.claude-sonnet-4-6",
                    "FAST_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "MEMORY_CLAIMSAGENTMEMORY_ID": "",
                    "AGENTCORE_MEMORY_ID": "",
                },
            )
        )
        for key in (
            "AWS_PROFILE",
            "AWS_DEFAULT_PROFILE",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
            "AWS_ROLE_ARN",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        ):
            os.environ.pop(key, None)
        for name in ("connect", "connect_ex"):
            stack.enter_context(patch.object(socket.socket, name, forbidden))
        stack.enter_context(patch.object(socket, "getaddrinfo", forbidden))
        from botocore.client import BaseClient

        stack.enter_context(patch.object(BaseClient, "_make_api_call", forbidden))
        tests = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        # A missing SDK must not masquerade as completed migration coverage.
        if result.skipped:
            print("Skipped tests are not a complete offline pass", file=sys.stderr)
        return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
