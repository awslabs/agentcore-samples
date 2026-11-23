# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`/api/config` — what the console needs to know about the deployment."""

from typing import Any

from fastapi import APIRouter

import aws

router = APIRouter()


@router.get("/config")
def get_config() -> dict[str, Any]:
    """Report which services are wired up, for the UI's status banner."""
    # The inference URL sits under the same gateway host as the MCP endpoint,
    # under /inference/v1. Compute it here rather than plumb a second output
    # from Terraform so the two URLs stay in lockstep.
    inference_url = ""
    if aws.GATEWAY_URL:
        base = aws.GATEWAY_URL.rstrip("/")
        if base.endswith("/mcp"):
            base = base[: -len("/mcp")]
        inference_url = f"{base}/inference/v1"

    return {
        "region": aws.REGION,
        "runtime_arn": aws.RUNTIME_ARN,
        "registry_id": aws.REGISTRY_ID,
        "gateway_id": aws.GATEWAY_ID,
        "gateway_url": aws.GATEWAY_URL,
        "gateway_inference_url": inference_url,
        "memory_id": aws.MEMORY_ID,
        "user_pool_id": aws.USER_POOL_ID,
        "guardrail_id": aws.GUARDRAIL_ID,
        "guardrail_version": aws.GUARDRAIL_VERSION,
        "inference_route": aws.INFERENCE_ROUTE,
        "policy_engine_id": aws.POLICY_ENGINE_ID,
        "policy_mode": aws.POLICY_MODE,
        "harness_id": aws.HARNESS_ID,
        "configured": all([aws.RUNTIME_ARN, aws.REGISTRY_ID, aws.GATEWAY_ID, aws.MEMORY_ID]),
        "demo_customers": [
            {
                "id": "CUST001",
                "name": "Acme Corporation Ltd",
                "industry": "Manufacturing",
                "expected": "APPROVE",
                "note": "Clean profile: A rating, no sanctions or PEP exposure",
            },
            {
                "id": "CUST002",
                "name": "TechStart Innovations Inc",
                "industry": "Technology",
                "expected": "CONDITIONAL",
                "note": "Thin financials: BB rating, net loss, elevated leverage",
            },
            {
                "id": "CUST003",
                "name": "Global Trading Partners LLC",
                "industry": "Import/Export",
                "expected": "ESCALATE",
                "note": "OFAC partial match, flagged PEP, structuring pattern",
            },
        ],
    }
