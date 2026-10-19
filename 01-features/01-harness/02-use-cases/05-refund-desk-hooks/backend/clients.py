"""boto3 client helpers."""

import os

import boto3
from botocore.config import Config

REGION = (
    os.environ.get("AWS_DEFAULT_REGION")
    or os.environ.get("AWS_REGION")
    or boto3.session.Session().region_name
    or "us-east-1"
)

# Synchronous Lambda hooks can hold the InvokeHarness stream idle for up to their
# timeoutSeconds, on top of normal model latency. Keep the read timeout well above
# the longest hook timeout configured in hooks.py.
_DP_CONFIG = Config(read_timeout=900, tcp_keepalive=True)


def client(service: str, **kwargs):
    return boto3.client(service, region_name=REGION, **kwargs)


def agentcore_client():
    """Harness data plane (InvokeHarness)."""
    return client("bedrock-agentcore", config=_DP_CONFIG)


def agentcore_control_client():
    """Harness control plane (CreateHarness, UpdateHarness, ...)."""
    return client("bedrock-agentcore-control")
