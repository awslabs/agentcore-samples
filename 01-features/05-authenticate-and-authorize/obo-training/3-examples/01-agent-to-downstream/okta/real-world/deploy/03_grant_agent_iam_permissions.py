"""
Grant the deployed agent's execution role the IAM permissions it needs for OBO.

The AgentCore CLI auto-creates an IAM execution role when you run `agentcore deploy`.
That role gets the baseline permissions for Runtime, but NOT the permissions needed
to call AgentCore Identity APIs or read the credential-provider secret. This script
adds them as an inline policy on the auto-created role.

Permissions added (scoped to this workload and to AgentCore-managed secrets):
  - bedrock-agentcore:GetWorkloadAccessToken{,ForJWT,ForUserId}
  - bedrock-agentcore:GetResourceOauth2Token
  - secretsmanager:GetSecretValue on `bedrock-agentcore-identity!default/oauth2/*`

Idempotent. Run whenever the agent is (re)deployed — the role name changes on
each deploy, but this script discovers it from CloudFormation.

Run from inside the CLI project folder (e.g., real-world/<AGENT_RUNTIME_NAME>/):
    python ../deploy/03_grant_agent_iam_permissions.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


INLINE_POLICY_NAME = "AgentCoreObo"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def _find_agent_execution_role(cfn, agent_runtime_name: str) -> str:
    """Find the IAM role the CLI created for the agent.

    The CLI's CDK stack name is `AgentCore-<AGENT_RUNTIME_NAME>-default`.
    The role we want has a name containing `ApplicationAgent` and the agent name.
    """
    stack_name = f"AgentCore-{agent_runtime_name}-default"
    try:
        resp = cfn.describe_stack_resources(StackName=stack_name)
    except ClientError as e:
        print(
            f"ERROR: Could not describe stack '{stack_name}': {e}\n"
            f"Has the agent been deployed? Run `agentcore deploy -y -v` first.",
            file=sys.stderr,
        )
        sys.exit(1)

    for resource in resp.get("StackResources", []):
        if resource.get("ResourceType") != "AWS::IAM::Role":
            continue
        logical_id = resource.get("LogicalResourceId", "")
        if "ApplicationAgent" in logical_id:
            return resource["PhysicalResourceId"]

    print(
        f"ERROR: Could not find an IAM role with 'ApplicationAgent' in its LogicalResourceId "
        f"inside stack {stack_name}.",
        file=sys.stderr,
    )
    for r in resp.get("StackResources", []):
        if r.get("ResourceType") == "AWS::IAM::Role":
            print(f"  - {r.get('LogicalResourceId')} → {r.get('PhysicalResourceId')}")
    sys.exit(1)


def _build_policy(region: str, account_id: str, workload_name: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowWorkloadAccessTokens",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{workload_name}",
                ],
            },
            {
                "Sid": "AllowResourceOauth2Token",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{workload_name}",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/*",
                ],
            },
            {
                "Sid": "AllowAgentCoreSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*",
            },
        ],
    }


def main() -> None:
    example_root = Path(__file__).resolve().parent.parent
    load_dotenv(example_root / ".env", override=True)

    region = os.environ.get("AWS_REGION", "us-west-2")
    workload_name = must_env("WORKLOAD_NAME")
    agent_runtime_name = must_env("AGENT_RUNTIME_NAME")

    sts = boto3.client("sts", region_name=region)
    cfn = boto3.client("cloudformation", region_name=region)
    iam = boto3.client("iam", region_name=region)

    account_id = sts.get_caller_identity()["Account"]
    print(f"Account:  {account_id}")
    print(f"Region:   {region}")
    print(f"Workload: {workload_name}")

    role_name = _find_agent_execution_role(cfn, agent_runtime_name)
    print(f"Role:     {role_name}")
    print()

    policy = _build_policy(region, account_id, workload_name)

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(policy),
    )
    print(f"✓ Attached inline policy '{INLINE_POLICY_NAME}' to role '{role_name}'.")
    print()
    print("Permissions now granted:")
    for stmt in policy["Statement"]:
        print(
            f"  - {stmt['Sid']}: {', '.join(stmt['Action'] if isinstance(stmt['Action'], list) else [stmt['Action']])}"
        )
    print()
    print("IAM changes take effect within seconds. Retry the frontend flow.")


if __name__ == "__main__":
    main()
