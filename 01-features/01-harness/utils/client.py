"""boto3 client helpers for AgentCore Harness tutorials."""

import os

import boto3
from botocore.config import Config

# Honour both region variables the AWS CLI/SDKs accept, and fall back to None so
# boto3 resolves the region itself (profile, ~/.aws/config, IMDS). Reading only
# AWS_DEFAULT_REGION meant that a shell exporting just AWS_REGION — or exporting
# AWS_DEFAULT_REGION as an empty string — passed "" straight through to the
# client, which fails with a confusing `Invalid endpoint:
# https://bedrock-agentcore-control..amazonaws.com` instead of resolving the
# configured region (or raising a clear NoRegionError).
REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or None

# Endpoint overrides (for non-production stages)
_CP_ENDPOINT = os.environ.get("BEDROCK_AGENTCORE_CP_ENDPOINT")
_DP_ENDPOINT = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")


def _make_session():
    """Create a boto3 session."""
    return boto3.Session(region_name=REGION)


# botocore's default read_timeout is 60s, which is shorter than a single agent turn:
# InvokeHarness streams while the agent thinks, calls tools and runs commands in its
# microVM, so a research-style prompt easily runs several minutes. On the default the
# socket is torn down mid-stream with a bare ReadTimeoutError — the sample looks broken
# even though the agent is working fine. Data-plane calls therefore get a generous
# read timeout by default; callers can still pass their own Config to override it.
DP_READ_TIMEOUT = 900
_DEFAULT_DP_CONFIG = Config(read_timeout=DP_READ_TIMEOUT)


def get_agentcore_client(config=None):
    """Return a boto3 client for the Harness data plane (invoke, ExecuteCommand).

    Args:
        config: Optional botocore.config.Config overriding the defaults, which
            already allow for long-running agent turns (see DP_READ_TIMEOUT).
    """
    kwargs = {"config": config if config is not None else _DEFAULT_DP_CONFIG}
    if _DP_ENDPOINT:
        kwargs["endpoint_url"] = _DP_ENDPOINT
    return _make_session().client("bedrock-agentcore", **kwargs)


def get_agentcore_control_client():
    """Return a boto3 client for the Harness control plane (create, get, update, delete)."""
    kwargs = {}
    if _CP_ENDPOINT:
        kwargs["endpoint_url"] = _CP_ENDPOINT
    return _make_session().client("bedrock-agentcore-control", **kwargs)
