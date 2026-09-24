#!/usr/bin/env python3
"""
Deploy OpenSearch MCP Server using AgentCore CLI.

This script deploys the OpenSearch MCP Server to Amazon Bedrock AgentCore Runtime
using the `agentcore deploy` CLI instead of the starter toolkit.

Prerequisites:
- AWS credentials configured
- Docker running
- agentcore CLI installed (pip install bedrock-agentcore)
- AOSS collection provisioned
- Configuration in SSM Parameter Store

Usage:
    python deploy.py
"""

import json
import os
import subprocess
import sys

import boto3

# Make the repo's utils/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from utils.idp_config import get_idp_provider


class SSMConfig:
    """Load configuration from SSM Parameter Store."""

    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.account_id = self.sts.get_caller_identity()["Account"]
        self.idp_provider = get_idp_provider(self.ssm)

        # Load SSM parameters
        self.opensearch_collection_endpoint = self._get_parameter(
            "/app/lakehouse-agent/opensearch-collection-endpoint"
        )
        self.opensearch_collection_arn = self._get_parameter("/app/lakehouse-agent/opensearch-collection-arn")

        # IdP-specific parameters
        if self.idp_provider == "cognito":
            self.cognito_user_pool_arn = self._get_parameter("/app/lakehouse-agent/cognito-user-pool-arn")
            self.cognito_m2m_client_id = self._get_parameter("/app/lakehouse-agent/cognito-m2m-client-id")
        else:  # okta
            self.okta_discovery_url = self._get_parameter("/app/lakehouse-agent/okta-discovery-url")
            self.okta_resource_server_audience = self._get_parameter(
                "/app/lakehouse-agent/okta-resource-server-audience"
            )

        print(f"✅ Configuration loaded (region={self.region}, idp={self.idp_provider})")

    def _get_parameter(self, name: str, required: bool = True) -> str:
        try:
            return self.ssm.get_parameter(Name=name)["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            if required:
                print(f"❌ SSM parameter {name} not found")
                sys.exit(1)
            return None

    def store_runtime_parameters(self, runtime_arn: str, runtime_id: str):
        """Store runtime info in SSM."""
        params = [
            ("/app/lakehouse-agent/opensearch-mcp-runtime-arn", runtime_arn),
            ("/app/lakehouse-agent/opensearch-mcp-runtime-id", runtime_id),
        ]
        for name, value in params:
            self.ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
            print(f"✅ Stored {name}")


def create_runtime_role(config: SSMConfig) -> str:
    """Create or update IAM role for the runtime."""
    iam = boto3.client("iam", region_name=config.region)
    role_name = "AgentCoreRuntimeRole-opensearch-mcp"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["aoss:APIAccessAll"],
                "Resource": config.opensearch_collection_arn,
            },
            {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["ssm:GetParameter", "ssm:GetParameters"],
                "Resource": f"arn:aws:ssm:{config.region}:{config.account_id}:parameter/app/lakehouse-agent/*",
            },
        ],
    }

    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Runtime role for OpenSearch MCP server (CLI deployment)",
            Tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "opensearch-mcp-role"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )
        print(f"✅ Created IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )
        print(f"✅ Updated IAM role: {role_arn}")

    return role_name


def update_aws_targets(config: SSMConfig, role_name: str):
    """Update aws-targets.json with new array format."""
    targets_path = os.path.join(os.path.dirname(__file__), "agentcore", "aws-targets.json")

    # New array format: name + account + region
    targets = [
        {
            "name": "default",
            "account": config.account_id,
            "region": config.region,
        }
    ]

    with open(targets_path, "w") as f:
        json.dump(targets, f, indent=2)

    print(f"✅ Updated {targets_path}")


def update_agentcore_json(config: SSMConfig):
    """Update agentcore.json with runtime config including auth and env vars."""
    agentcore_path = os.path.join(os.path.dirname(__file__), "agentcore", "agentcore.json")

    # Build environment variables
    env_vars = [
        {"name": "AWS_REGION", "value": config.region},
        {"name": "OPENSEARCH_COLLECTION_ENDPOINT", "value": config.opensearch_collection_endpoint},
        {"name": "LOG_LEVEL", "value": "DEBUG"},
    ]

    # Build authorizer configuration based on IdP
    if config.idp_provider == "cognito":
        user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
        issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        authorizer_config = {
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJwtAuthorizer": {
                    "allowedClients": [config.cognito_m2m_client_id],
                    "discoveryUrl": discovery_url,
                }
            },
        }
    else:  # okta
        authorizer_config = {
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJwtAuthorizer": {
                    "allowedAudience": [config.okta_resource_server_audience],
                    "discoveryUrl": config.okta_discovery_url,
                }
            },
        }

    # Build the full agentcore.json
    agentcore_config = {
        "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
        "name": "OpenSearchMcpServer",
        "version": 1,
        "managedBy": "CDK",
        "runtimes": [
            {
                "name": "opensearch_mcp_server",
                "build": "Container",
                "codeLocation": "../../4b-mcp-opensearch-server",
                "entrypoint": "server.py",
                "protocol": "MCP",
                "networkMode": "PUBLIC",
                "envVars": env_vars,
                **authorizer_config,
            }
        ],
        "memories": [],
        "credentials": [],
        "evaluators": [],
        "onlineEvalConfigs": [],
        "agentCoreGateways": [],
        "policyEngines": [],
    }

    with open(agentcore_path, "w") as f:
        json.dump(agentcore_config, f, indent=2)

    print(f"✅ Updated {agentcore_path}")


