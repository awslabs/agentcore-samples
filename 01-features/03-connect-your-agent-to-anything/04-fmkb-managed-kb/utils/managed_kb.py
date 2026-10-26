"""Optional helper to verify a Managed KB is active before wiring up the gateway.

This file does NOT create a KB — that belongs to bedrock-samples /
managed-knowledge-bases / 01-getting-started. It just describes/validates
what the user passes in.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError


def assert_kb_active(kb_id: str, region: str) -> dict:
    agent = boto3.client("bedrock-agent", region_name=region)
    try:
        kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
    except ClientError as e:
        raise SystemExit(
            f"KB {kb_id} not found in {region}: {e}. Create one with "
            f"bedrock-samples / managed-knowledge-bases / 01-getting-started first."
        )
    if kb["status"] != "ACTIVE":
        raise SystemExit(f"KB {kb_id} is in status {kb['status']}, expected ACTIVE.")
    if kb["knowledgeBaseConfiguration"]["type"] != "MANAGED":
        raise SystemExit(
            f"KB {kb_id} is type {kb['knowledgeBaseConfiguration']['type']}; this sample requires type=MANAGED."
        )
    return kb
