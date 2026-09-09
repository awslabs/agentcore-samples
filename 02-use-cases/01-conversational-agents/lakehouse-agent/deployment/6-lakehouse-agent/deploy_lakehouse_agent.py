#!/usr/bin/env python3
"""
Deploy Lakehouse Agent to AgentCore Runtime

This script deploys the health lakehouse data agent to Amazon Bedrock AgentCore Runtime
using the Bedrock AgentCore Starter Toolkit.

Prerequisites:
- AWS credentials configured
- Docker running
- Gateway configured (run create_gateway.py)
- Configuration in SSM Parameter Store (see README.md)
- bedrock-agentcore-starter-toolkit installed

Usage:
    python deploy_lakehouse_agent.py
    python deploy_lakehouse_agent.py --yes    # don't prompt; proceed without a Gateway ARN

The only prompt this script has is the one guarding a missing Gateway ARN in SSM.
Deploying without it produces an agent that cannot reach any Gateway tool, so the
confirmation is a real safety check rather than ceremony -- and ``--yes`` is the
supported way for an automated caller (notebook 06, a run harness) to accept that
outcome deliberately.
"""

import argparse
import json
import os
import sys

import boto3

# Make the repo's utils/ importable (idp_config lives there) when this script
# runs from its own deployment subdir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import get_idp_provider

try:
    from bedrock_agentcore_starter_toolkit import Runtime
except ImportError:
    print("\n❌ Error: bedrock-agentcore-starter-toolkit not installed")
    print("   Please install it with: pip install bedrock-agentcore-starter-toolkit")
    sys.exit(1)


class SSMConfig:
    """Load configuration from SSM Parameter Store."""

    def __init__(self):
        """Initialize and load configuration from SSM."""
        # Get region from boto3 session
        session = boto3.Session()
        self.region = session.region_name

        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)

        # Get account ID
        self.account_id = self.sts.get_caller_identity()["Account"]

        # IdP selector — read once (DR-8 flag-branch convention).
        self.idp_provider = get_idp_provider(self.ssm)

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")
        print(f"   IdP: {self.idp_provider}")

        # Load configuration from SSM
        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.gateway_arn = self._get_parameter("/app/lakehouse-agent/gateway-arn", required=False)
        # Cognito authorizer inputs — USER app client (the agent is user-invoked,
        # unlike the gateway-called MCP runtimes which validate the M2M client).
        self.cognito_user_pool_id = self._get_parameter("/app/lakehouse-agent/cognito-user-pool-id", required=False)
        self.cognito_app_client_id = self._get_parameter("/app/lakehouse-agent/cognito-app-client-id", required=False)

        # Okta authorizer inputs — required only on the Okta path (mirrors the MCP deployers).
        self.okta_discovery_url = None
        self.okta_resource_server_audience = None
        if self.idp_provider == "okta":
            self.okta_discovery_url = self._get_parameter("/app/lakehouse-agent/okta-discovery-url")
            self.okta_resource_server_audience = self._get_parameter(
                "/app/lakehouse-agent/okta-resource-server-audience"
            )

        if self.gateway_arn:
            print(f"   ✅ Gateway ARN: {self.gateway_arn}")
        else:
            print("   ⚠️  Gateway ARN not configured")

        if self.idp_provider == "okta":
            print("   ✅ Okta configured")
        elif self.cognito_user_pool_id and self.cognito_app_client_id:
            print("   ✅ Cognito configured")
        else:
            print("   ⚠️  IdP not configured - will use IAM authentication")

    def _get_parameter(self, parameter_name: str, required: bool = True) -> str:
        """Get parameter value from SSM Parameter Store."""
        try:
            response = self.ssm.get_parameter(Name=parameter_name)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            if required:
                print(f"❌ SSM parameter {parameter_name} not found")
                print("   Please run the setup scripts first")
                sys.exit(1)
            return None
        except Exception as e:
            if required:
                print(f"❌ Error retrieving parameter {parameter_name}: {e}")
                sys.exit(1)
            return None

    def store_agent_parameters(self, runtime_arn: str, runtime_id: str):
        """Store Lakehouse Agent runtime information in SSM Parameter Store."""
        print("\n💾 Storing agent configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/agent-runtime-arn",
                "value": runtime_arn,
                "description": "Lakehouse Agent runtime ARN on AgentCore",
            },
            {
                "name": "/app/lakehouse-agent/agent-runtime-id",
                "value": runtime_id,
                "description": "Lakehouse Agent runtime ID on AgentCore",
            },
            {
                "name": "/app/lakehouse-agent/agent-name",
                "value": "lakehouse_agent",
                "description": "Lakehouse Agent name",
            },
        ]

        for param in parameters:
            try:
                self.ssm.put_parameter(
                    Name=param["name"],
                    Value=param["value"],
                    Description=param["description"],
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter: {param['name']} = {param['value']}")
            except Exception as e:
                print(f"❌ Error storing parameter {param['name']}: {e}")
                raise


def create_agent_role(config: SSMConfig):
    """Create IAM role for Lakehouse Agent Runtime execution."""
    iam = boto3.client("iam", region_name=config.region)

    role_name = "AgentCoreRuntimeRole-lakehouse-agent"

    # Trust policy for AgentCore Runtime
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

    # Permissions policy
    permissions_policy = {
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
        # Create role
        print(f"Creating IAM role: {role_name}")
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Runtime execution role for lakehouse data agent",
            Tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "agent-role"},
            ],
        )
        role_arn = response["Role"]["Arn"]

        # Attach inline policy
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )

        print(f"✅ Created IAM role: {role_arn}")
        return role_arn

    except iam.exceptions.EntityAlreadyExistsException:
        print(f"ℹ️  Role {role_name} already exists, retrieving ARN")
        response = iam.get_role(RoleName=role_name)
        role_arn = response["Role"]["Arn"]

        # Update the role policy to ensure it has all required permissions
        print("   Updating role policy with latest permissions...")
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )
        print("   ✅ Role policy updated")

        return role_arn


