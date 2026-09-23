"""Deploy three PROVIDER inference targets behind one gateway.

A provider target gives explicit control over the endpoint, model mappings, and
operations (an allow-list of callable models). This script creates three:

- bedrock  (API_KEY)          : Amazon Bedrock via bedrock-mantle, using a
                                Bedrock API key (Authorization: Bearer).
- openai   (API_KEY)          : OpenAI directly (gated on OPENAI_API_KEY).
- gemini   (API_KEY)          : Google Gemini via its OpenAI-compatible endpoint
                                (gated on GEMINI_API_KEY). Gemini has no built-in
                                connector, so it must use a provider config.

With multiple targets, the gateway routes by the `model` field (see invoke.py).
The `operations` allow-list also governs which models each target may serve.

Requires GATEWAY_ID and AWS_BEARER_TOKEN_BEDROCK in environment or .env (the
Bedrock target is the core of this tutorial). openai/gemini targets are skipped
if their API key env var is not set; the gateway still works for the rest.

Usage:
    uv run python scripts/model-governance/deploy.py
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
    region = boto3.Session().region_name
    bedrock_endpoint = os.environ.get(
        "BEDROCK_MANTLE_ENDPOINT", f"https://bedrock-mantle.{region}.api.aws"
    )
    admin = GatewayBoto3Client(region=region)
    saved = {"GATEWAY_ID": gateway_id}

    # --- Grant the gateway role API-key fetch permissions ---
    # All three targets use API_KEY outbound, so the role only needs to fetch the
    # stored API keys. The Bedrock bearer token carries the Bedrock invoke/list
    # permissions itself, so no bedrock-mantle:* grant is required on the role.
    print("--- Granting outbound permissions to gateway role ---")
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    role_name = gw["roleArn"].split("/")[-1]
    admin.iam.put_role_policy(
        RoleName=role_name,
        PolicyName="InferenceOutboundPolicy",
        PolicyDocument=(
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Action":["bedrock-agentcore:GetApiKeyCredential",'
            '"bedrock-agentcore:GetResourceApiKey",'
            '"bedrock-agentcore:GetWorkloadAccessToken","secretsmanager:GetSecretValue"],'
            '"Resource":"*"}]}'
        ),
    )
    print(f"  Granted API-key fetch permissions to {role_name}")
    # Wait for IAM permissions to propagate before target discovery runs its
    # outbound calls (otherwise the target lands FAILED).
    print("  Waiting 45s for IAM propagation...")
    time.sleep(45)

    # --- 1. Bedrock provider target (API_KEY). The gateway authenticates to
    # bedrock-mantle with a Bedrock API key (Authorization: Bearer), the same
    # outbound pattern as the OpenAI target. modelMapping strips the provider
    # prefix; the operations allow-list permits only Claude + OpenAI-OSS families;
    # the /v1/messages operation overrides providerPath for Anthropic's native
    # API. ---
    bedrock_key = get_required_env("AWS_BEARER_TOKEN_BEDROCK")
    print("\n--- Creating Bedrock provider target ---")
    cred = admin.identity_client.create_api_key_credential_provider(
        name="governance-bedrock-key", apiKey=bedrock_key
    )
    saved["BEDROCK_CRED_ARN"] = cred["credentialProviderArn"]
    bedrock = admin.create_inference_target(
        gateway_id,
        name="bedrock",
        endpoint=bedrock_endpoint,
        model_mapping={"providerPrefix": {"strip": True, "separator": "."}},
        operations=[
            {
                "path": "/v1/chat/completions",
                "models": [
                    {"model": "anthropic.claude-opus-*"},
                    {"model": "anthropic.claude-sonnet-*"},
                    {"model": "openai.gpt-oss-*"},
                ],
            },
            {
                "path": "/v1/messages",
                "providerPath": "/anthropic/v1/messages",
                "models": [
                    {"model": "anthropic.claude-opus-*"},
                    {"model": "anthropic.claude-sonnet-*"},
                ],
            },
        ],
        credential_provider_type="API_KEY",
        api_key_provider_arn=cred["credentialProviderArn"],
        credential_parameter_name="Authorization",
        credential_location="HEADER",
        credential_prefix="Bearer ",
    )
    saved["BEDROCK_TARGET_ID"] = bedrock["targetId"]
    wait_ready(admin, gateway_id, bedrock["targetId"])

    # # --- 2. OpenAI provider target (API_KEY). ---
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        print("\n--- Creating OpenAI provider target ---")
        cred = admin.identity_client.create_api_key_credential_provider(
            name="governance-openai-key", apiKey=openai_key
        )
        saved["OPENAI_CRED_ARN"] = cred["credentialProviderArn"]
        openai_target = admin.create_inference_target(
            gateway_id,
            name="openai",
            endpoint="https://api.openai.com",
            operations=[
                {"path": "/v1/chat/completions", "models": [{"model": "gpt-*"}]}
            ],
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

    # --- 3. Gemini provider target (API_KEY) via Google's OpenAI-compatible
    # endpoint. Gemini has no built-in connector, so a provider config is the
    # only way to attach it. ---
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        print("\n--- Creating Gemini provider target ---")
        cred = admin.identity_client.create_api_key_credential_provider(
            name="governance-gemini-key", apiKey=gemini_key
        )
        saved["GEMINI_CRED_ARN"] = cred["credentialProviderArn"]
        gemini_target = admin.create_inference_target(
            gateway_id,
            name="google",
            endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
            # model_mapping={"providerPrefix": {"strip": True, "separator": "/"}},
            operations=[
                {
                    "path": "/v1/chat/completions",
                    "models": [{"model": "models/*"}],
                }
            ],
            credential_provider_type="API_KEY",
            api_key_provider_arn=cred["credentialProviderArn"],
            credential_parameter_name="Authorization",
            credential_location="HEADER",
            credential_prefix="Bearer",
        )
        saved["GEMINI_TARGET_ID"] = gemini_target["targetId"]
        wait_ready(admin, gateway_id, gemini_target["targetId"])
    else:
        print("\n--- Skipping Gemini target (GEMINI_API_KEY not set) ---")

    save_env(saved)
    print("\n  Saved target IDs and credential ARNs to .env")
    print("\nNext: uv run python scripts/model-governance/invoke.py")


if __name__ == "__main__":
    main()
