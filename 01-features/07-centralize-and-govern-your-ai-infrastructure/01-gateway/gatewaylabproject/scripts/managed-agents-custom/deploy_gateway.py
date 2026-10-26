"""Create (or reuse) the shared no-protocol-type gateway for HTTP targets.

HTTP passthrough targets attach to a gateway that has no protocol type set, so
this script creates the gateway directly via boto3 with inbound CUSTOM_JWT auth.
The gateway is shared with the A2A and HTTP runtime-agent labs, so if it already
exists this script reuses it. Writes GATEWAY_ID and GATEWAY_URL to the
script-local .env.

Usage:
    uv run python scripts/managed-agents-custom/deploy_gateway.py \
      --discovery-url "https://login.microsoftonline.com/$MICROSOFT_TENANT_ID/.well-known/openid-configuration" \
      --allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

# Shared across the A2A, HTTP runtime-agent, and Claude Managed Agents labs:
# all attach their target to the same gateway, so this name must match across
# the deploy/cleanup scripts in each lab.
GATEWAY_NAME = "runtime-agents-gateway"


def find_gateway_by_name(control, name):
    """Return (gatewayId, gatewayUrl) for an existing gateway, or (None, None)."""
    next_token = None
    while True:
        kwargs = {"maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = control.list_gateways(**kwargs)
        for gw in resp.get("items", []):
            if gw.get("name") == name:
                full = control.get_gateway(gatewayIdentifier=gw["gatewayId"])
                return full["gatewayId"], full["gatewayUrl"]
        next_token = resp.get("nextToken")
        if not next_token:
            return None, None


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
        description="Create a no-protocol-type gateway for HTTP passthrough targets"
    )
    parser.add_argument(
        "--discovery-url",
        required=True,
        help="Entra ID v1.0 OIDC discovery URL for inbound JWT auth",
    )
    parser.add_argument(
        "--allowed-audience",
        required=True,
        help="Comma-separated allowed audiences (for example, api://<gateway-client-id>)",
    )
    args = parser.parse_args()

    load_env()
    allowed_audience = [
        a.strip() for a in args.allowed_audience.split(",") if a.strip()
    ]

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    # The gateway is shared with the runtime-agent labs. Reuse it if it already
    # exists so every lab attaches its target to the same gateway.
    gateway_id, gateway_url = find_gateway_by_name(control, GATEWAY_NAME)
    if gateway_id:
        print(f"--- Gateway '{GATEWAY_NAME}' already exists, reusing it ---")
        print(f"  Gateway ID:  {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")
    else:
        print(f"--- Creating gateway IAM role for '{GATEWAY_NAME}' ---")
        role_arn = admin.create_gateway_role(GATEWAY_NAME, oauth_targets=True)

        print(f"\n--- Creating gateway '{GATEWAY_NAME}' (no protocol type) ---")
        # No protocolType key: required for http.passthrough targets.
        # Entra ID v1.0 access tokens carry api://<client-id> as the aud claim,
        # so the authorizer validates allowedAudience (not allowedClients).
        gw_resp = control.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "allowedAudience": allowed_audience,
                    "discoveryUrl": args.discovery_url,
                }
            },
            exceptionLevel="DEBUG",
        )
        gateway_id = gw_resp["gatewayId"]
        gateway_url = gw_resp["gatewayUrl"]
        print(f"  Gateway ID:  {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")

        print("\n  Waiting for gateway to become READY...")
        while True:
            time.sleep(10)
            status = control.get_gateway(gatewayIdentifier=gateway_id)["status"]
            print(f"    Status: {status}")
            if status in ["READY", "FAILED", "CREATE_FAILED"]:
                break

    save_env({"GATEWAY_ID": gateway_id, "GATEWAY_URL": gateway_url})
    print("\n  Saved GATEWAY_ID and GATEWAY_URL to .env")


if __name__ == "__main__":
    main()
