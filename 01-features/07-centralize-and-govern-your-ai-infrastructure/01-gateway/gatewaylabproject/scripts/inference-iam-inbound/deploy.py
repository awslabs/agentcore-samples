"""Deploy two inference targets behind an AWS_IAM-inbound gateway.

This is the outbound side of the IAM inbound-auth tutorial. The gateway itself is
created with AWS_IAM inbound auth (see deploy_gateway.py --authorizer-type
AWS_IAM); this script attaches the targets it fronts:

- bedrock-mantle : connector target, GATEWAY_IAM_ROLE outbound (no stored key).
- openai         : connector target, API_KEY outbound (gateway injects the key).

Inbound auth (how callers reach the gateway) is AWS SigV4 regardless of target;
see the tutorial README for the awscurl invocation.

Requires GATEWAY_ID and OPENAI_API_KEY in environment or .env.

Usage:
    uv run python scripts/inference-iam-inbound/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from env_utils import get_required_env, load_env
from gateway_admin import GatewayBoto3Client


def save_env(updates):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update(updates)
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")


def wait_ready(admin, gateway_id, target_id):
    for _ in range(18):
        time.sleep(10)
        status = admin.client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )["status"]
        print(f"    Status: {status}")
        if status in ("READY", "FAILED"):
            return status
    return "TIMEOUT"


def main():
    load_env(os.path.join(os.path.dirname(__file__), ".env"))

    gateway_id = get_required_env("GATEWAY_ID")
    openai_key = get_required_env("OPENAI_API_KEY")
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    saved = {"GATEWAY_ID": gateway_id}

    # --- Grant the gateway role outbound permissions (Bedrock + API-key fetch) ---
    print("--- Granting outbound permissions to gateway role ---")
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    role_name = gw["roleArn"].split("/")[-1]
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="InferenceOutboundPolicy",
        PolicyDocument=(
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Action":["bedrock-mantle:ListModels","bedrock-mantle:CreateInference",'
            '"bedrock-agentcore:GetApiKeyCredential","bedrock-agentcore:GetResourceApiKey",'
            '"bedrock-agentcore:GetWorkloadAccessToken","secretsmanager:GetSecretValue"],'
            '"Resource":"*"}]}'
        ),
    )
    print(f"  Granted bedrock-mantle + API-key permissions to {role_name}")
    # Wait for IAM permissions to propagate before target discovery runs its
    # outbound calls (otherwise the target lands FAILED).
    print("  Waiting 45s for IAM propagation...")
    time.sleep(45)

    # --- 1. Bedrock connector target (GATEWAY_IAM_ROLE outbound). ---
    print("\n--- Creating Bedrock connector target ---")
    bedrock = admin.create_inference_target(
        gateway_id,
        name="bedrock-mantle",
        connector_id="bedrock-mantle",
        credential_provider_type="GATEWAY_IAM_ROLE",
    )
    saved["BEDROCK_TARGET_ID"] = bedrock["targetId"]
    wait_ready(admin, gateway_id, bedrock["targetId"])

    # --- 2. OpenAI connector target (API_KEY outbound). ---
    print("\n--- Creating OpenAI connector target ---")
    cred = admin.identity_client.create_api_key_credential_provider(
        name="iam-inbound-openai-key", apiKey=openai_key
    )
    saved["OPENAI_CRED_ARN"] = cred["credentialProviderArn"]
    openai_target = admin.create_inference_target(
        gateway_id,
        name="openai",
        connector_id="openai",
        credential_provider_type="API_KEY",
        api_key_provider_arn=cred["credentialProviderArn"],
        credential_parameter_name="Authorization",
        credential_location="HEADER",
        credential_prefix="Bearer ",
    )
    saved["OPENAI_TARGET_ID"] = openai_target["targetId"]
    wait_ready(admin, gateway_id, openai_target["targetId"])

    save_env(saved)
    print("\n  Saved target IDs and credential ARN to .env")
    print("\nNext: invoke the gateway with SigV4 (awscurl). See the tutorial README.")


if __name__ == "__main__":
    main()
