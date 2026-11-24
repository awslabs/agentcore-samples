#!/usr/bin/env python3
"""
Execution roles for the three AgentCore Runtimes.

Creates or deletes the IAM roles the runtimes run as:

    lakehouse-mcp    AgentCoreRuntimeRole-lakehouse-mcp    claims MCP server  (4a-mcp-lakehouse-server)
    opensearch-mcp   AgentCoreRuntimeRole-opensearch-mcp   notes MCP server   (4b-mcp-opensearch-server)
    lakehouse-agent  AgentCoreRuntimeRole-lakehouse-agent  Strands agent      (6-lakehouse-agent)

Each role is created at its runtime's own notebook step, just before that
runtime deploys: `create --role lakehouse-mcp` in notebook 04, `--role
opensearch-mcp` in 05b, `--role lakehouse-agent` in 06. Notebook 09 runs
`delete --role <key>` for each, after that runtime's stack delete succeeds.

What each role's inline policy grants (all three also get ECR image pull,
CloudWatch Logs, X-Ray writes, and ssm:GetParameter(s) on
/app/lakehouse-agent/*):

    lakehouse-mcp    Athena query execution, Glue catalog reads, read/write on
                     the data bucket, lakeformation:GetDataAccess, Bedrock
                     InvokeModel (for text_to_sql), Marketplace subscription
                     actions for model access
    opensearch-mcp   aoss:APIAccessAll on the one OpenSearch Serverless collection
    lakehouse-agent  Bedrock InvokeModel, Marketplace subscription actions,
                     bedrock-agentcore:InvokeGateway / GetGateway on gateways in
                     this account and region

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

Each role this script creates gets an ownership record in SSM
(/app/lakehouse-agent/role-ids/<role name> = the role's RoleId). Re-running
create on a role with a matching record re-asserts its trust policy and its
inline policy (AgentCoreRuntimePermissions). A role of the same name without a
matching record is refused, and nothing is changed. Delete removes a role only
when its record matches and it carries nothing but this script's inline policy;
a role with no record is left in place with a warning, and a missing role is
reported and skipped.

Usage:
    python setup_runtime_roles.py create                          # all three roles
    python setup_runtime_roles.py create --role lakehouse-agent   # one role (repeatable)
    python setup_runtime_roles.py delete                          # all three roles
    python setup_runtime_roles.py delete --role opensearch-mcp

Arguments:
    --account-id: (Optional) AWS Account ID used in policy ARNs. If not provided, uses current account.
    --role:       (Optional, repeatable) Limit the action to the named role(s). Default: all three.

Exit code: 0 on success; 1 on any failure; 3 when delete's only problem was a
role left in place because this sample cannot prove it created it.
"""

import argparse
import json
import os
import sys
from typing import Any

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.iam_roles import FAILED, OK, RoleOwnershipError, combine, delete_owned_role, ensure_role

SSM_PREFIX = "/app/lakehouse-agent/"
INLINE_POLICY_NAME = "AgentCoreRuntimePermissions"

# Everything create puts on a runtime role. Delete refuses a role carrying anything else.
EXPECTED_INLINE = {INLINE_POLICY_NAME}
EXPECTED_MANAGED: set[str] = set()

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
    """Read access to this sample's SSM parameters under /app/lakehouse-agent/."""
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
    """Tags applied when a role is first created (an existing role's tags are not changed)."""
    return [
        {"Key": "Application", "Value": "lakehouse-agent"},
        {"Key": "Purpose", "Value": purpose},
    ]


class RuntimeRoles:
    """Create or delete the runtime execution roles listed in ROLES."""

    def __init__(self, account_id: str | None = None, iam_client=None, ssm_client=None):
        # IAM and SSM clients can be injected so the class is testable offline.
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
        """Create the selected roles, or update this sample's own; return {role name: role ARN}.

        All SSM inputs are read before any IAM call, so a missing input stops
        the run before a role is half-configured. A same-name role this sample
        did not create raises RoleOwnershipError before any policy call.
        """
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
        """Create one role (or reuse this sample's own), then put its inline policy."""
        role_name = spec["name"]
        print(f"\nCreating IAM role: {role_name}")
        role_arn, created = ensure_role(
            self.iam,
            self.ssm,
            role_name,
            TRUST_POLICY,
            description=spec["description"],
            tags=role_tags(spec["purpose"]),
        )
        self.iam.put_role_policy(
            RoleName=role_name,
            PolicyName=INLINE_POLICY_NAME,
            PolicyDocument=json.dumps(permissions_policy),
        )
        if created:
            print(f"✅ Created IAM role: {role_arn}")
        else:
            print(f"✅ Updated this sample's existing IAM role: {role_arn}")
        return role_arn

    def delete(self, role_keys: list[str]) -> int:
        """Delete the selected roles; return OK, FAILED or LEFT combined over all of them."""
        print("\n🗑️  Deleting AgentCore Runtime execution roles")
        return combine([self.delete_one(ROLES[rk]["name"]) for rk in role_keys])

    def delete_one(self, role_name: str) -> int:
        """Delete one role if this sample created it and it carries only this script's inline policy."""
        return delete_owned_role(self.iam, self.ssm, role_name, EXPECTED_INLINE, EXPECTED_MANAGED)


def main(argv=None) -> int:
    """CLI entry point. Exit code: 0 success, 1 any failure, 3 a role left in place on delete."""
    parser = argparse.ArgumentParser(description="Create or delete the AgentCore Runtime execution roles")
    parser.add_argument(
        "--account-id",
        required=False,
        default=None,
        help="AWS Account ID used in policy ARNs (optional, defaults to current account)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("create", "Create the roles (or update this sample's own)"),
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
        try:
            roles.create(role_keys)
        except RoleOwnershipError as e:
            print(e)
            return FAILED
        return OK
    return roles.delete(role_keys)


if __name__ == "__main__":
    sys.exit(main())
