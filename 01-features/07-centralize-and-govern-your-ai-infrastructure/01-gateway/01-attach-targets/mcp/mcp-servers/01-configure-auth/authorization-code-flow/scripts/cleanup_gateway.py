"""Delete the gateway and its IAM role for one MCP server profile.

Both names are derived from the profile, so this deletes exactly what
deploy_gateway.py created.

Refuses to run while any target is still attached. Targets are
cleanup_targets.py's job, and a gateway is shared -- deleting it out from under
a sibling server's target would break that server. Run cleanup_targets.py
first.

Does NOT delete the Cognito stack; the README deletes that explicitly, since
other tutorials share it.

Usage:
    uv run python scripts/cleanup_gateway.py --github
"""

import os
import sys

import boto3
from gateway_admin import GatewayBoto3Client
from mcp_config import gateway_name, load_env, select_profile


def main():
    profile = select_profile(__doc__)
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Nothing to clean up.")
        sys.exit(1)

    gw_name = gateway_name(profile)
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    try:
        remaining = admin.client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=100
        ).get("items", [])
    except Exception as e:
        print(f"ERROR: could not list targets on {gateway_id}: {e}")
        sys.exit(1)

    if remaining:
        print(f"--- Keeping gateway {gateway_id} ---")
        print(f"  {len(remaining)} target(s) still attached:")
        for t in remaining:
            print(f"    {t['name']}")
        print("\n  Delete them first:")
        print(f"    uv run python scripts/cleanup_targets.py --{profile['name']}")
        sys.exit(1)

    print(f"--- Deleting gateway {gw_name} ---")
    try:
        admin.client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted: {gateway_id}")
    except Exception as e:
        print(f"  Error: {e}")

    print("\n--- Deleting IAM role ---")
    try:
        admin.delete_gateway_role(gw_name)
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
