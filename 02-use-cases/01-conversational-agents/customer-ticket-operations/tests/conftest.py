"""Fail tests on *attempted* AWS SDK egress, including errors swallowed by telemetry.

Installed before test modules import boto3/Runtime entrypoints. Browser loopback fixtures
use their own explicit route guard; this guard covers botocore transport, not local HTTP.
"""

from __future__ import annotations

import os

# Avoid credential-chain metadata, profiles and any ambient AWS identity before module import.
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
os.environ.pop("AWS_PROFILE", None)
os.environ["AWS_ACCESS_KEY_ID"] = "offline-only"
os.environ["AWS_SECRET_ACCESS_KEY"] = "offline-only"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

import botocore.client  # noqa: E402
import botocore.endpoint  # noqa: E402
import pytest  # noqa: E402

ATTEMPTED_AWS: list[str] = []
ATTEMPTED_TRANSPORT: list[str] = []
TOTAL_AWS_ATTEMPTS = 0
TOTAL_TRANSPORT_ATTEMPTS = 0


def forbid_sdk_egress(self, operation_name, api_params):
    global TOTAL_AWS_ATTEMPTS
    service = self.meta.service_model.service_name
    TOTAL_AWS_ATTEMPTS += 1
    ATTEMPTED_AWS.append(f"{service}.{operation_name}")
    raise AssertionError("AWS SDK egress attempted in offline tests")


def forbid_transport(self, request):
    global TOTAL_TRANSPORT_ATTEMPTS
    TOTAL_TRANSPORT_ATTEMPTS += 1
    ATTEMPTED_TRANSPORT.append("botocore.Endpoint._send")
    raise AssertionError("AWS transport attempted in offline tests")


# Instrument both SDK and actual transport before test-module imports. A telemetry writer
# swallowing one exception still fails teardown, and no transport may reach the network.
botocore.client.BaseClient._make_api_call = forbid_sdk_egress
botocore.endpoint.Endpoint._send = forbid_transport


@pytest.fixture(autouse=True)
def no_attempted_aws_egress():
    assert not ATTEMPTED_AWS and not ATTEMPTED_TRANSPORT, (
        "AWS egress attempted before test fixture admission"
    )
    yield
    attempts = list(ATTEMPTED_AWS)
    transport = list(ATTEMPTED_TRANSPORT)
    ATTEMPTED_AWS.clear()
    ATTEMPTED_TRANSPORT.clear()
    assert not attempts and not transport, (
        f"AWS egress attempted (possibly swallowed): SDK {attempts}; transport {transport}"
    )


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(
        f"Offline AWS SDK egress guard: {TOTAL_AWS_ATTEMPTS} attempted operations; "
        f"outgoing AWS transport: {TOTAL_TRANSPORT_ATTEMPTS} requests"
    )
