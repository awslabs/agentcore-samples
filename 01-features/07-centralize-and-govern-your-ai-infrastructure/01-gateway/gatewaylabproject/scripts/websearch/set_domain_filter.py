"""Optional: configure a domain denylist on the Web Search target.

Restricts which domains the Web Search Tool may query by updating the target's
WebSearch configuration with a domainFilter exclude list. Run this after deploy.py
when you want domain governance; it is not required for basic search.

Pass domains as arguments, or set DOMAIN_DENYLIST (comma-separated) in the .env.

Requires GATEWAY_ID and TARGET_ID in environment or .env (set by deploy.py).

Usage:
    uv run python scripts/websearch/set_domain_filter.py blocked-1.com blocked-2.com
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


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
        print(f"ERROR: {key} not set. Run deploy.py first.")
        sys.exit(1)
    return val


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    target_id = get_required_env("TARGET_ID")

    denylist = sys.argv[1:]
    if not denylist and os.environ.get("DOMAIN_DENYLIST"):
        denylist = [
            d.strip() for d in os.environ["DOMAIN_DENYLIST"].split(",") if d.strip()
        ]
    if not denylist:
        print("ERROR: provide one or more domains, or set DOMAIN_DENYLIST in .env")
        sys.exit(1)

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"--- Updating Web Search target with domain denylist: {denylist} ---")
    control.update_gateway_target(
        gatewayIdentifier=gateway_id,
        targetId=target_id,
        name="web-search-tool",
        targetConfiguration={
            "mcp": {
                "connector": {
                    "source": {"connectorId": "web-search"},
                    "configurations": [
                        {
                            "name": "WebSearch",
                            "parameterValues": {"domainFilter": {"exclude": denylist}},
                        }
                    ],
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )

    print("\n  Waiting for target to become READY...")
    for _ in range(18):
        time.sleep(10)
        status = control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )["status"]
        print(f"    Status: {status}")
        if status in ("READY", "FAILED"):
            break

    print("\n  Domain filter applied. Searches will exclude the listed domains.")


if __name__ == "__main__":
    main()
