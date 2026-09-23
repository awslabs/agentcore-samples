"""Clean up everything created by deploy_gateway.py and waf/deploy.py.

Disassociates the web ACL from the gateway (required before deleting the
gateway), deletes the web ACL, deletes the gateway (and its Exa target), the
gateway IAM role, and finally removes the local .env file. Tolerant of
already-deleted resources.

Usage:
    uv run python scripts/waf/cleanup.py
"""

import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

GATEWAY_NAME = "waf-gateway"
WEB_ACL_NAME = "waf-gateway-acl"


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def wafv2_client(region):
    """Build a wafv2 client for the default public AWS WAF endpoint."""
    return boto3.client("wafv2", region_name=region)


def main():
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID", "")
    gateway_arn = os.environ.get("GATEWAY_ARN", "")
    web_acl_id = os.environ.get("WEB_ACL_ID", "")

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    wafv2 = wafv2_client(region)

    # --- Disassociate the web ACL (must happen before deleting the gateway) ---
    if gateway_arn:
        print("--- Disassociating web ACL from gateway ---")
        try:
            wafv2.disassociate_web_acl(ResourceArn=gateway_arn)
            print("  Disassociated.")
        except Exception as e:  # noqa: BLE001 - tolerate already-disassociated
            print(f"  Error: {e}")

    # --- Delete the web ACL (needs a fresh LockToken from get_web_acl) ---
    # Fall back to looking the ACL up by name if WEB_ACL_ID is not in .env
    # (e.g. deploy crashed, or .env was already removed), so a partially
    # created ACL is never orphaned.
    if not web_acl_id:
        try:
            for acl in wafv2.list_web_acls(Scope="REGIONAL")["WebACLs"]:
                if acl["Name"] == WEB_ACL_NAME:
                    web_acl_id = acl["Id"]
                    break
        except Exception as e:  # noqa: BLE001
            print(f"  Could not list web ACLs: {e}")

    if web_acl_id:
        print("--- Deleting web ACL ---")
        try:
            acl = wafv2.get_web_acl(Name=WEB_ACL_NAME, Scope="REGIONAL", Id=web_acl_id)
            wafv2.delete_web_acl(
                Name=WEB_ACL_NAME,
                Scope="REGIONAL",
                Id=web_acl_id,
                LockToken=acl["LockToken"],
            )
            print(f"  Deleted web ACL: {WEB_ACL_NAME}")
        except Exception as e:  # noqa: BLE001
            print(f"  Error: {e}")

    # --- Delete only the target created by this tutorial ---
    target_id = os.environ.get("TARGET_ID", "")
    if gateway_id and target_id:
        print(f"--- Deleting target {target_id} from gateway ---")
        try:
            admin.client.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=target_id
            )
            print(f"  Deleted target: {target_id}")
        except Exception as e:  # noqa: BLE001
            print(f"  Error: {e}")

    # --- Delete the gateway IAM role ---
    print("--- Deleting gateway IAM role ---")
    try:
        admin.delete_gateway_role(GATEWAY_NAME)
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        os.remove(env_path)
        print(f"--- Removed {env_path} ---")

    print("\nDone.")


if __name__ == "__main__":
    main()
