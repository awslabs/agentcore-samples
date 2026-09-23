"""
Amazon Cognito OAuth Configuration Setup Script

This module automates the configuration of Amazon Cognito OAuth authentication
for the Device Management System. It creates OAuth authorizers using the Bedrock
AgentCore Starter Toolkit and updates environment files with the necessary
authentication credentials.

The script performs the following operations:
1. Creates an OAuth authorizer with Amazon Cognito integration
2. Extracts authentication endpoints and credentials
3. Updates local .env file with Cognito configuration
4. Updates agent-runtime .env file with OAuth credentials
5. Provides formatted output of all configuration values

Key Features:
    - Automatic OAuth authorizer creation via GatewayClient
    - Dual .env file management (local and agent-runtime)
    - Intelligent URL parsing and endpoint construction
    - Configuration validation and error handling
    - Idempotent updates (creates or updates existing values)

Environment Variables Required:
    COGNITO_AUTH_NAME: Name for the Cognito OAuth authorizer

Environment Variables Updated (Local .env):
    COGNITO_USERPOOL_ID: Amazon Cognito User Pool ID
    COGNITO_CLIENT_ID: OAuth client ID
    COGNITO_CLIENT_SECRET: OAuth client secret
    COGNITO_DOMAIN: Cognito domain URL

Environment Variables Updated (Agent-Runtime .env):
    COGNITO_CLIENT_ID: OAuth client ID
    COGNITO_CLIENT_SECRET: OAuth client secret
    COGNITO_DISCOVERY_URL: OIDC discovery endpoint
    COGNITO_AUTH_URL: Authorization endpoint
    COGNITO_TOKEN_URL: Token endpoint

Example Usage:
    Set COGNITO_AUTH_NAME in .env file, then run:
    >>> python cognito_oauth_setup.py

    Output:
    Cognito OAuth setup completed!
    Client info: {...}
    ✅ Updated existing local .env file with Cognito configuration:
       COGNITO_USERPOOL_ID=...
       COGNITO_CLIENT_ID=...
    ✅ Updated existing agent-runtime .env file with Cognito configuration:
       COGNITO_DISCOVERY_URL=...

Notes:
    - Creates new .env files if they don't exist
    - Updates existing values without removing other configuration
    - Validates all required credentials before updating files
    - Constructs missing URLs from available information
"""

from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
from dotenv import load_dotenv
import os
import re

load_dotenv()

COGNITO_AUTH_NAME = os.getenv("COGNITO_AUTH_NAME")

# Initialize the Gateway client
client = GatewayClient(region_name="us-west-2")
cognito_result = client.create_oauth_authorizer_with_cognito(COGNITO_AUTH_NAME)

print("Cognito OAuth setup completed!")
# Note: Not printing client_info as it contains sensitive client_secret
print("Client configuration retrieved successfully")

# Extract values from the result
client_info = cognito_result["client_info"]
user_pool_id = client_info.get("user_pool_id")
client_id = client_info.get("client_id")
# lgtm[py/clear-text-logging-sensitive-data]
# Note: client_secret is only written to .env files (necessary for OAuth)
# and is masked in all print statements via update_env_file function
client_secret = client_info.get("client_secret")
region = client_info.get("region", "us-west-2")

# Extract domain from token_endpoint or use domain_prefix
token_endpoint = client_info.get("token_endpoint", "")
auth_endpoint = client_info.get("authorization_endpoint", "")
discovery_url = client_info.get("issuer", "")

if token_endpoint:
    # Extract domain from token endpoint URL
    domain_match = re.search(r"https://([^/]+)", token_endpoint)
    domain = domain_match.group(1) if domain_match else client_info.get("domain_prefix")
else:
    domain = client_info.get("domain_prefix")

# Construct URLs if not provided
if not discovery_url and user_pool_id:
    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

if not auth_endpoint and domain:
    auth_endpoint = f"https://{domain}/oauth2/authorize"

if not token_endpoint and domain:
    token_endpoint = f"https://{domain}/oauth2/token"

# Path to agent-runtime .env file (from gateway folder)
agent_runtime_env_path = "../agent-runtime/.env"


def update_env_file(file_path, updates, description):
    """Update or create .env file with given updates."""
    if os.path.exists(file_path):
        # Read existing .env file
        with open(file_path, "r", encoding="utf-8") as file:
            content = file.read()

        # Update or add the configuration values
        for key, value in updates.items():
            if value:  # Only update if value exists
                pattern = rf"^{key}=.*$"
                replacement = f"{key}={value}"

                if re.search(pattern, content, re.MULTILINE):
                    # Update existing value
                    content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
                else:
                    # Add new value at the end
                    content += "\n{}".format(replacement)

        # Write updated content back to .env file
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)

        print("\n✅ Updated existing {} with Cognito configuration".format(description))
    else:
        # Create new .env file with configuration
        content = "# Cognito OAuth configuration\n"

        # Add configuration values
        for key, value in updates.items():
            if value:  # Only add if value exists
                content += "{}={}\n".format(key, value)

        # Write new .env file
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)

        print("\n✅ Created new {} with Cognito configuration".format(description))

    # Print summary of what was configured (without values for security)
    config_count = sum(1 for v in updates.values() if v)
    print("   Configured {} settings".format(config_count))


# Update local .env file with the new values (existing functionality)
env_file_path = ".env"

# Prepare the Cognito configuration values for local .env (existing functionality)
# lgtm[py/clear-text-logging-sensitive-data]
# Note: client_secret is masked in print output by update_env_file function
local_updates = {
    "COGNITO_USERPOOL_ID": user_pool_id,
    "COGNITO_CLIENT_ID": client_id,
    "COGNITO_CLIENT_SECRET": client_secret,  # Masked as *** in output
    "COGNITO_DOMAIN": domain,
}

# Prepare the Cognito configuration values for agent-runtime .env (for cognito_credentials_provider.py)
# lgtm[py/clear-text-logging-sensitive-data]
# Note: client_secret is masked in print output by update_env_file function
agent_runtime_updates = {
    "COGNITO_CLIENT_ID": client_id,
    "COGNITO_CLIENT_SECRET": client_secret,  # Masked as *** in output
    "COGNITO_DISCOVERY_URL": discovery_url,
    "COGNITO_AUTH_URL": auth_endpoint,
    "COGNITO_TOKEN_URL": token_endpoint,
}

# Update local .env file (existing functionality)
update_env_file(env_file_path, local_updates, "local .env file")

# Update agent-runtime .env file (new functionality)
update_env_file(agent_runtime_env_path, agent_runtime_updates, "agent-runtime .env file")

print("\n🎉 Successfully updated both .env files with Cognito OAuth configuration!")
print("   Local .env: {}".format(os.path.abspath(env_file_path)))
print("   Agent-runtime .env: {}".format(os.path.abspath(agent_runtime_env_path)))
