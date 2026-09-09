#!/usr/bin/env python3
"""
Deploy MCP OpenSearch Server to AgentCore Runtime

This script deploys the OpenSearch_MCP_Server to Amazon Bedrock AgentCore
Runtime using the Bedrock AgentCore Starter Toolkit. The server provides
free-text search over claim-note documents with per-user row-level security
via a query-time `owner_user_sub` term filter.

Prerequisites:
- AWS credentials configured
- Docker running
- AOSS collection provisioned (see deployment/5b-obo-gateway-setup/01_*.py)
- Configuration in SSM Parameter Store
- bedrock-agentcore-starter-toolkit installed

Usage:
    python deploy_runtime.py
"""

import json
import os
import sys

import boto3

# Make the repo's utils/ importable (idp_config lives there).
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

        # Load configuration from SSM
        # AOSS collection endpoint + ARN are provisioned upstream by
        # deployment/5b-obo-gateway-setup/01_deploy_opensearch_collection.py;
        # this runtime is read-only against that collection.
        self.opensearch_collection_endpoint = self._get_parameter("/app/lakehouse-agent/opensearch-collection-endpoint")
        self.opensearch_collection_arn = self._get_parameter("/app/lakehouse-agent/opensearch-collection-arn")

        # IdP selector — read once (DR-8). The runtime authorizer must validate
        # the GW2 gateway's M2M token: Okta = audience-validated OBO leg; Cognito =
        # M2M client_credentials (validate by client_id). DR-9 requires this branch
        # so the Cognito GW2 interceptor gateway can reach this runtime.
        self.idp_provider = get_idp_provider(self.ssm)
        if self.idp_provider == "okta":
            # [OKTA] custom-auth-server discovery + audience
            self.okta_org_url = self._get_parameter("/app/lakehouse-agent/okta-org-url")
            self.okta_auth_server_id = self._get_parameter("/app/lakehouse-agent/okta-auth-server-id")
            self.okta_resource_server_audience = self._get_parameter(
                "/app/lakehouse-agent/okta-resource-server-audience"
            )
            self.okta_discovery_url = self._get_parameter("/app/lakehouse-agent/okta-discovery-url")
        else:  # cognito
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
        return all([self.opensearch_collection_endpoint, self.opensearch_collection_arn, self.region, self.account_id])

    def print_status(self):
        """Print configuration status."""
        print("\n📋 Configuration Status:")
        print(f"   AWS Account: {self.account_id}")
        print(f"   Region: {self.region}")
        print(f"   OpenSearch Collection Endpoint: {self.opensearch_collection_endpoint}")
        print(f"   OpenSearch Collection ARN: {self.opensearch_collection_arn}")
        print(f"   IdP: {self.idp_provider}")
        if self.idp_provider == "okta":
            print(f"   Okta Org URL: {self.okta_org_url}")
            print(f"   Okta Auth Server ID: {self.okta_auth_server_id}")
            print(f"   Okta Resource Server Audience: {self.okta_resource_server_audience}")
        else:  # cognito
            print(f"   Cognito User Pool ARN: {self.cognito_user_pool_arn}")
        print(f"   Log Level: {self.log_level}")

    def store_runtime_parameters(self, runtime_arn: str, runtime_id: str):
        """Store OpenSearch MCP server runtime information in SSM Parameter Store."""
        print("\n💾 Storing runtime configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/opensearch-mcp-runtime-arn",
                "value": runtime_arn,
                "description": "OpenSearch MCP Server runtime ARN on AgentCore",
            },
            {
                "name": "/app/lakehouse-agent/opensearch-mcp-runtime-id",
                "value": runtime_id,
                "description": "OpenSearch MCP Server runtime ID on AgentCore",
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

    role_name = "AgentCoreRuntimeRole-opensearch-mcp"

    # Trust policy for AgentCore Runtime
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole"}
        ],
    }

    # Permissions policy.
    # Broad data-plane permission on the AOSS collection
    # ARN (aoss:APIAccessAll); read-only is enforced later by the collection's
    # data-access policy provisioned in 5b-obo-gateway-setup. No Athena/Glue/
    # S3/LakeFormation/Bedrock-runtime/marketplace permissions — this runtime
    # only talks to AOSS for query-time RLS.
    statements = [
        {"Effect": "Allow", "Action": ["aoss:APIAccessAll"], "Resource": config.opensearch_collection_arn},
        {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], "Resource": "*"},
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
            Description="AgentCore Runtime execution role for OpenSearch claim-notes MCP server",
            Tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "opensearch-mcp-role"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        print(json.dumps(permissions_policy))
        # Attach inline policy
        iam.put_role_policy(
            RoleName=role_name, PolicyName="AgentCoreRuntimePermissions", PolicyDocument=json.dumps(permissions_policy)
        )

        print(f"✅ Created IAM role: {role_arn}")
        return role_arn

    except iam.exceptions.EntityAlreadyExistsException:
        # Idempotent in-place update — preserves any out-of-band attachments.
        # Re-asserts this script's own trust policy + the 'AgentCoreRuntimePermissions'
        # inline policy (overwriting any hand-edits to *those two* on re-run, by design),
        # while leaving every OTHER attachment untouched: other inline policies,
        # managed-policy attachments, and instance-profile memberships. No detach-all,
        # no delete_role, no sleep. (Mirrors the 5a/5b in-place role-update fix.)
        print(f"ℹ️  Role {role_name} already exists — updating in place (preserving any out-of-band attachments)")

        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

        # Repair the trust policy in place (no delete).
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))

        # Upsert ONLY our known inline policy; put_role_policy overwrites an
        # existing PolicyName, so this is a safe in-place update.
        iam.put_role_policy(
            RoleName=role_name, PolicyName="AgentCoreRuntimePermissions", PolicyDocument=json.dumps(permissions_policy)
        )

        print(f"✅ Updated existing IAM role in place: {role_arn}")
        return role_arn