def deploy_to_runtime(config: SSMConfig, role_arn: str):
    """Deploy lakehouse agent to AgentCore Runtime using starter toolkit."""
    runtime_name = "lakehouse_agent"  # Must use underscores, not hyphens

    try:
        print("\n🚀 Deploying Lakehouse Agent to AgentCore Runtime...")
        print(f"   Name: {runtime_name}")
        print(f"   Region: {config.region}")
        print("   This will build a Docker container and deploy it...")

        # Build environment variables
        env_vars = {"AWS_REGION": config.region}

        if config.gateway_arn:
            env_vars["GATEWAY_ARN"] = config.gateway_arn

        print("\n📋 Environment variables:")
        for key, value in env_vars.items():
            print(f"   {key}: {value}")

        # Initialize Runtime from starter toolkit
        agentcore_runtime = Runtime()

        # Configure the runtime
        print("\n🔧 Configuring AgentCore Runtime...")

        # Extract role name from ARN (format: arn:aws:iam::account:role/RoleName)
        role_name = role_arn.split("/")[-1]

        # Build configuration parameters
        config_params = {
            "entrypoint": "lakehouse_agent.py",
            "execution_role": role_name,  # Use role name, not ARN
            "auto_create_ecr": True,
            "requirements_file": "requirements.txt",
            "region": config.region,
            # Note: Not specifying protocol - will use default HTTP protocol for JWT auth
            "agent_name": runtime_name,
        }

        # JWT authorizer differs by IdP (DR-8). The agent runtime is invoked by
        # the END USER (Streamlit forwards `Authorization: Bearer <access_token>`),
        # so it must validate that user token: Cognito by USER app-client-id (NOT
        # the M2M client the gateway-called MCP runtimes use), Okta by resource-
        # server audience. A pure-IAM fallback remains only for a genuinely-
        # unconfigured deploy (idp not in {cognito, okta} with keys present).
        if config.idp_provider == "cognito" and config.cognito_user_pool_id and config.cognito_app_client_id:
            # [COGNITO] upstream verbatim — USER app client.
            print("   Configuring JWT authentication...")
            issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{config.cognito_user_pool_id}"
            discovery_url = f"{issuer}/.well-known/openid-configuration"

            print(f"   Discovery URL: {discovery_url}")
            print(f"   Allowed Clients: {config.cognito_app_client_id}")

            config_params["authorizer_configuration"] = {
                "customJWTAuthorizer": {
                    "allowedClients": [config.cognito_app_client_id],
                    "discoveryUrl": discovery_url,
                }
            }

            print("✅ JWT authentication will be configured")
        elif config.idp_provider == "okta":
            # [OKTA] custom-auth-server discovery + audience (canonical §6 names);
            # mirrors the MCP Okta branch. Okta access tokens carry `aud` →
            # validate by resource-server audience.
            print("   Configuring JWT authentication...")
            print(f"   Discovery URL: {config.okta_discovery_url}")
            print(f"   Allowed Audience: {config.okta_resource_server_audience}")

            config_params["authorizer_configuration"] = {
                "customJWTAuthorizer": {
                    "allowedAudience": [config.okta_resource_server_audience],
                    "discoveryUrl": config.okta_discovery_url,
                }
            }

            print("✅ JWT authentication will be configured")
        else:
            print("⚠️  IdP not configured - runtime will use IAM authentication")

        # Add Authorization header to allowlist for OAuth token propagation.
        # UNCONDITIONAL (both IdP paths): hoisted out of the Cognito branch so the
        # Okta path keeps it (the OAuth access token must reach the runtime).
        config_params["request_header_configuration"] = {"requestHeaderAllowlist": ["Authorization"]}

        agentcore_runtime.configure(**config_params)
        print("✅ Configuration complete")

        # Launch the runtime (builds Docker image and deploys)
        print("\n🚀 Launching to AgentCore Runtime...")
        print("   This may take several minutes...")
        launch_result = agentcore_runtime.launch(env_vars=env_vars)

        runtime_arn = launch_result.agent_arn
        runtime_id = launch_result.agent_id

        print("\n✅ Lakehouse Agent deployed successfully!")
        print(f"   Runtime ARN: {runtime_arn}")
        print(f"   Runtime ID: {runtime_id}")

        # Tag the runtime (post-launch). The starter toolkit's configure()/launch()
        # do not surface a tags= kwarg, so apply the Application/Purpose tags via the
        # control-plane TagResource on the returned runtime ARN (mirrors 4a/4b + the
        # Application-tag convention). Fail-soft: tags are inventory/cost-allocation
        # metadata, not load-bearing for the deployment.
        try:
            boto3.client("bedrock-agentcore-control", region_name=config.region).tag_resource(
                resourceArn=runtime_arn,
                tags={"Application": "lakehouse-agent", "Purpose": "lakehouse-agent"},
            )
            print("   🏷️  Tagged runtime: Application=lakehouse-agent, Purpose=lakehouse-agent")
        except Exception as tag_err:
            print(f"   ⚠️  Could not tag runtime (non-fatal): {tag_err}")

        return {
            "runtime_arn": runtime_arn,
            "runtime_id": runtime_id,
            "role_arn": role_arn,
        }

    except Exception as e:
        print(f"\n❌ Error deploying runtime: {e!s}")
        import traceback

        traceback.print_exc()
        raise


