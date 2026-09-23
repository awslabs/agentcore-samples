"""Detach policy engine from gateway (for before/after demo).

Removes Cedar policy evaluation from the gateway by calling update_gateway
without the policyEngineConfiguration field. After detaching, all tool calls
pass through without access control — any authenticated user can call any tool.

This is the "turn off" half of the before/after demo:
  1. python policy/detach_policy.py   → no access control
  2. python policy/attach_policy.py   → Cedar policies enforced
"""

import os
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def main():
    profile = os.getenv("awscred_profile_name")
    region = os.getenv("aws_default_region", "us-east-1")
    session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
    client = session.client("bedrock-agentcore-control", region_name=region)

    gateway_id = os.getenv("gateway_id")
    print(f"🔓 Detaching policy engine from gateway {gateway_id}...")

    gw = client.get_gateway(gatewayIdentifier=gateway_id)

    # Check if policy is attached
    pe = gw.get("policyEngineConfiguration", {})
    if not pe or not pe.get("arn"):
        print("   Already detached — no policy engine on gateway")
        return

    # update_gateway without policyEngineConfiguration detaches the engine.
    # All existing fields (name, roleArn, etc.) must be passed back.
    client.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gw["name"],
        roleArn=gw["roleArn"],
        protocolType=gw["protocolType"],
        authorizerType=gw["authorizerType"],
        authorizerConfiguration=gw.get("authorizerConfiguration", {}),
    )

    print("   ⏳ Waiting 10s for detachment to propagate...")
    time.sleep(10)
    print("   ✅ Policy engine detached — gateway has NO access control")


if __name__ == "__main__":
    main()
