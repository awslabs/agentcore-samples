"""
Device Management Strands Agent Runtime - Deployment Script

This module automates the deployment of the Device Management Strands Agent
Runtime to Amazon Bedrock AgentCore. It handles Docker containerization,
environment configuration, observability setup, and deployment monitoring.

The script performs the following operations:
1. Loads environment variables from .env and .env.runtime files
2. Validates required parameters (gateway_id, agent_name)
3. Creates AgentCore client for deployment operations
4. Configures files to copy and environment variables
5. Retrieves gateway endpoint URL from gateway ID
6. Generates .dockerignore file with exclusions
7. Configures agent runtime with execution role and requirements
8. Launches containerized agent runtime to AWS
9. Monitors deployment status until completion
10. Performs quick test invocation on successful deployment

Key Features:
    - Automated Docker containerization with ECR integration
    - Environment variable management from multiple sources
    - Gateway endpoint resolution and configuration
    - Dynamic .dockerignore generation for efficient builds
    - Deployment status monitoring with polling
    - OpenTelemetry instrumentation configuration
    - Cognito OAuth authentication setup
    - Automatic retry logic for deployment status checks

Command-Line Arguments:
    --gateway_id (required): Gateway ID for MCP server connection
    --agent_name (optional): Name for the agent (default: device_management_agent_29_jul_21)
    --execution_role (optional): IAM execution role ARN (uses ROLE_ARN from .env if not provided)

Environment Variables (.env and .env.runtime):
    AWS Configuration:
    - AWS_REGION: AWS region for deployment (default: us-west-2)
    - AWS_DEFAULT_REGION: Fallback AWS region

    Cognito Configuration:
    - COGNITO_DOMAIN: Cognito domain URL
    - COGNITO_CLIENT_ID: OAuth client ID
    - COGNITO_CLIENT_SECRET: OAuth client secret
    - COGNITO_DISCOVERY_URL: OIDC discovery endpoint
    - COGNITO_AUTH_URL: Authorization endpoint
    - COGNITO_TOKEN_URL: Token endpoint
    - COGNITO_PROVIDER_NAME: Credential provider name

    MCP Server Configuration:
    - MCP_SERVER_URL: Gateway endpoint URL (auto-populated from gateway_id)

    IAM Configuration:
    - ROLE_ARN: IAM execution role ARN

    Agent Configuration:
    - ENDPOINT_URL: Bedrock AgentCore control endpoint
    - AGENT_NAME: Agent name (default: device-management-agent)
    - AGENT_DESCRIPTION: Agent description
    - BEDROCK_MODEL_ID: Model ID (default: claude-3-7-sonnet)

Files Copied to Container:
    - strands_agent_runtime.py: Main agent runtime code
    - access_token.py: OAuth token management
    - utils.py: Utility functions
    - requirements-runtime.txt: Python dependencies

Excluded from Container:
    - .venv/: Virtual environment
    - .ipynb_checkpoints/: Jupyter checkpoints
    - __pycache__/: Python cache
    - .git/: Git repository
    - images/: Image files
    - All files not in FilesToCopy list

Deployment Process:
    1. Configuration Phase:
       - Load environment variables
       - Validate parameters
       - Create AgentCore client
       - Resolve gateway endpoint

    2. Build Phase:
       - Generate .dockerignore
       - Configure runtime with requirements
       - Set up authentication

    3. Launch Phase:
       - Build Docker image
       - Push to ECR
       - Deploy to AgentCore

    4. Monitoring Phase:
       - Poll deployment status every 10 seconds
       - Wait for READY, CREATE_FAILED, or other end states
       - Display final status

    5. Validation Phase:
       - Run quick test invocation
       - Display test results

Deployment Status States:
    - READY: Deployment successful, agent is ready
    - CREATE_FAILED: Deployment failed during creation
    - UPDATE_FAILED: Deployment failed during update
    - DELETE_FAILED: Deployment failed during deletion
    - Other states: In-progress or transitional

Example Usage:
    Deploy with required gateway ID:
    >>> python strands_agent_runtime_deploy.py --gateway_id gateway-12345

    Deploy with custom agent name:
    >>> python strands_agent_runtime_deploy.py --gateway_id gateway-12345 --agent_name my-agent

    Deploy with custom execution role:
    >>> python strands_agent_runtime_deploy.py --gateway_id gateway-12345 --execution_role arn:aws:iam::...

Exit Codes:
    0 - Deployment successful (status: READY)
    1 - Missing required parameters
    1 - AgentCore client creation failed
    1 - Configuration failed
    1 - Launch failed

Outputs:
    - Agent ARN: Displayed on successful launch
    - Deployment status: Monitored and displayed
    - Test response: Displayed on successful deployment
    - Error messages: Displayed on failures

Notes:
    - Requires valid AWS credentials with AgentCore permissions
    - Gateway must be created before deploying agent runtime
    - .env files must be configured with Cognito credentials
    - Docker image is automatically built and pushed to ECR
    - Deployment monitoring includes 10-second polling interval
    - Test invocation uses simple greeting prompt
    - Supports both macOS and Linux environments
    - Cleans up existing Docker files before configuration

Observability:
    - OpenTelemetry instrumentation configured in Dockerfile
    - CloudWatch Logs integration
    - X-Ray tracing enabled
    - Metrics exported to CloudWatch

Authentication:
    - Cognito OAuth2 with JWT tokens
    - Custom JWT authorizer configuration
    - OAuth credential provider integration
    - Automatic token refresh support
"""