GATEWAY_ARN_SSM_KEY = "/app/lakehouse-agent/gateway-arn"


def confirm_missing_gateway_arn(assume_yes: bool) -> None:
    """Handle the missing-Gateway-ARN confirmation.

    Three paths, deliberately distinct:

    * ``--yes``          -> proceed, and say so. The caller has accepted the
                            consequence in advance.
    * interactive TTY    -> prompt, exactly as before.
    * no TTY, no ``--yes`` -> FAIL FAST with the missing key named.

    That third path is the important one. ``input()`` on a stdin that nobody can
    answer does not error -- it blocks. Backgrounded (``nohup ... &``) the read
    takes SIGTTIN and the process STOPS, which from the outside is
    indistinguishable from a slow deploy: no output, no exit, nothing to
    diagnose. A clean failure naming the missing SSM key is strictly better than
    a hang that looks like slowness, for the same reason a deterministic error
    beats an intermittent one -- it tells the operator what to fix instead of
    inviting them to wait and guess.
    """
    print("\n⚠️  Warning: GATEWAY_ARN not set in SSM Parameter Store")
    print(f"   Expected SSM parameter: {GATEWAY_ARN_SSM_KEY}")
    print("   The agent will not be able to access Gateway tools")

    if assume_yes:
        print("   ➡️  --yes supplied: proceeding without a Gateway ARN.")
        return

    if not sys.stdin.isatty():
        print("\n❌ Cannot prompt for confirmation: stdin is not a terminal.")
        print(f"   Set {GATEWAY_ARN_SSM_KEY} in SSM (run create_gateway.py), or")
        print("   re-run with --yes to deploy an agent with no Gateway access on purpose.")
        print("   Refusing to wait on a prompt nobody can answer — a blocked read here")
        print("   would look like a hung deployment rather than a configuration error.")
        sys.exit(1)

    response = input("\nProceed anyway? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("Deployment cancelled")
        sys.exit(0)


def parse_args(argv=None):
    """Parse command-line arguments.

    Exists because ``--yes`` was previously accepted-and-ignored: notebook 06 has
    always passed it, and without argparse the flag did nothing while the prompt
    below stayed reachable. The flag is honoured here rather than removed from the
    caller -- the prompt is a legitimate safety check, and the caller's intent to
    bypass it is legitimate too.
    """
    parser = argparse.ArgumentParser(description="Deploy the Lakehouse Data Agent to AgentCore Runtime")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        dest="assume_yes",
        help="Skip the missing-Gateway-ARN confirmation and proceed anyway (for unattended runs)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Main deployment function."""
    args = parse_args(argv)

    print("=" * 70)
    print("Lakehouse Data Agent Deployment to AgentCore Runtime")
    print("=" * 70)

    # Load configuration from SSM
    config = SSMConfig()

    # Validate configuration
    print("\n🔍 Validating configuration...")

    if not config.gateway_arn:
        confirm_missing_gateway_arn(args.assume_yes)

    print("✅ Configuration validated")

    # Print configuration summary
    print("\n📋 Configuration:")
    print(f"   Region: {config.region}")
    print(f"   Gateway ARN: {config.gateway_arn or 'Not configured'}")

    try:
        # Step 1: Create IAM role
        print("\n" + "=" * 70)
        print("Step 1: Creating IAM Role")
        print("=" * 70)
        role_arn = create_agent_role(config)

        # Step 2: Deploy to runtime
        print("\n" + "=" * 70)
        print("Step 2: Deploying to AgentCore Runtime")
        print("=" * 70)
        result = deploy_to_runtime(config, role_arn)

        # Step 3: Store agent parameters in SSM
        print("\n" + "=" * 70)
        print("Step 3: Storing Agent Configuration")
        print("=" * 70)
        config.store_agent_parameters(result["runtime_arn"], result["runtime_id"])

        # Print summary
        print("\n" + "=" * 70)
        print("Deployment Complete!")
        print("=" * 70)

        print("\n✅ Agent configuration stored in SSM Parameter Store:")
        print("   /app/lakehouse-agent/agent-runtime-arn")
        print("   /app/lakehouse-agent/agent-runtime-id")
        print("   /app/lakehouse-agent/agent-name")

        # Print JWT configuration status
        if config.idp_provider == "cognito" and config.cognito_user_pool_id and config.cognito_app_client_id:
            print("\n✅ JWT Authentication Configured (Cognito):")
            print(
                f"   Discovery URL: https://cognito-idp.{config.region}.amazonaws.com/{config.cognito_user_pool_id}/.well-known/openid-configuration"
            )
            print(f"   Allowed Clients: {config.cognito_app_client_id}")
            print("   Authorization header: Enabled for OAuth token propagation")
        elif config.idp_provider == "okta":
            print("\n✅ JWT Authentication Configured (Okta):")
            print(f"   Discovery URL: {config.okta_discovery_url}")
            print(f"   Allowed Audience: {config.okta_resource_server_audience}")
            print("   Authorization header: Enabled for OAuth token propagation")
        else:
            print("\n⚠️  JWT Authentication Not Configured:")
            print("   Runtime deployed with IAM authentication")
            print("   To enable JWT auth, set the IdP flag (notebook 01) and the IdP's SSM keys, then redeploy")

        print("\n📋 Next Steps:")
        print("   1. Test the agent: python ../test_agent_simple.py")
        print("   2. Test E2E flow: python ../test_e2e_flow.py")
        print("   3. Deploy the Streamlit UI: cd ../streamlit-ui && streamlit run streamlit_app.py")

        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ Deployment failed: {e!s}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
