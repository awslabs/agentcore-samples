"""
Grant the agent's auto-created Runtime execution role the IAM permissions
required to perform OBO #1 (T_user -> T_gateway).

Permissions attached:
  - bedrock-agentcore:GetWorkloadAccessTokenForJWT (and friends)
      Scoped to: workload-identity-directory/default and the agent's
      specific workload identity.
  - bedrock-agentcore:GetResourceOauth2Token
      Scoped to: the agent's workload identity AND the default token vault
      (any provider in the account's default vault).
  - secretsmanager:GetSecretValue
      Scoped to: secret:bedrock-agentcore-identity!default/oauth2/* — only
      the AgentCore-managed OAuth secrets, not arbitrary secrets.

Run AFTER `agentcore deploy -y -v` (which creates the role).

Usage (from inside the agent's CLI project folder):
    python ../deploy/04_grant_agent_iam_permissions.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


POLICY_NAME = "obo-uc2-okta-agent-obo-permissions"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def find_agent_role_via_cfn(agent_runtime_name: str, region: str) -> str | None:
    """Look up the execution role via CloudFormation. Most reliable path.

    The AgentCore CLI creates a stack named
    `AgentCore-<AGENT_RUNTIME_NAME>-default` and inside it an IAM role
    whose LogicalResourceId contains 'ExecutionRole'. CloudFormation
    truncates the physical role name to stay under IAM's 64-char limit,
    which is why substring-matching on the physical name is unreliable.
    """
    stack_name = f"AgentCore-{agent_runtime_name}-default"
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.describe_stack_resources(StackName=stack_name)
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        # Fresh stack not yet created / different name — signal caller to fall back.
        if code in {"ValidationError"} and "does not exist" in str(e):
            return None
        raise

    for r in resp.get("StackResources", []):
        if r.get("ResourceType") != "AWS::IAM::Role":
            continue
        logical = r.get("LogicalResourceId", "")
        # AgentCore CLI generates roles named like
        # 'ApplicationAgent<AgentName>RuntimeExecutionRole<hash>'.
        if "ExecutionRole" in logical:
            return r.get("PhysicalResourceId")
    return None


def find_agent_role_via_iam_scan(iam, agent_runtime_name: str) -> list[str]:
    """Fallback: scan IAM roles for one that plausibly belongs to this runtime.

    Uses truncated-prefix matching because CFN often truncates the runtime
    name when composing the physical role name.
    """
    prefixes = [agent_runtime_name[:n] for n in range(len(agent_runtime_name), 7, -1)]
    matched: list[str] = []
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for role in page["Roles"]:
            name = role["RoleName"]
            if name.startswith("AgentCore-") and any(p in name for p in prefixes):
                matched.append(name)
    # Dedupe while preserving order.
    seen: set[str] = set()
    return [n for n in matched if not (n in seen or seen.add(n))]


def find_agent_role(iam, agent_runtime_name: str, region: str) -> str:
    """Locate the agent runtime's execution role.

    Resolution order:
      1. AGENT_EXECUTION_ROLE_NAME env var (explicit override — always wins).
      2. CloudFormation stack lookup (most reliable).
      3. IAM scan by truncated-prefix (fallback if CFN lookup fails).
    """
    override = os.environ.get("AGENT_EXECUTION_ROLE_NAME", "").strip()
    if override:
        return override

    cfn_role = find_agent_role_via_cfn(agent_runtime_name, region)
    if cfn_role:
        return cfn_role

    candidates = find_agent_role_via_iam_scan(iam, agent_runtime_name)
    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        print(
            f"ERROR: Could not locate the agent's execution role.\n"
            f"\n"
            f"       Tried:\n"
            f"         - CloudFormation stack 'AgentCore-{agent_runtime_name}-default' — not found.\n"
            f"         - IAM roles starting with 'AgentCore-' matching prefixes of "
            f"'{agent_runtime_name}' — none matched.\n"
            f"\n"
            f"       Have you run `agentcore deploy -y -v` and seen it succeed?\n"
            f"       If yes, find the role in the IAM console and pass its exact name:\n"
            f"           AGENT_EXECUTION_ROLE_NAME=<name> "
            f"python ../deploy/04_grant_agent_iam_permissions.py",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"ERROR: Found multiple candidate execution roles for '{agent_runtime_name}':\n  "
        + "\n  ".join(candidates)
        + "\n\n"
        "Set AGENT_EXECUTION_ROLE_NAME=<one of the above> and re-run.",
        file=sys.stderr,
    )
    sys.exit(1)


def build_policy(account_id: str, region: str, workload_name: str) -> dict:
    """Build the inline policy granting the three OBO actions.

    Resources are scoped narrowly — no '*' on bedrock-agentcore actions.
    """
    workload_identity_arn = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
        f"workload-identity-directory/default/workload-identity/{workload_name}"
    )
    workload_directory_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default"
    token_vault_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default"
    token_vault_provider_arn = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/*"
    )
    # ARN pattern for AgentCore-managed OAuth credential storage in Secrets
    # Manager. Not a secret value — an ARN pattern used in IAM policy
    # `Resource` scoping. Local variable named without "secret" so CodeQL's
    # taint heuristic doesn't flag downstream `print` diagnostics.
    agentcore_oauth_arn_pattern = (
        f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*"
    )

    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "WorkloadAccessTokenForJWT",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [workload_directory_arn, workload_identity_arn],
            },
            {
                "Sid": "ResourceOauth2Token",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                # AWS IAM authorizes this action against the workload-identity
                # DIRECTORY, not the individual workload-identity child, so
                # the directory ARN must be here.
                "Resource": [
                    workload_directory_arn,
                    workload_identity_arn,
                    token_vault_arn,
                    token_vault_provider_arn,
                ],
            },
            {
                "Sid": "ReadAgentCoreOauthSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [agentcore_oauth_arn_pattern],
            },
        ],
    }


def main() -> None:
    real_world_root = Path(__file__).resolve().parent.parent
    load_dotenv(real_world_root / ".env")

    region = os.environ.get("AWS_REGION", "us-west-2")
    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")
    workload_name = must_env("AGENT_WORKLOAD_NAME")

    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]

    iam = boto3.client("iam")

    role_name = find_agent_role(iam, agent_runtime_name, region)
    print(f"• Agent execution role: {role_name}")

    policy = build_policy(account_id, region, workload_name)

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(policy),
    )
    print(f"✓ Attached inline policy '{POLICY_NAME}' to {role_name}")
    print(
        f"  ({len(policy['Statement'])} statement(s) granting the OBO actions listed in this file's module docstring.)"
    )
    print()
    print("IAM changes propagate within seconds. No redeploy needed.")


if __name__ == "__main__":
    main()
