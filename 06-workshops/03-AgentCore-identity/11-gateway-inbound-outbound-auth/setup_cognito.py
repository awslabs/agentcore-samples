"""
Setup script: Creates a Cognito User Pool for AgentCore Gateway inbound JWT auth.

Usage:
    python setup_cognito.py

Outputs:
    cognito_config.json  - Cognito configuration used by subsequent scripts
"""

import boto3
import json
from boto3.session import Session

POOL_NAME = "GatewayAuthDemoPool"
USERNAME = "testuser"
PASSWORD = "AgentCoreTest1!"  # pragma: allowlist secret
TEMP_PASSWORD = "TempPass123!"  # pragma: allowlist secret


def setup_cognito():
    session = Session()
    region = session.region_name
    cognito = boto3.client("cognito-idp", region_name=region)

    print("Creating Cognito User Pool...")
    pool = cognito.create_user_pool(
        PoolName=POOL_NAME,
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = pool["UserPool"]["Id"]
    print(f"  Pool ID: {pool_id}")

    # Domain is required for the client_credentials (M2M) token endpoint
    domain_prefix = f"gateway-demo-{pool_id.split('_')[1].lower()}"
    print(f"Creating Cognito domain '{domain_prefix}'...")
    cognito.create_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
    token_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
    print(f"  Token endpoint: {token_endpoint}")

    # Resource server (required for client_credentials scopes)
    print("Creating resource server...")
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier="https://gateway.demo.internal",
        Name="GatewayDemoAPI",
        Scopes=[{"ScopeName": "access", "ScopeDescription": "Gateway access"}],
    )

    print("Creating App Client (user-facing)...")
    user_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{POOL_NAME}UserClient",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    user_client_id = user_client["UserPoolClient"]["ClientId"]
    print(f"  User Client ID: {user_client_id}")

    # Agent client for authenticating with the gateway (client_credentials grant)
    print("Creating App Client (agent-facing, with client secret)...")
    agent_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{POOL_NAME}AgentClient",
        GenerateSecret=True,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=["https://gateway.demo.internal/access"],
        AllowedOAuthFlowsUserPoolClient=True,
    )
    agent_client_id = agent_client["UserPoolClient"]["ClientId"]
    agent_client_secret = agent_client["UserPoolClient"]["ClientSecret"]
    print(f"  Agent Client ID: {agent_client_id}")

    print(f"Creating test user '{USERNAME}'...")
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=USERNAME,
        TemporaryPassword=TEMP_PASSWORD,
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=pool_id,
        Username=USERNAME,
        Password=PASSWORD,
        Permanent=True,
    )

    print("Verifying user authentication...")
    auth = cognito.initiate_auth(
        ClientId=user_client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": USERNAME, "PASSWORD": PASSWORD},
    )
    _ = auth["AuthenticationResult"]["AccessToken"]

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

    config = {
        "pool_id": pool_id,
        "user_client_id": user_client_id,
        "agent_client_id": agent_client_id,
        "agent_client_secret": agent_client_secret,
        "discovery_url": discovery_url,
        "region": region,
        "username": USERNAME,
        "password": PASSWORD,
    }

    with open("cognito_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\nCognito setup complete!")
    print("\nValues for Step 3 (agentcore add gateway):")
    print(f"  --discovery-url    {discovery_url}")
    print(f"  --allowed-audience {pool_id}")
    print(f"  --allowed-clients  {user_client_id}")
    print(f"  --client-id        {agent_client_id}")
    print("  --client-secret    (saved to cognito_config.json)")
    print("\nConfiguration saved to cognito_config.json")

    return config


if __name__ == "__main__":
    setup_cognito()