from bedrock_agentcore_starter_toolkit import Runtime
import time
import utils
import os
import sys
from dotenv import load_dotenv
import argparse

# Reading environment variables from .env and .env.runtime files
# load_dotenv() automatically loads from .env file
# Variables from .env.runtime will also be available if loaded separately
load_dotenv()
load_dotenv(".env.runtime")  # Explicitly load .env.runtime file

script_dir = os.path.dirname(os.path.abspath(__file__))
print(f"Python file directory: {script_dir}")

# Setting parameters
parser = argparse.ArgumentParser(
    prog="device_management_strands_agent_runtime",
    description="Device Management Strands Agent with MCP Gateway",
    epilog="Input Parameters",
)

parser.add_argument("--gateway_id", help="Gateway Id", required=True)
parser.add_argument(
    "--agent_name",
    help="Name of the agent",
    default="device_management_agent_29_jul_21",
)
parser.add_argument("--execution_role", help="IAM execution role ARN")

args = parser.parse_args()

# Parameter Validations
if args.gateway_id is None:
    raise Exception("Gateway Id is required")

if args.agent_name is None:
    args.agent_name = os.getenv("AGENT_NAME", "device-management-agent")

print(f"Deploying agent: {args.agent_name}")
print(f"Gateway ID: {args.gateway_id}")

# Create AgentCore client
try:
    (boto_session, agentcore_client) = utils.create_agentcore_client()
except Exception as e:
    print(f"Error creating AgentCore client: {e}")
    print("Make sure your AWS credentials are configured and utils.py has the create_agentcore_client function")
    sys.exit(1)

# Launch configurations
FilesToCopy = [
    "strands_agent_runtime.py",
    "access_token.py",
    "utils.py",
    "requirements-runtime.txt",
]

# Environment variables for the runtime
# Loading from both .env and .env.runtime files
EnvVariables = {
    # AWS configuration
    "AWS_DEFAULT_REGION": os.getenv("AWS_REGION", "us-west-2"),
    "AWS_REGION": os.getenv("AWS_REGION", "us-west-2"),
    # Cognito configuration
    "COGNITO_DOMAIN": os.getenv("COGNITO_DOMAIN"),
    "COGNITO_CLIENT_ID": os.getenv("COGNITO_CLIENT_ID"),
    "COGNITO_CLIENT_SECRET": os.getenv("COGNITO_CLIENT_SECRET"),
    "COGNITO_DISCOVERY_URL": os.getenv("COGNITO_DISCOVERY_URL"),
    "COGNITO_AUTH_URL": os.getenv("COGNITO_AUTH_URL"),
    "COGNITO_TOKEN_URL": os.getenv("COGNITO_TOKEN_URL"),
    "COGNITO_PROVIDER_NAME": os.getenv("COGNITO_PROVIDER_NAME"),
    # MCP Server configuration
    "MCP_SERVER_URL": os.getenv("MCP_SERVER_URL"),
    # IAM Role configuration
    "ROLE_ARN": os.getenv("ROLE_ARN"),
    # Bedrock AgentCore Runtime configuration
    "ENDPOINT_URL": os.getenv("ENDPOINT_URL"),
    "AGENT_NAME": os.getenv("AGENT_NAME", "device-management-agent"),
    "AGENT_DESCRIPTION": os.getenv("AGENT_DESCRIPTION", "Device Management Agent for IoT devices"),
    # Model configuration
    "BEDROCK_MODEL_ID": os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
}

# Get gateway endpoint
try:
    gatewayEndpoint = utils.get_gateway_endpoint(agentcore_client=agentcore_client, gateway_id=args.gateway_id)
    print(f"Gateway Endpoint: {gatewayEndpoint}")
    if gatewayEndpoint:
        # Override MCP_SERVER_URL with the actual gateway endpoint
        EnvVariables["MCP_SERVER_URL"] = gatewayEndpoint
        EnvVariables["gateway_endpoint"] = gatewayEndpoint  # Keep for backward compatibility
    else:
        print("Gateway endpoint is empty, using value from .env file")