def get_node20_env():
    """Get environment with Node 20+ in PATH (homebrew node)."""
    env = os.environ.copy()
    # Prepend homebrew bin to ensure Node 20+ is used (agentcore CLI requires Node 20+)
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def run_agentcore_create_if_needed():
    """Run agentcore create if CDK project doesn't exist."""
    import shutil

    project_root = os.path.dirname(__file__)
    cdk_dir = os.path.join(project_root, "agentcore", "cdk")

    if os.path.exists(cdk_dir):
        print("✅ CDK project already exists, skipping create")
        return

    print("\n🚀 Running agentcore create (first-time setup)...")
    project_name = "OpenSearchMcpServer"
    created_project_dir = os.path.join(project_root, project_name)

    # agentcore create always creates a new project folder
    result = subprocess.run(
        [
            "agentcore", "create",
            "--project-name", project_name,
            "--no-agent",
            "--skip-git",
            "--skip-python-setup",
            "--skip-install",
            "--output-dir", ".",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=get_node20_env(),
    )

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print(f"❌ agentcore create failed with code {result.returncode}")
        sys.exit(1)

    # Copy CDK folder from created project to our existing agentcore/ folder
    created_cdk_dir = os.path.join(created_project_dir, "agentcore", "cdk")
    if os.path.exists(created_cdk_dir):
        shutil.copytree(created_cdk_dir, cdk_dir)
        print(f"✅ Copied CDK project to {cdk_dir}")

        # Clean up the created project folder
        shutil.rmtree(created_project_dir)
        print(f"✅ Cleaned up {created_project_dir}")
    else:
        print(f"❌ CDK folder not found at {created_cdk_dir}")
        sys.exit(1)

    print("✅ CDK project created")


def run_agentcore_deploy():
    """Run agentcore deploy CLI command."""
    # Run from project root (parent of agentcore folder), not from agentcore folder itself
    project_root = os.path.dirname(__file__)

    # Ensure CDK project exists
    run_agentcore_create_if_needed()

    print("\n🚀 Running agentcore deploy...")
    result = subprocess.run(
        ["agentcore", "deploy", "-y"],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=get_node20_env(),
    )

    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    if result.returncode != 0:
        print(f"❌ agentcore deploy failed with code {result.returncode}")
        sys.exit(1)


def get_runtime_info(config: SSMConfig) -> tuple:
    """Get runtime ARN and ID from AgentCore API after deployment."""
    client = boto3.client("bedrock-agentcore-control", region_name=config.region)

    # List runtimes and find ours by name
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page.get("agentRuntimeSummaries", []):
            if runtime.get("agentRuntimeName") == "opensearch_mcp_server":
                runtime_id = runtime.get("agentRuntimeId")
                runtime_arn = runtime.get("agentRuntimeArn")
                return runtime_arn, runtime_id

    return None, None


def main():
    print("=" * 70)
    print("OpenSearch MCP Server Deployment (CLI)")
    print("=" * 70)

    # Load config
    config = SSMConfig()

    # Create/update IAM role
    print("\n📋 Step 1: IAM Role")
    role_name = create_runtime_role(config)

    # Update aws-targets.json
    print("\n📋 Step 2: AWS Targets")
    update_aws_targets(config, role_name)

    # Update agentcore.json with runtime config including auth
    print("\n📋 Step 3: AgentCore Config")
    update_agentcore_json(config)

    # Run agentcore deploy
    print("\n📋 Step 4: Deploy")
    run_agentcore_deploy()

    # Get runtime info and store in SSM
    print("\n📋 Step 5: Store Parameters")
    runtime_arn, runtime_id = get_runtime_info(config)
    if runtime_arn and runtime_id:
        config.store_runtime_parameters(runtime_arn, runtime_id)
        print(f"\n✅ Deployment complete!")
        print(f"   Runtime ARN: {runtime_arn}")
        print(f"   Runtime ID: {runtime_id}")
    else:
        print("⚠️  Could not retrieve runtime info. Check AgentCore console.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
