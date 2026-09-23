"""
Tear down AgentCore Identity resources created by 01_create_providers.py.

Deletes both credential providers and the workload identity. Safe to run even
if some resources are already gone (e.g., you deleted the credential providers
manually in the AWS console).

Run:
    python teardown.py
"""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def _delete_provider(client, name: str) -> None:
    if not name:
        return
    try:
        client.delete_oauth2_credential_provider(name=name)
        print(f"✓ Deleted credential provider: {name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            print(f"• Credential provider already gone: {name}")
        else:
            print(f"✗ Failed to delete provider {name}: {e}", file=sys.stderr)


def _delete_workload(client, name: str) -> None:
    if not name:
        return
    try:
        client.delete_workload_identity(name=name)
        print(f"✓ Deleted workload identity: {name}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            print(f"• Workload identity already gone: {name}")
        else:
            print(f"✗ Failed to delete workload: {e}", file=sys.stderr)


def main() -> None:
    load_dotenv()
    region = os.environ.get("AWS_REGION", "us-west-2")

    workload_name = os.environ.get("WORKLOAD_NAME", "")
    client_provider_name = os.environ.get("CLIENT_PROVIDER_NAME", "")
    actor_provider_name = os.environ.get("ACTOR_PROVIDER_NAME", "")

    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    # Credential providers must be deleted before the workload identity.
    _delete_provider(ac, client_provider_name)
    _delete_provider(ac, actor_provider_name)
    _delete_workload(ac, workload_name)

    print("\n✓ Teardown complete. Run `python 01_create_providers.py` to recreate.")
    print("  Note: this does NOT delete your Okta app registrations or authorization")
    print("  server — those are still valid and you can reuse them.")


if __name__ == "__main__":
    main()
