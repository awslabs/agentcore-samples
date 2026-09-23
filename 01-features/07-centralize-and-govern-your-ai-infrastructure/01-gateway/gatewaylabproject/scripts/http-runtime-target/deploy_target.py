"""Attach an HTTP-protocol AgentCore Runtime agent to the gateway as a target.

Creates an http.agentcoreRuntime target pointing at the runtime ARN, using the
OBO (on-behalf-of) credential provider for outbound auth. The gateway exchanges
the caller's inbound Entra ID token for a token scoped to the runtime resource.

HTTP-protocol runtimes do not get a default schema (unlike MCP and A2A), so this
script attaches an OpenAPI schema inline. The schema lets the gateway enable
policy-engine features such as guardrails. Writes TARGET_ID to the script-local
.env.

Usage:
    uv run python scripts/http-runtime-target/deploy_target.py \
      --gateway-id $GATEWAY_ID \
      --runtime-arn $RUNTIME_ARN \
      --credential-provider-arn $CREDENTIAL_PROVIDER_ARN \
      --scopes "api://$MICROSOFT_RUNTIME_CLIENT_ID/.default"
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "http-runtime-target"
DEFAULT_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "agent-schema.yaml")


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
    env_vars: dict[str, str] = {}
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


def main():
    parser = argparse.ArgumentParser(
        description="Create an http.agentcoreRuntime target with OBO outbound auth"
    )
    parser.add_argument("--gateway-id", required=True, help="Gateway ID")
    parser.add_argument("--runtime-arn", required=True, help="AgentCore Runtime ARN")
    parser.add_argument(
        "--credential-provider-arn",
        required=True,
        help="OBO credential provider ARN for outbound auth",
    )
    parser.add_argument(
        "--scopes",
        default="",
        help="Comma-separated OBO scopes (e.g. api://<runtime-client-id>/.default)",
    )
    parser.add_argument(
        "--qualifier", default="DEFAULT", help="Runtime qualifier (default: DEFAULT)"
    )
    parser.add_argument(
        "--schema-file",
        default=DEFAULT_SCHEMA_FILE,
        help="Path to the OpenAPI schema for the HTTP runtime (default: agent-schema.yaml)",
    )
    args = parser.parse_args()

    load_env()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]

    # HTTP-protocol runtime targets require a schema (MCP/A2A get a default).
    # Ship it inline so the tutorial needs no S3 bucket.
    with open(args.schema_file) as f:
        schema_payload = f.read()

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    print(f"--- Creating http.agentcoreRuntime target '{TARGET_NAME}' ---")
    target = control.create_gateway_target(
        name=TARGET_NAME,
        gatewayIdentifier=args.gateway_id,
        targetConfiguration={
            "http": {
                "agentcoreRuntime": {
                    "arn": args.runtime_arn,
                    "qualifier": args.qualifier,
                    "schema": {"source": {"inlinePayload": schema_payload}},
                }
            }
        },
        credentialProviderConfigurations=[
            {
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": args.credential_provider_arn,
                        "scopes": scopes,
                        "grantType": "TOKEN_EXCHANGE",
                        "customParameters": {"requested_token_use": "on_behalf_of"},
                    }
                },
            }
        ],
    )
    target_id = target["targetId"]
    print(f"  Target ID: {target_id}")

    print("\n  Waiting for target to become READY...")
    while True:
        time.sleep(10)
        t = control.get_gateway_target(
            gatewayIdentifier=args.gateway_id, targetId=target_id
        )
        status = t["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    save_env({"TARGET_ID": target_id, "TARGET_NAME": TARGET_NAME})
    print("\n  Saved TARGET_ID to .env")


if __name__ == "__main__":
    main()
