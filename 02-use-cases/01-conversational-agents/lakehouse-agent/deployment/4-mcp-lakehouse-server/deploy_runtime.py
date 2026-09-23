#!/usr/bin/env python3
"""
Deploy MCP Athena Server to AgentCore Runtime

This script deploys the MCP server to Amazon Bedrock AgentCore Runtime using
the Bedrock AgentCore Starter Toolkit. The server provides secure Athena query
tools with Lake Formation RLS.

Prerequisites:
- AWS credentials configured
- Docker running
- Lake Formation RLS configured (run setup_lake_formation.py)
- Configuration in SSM Parameter Store
- bedrock-agentcore-starter-toolkit installed

Usage:
    python deploy_runtime.py
"""

import boto3
import json
import sys

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

        # Load configuration from SSM
        self.s3_bucket_name = self._get_parameter("/app/lakehouse-agent/s3-bucket-name")
        self.database_name = self._get_parameter("/app/lakehouse-agent/database-name")
        self.catalog_name = self._get_parameter("/app/lakehouse-agent/catalog-name", required=False)
        self.cognito_user_pool_arn = self._get_parameter("/app/lakehouse-agent/cognito-user-pool-arn")

        # Constants
        self.log_level = "DEBUG"

        print("✅ Configuration loaded from SSM Parameter Store")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

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

    def is_valid(self) -> bool:
        """Check if all required configuration is present."""
        return all([self.s3_bucket_name, self.database_name, self.region, self.account_id])

    def print_status(self):
        """Print configuration status."""
        print("\n📋 Configuration Status:")
        print(f"   AWS Account: {self.account_id}")
        print(f"   Region: {self.region}")
        print(f"   S3 Bucket: {self.s3_bucket_name}")
        print(f"   Database: {self.database_name}")
        print(f"   Catalog: {self.catalog_name or 'default'}")
        print(f"   Cognito User Pool ARN: {self.cognito_user_pool_arn}")
        print(f"   Log Level: {self.log_level}")

    def store_runtime_parameters(self, runtime_arn: str, runtime_id: str):
        """Store MCP server runtime information in SSM Parameter Store."""
        print("\n💾 Storing runtime configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/mcp-server-runtime-arn",
                "value": runtime_arn,
                "description": "MCP Athena Server runtime ARN on AgentCore",
            },
            {
                "name": "/app/lakehouse-agent/mcp-server-runtime-id",
                "value": runtime_id,
                "description": "MCP Athena Server runtime ID on AgentCore",
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


def create_runtime_role(config: SSMConfig):
    """Create IAM role for AgentCore Runtime execution."""
    iam = boto3.client("iam", region_name=config.region)

    role_name = "AgentCoreRuntimeRole-lakehouse-mcp"

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

    # Permissions policy - base statements
    statements = [
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
                f"arn:aws:s3:::{config.s3_bucket_name}/*",
                f"arn:aws:s3:::{config.s3_bucket_name}",
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
    ]

    permissions_policy = {"Version": "2012-10-17", "Statement": statements}

    try:
        # Create role
        print(f"Creating IAM role: {role_name}")
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Runtime execution role for lakehouse data MCP server",
        )
        role_arn = response["Role"]["Arn"]
        print(json.dumps(permissions_policy))
        # Attach inline policy
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )

        print(f"✅ Created IAM role: {role_arn}")
        return role_arn

    except iam.exceptions.EntityAlreadyExistsException:
        print(f"ℹ️  Role {role_name} already exists, deleting and recreating...")

        # Delete inline policies
        try:
            policy_names = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
            for policy_name in policy_names:
                print(f"   Deleting inline policy: {policy_name}")
                iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        except Exception as e:
            print(f"   ⚠️  Error deleting inline policies: {e}")

        # Detach managed policies
        try:
            attached_policies = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]
            for policy in attached_policies:
                print(f"   Detaching managed policy: {policy['PolicyArn']}")
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
        except Exception as e:
            print(f"   ⚠️  Error detaching managed policies: {e}")

        # Remove from instance profiles
        try:
            instance_profiles = iam.list_instance_profiles_for_role(RoleName=role_name)["InstanceProfiles"]
            for profile in instance_profiles:
                print(f"   Removing from instance profile: {profile['InstanceProfileName']}")
                iam.remove_role_from_instance_profile(
                    InstanceProfileName=profile["InstanceProfileName"],
                    RoleName=role_name,
                )
        except Exception as e:
            print(f"   ⚠️  Error removing from instance profiles: {e}")

        # Delete the role
        try:
            iam.delete_role(RoleName=role_name)
            print("   ✅ Deleted existing role")
        except Exception as e:
            print(f"   ❌ Error deleting role: {e}")
            raise

        # Wait a moment for IAM to propagate
        import time

        time.sleep(2)

        # Recreate the role
        print(f"   Creating new role: {role_name}")
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Runtime execution role for lakehouse data MCP server",
        )
        role_arn = response["Role"]["Arn"]

        # Attach inline policy
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AgentCoreRuntimePermissions",
            PolicyDocument=json.dumps(permissions_policy),
        )

        print(f"✅ Recreated IAM role: {role_arn}")
        return role_arn


