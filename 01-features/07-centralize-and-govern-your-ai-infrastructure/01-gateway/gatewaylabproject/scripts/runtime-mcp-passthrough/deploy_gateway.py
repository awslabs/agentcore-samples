"""Create (or reuse) the shared no-auth gateway for MCP passthrough targets.

This gateway (context7-gateway) is shared with the Context7 and GitHub MCP labs.
It uses NO inbound authorization (authorizerType=NONE) and forwards the caller's
own Authorization header outbound via JWT passthrough. HTTP passthrough targets
require a gateway with no protocol type set, so the gateway is created directly
via boto3 (the shared GatewayBoto3Client.create_gateway hardcodes
protocolType=MCP). Writes GATEWAY_ID and GATEWAY_URL to the script-local .env.

> No-auth gateways are for public or token-forwarding targets only. Do not use a
> NONE-auth gateway for sensitive targets without your own access controls.

Usage:
    uv run python scripts/runtime-mcp-passthrough/deploy_gateway.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

# Shared with the Context7 and GitHub MCP labs: all attach their passthrough
# target to this same no-auth gateway, so the name must match across labs.
GATEWAY_NAME = "context7-gateway"


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
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    gateway_id, gateway_url = find_gateway_by_name(control, GATEWAY_NAME)
    if gateway_id:
        print(f"--- Gateway '{GATEWAY_NAME}' already exists, reusing it ---")
        print(f"  Gateway ID:  {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")
    else:
        print(f"--- Creating gateway IAM role for '{GATEWAY_NAME}' ---")
        role_arn = admin.create_gateway_role(GATEWAY_NAME, oauth_targets=True)

        print(
            f"\n--- Creating gateway '{GATEWAY_NAME}' (no auth, no protocol type) ---"
        )
        # authorizerType=NONE: no inbound authorization (token forwarded outbound).
        # authorizerConfiguration is omitted (only required for CUSTOM_JWT).
        # No protocolType key: required for http.passthrough targets.
        gw_resp = control.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            authorizerType="NONE",
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
