#!/usr/bin/env python3
"""
Execution roles for the three AgentCore Runtimes.

Creates or deletes the IAM roles the runtimes run as:

    lakehouse-mcp    AgentCoreRuntimeRole-lakehouse-mcp    claims MCP server  (4a-mcp-lakehouse-server)
    opensearch-mcp   AgentCoreRuntimeRole-opensearch-mcp   notes MCP server   (4b-mcp-opensearch-server)
    lakehouse-agent  AgentCoreRuntimeRole-lakehouse-agent  Strands agent      (6-lakehouse-agent)

The role names are fixed on purpose. The runtime deployment imports these roles
by name instead of generating its own, and the OpenSearch collection's
data-access policy grants the opensearch-mcp role by name
(5b-obo-gateway-setup/01_deploy_opensearch_collection.py).

Inputs read from SSM Parameter Store (each role reads only its own):

    lakehouse-mcp    /app/lakehouse-agent/s3-bucket-name
                     written by 3-s3tables-setup/setup_s3tables.py
    opensearch-mcp   /app/lakehouse-agent/opensearch-collection-arn
                     written by 5b-obo-gateway-setup/01_deploy_opensearch_collection.py
    lakehouse-agent  (none)

Create is idempotent: an existing role keeps its other attachments, and this
script re-asserts only its own trust policy and its own inline policy
(AgentCoreRuntimePermissions). Delete is idempotent: a missing role is reported
and skipped.

Usage:
    python setup_runtime_roles.py create                          # all three roles
    python setup_runtime_roles.py create --role lakehouse-agent   # one role (repeatable)
    python setup_runtime_roles.py delete                          # all three roles
    python setup_runtime_roles.py delete --role opensearch-mcp

Arguments:
    --account-id: (Optional) AWS Account ID used in policy ARNs. If not provided, uses current account.
    --role:       (Optional, repeatable) Limit the action to the named role(s). Default: all three.
"""

import argparse
import json
import sys
from typing import Any

import boto3

SSM_PREFIX = "/app/lakehouse-agent/"
INLINE_POLICY_NAME = "AgentCoreRuntimePermissions"

# Trust policy shared by all three roles: only the AgentCore service may assume them.
TRUST_POLICY: dict[str, Any] = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

ECR_PULL_STATEMENT: dict[str, Any] = {
    "Effect": "Allow",
    "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
    ],
    "Resource": "*",
}


def ssm_read_statement(region: str, account_id: str) -> dict[str, Any]:
    return {
        "Effect": "Allow",
        "Action": ["ssm:GetParameter", "ssm:GetParameters"],
        "Resource": f"arn:aws:ssm:{region}:{account_id}:parameter/app/lakehouse-agent/*",
    }