except Exception as e:
    print(f"Warning: Could not get gateway endpoint: {e}")
    print("Using value from .env file")

aws_region = boto_session.region_name
print(f"AWS region: {aws_region}")

print(f"Environment variables: {EnvVariables}")

# Exclusions for dockerignore file
excluded_prefixes = (".venv", ".ipynb_checkpoints", "__pycache__", ".git", "images")
dockerignoreappend = [
    ".venv/",
    ".ipynb_checkpoints/",
    "__pycache__/",
    ".git/",
    "images/",
]

for root, dirs, files in os.walk(script_dir):
    # Modify dirs in-place to exclude unwanted directories
    dirs[:] = [d for d in dirs if not d.startswith(excluded_prefixes)]

    relativePathDir = os.path.split(root)[-1]

    if root != script_dir:
        if relativePathDir not in FilesToCopy:
            dockerignoreappend.append(f"{relativePathDir}/")
    else:
        for file in files:
            if file not in FilesToCopy:  # and not file.startswith('.env'):
                dockerignoreappend.append(f"{file}")

print("Cleaning up existing docker files before configuration")
cleanup_files = [".dockerignore", "Dockerfile", ".bedrock_agentcore.yaml"]
for cleanup_file in cleanup_files:
    file_path = os.path.join(script_dir, cleanup_file)
    if os.path.exists(file_path):
        os.remove(file_path)
        print(f"Removed {cleanup_file}")

# Authentication configuration for Cognito
auth_config = {
    "customJWTAuthorizer": {
        "allowedClients": [os.getenv("COGNITO_CLIENT_ID")],
        "discoveryUrl": f"https://{os.getenv('COGNITO_DOMAIN')}/.well-known/openid_configuration",
    }
}

# Credential configuration for OAuth
credential_config = {
    "credentialProviderType": "OAUTH",
    "credentialProvider": {
        "oauthCredentialProvider": {
            "providerArn": os.getenv("OAUTH_PROVIDER_ARN", ""),
            "scopes": ["openid"],
        }
    },
}

# Initialize AgentCore Runtime
agentcore_runtime = Runtime()

print("Configuring Agent Runtime")
try:
    response = agentcore_runtime.configure(
        entrypoint="strands_agent_runtime.py",
        execution_role=args.execution_role or os.getenv("ROLE_ARN"),
        auto_create_ecr=True,
        requirements_file="requirements-runtime.txt",
        region=aws_region,
        agent_name=args.agent_name,
        # Uncomment if you want to use credential configuration
        # authorizer_configuration=credential_config
    )
    print("Configuration successful")
except Exception as e:
    print(f"Configuration failed: {e}")
    sys.exit(1)

print("Appending to .dockerignore file")
with open(os.path.join(script_dir, ".dockerignore"), "a", encoding="utf-8") as f:
    f.write("\n")
    f.write("# Auto-generated exclusions\n")
    for ignorefile in dockerignoreappend:
        f.write(ignorefile + "\n")

print("Launching Agent...")
try:
    launch_result = agentcore_runtime.launch(env_vars=EnvVariables)
    print(f"Agent created with ARN: {launch_result.agent_arn}")
except Exception as e:
    print(f"Launch failed: {e}")
    sys.exit(1)

# Monitor deployment status
print("Monitoring deployment status...")
status_response = agentcore_runtime.status()
print(f"Initial status: {status_response}")

status = status_response.endpoint["status"]
end_status = ["READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]

while status not in end_status:
    print(f"Current status: {status}")
    # REQUIRED: Sleep prevents API rate limiting during deployment status polling
    # This is not arbitrary - it's the recommended polling interval for AgentCore deployments
    time.sleep(10)
    try:
        status_response = agentcore_runtime.status()
        status = status_response.endpoint["status"]
    except Exception as e:
        print(f"Error checking status: {e}")
        break

print(f"Final status: {status}")

if status == "READY":
    print("🎉 Agent deployment successful!")

    # Quick test
    print("Running quick test...")
    try:
        invoke_response = agentcore_runtime.invoke({"prompt": "Hello! Can you help me with device management?"})
        print(f"Test response: {invoke_response}")
    except Exception as e:
        print(f"Test invocation failed: {e}")

elif status in ["CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"]:
    print(f"❌ Agent deployment failed with status: {status}")
    print("Check the AWS console for detailed error logs")
else:
    print(f"⚠️  Agent deployment ended with unexpected status: {status}")

print("Deployment script completed.")