def deploy_to_runtime(config: SSMConfig, role_arn: str):
    """Deploy MCP server to AgentCore Runtime using starter toolkit."""
    runtime_name = "lakehouse_mcp_server"  # Must use underscores, not hyphens

    try:
        print("\n🚀 Deploying MCP server to AgentCore Runtime...")
        print(f"   Name: {runtime_name}")
        print(f"   Region: {config.region}")
        print("   This will build a Docker container and deploy it...")

        # Build environment variables
        env_vars = {
            "AWS_REGION": config.region,
            "S3_BUCKET_NAME": config.s3_bucket_name,
            "ATHENA_DATABASE_NAME": config.database_name,
            "LOG_LEVEL": config.log_level,
        }
        if config.catalog_name:
            env_vars["CATALOG_NAME"] = config.catalog_name

        print("\n📋 Environment variables:")
        for key, value in env_vars.items():
            print(f"   {key}: {value}")

        # Initialize Runtime from starter toolkit
        agentcore_runtime = Runtime()

        # Configure the runtime
        print("\n🔧 Configuring AgentCore Runtime...")

        # Extract role name from ARN (format: arn:aws:iam::account:role/RoleName)
        role_name = role_arn.split("/")[-1]

        # Extract user pool ID from ARN and build JWT configuration
        user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
        issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        # Get M2M client ID
        response = config.ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-id")
        cognito_m2m_client_id = response["Parameter"]["Value"]
        allowed_clients = [cognito_m2m_client_id]
        print("\n🔐 JWT Authentication Configuration:")
        print(f"   Discovery URL: {discovery_url}")
        print("   Allowed Clients:")
        print(f"      - {cognito_m2m_client_id} (M2M only)")
        auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": allowed_clients,
                "discoveryUrl": discovery_url,
            }
        }

        # Note: Environment variables are read from SSM Parameter Store by the MCP server
        # The starter toolkit will package the entire directory
        agentcore_runtime.configure(
            entrypoint="server.py",
            execution_role=role_name,  # Use role name, not ARN
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            region=config.region,
            protocol="MCP",
            agent_name=runtime_name,
            authorizer_configuration=auth_config,
        )
        print("✅ Configuration complete with JWT authentication")

        # Launch the runtime (builds Docker image and deploys)
        print("\n🚀 Launching to AgentCore Runtime...")
        print("   This may take several minutes...")
        launch_result = agentcore_runtime.launch(env_vars=env_vars)

        runtime_arn = launch_result.agent_arn
        runtime_id = launch_result.agent_id

        print("\n✅ MCP Server deployed successfully!")
        print(f"   Runtime ARN: {runtime_arn}")
        print(f"   Runtime ID: {runtime_id}")

        # Note about JWT authentication
        print("\n⚠️  Important: Configure JWT Authentication")
        print("   The runtime is deployed but needs JWT authentication configured.")
        print("   Run the configuration script:")
        print("   cd mcp-lakehouse-server")
        print("   python configure_runtime_auth.py")

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
    print("MCP Athena Server Deployment to AgentCore Runtime")
    print("=" * 70)

    # Load configuration from SSM
    print("\n🔍 Loading configuration from SSM Parameter Store...")
    config = SSMConfig()

    # Validate configuration
    if not config.is_valid():
        print("\n❌ Configuration is invalid!")
        config.print_status()
        print("\n📝 Please run the setup scripts first.")
        sys.exit(1)

    print("✅ Configuration validated")

    # Print configuration summary
    config.print_status()

    try:
        # Step 1: Create IAM role
        print("\n" + "=" * 70)
        print("Step 1: Creating IAM Role")
        print("=" * 70)
        role_arn = create_runtime_role(config)

        # Step 2: Deploy to runtime
        print("\n" + "=" * 70)
        print("Step 2: Deploying to AgentCore Runtime")
        print("=" * 70)
        result = deploy_to_runtime(config, role_arn)

        # Step 3: Store runtime parameters in SSM
        print("\n" + "=" * 70)
        print("Step 3: Storing Runtime Configuration")
        print("=" * 70)
        config.store_runtime_parameters(result["runtime_arn"], result["runtime_id"])

        # Print summary
        print("\n" + "=" * 70)
        print("Deployment Complete!")
        print("=" * 70)

        print("\n✅ Runtime configuration stored in SSM Parameter Store:")
        print("   /app/lakehouse-agent/mcp-server-runtime-arn")
        print("   /app/lakehouse-agent/mcp-server-runtime-id")

        print("\n📋 Next Steps:")
        print("   1. Deploy the Gateway and Interceptor (Step 7)")
        print("   2. Deploy the Lakehouse Agent (Step 8)")
        print("   3. Test the system end-to-end")

        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ Deployment failed: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
