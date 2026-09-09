"""Delete the JWT-inbound gateway and IAM role for one IdP profile.

Both names are derived from the IdP profile, so this deletes exactly what
deploy_gateway_entra.py created.

Separate from cleanup_gateway.py rather than merged into it, because the two read
from different profile namespaces -- servers/ and idps/. Merging them would put a
--github flag on a script that can delete an entra-named gateway, and these
deletes are not recoverable.

Refuses to run while any target is still attached; that is cleanup_targets.py's
job. Also refuses while a consent portal still points at this gateway, since the
portal's only source would then be gone.

Does NOT delete the Entra resource app -- it is shared with the portal's IdP
config and with every other client of its scope.

Usage:
    uv run python scripts/cleanup_gateway_entra.py --entra
"""

import os
import sys

import boto3
from gateway_admin import GatewayBoto3Client
from idp_config import gateway_name, portal_name, select_idp
from mcp_config import load_env


def portal_still_attached(control, gateway_id, name):
    """Return the portal id if a portal by this name still exists, else None.

    Checked by name rather than by scanning every portal's sources: sources are
    not returned in the list summary, and the portal this directory creates is
    the one named by the profile.
    """
    kwargs = {"maxResults": 50}
    while True:
        try:
            page = control.list_consent_portals(**kwargs)
        except Exception:  # noqa: BLE001
            # Portal APIs unavailable in this region tells us nothing about the
            # gateway; do not block the delete on it.
            return None
        for item in page.get("consentPortals", []):
            if item.get("name") == name:
                return item["consentPortalId"]
        token = page.get("nextToken")
        if not token:
            return None
        kwargs["nextToken"] = token


def main():
    idp = select_idp(__doc__)
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID", "")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Nothing to clean up.")
        sys.exit(1)

    gw_name = gateway_name(idp)
    region = boto3.Session().region_name
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    try:
        remaining = control.list_gateway_targets(
            gatewayIdentifier=gateway_id, maxResults=100
        ).get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not list targets on {gateway_id}: {e}")
        sys.exit(1)

    if remaining:
        print(f"--- Keeping gateway {gateway_id} ---")
        print(f"  {len(remaining)} target(s) still attached:")
        for t in remaining:
            print(f"    {t['name']}")
        print("\n  Delete them first, e.g.:")
        print("    uv run python scripts/cleanup_targets.py --github")
        sys.exit(1)

    portal_id = portal_still_attached(control, gateway_id, portal_name(idp))
    if portal_id:
        print(f"--- Keeping gateway {gateway_id} ---")
        print(f"  A consent portal still exists: {portal_id}")
        print("  It has this gateway as its only source. Delete it first:")
        print(f"    uv run python scripts/cleanup_portal.py --{idp['name']}")
        sys.exit(1)

    print(f"--- Deleting gateway {gw_name} ---")
    try:
        control.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"  Deleted: {gateway_id}")
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    print("\n--- Deleting IAM role ---")
    try:
        admin.delete_gateway_role(gw_name)
    except Exception as e:  # noqa: BLE001
        print(f"  Error: {e}")

    print("\n--- Left in place (on purpose) ---")
    print(f"  the {idp['displayName']} resource app ({idp['audienceEnv']}) -- shared")
    print("    with the portal's IdP config and any other client of its scope")


if __name__ == "__main__":
    main()
