"""Deploy MCP server with Keycloak JWT authentication"""
import os
from dotenv import load_dotenv
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session

load_dotenv()

# Keycloak configuration
KEYCLOAK_URL = os.environ["KEYCLOAK_URL"]
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "main")
DISCOVERY_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"

boto_session = Session()
region = boto_session.region_name
runtime = Runtime()

# Custom JWT authorizer for Keycloak
# Keycloak tokens have "aud": "account" by default for public clients
auth_config = {
    "customJWTAuthorizer": {
        "discoveryUrl": DISCOVERY_URL,
        "allowedAudience": ["account"],
    }
}

print(f"Discovery URL: {DISCOVERY_URL}")
print(f"Auth config: {auth_config}")

# Configure deployment with Keycloak auth
runtime.configure(
    entrypoint="mcp_server.py",
    auto_create_execution_role=True,
    region=region,
    agent_name="mcp_keycloak_auth",
    protocol="MCP",
    memory_mode="NO_MEMORY",
    deployment_type="direct_code_deploy",
    runtime_type="PYTHON_3_13",
    authorizer_configuration=auth_config,
)

# Deploy
result = runtime.launch()
print(f"\nAgent ARN: {result.agent_arn}")
print(f"Agent ID: {result.agent_id}")
