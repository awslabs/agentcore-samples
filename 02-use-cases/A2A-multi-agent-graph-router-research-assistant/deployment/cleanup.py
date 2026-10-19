"""Destroy all distributed A2A agents from AgentCore Runtime.

Tears down all 4 agents (3 sub-agents + supervisor) deployed by
deployment/deploy.py, including their auto-created IAM execution roles.

Uses the AWS API directly so it works regardless of whether
.bedrock_agentcore.yaml exists.

Usage:
    python -m deployment.cleanup
    python -m deployment.cleanup --region us-east-1
"""

from __future__ import annotations

import argparse
import sys

import boto3

_AGENT_NAMES = [
    "supervisor_a2a",
    "writer_a2a",
    "analysis_a2a",
    "research_a2a",
]


def _find_agents(region: str) -> dict[str, dict]:
    """Find all project agents by name in the given region.

    Args:
        region: AWS region to search.

    Returns:
        Dict mapping agent name to {"id": ..., "arn": ...}.
    """
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    found: dict[str, dict] = {}

    paginator_params: dict = {}
    while True:
        response = client.list_agent_runtimes(**paginator_params)
        for runtime in response.get("agentRuntimes", []):
            name = runtime.get("agentRuntimeName")
            if name in _AGENT_NAMES:
                found[name] = {
                    "id": runtime["agentRuntimeId"],
                    "arn": runtime["agentRuntimeArn"],
                }
        next_token = response.get("nextToken")
        if not next_token:
            break
        paginator_params["nextToken"] = next_token

    return found


def _delete_runtime(agent_id: str, region: str) -> bool:
    """Delete an AgentCore runtime.

    Args:
        agent_id: The agent runtime ID.
        region: AWS region.

    Returns:
        True if deleted successfully, False otherwise.
    """
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        client.delete_agent_runtime(agentRuntimeId=agent_id)
        return True
    except client.exceptions.ResourceNotFoundException:
        return False


def _delete_iam_role(role_name: str) -> bool:
    """Delete an IAM role and all its inline policies.

    Args:
        role_name: The IAM role name.

    Returns:
        True if deleted, False if not found.
    """
    iam = boto3.client("iam")

    try:
        # Delete all inline policies first (required before role deletion)
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # Detach any managed policies
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

        iam.delete_role(RoleName=role_name)
        return True
    except iam.exceptions.NoSuchEntityException:
        return False


def _find_execution_roles() -> list[str]:
    """Find all auto-created AgentCore execution roles.

    Returns:
        List of role names matching the auto-created pattern.
    """
    iam = boto3.client("iam")
    roles = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page.get("Roles", []):
            name = role["RoleName"]
            if name.startswith("AmazonBedrockAgentCoreSDKRuntime-"):
                roles.append(name)
    return roles


def main() -> None:
    """Destroy all distributed agents and their IAM roles."""
    parser = argparse.ArgumentParser(description="Destroy distributed A2A agents from AgentCore")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    args = parser.parse_args()

    print("=" * 70)
    print("Cleaning up distributed A2A router-supervisor from AgentCore")
    print("=" * 70)

    # Step 1: Find and delete agent runtimes
    print("\n--- Finding deployed agents ---")
    agents = _find_agents(args.region)

    if not agents:
        print("  No project agents found in AgentCore")
    else:
        for name in _AGENT_NAMES:
            if name not in agents:
                print(f"  {name}: not found (skipping)")
                continue
            agent_id = agents[name]["id"]
            print(f"  Destroying {name} ({agent_id})...", end=" ")
            if _delete_runtime(agent_id, args.region):
                print("done")
            else:
                print("not found", file=sys.stderr)

    # Step 2: Find and delete auto-created IAM execution roles
    print("\n--- Cleaning up IAM execution roles ---")
    roles = _find_execution_roles()

    if not roles:
        print("  No auto-created execution roles found")
    else:
        for role_name in roles:
            print(f"  Deleting {role_name}...", end=" ")
            if _delete_iam_role(role_name):
                print("done")
            else:
                print("not found", file=sys.stderr)

    # Step 3: Clean up local config
    from pathlib import Path

    config_path = Path(".bedrock_agentcore.yaml")
    if config_path.exists():
        config_path.unlink()
        print("\n  Removed .bedrock_agentcore.yaml")

    print("\n" + "=" * 70)
    print("Cleanup complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
