"""Delete gateway targets and OAuth2 credential providers.

Scoped by profile on purpose. One gateway can front several MCP servers, so
deleting every target would tear down a sibling server's target too.

    --github   delete only the targets and credential provider named by the
               github profile; anything else on the gateway is reported and
               left in place.
    --all      delete every target on this gateway, plus the credential
               provider of every profile in scripts/servers/.

--all is scoped to this gateway and to the profiles that exist here. It does
not touch credential providers belonging to other tutorials in the same
account. It still deletes resources irreversibly, so it prompts for
confirmation unless --yes is passed.

The gateway and its IAM role are left alone -- that is cleanup_gateway.py.

Does NOT delete the Cognito stack; the README deletes that explicitly, since
other tutorials share it.

Usage:
    uv run python scripts/cleanup_targets.py --github
    uv run python scripts/cleanup_targets.py --all
    uv run python scripts/cleanup_targets.py --all --yes
"""

import os
import sys
import time

import boto3
from gateway_admin import GatewayBoto3Client
from mcp_config import (
    credential_provider_name,
    implicit_target_name,
    load_env,
    schema_target_name,
    select_profiles_or_all,
)


def main():
    profiles, is_all, args = select_profiles_or_all(
        __doc__,
        extra_args=[
            (["--yes"], {"action": "store_true", "help": "skip the --all prompt"}),
        ],
    )
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Nothing to clean up.")
        sys.exit(1)

    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)

    provider_names = [credential_provider_name(p) for p in profiles]
    owned_target_names = set()
    for p in profiles:
        owned_target_names.add(implicit_target_name(p))
        owned_target_names.add(schema_target_name(p))

    try:
        targets = admin.client.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=100
        ).get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not list targets on {gateway_id}: {e}")
        sys.exit(1)

    if is_all:
        doomed = targets
    else:
        doomed = [t for t in targets if t["name"] in owned_target_names]

    print(f"--- Planned deletions on gateway {gateway_id} ---")
    for t in doomed:
        print(f"  target:   {t['name']}")
    for name in provider_names:
        print(f"  provider: {name}")
    if not doomed and not provider_names:
        print("  nothing to delete")
        return

    if is_all and not args.yes:
        print()
        print(
            f"  --all deletes {len(doomed)} target(s) and "
            f"{len(provider_names)} credential provider(s)."
        )
        print("  This cannot be undone.")
        if input("  Type 'delete' to continue: ").strip() != "delete":
            print("  Aborted. Nothing was deleted.")
            return

    print("\n--- Deleting targets ---")
    for t in doomed:
        try:
            print(f"  Deleting: {t['name']}")
            admin.client.delete_gateway_target(
                gatewayIdentifier=gateway_id, targetId=t["targetId"]
            )
            time.sleep(5)
        except Exception as e:  # noqa: BLE001
            print(f"    Error: {e}")

    if not is_all:
        skipped = [t["name"] for t in targets if t["name"] not in owned_target_names]
        for name in skipped:
            print(f"  Keeping (not this profile's): {name}")

    print("\n--- Deleting credential providers ---")
    for name in provider_names:
        try:
            admin.client.delete_oauth2_credential_provider(name=name)
            print(f"  Deleted: {name}")
        except Exception as e:  # noqa: BLE001
            print(f"  Error deleting {name}: {e}")

    print("\n  Gateway and IAM role left in place.")
    print("  Run cleanup_gateway.py to remove those.")


if __name__ == "__main__":
    main()
