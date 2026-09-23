"""
Setup script: Creates a Cognito User Pool for AgentCore Runtime inbound JWT auth.

Usage:
    python setup_cognito.py

Outputs:
    cognito_config.json  - Cognito configuration used by subsequent scripts
"""

import boto3
import json
from boto3.session import Session

POOL_NAME = "RuntimeAuthDemoPool"
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

    print("Creating App Client...")
    client_resp = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{POOL_NAME}Client",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = client_resp["UserPoolClient"]["ClientId"]
    print(f"  Client ID: {client_id}")

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

    print("Verifying authentication...")
    auth = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": USERNAME, "PASSWORD": PASSWORD},
    )
    _ = auth["AuthenticationResult"]["AccessToken"]

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

    config = {
        "pool_id": pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
        "region": region,
        "username": USERNAME,
        "password": PASSWORD,
    }

    with open("cognito_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("\nCognito setup complete!")
    print("\nSave these values for Step 4 (agentcore add agent):")
    print(f"  --discovery-url    {discovery_url}")
    print(f"  --allowed-clients  {client_id}")
    print("\nConfiguration saved to cognito_config.json")

    return config


if __name__ == "__main__":
    setup_cognito()
