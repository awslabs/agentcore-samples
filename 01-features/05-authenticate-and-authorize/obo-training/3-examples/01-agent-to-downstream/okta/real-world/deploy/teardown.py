"""
Tear down AgentCore Identity resources created by the Okta real-world example.

For the Runtime itself, use the AgentCore CLI:
    agentcore remove agent <AGENT_RUNTIME_NAME>
    agentcore deploy

This script only removes the AgentCore Identity resources (workload identity
and credential provider) that were created by deploy/01_create_providers.py.

Does NOT delete the Okta app registrations — those stay configured and
reusable.

Run:
    python deploy/teardown.py
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def main() -> None:
    example_root = Path(__file__).resolve().parent.parent
    load_dotenv(example_root / ".env")

    region = os.environ.get("AWS_REGION", "us-west-2")
    workload_name = os.environ.get("WORKLOAD_NAME")
    actor_provider_name = os.environ.get("ACTOR_PROVIDER_NAME")
    agent_runtime_name = os.environ.get("AGENT_RUNTIME_NAME")

    ac_control = boto3.client("bedrock-agentcore-control", region_name=region)

    if actor_provider_name:
        try:
            ac_control.delete_oauth2_credential_provider(name=actor_provider_name)
            print(f"✓ Deleted credential provider: {actor_provider_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"• Credential provider already gone: {actor_provider_name}")
            else:
                print(f"✗ Failed to delete provider: {e}")

    if workload_name:
        try:
            ac_control.delete_workload_identity(name=workload_name)
            print(f"✓ Deleted workload identity: {workload_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"• Workload identity already gone: {workload_name}")
            else:
                print(f"✗ Failed to delete workload: {e}")

    print()
    print("✓ AgentCore Identity resources torn down.")
    print(f"\nTo remove the deployed Runtime '{agent_runtime_name}':")
    print(f"  cd {example_root}/{agent_runtime_name}")
    print(f"  agentcore remove agent --name {agent_runtime_name} -y")
    print("  agentcore deploy -y -v   # tears down the CloudFormation stack")
    print()
    print("Okta app registrations are left in place — delete them from the Okta")
    print("admin console if you no longer need them.")


if __name__ == "__main__":
    main()
