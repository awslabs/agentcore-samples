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
"""

import sys
import boto3
import json

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

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # Load configuration from SSM
        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.gateway_arn = self._get_parameter("/app/lakehouse-agent/gateway-arn", required=False)
        self.cognito_user_pool_id = self._get_parameter("/app/lakehouse-agent/cognito-user-pool-id", required=False)
        self.cognito_app_client_id = self._get_parameter("/app/lakehouse-agent/cognito-app-client-id", required=False)

        if self.gateway_arn:
            print(f"   ✅ Gateway ARN: {self.gateway_arn}")
        else:
            print("   ⚠️  Gateway ARN not configured")

        if self.cognito_user_pool_id and self.cognito_app_client_id:
            print("   ✅ Cognito configured")
        else:
            print("   ⚠️  Cognito not configured - will use IAM authentication")

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

        # Add JWT authentication configuration if Cognito is configured
        if config.cognito_user_pool_id and config.cognito_app_client_id:
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

            # Add Authorization header to allowlist for OAuth token propagation
            config_params["request_header_configuration"] = {"requestHeaderAllowlist": ["Authorization"]}

            print("✅ JWT authentication will be configured")
        else:
            print("⚠️  Cognito not configured - runtime will use IAM authentication")

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

        return {
            "runtime_arn": runtime_arn,
            "runtime_id": runtime_id,
            "role_arn": role_arn,
        }

    except Exception as e:
        print(f"\n❌ Error deploying runtime: {str(e)}")
        import traceback

        traceback.print_exc()
        raise


def main():
    """Main deployment function."""
    print("=" * 70)
    print("Lakehouse Data Agent Deployment to AgentCore Runtime")
    print("=" * 70)

    # Load configuration from SSM
    config = SSMConfig()

    # Validate configuration
    print("\n🔍 Validating configuration...")

    if not config.gateway_arn:
        print("\n⚠️  Warning: GATEWAY_ARN not set in SSM Parameter Store")
        print("   The agent will not be able to access Gateway tools")
        response = input("\nProceed anyway? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Deployment cancelled")
            sys.exit(0)

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
        if config.cognito_user_pool_id and config.cognito_app_client_id:
            print("\n✅ JWT Authentication Configured:")
            print(
                f"   Discovery URL: https://cognito-idp.{config.region}.amazonaws.com/{config.cognito_user_pool_id}/.well-known/openid-configuration"
            )
            print(f"   Allowed Clients: {config.cognito_app_client_id}")
            print("   Authorization header: Enabled for OAuth token propagation")
        else:
            print("\n⚠️  JWT Authentication Not Configured:")
            print("   Runtime deployed with IAM authentication")
            print("   To enable JWT auth, set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID in SSM and redeploy")

        print("\n📋 Next Steps:")
        print("   1. Test the agent: python ../test_agent_simple.py")
        print("   2. Test E2E flow: python ../test_e2e_flow.py")
        print("   3. Deploy the Streamlit UI: cd ../streamlit-ui && streamlit run streamlit_app.py")

        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ Deployment failed: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
