# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Shared AWS client construction and request helpers.

Split out of main.py so each service router can import the plumbing it needs
without the routers depending on one another.
"""

import os

import boto3
from botocore.config import Config as BotoConfig
from fastapi import HTTPException

REGION = os.environ.get("AWS_REGION", "us-east-1")

# Resource identifiers, injected by Terraform (deployed) or scripts/write_env.py
# (local). Empty means "not configured"; `require` turns that into a 503 rather
# than an opaque boto3 error.
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
REGISTRY_ID = os.environ.get("REGISTRY_ID", "")
GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
KYC_TOOLS_LAMBDA = os.environ.get("KYC_TOOLS_LAMBDA", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")

# Model plane — Terraform sets these from the guardrail + inference target.
# INFERENCE_ROUTE is echoed to the console so the demo can label which path
# handled each assessment; the runtime itself reads the same variable to
# decide whether to call Bedrock directly or through the Gateway.
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "")
INFERENCE_ROUTE = os.environ.get("INFERENCE_ROUTE", "direct")

# Policy plane. POLICY_MODE is the one that matters for the demo: ENFORCE means
# the Gateway denies violating requests outright, LOG_ONLY means it evaluates
# and records them but lets them through.
POLICY_ENGINE_ID = os.environ.get("POLICY_ENGINE_ID", "")
POLICY_MODE = os.environ.get("POLICY_MODE", "")

# AgentCore Harness — the managed agent loop. Empty when the deployment opts out
# (enable_harness = false). Echoed to the console so it can show the harness as a
# declarative counterpart to the code-defined Runtime.
HARNESS_ID = os.environ.get("HARNESS_ID", "")

# A full two-specialist assessment takes 25-60s, and the Runtime holds the
# connection open for the whole run. boto3's 60s default read timeout cuts the
# stream off mid-assessment, so the runtime client gets a longer one. Retries are
# disabled because a retried invocation would re-run the whole assessment.
_RUNTIME_CONFIG = BotoConfig(
    read_timeout=600, connect_timeout=15, retries={"max_attempts": 0}
)


def client(service: str):
    """Build a boto3 client for `service` in the configured region."""
    return boto3.client(service, region_name=REGION)


def runtime_client():
    """Client for invoking the agent runtime, with a long read timeout."""
    return boto3.client("bedrock-agentcore", region_name=REGION, config=_RUNTIME_CONFIG)


def require(value: str, name: str) -> str:
    """Return `value`, or raise 503 if it was never configured.

    Raises:
        HTTPException: 503 when the identifier is missing, so the UI can show a
            setup hint instead of a generic failure.
    """
    if not value:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{name} is not configured. "
                "Run scripts/write_env.py after terraform apply."
            ),
        )
    return value
