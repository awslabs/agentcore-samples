"""Create API Gateway targets for the PetStore tutorial.

Creates two targets:
  1. IAM-authorized target for /pets and /pets/{petId}
  2. API Key-authorized target for /orders/{orderId}

API Gateway does not preserve operationId in exported specs, so toolOverrides
are required to name the tools.

Requires API_ID, API_KEY_VALUE, GATEWAY_ID in environment or .env.

Usage:
    uv run python scripts/api-gateway/deploy_targets.py
"""

import json
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def grant_execute_api_permission(
    admin: GatewayBoto3Client, gateway_id: str, api_id: str, region: str
):
    """Add execute-api:Invoke to the gateway role for API Gateway targets using GATEWAY_IAM_ROLE."""
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    role_arn = gw["roleArn"]
    role_name = role_arn.split("/")[-1]

    api_arn = f"arn:aws:execute-api:{region}:{admin.account_id}:{api_id}/*"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "execute-api:Invoke",
                "Resource": api_arn,
            }
        ],
    }
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="ApiGatewayInvokePolicy",
        PolicyDocument=json.dumps(policy),
    )
    print(f"  Granted execute-api:Invoke on {api_arn} to {role_name}")


def grant_api_key_permissions(admin: GatewayBoto3Client, gateway_id: str, region: str):
    """Add API key credential permissions to the gateway role."""
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    role_name = gw["roleArn"].split("/")[-1]
    gateway_arn = f"arn:aws:bedrock-agentcore:{region}:{admin.account_id}:gateway/*"
    identity_arn = f"arn:aws:bedrock-agentcore:{region}:{admin.account_id}:workload-identity-directory/default"

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetApiKeyCredential",
                    "bedrock-agentcore:GetResourceApiKey",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "secretsmanager:GetSecretValue",
                ],
                "Resource": [gateway_arn, identity_arn, "*"],
            }
        ],
    }
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="ApiKeyCredentialPolicy",
        PolicyDocument=json.dumps(policy),
    )
    print(f"  Granted API key credential permissions to {role_name}")


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def main():
    load_env()

    region = boto3.Session().region_name
    api_id = get_required_env("API_ID")
    api_key_value = get_required_env("API_KEY_VALUE")
    gateway_id = get_required_env("GATEWAY_ID")

    admin = GatewayBoto3Client(region=region)

    print("--- Creating Target 1: IAM-authorized /pets endpoints ---")
    target1 = admin.client.create_gateway_target(
        name="api-gateway-target-pets",
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "mcp": {
                "apiGateway": {
                    "restApiId": api_id,
                    "stage": "dev",
                    "apiGatewayToolConfiguration": {
                        "toolFilters": [
                            {"filterPath": "/pets", "methods": ["GET", "POST"]},
                            {"filterPath": "/pets/{petId}", "methods": ["GET"]},
                        ],
                        "toolOverrides": [
                            {
                                "name": "ListPets",
                                "path": "/pets",
                                "method": "GET",
                                "description": "List all available pets",
                            },
                            {
                                "name": "AddPet",
                                "path": "/pets",
                                "method": "POST",
                                "description": "Add a new pet",
                            },
                            {
                                "name": "GetPetById",
                                "path": "/pets/{petId}",
                                "method": "GET",
                                "description": "Get pet details by ID",
                            },
                        ],
                    },
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    print(f"  Target 1 ID: {target1['targetId']}")

    print("\n--- Granting execute-api:Invoke to gateway role ---")
    grant_execute_api_permission(admin, gateway_id, api_id, region)

    print("\n--- Creating API Key credential provider ---")
    cred = admin.client.create_api_key_credential_provider(
        name="apigw-orders-api-key",
        apiKey=api_key_value,
    )
    cred_arn = cred["credentialProviderArn"]
    print(f"  Credential ARN: {cred_arn}")

    print("\n--- Creating Target 2: API Key-authorized /orders endpoint ---")
    target2 = admin.client.create_gateway_target(
        name="api-gateway-target-orders",
        gatewayIdentifier=gateway_id,
        targetConfiguration={
            "mcp": {
                "apiGateway": {
                    "restApiId": api_id,
                    "stage": "dev",
                    "apiGatewayToolConfiguration": {
                        "toolFilters": [
                            {"filterPath": "/orders/{orderId}", "methods": ["GET"]},
                        ],
                        "toolOverrides": [
                            {
                                "name": "GetOrderById",
                                "path": "/orders/{orderId}",
                                "method": "GET",
                                "description": "Get order details by ID",
                            },
                        ],
                    },
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "API_KEY",
                "credentialProvider": {
                    "apiKeyCredentialProvider": {
                        "providerArn": cred_arn,
                        "credentialParameterName": "x-api-key",
                        "credentialLocation": "HEADER",
                    }
                },
            }
        ],
    )
    print(f"  Target 2 ID: {target2['targetId']}")

    print("\n--- Granting API key credential permissions to gateway role ---")
    grant_api_key_permissions(admin, gateway_id, region)

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars["GATEWAY_ID"] = gateway_id
    env_vars["CREDENTIAL_PROVIDER_ARN"] = cred_arn
    env_vars["TARGET1_ID"] = target1["targetId"]
    env_vars["TARGET2_ID"] = target2["targetId"]
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print("\n  Saved to .env")


if __name__ == "__main__":
    main()
