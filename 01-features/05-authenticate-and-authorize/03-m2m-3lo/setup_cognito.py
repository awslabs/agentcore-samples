"""
Setup script: Creates a Cognito User Pool for AgentCore Runtime inbound JWT auth,
plus a Cognito domain and machine client (client credentials) for the M2M flow.

Usage:
    python setup_cognito.py

Outputs:
    cognito_config.json
"""

import re
import json
import boto3
from boto3.session import Session

POOL_NAME = "M2MAuthCodeDemoPool"
USERNAME = "testuser"
PASSWORD = "AgentCoreTest1!"  # pragma: allowlist secret
TEMP_PASSWORD = "TempPass123!"  # pragma: allowlist secret
RESOURCE_SERVER_ID = "https://api.m2m-demo.internal"


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

    # Cognito domain is required for client_credentials token endpoint
    domain_prefix = "m2m-demo-" + re.sub(r"[^a-z0-9]", "-", pool_id.lower())[:18]
    print(f"Creating Cognito domain '{domain_prefix}'...")
    cognito.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
    token_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
    print(f"  Token endpoint: {token_endpoint}")

    # Resource server defines the scopes the machine client can request
    print("Creating resource server for M2M scopes...")
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=RESOURCE_SERVER_ID,
        Name="M2MDemoAPI",
        Scopes=[{"ScopeName": "read", "ScopeDescription": "Read access"}],
    )
    m2m_scope = f"{RESOURCE_SERVER_ID}/read"

    print("Creating App Client (for user login / inbound auth)...")
    user_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{POOL_NAME}UserClient",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    user_client_id = user_client["UserPoolClient"]["ClientId"]
    print(f"  User Client ID: {user_client_id}")

    print("Creating machine client (for M2M client credentials)...")
    machine_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{POOL_NAME}MachineClient",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[m2m_scope],
        AllowedOAuthFlowsUserPoolClient=True,
    )
    machine_client_id = machine_client["UserPoolClient"]["ClientId"]
    machine_client_secret = machine_client["UserPoolClient"]["ClientSecret"]
    print(f"  Machine Client ID: {machine_client_id}")

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
    cognito.initiate_auth(
        ClientId=user_client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": USERNAME, "PASSWORD": PASSWORD},
    )

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

    config = {
        "pool_id": pool_id,
        "client_id": user_client_id,
        "discovery_url": discovery_url,
        "region": region,
        "username": USERNAME,
        "password": PASSWORD,
        "m2m_client_id": machine_client_id,
        "m2m_client_secret": machine_client_secret,
        "m2m_token_endpoint": token_endpoint,
        "m2m_scope": m2m_scope,
    }

    with open("cognito_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\nCognito setup complete!")
    print("\nSave these values for Step 6 (agentcore add agent):")
    print(f"  --discovery-url    {discovery_url}")
    print(f"  --allowed-clients  {user_client_id}")
    print("\nM2M (client credentials):")
    print(f"  tokenEndpoint: {token_endpoint}")
    print(f"  clientId     : {machine_client_id}")
    print(f"  scope        : {m2m_scope}")
    print("\nConfiguration saved to cognito_config.json")

    return config


if __name__ == "__main__":
    setup_cognito()