def lakehouse_mcp_policy(region: str, account_id: str, inputs: dict[str, str]) -> dict[str, Any]:
    """Claims MCP server: Athena, Glue, the data bucket, Lake Formation data access, Bedrock."""
    bucket = inputs["s3-bucket-name"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:GetWorkGroup",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetPartition",
                    "glue:GetPartitions",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:PutObject",
                    "s3:GetBucketLocation",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket}/*",
                    f"arn:aws:s3:::{bucket}",
                ],
            },
            {"Effect": "Allow", "Action": ["lakeformation:GetDataAccess"], "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "aws-marketplace:ViewSubscriptions",
                    "aws-marketplace:Subscribe",
                    "aws-marketplace:Unsubscribe",
                ],
                "Resource": "*",
            },
            {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
            ECR_PULL_STATEMENT,
            ssm_read_statement(region, account_id),
        ],
    }


def opensearch_mcp_policy(region: str, account_id: str, inputs: dict[str, str]) -> dict[str, Any]:
    """Notes MCP server: data-plane access to the one AOSS collection, nothing else.

    aoss:APIAccessAll is broad on purpose; read-only is enforced by the
    collection's data-access policy (5b-obo-gateway-setup). No Athena, Glue, S3,
    Lake Formation, Bedrock or Marketplace permissions: this runtime only talks
    to OpenSearch Serverless for query-time row-level security.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["aoss:APIAccessAll"], "Resource": inputs["opensearch-collection-arn"]},
            {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], "Resource": "*"},
            ECR_PULL_STATEMENT,
            ssm_read_statement(region, account_id),
        ],
    }


def lakehouse_agent_policy(region: str, account_id: str, inputs: dict[str, str]) -> dict[str, Any]:
    """Agent: Bedrock model invocation and gateway invocation."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "aws-marketplace:ViewSubscriptions",
                    "aws-marketplace:Subscribe",
                    "aws-marketplace:Unsubscribe",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:GetGateway",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
            },
            {"Effect": "Allow", "Action": ["logs:*", "xray:*"], "Resource": ["*"]},
            ECR_PULL_STATEMENT,
            ssm_read_statement(region, account_id),
        ],
    }


# Role key -> name, description, Purpose tag, SSM inputs, permissions-policy builder.
ROLES: dict[str, dict[str, Any]] = {
    "lakehouse-mcp": {
        "name": "AgentCoreRuntimeRole-lakehouse-mcp",
        "description": "AgentCore Runtime execution role for lakehouse data MCP server",
        "purpose": "lakehouse-mcp-role",
        "inputs": {"s3-bucket-name": "3-s3tables-setup/setup_s3tables.py"},
        "policy": lakehouse_mcp_policy,
    },
    "opensearch-mcp": {
        "name": "AgentCoreRuntimeRole-opensearch-mcp",
        "description": "AgentCore Runtime execution role for OpenSearch claim-notes MCP server",
        "purpose": "opensearch-mcp-role",
        "inputs": {"opensearch-collection-arn": "5b-obo-gateway-setup/01_deploy_opensearch_collection.py"},
        "policy": opensearch_mcp_policy,
    },
    "lakehouse-agent": {
        "name": "AgentCoreRuntimeRole-lakehouse-agent",
        "description": "AgentCore Runtime execution role for lakehouse data agent",
        "purpose": "agent-role",
        "inputs": {},
        "policy": lakehouse_agent_policy,
    },
}


def role_tags(purpose: str) -> list[dict[str, str]]:
    return [
        {"Key": "Application", "Value": "lakehouse-agent"},
        {"Key": "Purpose", "Value": purpose},
    ]


class RuntimeRoles:
    def __init__(self, account_id: str | None = None, iam_client=None, ssm_client=None):
        session = boto3.Session()
        self.region = session.region_name
        if not self.region:
            print("❌ No AWS region configured (set AWS_DEFAULT_REGION or a profile region).")
            sys.exit(1)
        self.account_id = account_id or boto3.client("sts").get_caller_identity()["Account"]
        self.iam = iam_client or boto3.client("iam")
        self.ssm = ssm_client or boto3.client("ssm", region_name=self.region)

    def read_inputs(self, keys: list[str]) -> dict[str, str]:
        """Read every SSM input the selected roles need; exit naming any that are missing."""
        values, missing = {}, []
        for key in keys:
            role_key = next(k for k, spec in ROLES.items() if key in spec["inputs"])
            try:
                values[key] = self.ssm.get_parameter(Name=f"{SSM_PREFIX}{key}")["Parameter"]["Value"]
            except self.ssm.exceptions.ParameterNotFound:
                missing.append(f"{SSM_PREFIX}{key} (written by {ROLES[role_key]['inputs'][key]})")
        if missing:
            print("❌ SSM parameter(s) not found:")
            for item in missing:
                print(f"   - {item}")
            print("   Run the step that writes each one first, or limit the roles with --role.")
            sys.exit(1)
        return values

    def create(self, role_keys: list[str]) -> dict[str, str]:
        print("\n🚀 Creating AgentCore Runtime execution roles")
        print(f"   Region: {self.region}")
        print(f"   Account ID: {self.account_id}")
        needed = [key for rk in role_keys for key in ROLES[rk]["inputs"]]
        inputs = self.read_inputs(needed)

        arns = {}
        for rk in role_keys:
            spec = ROLES[rk]
            permissions_policy = spec["policy"](self.region, self.account_id, inputs)
            arns[spec["name"]] = self.create_one(spec, permissions_policy)

        print("\n✨ Runtime execution roles ready:")
        for name, arn in arns.items():
            print(f"   - {name}")
            print(f"     ARN: {arn}")
        return arns

    def create_one(self, spec: dict[str, Any], permissions_policy: dict[str, Any]) -> str:
        role_name = spec["name"]
        try:
            print(f"\nCreating IAM role: {role_name}")
            response = self.iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
                Description=spec["description"],
                Tags=role_tags(spec["purpose"]),
            )
            role_arn = response["Role"]["Arn"]
            self.iam.put_role_policy(
                RoleName=role_name,
                PolicyName=INLINE_POLICY_NAME,
                PolicyDocument=json.dumps(permissions_policy),
            )
            print(f"✅ Created IAM role: {role_arn}")
            return role_arn

        except self.iam.exceptions.EntityAlreadyExistsException:
            # Idempotent in-place update: re-assert this script's trust policy and
            # its own inline policy, and leave every other attachment untouched
            # (other inline policies, managed policies, instance profiles).
            # No detach-all, no delete-and-recreate.
            print(f"ℹ️  Role {role_name} already exists — updating in place (preserving out-of-band attachments)")
            role_arn = self.iam.get_role(RoleName=role_name)["Role"]["Arn"]
            self.iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(TRUST_POLICY))
            self.iam.put_role_policy(
                RoleName=role_name,
                PolicyName=INLINE_POLICY_NAME,
                PolicyDocument=json.dumps(permissions_policy),
            )
            print(f"✅ Updated existing IAM role in place: {role_arn}")
            return role_arn

    def delete(self, role_keys: list[str]) -> bool:
        """Delete the selected roles. Returns False if any deletion failed."""
        print("\n🗑️  Deleting AgentCore Runtime execution roles")
        ok = True
        for rk in role_keys:
            ok = self.delete_one(ROLES[rk]["name"]) and ok
        return ok

    def delete_one(self, role_name: str) -> bool:
        try:
            self.iam.get_role(RoleName=role_name)
        except self.iam.exceptions.NoSuchEntityException:
            print(f"   ⏭️  Role not found: {role_name}")
            return True
        try:
            for p in self.iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                self.iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            for p in self.iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                self.iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
            self.iam.delete_role(RoleName=role_name)
            print(f"   ✅ Deleted role: {role_name}")
            return True
        except Exception as e:
            print(f"   ❌ Error deleting {role_name}: {e}")
            return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create or delete the AgentCore Runtime execution roles")
    parser.add_argument(
        "--account-id",
        required=False,
        default=None,
        help="AWS Account ID used in policy ARNs (optional, defaults to current account)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("create", "Create the roles (or update them in place)"),
        ("delete", "Delete the roles"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        sub.add_argument(
            "--role",
            action="append",
            choices=list(ROLES),
            help="Limit to this role; repeat for several (default: all three)",
        )
    args = parser.parse_args(argv)

    # Keep a stable order regardless of how --role was given.
    role_keys = [rk for rk in ROLES if not args.role or rk in args.role]
    roles = RuntimeRoles(account_id=args.account_id)
    if args.command == "create":
        roles.create(role_keys)
        return 0
    return 0 if roles.delete(role_keys) else 1


if __name__ == "__main__":
    sys.exit(main())
