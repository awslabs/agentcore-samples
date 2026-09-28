#!/usr/bin/env python3
"""
Deploy Lakehouse Agent using AgentCore CLI.

This script deploys the Lakehouse Agent to Amazon Bedrock AgentCore Runtime
using the `agentcore deploy` CLI instead of the starter toolkit.

Prerequisites:
- AWS credentials configured
- Docker running
- agentcore CLI installed (pip install bedrock-agentcore)
- Gateway configured (or --yes to skip)
- Configuration in SSM Parameter Store

Usage:
    python deploy.py
    python deploy.py --yes    # Skip missing Gateway ARN confirmation
"""

import argparse
import json
import os
import shutil
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
        self.gateway_arn = self._get_parameter("/app/lakehouse-agent/gateway-arn", required=False)

        # IdP-specific parameters
        if self.idp_provider == "cognito":
            self.cognito_user_pool_id = self._get_parameter(
                "/app/lakehouse-agent/cognito-user-pool-id", required=False
            )
            self.cognito_app_client_id = self._get_parameter(
                "/app/lakehouse-agent/cognito-app-client-id", required=False
            )
        elif self.idp_provider == "auth0":
            self.auth0_domain = self._get_parameter("/app/lakehouse-agent/auth0-domain")
            self.auth0_audience = self._get_parameter("/app/lakehouse-agent/auth0-audience")
        else:  # okta
            self.okta_discovery_url = self._get_parameter("/app/lakehouse-agent/okta-discovery-url")
            self.okta_resource_server_audience = self._get_parameter(
                "/app/lakehouse-agent/okta-resource-server-audience"
            )

        print(f"✅ Configuration loaded (region={self.region}, idp={self.idp_provider})")
        if self.gateway_arn:
            print(f"   Gateway ARN: {self.gateway_arn}")
        else:
            print("   ⚠️  Gateway ARN not configured")

    def _get_parameter(self, name: str, required: bool = True) -> str:
        try:
            return self.ssm.get_parameter(Name=name)["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            if required:
                print(f"❌ SSM parameter {name} not found")
                sys.exit(1)
            return None

    def store_agent_parameters(self, runtime_arn: str, runtime_id: str):
        """Store agent info in SSM."""
        params = [
            ("/app/lakehouse-agent/agent-runtime-arn", runtime_arn),
            ("/app/lakehouse-agent/agent-runtime-id", runtime_id),
            ("/app/lakehouse-agent/agent-name", "lakehouse_agent"),
        ]
        for name, value in params:
            self.ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
            print(f"✅ Stored {name}")


def create_agent_role(config: SSMConfig) -> str:
    """Create or update IAM role for the agent."""
    iam = boto3.client("iam", region_name=config.region)
    role_name = "AgentCoreRuntimeRole-lakehouse-agent"

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
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway", "bedrock-agentcore:GetGateway"],
                "Resource": f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:gateway/*",
            },
            {"Effect": "Allow", "Action": ["logs:*", "xray:*"], "Resource": ["*"]},
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
            Description="AgentCore Runtime role for Lakehouse Agent (CLI deployment)",
            Tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "agent-role"},
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
    """Update aws-targets.json with dynamic configuration."""
    targets_path = os.path.join(os.path.dirname(__file__), "agentcore", "aws-targets.json")

    # Use array format required by newer AgentCore CLI
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
    """Update agentcore.json with runtime config including auth based on IdP."""
    agentcore_path = os.path.join(os.path.dirname(__file__), "agentcore", "agentcore.json")

    # Build authorizer configuration based on IdP
    if config.idp_provider == "cognito":
        user_pool_id = config.cognito_user_pool_id
        issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        authorizer_config = {
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJwtAuthorizer": {
                    "allowedClients": [config.cognito_app_client_id],
                    "discoveryUrl": discovery_url,
                }
            },
        }
    elif config.idp_provider == "auth0":
        discovery_url = f"https://{config.auth0_domain}/.well-known/openid-configuration"
        authorizer_config = {
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJwtAuthorizer": {
                    "allowedAudience": [config.auth0_audience],
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
        "name": "LakehouseAgent",
        "version": 1,
        "managedBy": "CDK",
        "runtimes": [
            {
                "name": "lakehouse_agent",
                "build": "Container",
                "codeLocation": "../../6-lakehouse-agent",
                "entrypoint": "lakehouse_agent.py",
                "protocol": "HTTP",
                "networkMode": "PUBLIC",
                **authorizer_config,
                # Forward Authorization header to agent code for user token passthrough
                "requestHeaderAllowlist": ["Authorization"],
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

    print(f"✅ Updated {agentcore_path} (IdP: {config.idp_provider})")


def get_node20_env():
    """Get environment with Node 20+ in PATH (homebrew node)."""
    env = os.environ.copy()
    # Prepend homebrew bin to ensure Node 20+ is used (agentcore CLI requires Node 20+)
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    return env


def run_agentcore_create_if_needed():
    """Run agentcore create to generate CDK folder if it doesn't exist."""
    project_root = os.path.dirname(__file__)
    agentcore_dir = os.path.join(project_root, "agentcore")
    cdk_dir = os.path.join(agentcore_dir, "cdk")

    if os.path.exists(cdk_dir):
        print(f"✅ CDK folder already exists at {cdk_dir}")
        return

    print("\n📦 CDK folder not found. Running agentcore create to generate it...")

    # Create a temp project name (max 23 chars)
    temp_project_name = "TempLakehouseProj"

    result = subprocess.run(
        ["agentcore", "create", "--project-name", temp_project_name, "--defaults", "--skip-git", "--skip-python-setup", "--skip-install", "--output-dir", "."],
        cwd=project_root,
        capture_output=True,
        text=True,
        env=get_node20_env(),
    )

    if result.returncode != 0:
        print(f"❌ agentcore create failed: {result.stderr}")
        sys.exit(1)

    # Copy the cdk folder from temp project to our agentcore folder
    temp_cdk_dir = os.path.join(project_root, temp_project_name, "agentcore", "cdk")
    if os.path.exists(temp_cdk_dir):
        shutil.copytree(temp_cdk_dir, cdk_dir)
        print(f"✅ Copied CDK folder to {cdk_dir}")

        # Clean up temp project
        temp_project_dir = os.path.join(project_root, temp_project_name)
        shutil.rmtree(temp_project_dir)
        print(f"✅ Cleaned up temp project folder")
    else:
        print(f"❌ CDK folder not found in temp project at {temp_cdk_dir}")
        sys.exit(1)


def run_agentcore_deploy():
    """Run agentcore deploy CLI command."""
    # Run from project root (parent of agentcore folder), not from agentcore folder itself
    project_root = os.path.dirname(__file__)

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
            if runtime.get("agentRuntimeName") == "lakehouse_agent":
                runtime_id = runtime.get("agentRuntimeId")
                runtime_arn = runtime.get("agentRuntimeArn")
                return runtime_arn, runtime_id

    return None, None


def confirm_missing_gateway_arn(assume_yes: bool) -> None:
    """Handle missing Gateway ARN confirmation."""
    print("\n⚠️  Warning: GATEWAY_ARN not set in SSM Parameter Store")
    print("   Expected SSM parameter: /app/lakehouse-agent/gateway-arn")
    print("   The agent will not be able to access Gateway tools")

    if assume_yes:
        print("   ➡️  --yes supplied: proceeding without a Gateway ARN.")
        return

    if not sys.stdin.isatty():
        print("\n❌ Cannot prompt for confirmation: stdin is not a terminal.")
        print("   Set /app/lakehouse-agent/gateway-arn in SSM, or")
        print("   re-run with --yes to deploy without Gateway access.")
        sys.exit(1)

    response = input("\nProceed anyway? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("Deployment cancelled")
        sys.exit(0)


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy Lakehouse Agent using AgentCore CLI")
    parser.add_argument(
        "--yes", "-auc", action="store_true", dest="assume_yes", help="Skip missing Gateway ARN confirmation"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("Lakehouse Agent Deployment (CLI)")
    print("=" * 70)

    # Load config
    config = SSMConfig()

    # Check Gateway ARN
    if not config.gateway_arn:
        confirm_missing_gateway_arn(args.assume_yes)

    # Create/update IAM role
    print("\n📋 Step 1: IAM Role")
    role_name = create_agent_role(config)

    # Update aws-targets.json
    print("\n📋 Step 2: AWS Targets")
    update_aws_targets(config, role_name)

    # Update agentcore.json with IdP-specific config
    print("\n📋 Step 3: AgentCore Config")
    update_agentcore_json(config)

    # Ensure CDK folder exists
    print("\n📋 Step 4: CDK Setup")
    run_agentcore_create_if_needed()

    # Run agentcore deploy
    print("\n📋 Step 5: Deploy")
    run_agentcore_deploy()

    # Get runtime info and store in SSM
    print("\n📋 Step 6: Store Parameters")
    runtime_arn, runtime_id = get_runtime_info(config)
    if runtime_arn and runtime_id:
        config.store_agent_parameters(runtime_arn, runtime_id)
        print(f"\n✅ Deployment complete!")
        print(f"   Runtime ARN: {runtime_arn}")
        print(f"   Runtime ID: {runtime_id}")
    else:
        print("⚠️  Could not retrieve runtime info. Check AgentCore console.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
