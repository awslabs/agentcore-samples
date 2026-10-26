"""Deploy three inference connector targets behind one gateway.

Creates connector inference targets for Amazon Bedrock, OpenAI, and Anthropic
so a single gateway endpoint can route LLM traffic to all three providers based
on the model in the request. Bedrock uses GATEWAY_IAM_ROLE (SigV4); OpenAI and
Anthropic use API_KEY credential providers that the gateway injects outbound.

Requires GATEWAY_ID in environment or .env. OPENAI_API_KEY and ANTHROPIC_API_KEY
are required to create the OpenAI/Anthropic targets; if either is missing, that
provider is skipped (the gateway still works for the providers you configured).

Usage:
    uv run python scripts/unified-multi-provider/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


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


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


def wait_ready(admin, gateway_id, target_id):
    for _ in range(18):
        time.sleep(10)
        resp = admin.client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = resp["status"]
        print(f"    Status: {status}")
        if status in ("READY", "FAILED"):
            return status
    return "TIMEOUT"


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    # Region-specific bedrock-mantle endpoint (provider mode would need this;
    # the connector resolves it automatically, shown here for reference).
    saved: dict[str, str] = {"GATEWAY_ID": gateway_id}

    # --- Grant the gateway role permission for all configured providers ---
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
    # Wait for IAM permissions to propagate before target discovery runs the
    # outbound bedrock-mantle:ListModels call (otherwise the target lands FAILED).
    print("  Waiting 45s for IAM propagation...")
    time.sleep(45)

    # --- 1. Bedrock connector (GATEWAY_IAM_ROLE, no stored secret) ---
    print("\n--- Creating Bedrock connector target ---")
    bedrock_target = admin.create_inference_target(
        gateway_id,
        name="bedrock-mantle",
        connector_id="bedrock-mantle",
        credential_provider_type="GATEWAY_IAM_ROLE",
    )
    saved["BEDROCK_TARGET_ID"] = bedrock_target["targetId"]
    wait_ready(admin, gateway_id, bedrock_target["targetId"])

    # --- 2. OpenAI connector (API_KEY) ---
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        print("\n--- Creating OpenAI connector target ---")
        cred = admin.identity_client.create_api_key_credential_provider(
            name="unified-openai-key", apiKey=openai_key
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
    else:
        print("\n--- Skipping OpenAI target (OPENAI_API_KEY not set) ---")

    # --- 3. Anthropic connector (API_KEY) ---
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        print("\n--- Creating Anthropic connector target ---")
        cred = admin.identity_client.create_api_key_credential_provider(
            name="unified-anthropic-key", apiKey=anthropic_key
        )
        saved["ANTHROPIC_CRED_ARN"] = cred["credentialProviderArn"]
        anthropic_target = admin.create_inference_target(
            gateway_id,
            name="anthropic",
            connector_id="anthropic",
            credential_provider_type="API_KEY",
            api_key_provider_arn=cred["credentialProviderArn"],
            credential_parameter_name="x-api-key",
            credential_location="HEADER",
        )
        saved["ANTHROPIC_TARGET_ID"] = anthropic_target["targetId"]
        wait_ready(admin, gateway_id, anthropic_target["targetId"])
    else:
        print("\n--- Skipping Anthropic target (ANTHROPIC_API_KEY not set) ---")

    save_env(saved)
    print("\n  Saved target IDs and credential ARNs to .env")
    print("\nNext: uv run python scripts/unified-multi-provider/invoke.py")


if __name__ == "__main__":
    main()
