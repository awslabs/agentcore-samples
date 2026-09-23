"""Re-attach policy engine to gateway (for before/after demo).

Finds the policy engine by name via list_policy_engines API —
no config file needed. The engine must already exist (created by setup_policy.py).

This is the "turn on" half of the before/after demo:
  1. python policy/detach_policy.py   → no access control
  2. python policy/attach_policy.py   → Cedar policies enforced
"""

import os
import time
from pathlib import Path

import boto3
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ENGINE_NAME = "HealthcarePolicyEngine"


def main():
    profile = os.getenv("awscred_profile_name")
    region = os.getenv("aws_default_region", "us-east-1")
    session = boto3.Session(profile_name=profile, region_name=region) if profile else boto3.Session(region_name=region)
    client = session.client("bedrock-agentcore-control", region_name=region)

    gateway_id = os.getenv("gateway_id")

    # Find engine ARN by name
    engines = client.list_policy_engines().get("policyEngines", [])
    engine = next((e for e in engines if e["name"] == ENGINE_NAME), None)
    if not engine:
        print(f"❌ Policy engine '{ENGINE_NAME}' not found. Run setup_policy.py first.")
        return

    engine_arn = engine["policyEngineArn"]

    print(f"🔒 Attaching policy engine to gateway {gateway_id}...")

    gw = client.get_gateway(gatewayIdentifier=gateway_id)

    # Check if already attached
    pe = gw.get("policyEngineConfiguration", {})
    if pe and pe.get("arn") == engine_arn:
        print(f"   Already attached (mode: {pe.get('mode')})")
        return

    client.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gw["name"],
        roleArn=gw["roleArn"],
        protocolType=gw["protocolType"],
        authorizerType=gw["authorizerType"],
        authorizerConfiguration=gw.get("authorizerConfiguration", {}),
        policyEngineConfiguration={
            "arn": engine_arn,
            # ENFORCE = block denied requests; LOG_ONLY = log but don't block
            "mode": "ENFORCE",
        },
    )

    print("   ⏳ Waiting 10s for attachment to propagate...")
    time.sleep(10)
    print("   ✅ Policy engine attached — Cedar policies are now enforced")


if __name__ == "__main__":
    main()