def deploy_to_runtime(config: SSMConfig, role_arn: str):
    """Deploy MCP server to AgentCore Runtime using starter toolkit."""
    runtime_name = "opensearch_mcp_server"  # Must use underscores, not hyphens

    try:
        print("\n🚀 Deploying OpenSearch MCP server to AgentCore Runtime...")
        print(f"   Name: {runtime_name}")
        print(f"   Region: {config.region}")
        print("   This will build a Docker container and deploy it...")

        # Build environment variables
        env_vars = {
            "AWS_REGION": config.region,
            "OPENSEARCH_COLLECTION_ENDPOINT": config.opensearch_collection_endpoint,
            "LOG_LEVEL": config.log_level,
            # DR-9: the server reads IDP_PROVIDER to pick its identity source
            # (Cognito interceptor-injected context.user_id vs Okta bearer sub).
            "IDP_PROVIDER": config.idp_provider,
        }

        print("\n📋 Environment variables:")
        for key, value in env_vars.items():
            print(f"   {key}: {value}")

        # Initialize Runtime from starter toolkit
        agentcore_runtime = Runtime()

        # Configure the runtime
        print("\n🔧 Configuring AgentCore Runtime...")

        # Extract role name from ARN (format: arn:aws:iam::account:role/RoleName)
        role_name = role_arn.split("/")[-1]

        # JWT authorizer for the GW2 gateway's M2M token (DR-8/DR-9). Cognito access
        # tokens carry no `aud` → validate by M2M client_id; Okta tokens carry `aud`.
        print("\n🔐 JWT Authentication Configuration:")
        if config.idp_provider == "cognito":
            # [COGNITO] M2M client_credentials from the gateway; validate by client_id
            user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
            issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
            discovery_url = f"{issuer}/.well-known/openid-configuration"
            m2m_client_id = config.ssm.get_parameter(Name="/app/lakehouse-agent/cognito-m2m-client-id")["Parameter"][
                "Value"
            ]
            print(f"   Discovery URL: {discovery_url}")
            print(f"   Allowed Clients: {m2m_client_id} (M2M)")
            auth_config = {"customJWTAuthorizer": {"allowedClients": [m2m_client_id], "discoveryUrl": discovery_url}}
        else:  # okta
            discovery_url = config.okta_discovery_url
            allowed_audience = [config.okta_resource_server_audience]
            print(f"   Discovery URL: {discovery_url}")
            print("   Allowed Audience:")
            print(f"      - {config.okta_resource_server_audience}")
            auth_config = {"customJWTAuthorizer": {"allowedAudience": allowed_audience, "discoveryUrl": discovery_url}}

        # The runtime authorizer validates inbound JWTs (security gate) but does
        # NOT forward the validated Authorization header to user code by default.
        # requestHeaderAllowlist makes the header readable via FastMCP's
        # ctx.request_context.request.headers — load-bearing for this
        # OpenSearch_MCP_Server, which extracts `sub` from the validated
        # header (no interceptor injects user identity on the OBO path).
        # Note: this config is accepted by the toolkit and persisted to
        # .bedrock_agentcore.yaml, but does NOT surface via get_agent_runtime;
        # a data-path test (issuing a request that reads the header) is the
        # only way to confirm it actually applies.
        request_header_config = {"requestHeaderAllowlist": ["Authorization"]}

        # Note: Environment variables are read from SSM Parameter Store by
        # the MCP server. The starter toolkit will package the entire
        # directory.
        agentcore_runtime.configure(
            entrypoint="server.py",
            execution_role=role_name,  # Use role name, not ARN
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            region=config.region,
            protocol="MCP",
            agent_name=runtime_name,
            authorizer_configuration=auth_config,
            request_header_configuration=request_header_config,
        )
        print("✅ Configuration complete with JWT authentication")

        # Launch the runtime (builds Docker image and deploys)
        print("\n🚀 Launching to AgentCore Runtime...")
        print("   This may take several minutes...")
        launch_result = agentcore_runtime.launch(env_vars=env_vars)

        runtime_arn = launch_result.agent_arn
        runtime_id = launch_result.agent_id

        print("\n✅ OpenSearch MCP Server deployed successfully!")
        print(f"   Runtime ARN: {runtime_arn}")
        print(f"   Runtime ID: {runtime_id}")

        # Tag the runtime (post-launch). The starter toolkit's configure()/launch()
        # do not surface a tags= kwarg, so apply the Application/Purpose tags via the
        # control-plane TagResource on the returned runtime ARN (mirrors the
        # Application-tag convention applied at create time on the other net-new
        # resources). Fail-soft: tags are inventory/cost-allocation metadata, not
        # load-bearing for the deployment.
        try:
            boto3.client("bedrock-agentcore-control", region_name=config.region).tag_resource(
                resourceArn=runtime_arn,
                tags={"Application": "lakehouse-agent", "Purpose": "opensearch-mcp"},
            )
            print("   🏷️  Tagged runtime: Application=lakehouse-agent, Purpose=opensearch-mcp")
        except Exception as tag_err:
            print(f"   ⚠️  Could not tag runtime (non-fatal): {tag_err}")

        return {"runtime_arn": runtime_arn, "runtime_id": runtime_id, "role_arn": role_arn}

    except Exception as e:
        print(f"\n❌ Error deploying runtime: {e!s}")
        import traceback

        traceback.print_exc()
        raise


def main():
    """Main deployment function."""
    print("=" * 70)
    print("OpenSearch MCP Server Deployment to AgentCore Runtime")
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
        print("   /app/lakehouse-agent/opensearch-mcp-runtime-arn")
        print("   /app/lakehouse-agent/opensearch-mcp-runtime-id")

        print("\n📋 Next Steps:")
        print("   1. Load sample claim-note documents (load_sample_opensearch_data.py)")
        print("   2. Deploy the OBO Gateway (deployment/5b-obo-gateway-setup/)")
        print("   3. Deploy the Lakehouse Agent (06-deploy-agent.ipynb) with two-MCPClient routing")

        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ Deployment failed: {e!s}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
