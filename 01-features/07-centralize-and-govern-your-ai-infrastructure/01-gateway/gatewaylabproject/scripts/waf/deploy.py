"""Attach a no-auth MCP target and protect the gateway with an AWS WAF web ACL.

Creates:
1. A no-auth MCP server target pointing at the public Exa MCP server
   (https://mcp.exa.ai/mcp), so WAF has a real target to protect.
2. A regional AWS WAF web ACL with two rules:
     - AWS Managed Rules (AWSManagedRulesCommonRuleSet), in COUNT mode by
       default so you can observe matches before enforcing.
     - A rate-based rule (Block) to demonstrate volumetric protection.
3. Associates the web ACL with the gateway via the wafv2 API.

Requires GATEWAY_ID (and GATEWAY_URL) in environment or .env, created by the
shared scripts/deploy_gateway.py. The gateway must be in READY state.

Usage:
    uv run python scripts/waf/deploy.py             # managed rules in COUNT mode
    uv run python scripts/waf/deploy.py --mode block  # managed rules in BLOCK mode
"""

import argparse
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

TARGET_NAME = "exa-mcp"
EXA_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"
WEB_ACL_NAME = "waf-gateway-acl"
RATE_LIMIT = 100  # requests per 5-minute window, per IP


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


def wafv2_client(region):
    """Build a wafv2 client for the default public AWS WAF endpoint."""
    return boto3.client("wafv2", region_name=region)


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


def wait_target_ready(admin, gateway_id, target_id):
    for _ in range(18):
        time.sleep(10)
        status = admin.client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )["status"]
        print(f"    Status: {status}")
        if status in ("READY", "FAILED", "CREATE_FAILED"):
            return status
    return "TIMEOUT"


def build_rules(managed_mode):
    """Two rules: AWS managed common rule set + a rate-based rule.

    managed_mode is "count" or "block"; it controls whether the managed rule
    group runs in COUNT (observe) or BLOCK (enforce) mode via an override.
    """
    managed_override = {"Count": {}} if managed_mode == "count" else {"None": {}}
    return [
        {
            "Name": "common-rule-set",
            "Priority": 0,
            "OverrideAction": managed_override,
            "Statement": {
                "ManagedRuleGroupStatement": {
                    "VendorName": "AWS",
                    "Name": "AWSManagedRulesCommonRuleSet",
                }
            },
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "common-rule-set",
            },
        },
        {
            "Name": "rate-limit",
            "Priority": 1,
            "Action": {"Block": {}},
            "Statement": {
                "RateBasedStatement": {
                    "Limit": RATE_LIMIT,
                    "AggregateKeyType": "IP",
                }
            },
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "rate-limit",
            },
        },
    ]


def main():
    parser = argparse.ArgumentParser(description="Protect a gateway with AWS WAF")
    parser.add_argument(
        "--mode",
        choices=["count", "block"],
        default="count",
        help="Managed rule group mode: count (observe, default) or block (enforce)",
    )
    args = parser.parse_args()

    load_env()
    gateway_id = get_required_env("GATEWAY_ID")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    wafv2 = wafv2_client(region)

    # --- Resolve the gateway resource ARN; confirm READY ---
    gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
    if gw.get("status") != "READY":
        print(f"ERROR: gateway {gateway_id} is {gw.get('status')}, expected READY")
        sys.exit(1)
    gateway_arn = gw.get(
        "gatewayArn",
        f"arn:aws:bedrock-agentcore:{region}:{admin.account_id}:gateway/{gateway_id}",
    )
    print(f"Gateway ARN: {gateway_arn}")

    # --- 1. Attach a no-auth MCP server target (public Exa MCP server) ---
    print("\n--- Creating no-auth MCP target (Exa) ---")
    target = admin.create_target(
        gateway_id, name=TARGET_NAME, endpoint=EXA_MCP_ENDPOINT
    )
    target_id = target["targetId"]
    wait_target_ready(admin, gateway_id, target_id)

    # --- 2. Create the regional web ACL (managed + rate-based rules) ---
    print(f"\n--- Creating web ACL ({WEB_ACL_NAME}, managed mode={args.mode}) ---")
    acl = wafv2.create_web_acl(
        Name=WEB_ACL_NAME,
        Scope="REGIONAL",
        DefaultAction={"Allow": {}},
        Rules=build_rules(args.mode),
        VisibilityConfig={
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": WEB_ACL_NAME,
        },
    )
    web_acl_arn = acl["Summary"]["ARN"]
    web_acl_id = acl["Summary"]["Id"]
    print(f"  Web ACL ARN: {web_acl_arn}")

    # Persist immediately so cleanup can delete the web ACL even if the
    # association step below fails (otherwise the ACL is orphaned).
    save_env(
        {
            "GATEWAY_ID": gateway_id,
            "GATEWAY_ARN": gateway_arn,
            "TARGET_ID": target_id,
            "WEB_ACL_NAME": WEB_ACL_NAME,
            "WEB_ACL_ID": web_acl_id,
            "WEB_ACL_ARN": web_acl_arn,
        }
    )

    # --- 3. Associate the web ACL with the gateway ---
    print("\n--- Associating web ACL with gateway ---")
    wafv2.associate_web_acl(WebACLArn=web_acl_arn, ResourceArn=gateway_arn)
    print("  Associated.")

    print("\n  Saved target + web ACL details to .env")
    print("\nNext: uv run python scripts/waf/invoke.py")


if __name__ == "__main__":
    main()
