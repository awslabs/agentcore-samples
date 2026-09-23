import os
import boto3

REGION = os.environ.get("AWS_DEFAULT_REGION")

# Endpoint overrides
_CP_ENDPOINT = os.environ.get("BEDROCK_AGENTCORE_CP_ENDPOINT")
_DP_ENDPOINT = os.environ.get("BEDROCK_AGENTCORE_DP_ENDPOINT")


def _make_session():
    """Create a boto3 session"""
    session = boto3.Session(region_name=REGION)
    return session


def get_agentcore_client():
    """Return a boto3 client for the Harness data plane (invoke, ExecuteCommand)."""
    kwargs = {}
    if _DP_ENDPOINT:
        kwargs["endpoint_url"] = _DP_ENDPOINT
    return _make_session().client("bedrock-agentcore", **kwargs)


def get_agentcore_control_client():
    """Return a boto3 client for the Harness control plane (create, get, update, delete)."""
    kwargs = {}
    if _CP_ENDPOINT:
        kwargs["endpoint_url"] = _CP_ENDPOINT
    return _make_session().client("bedrock-agentcore-control", **kwargs)
