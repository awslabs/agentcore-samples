"""Delete the Consent Portal resources created by deploy_portal.py.

Reverse creation order, and each step is skipped rather than fatal if the
resource is already gone, so a partial deploy can be cleaned up:

    1. the consent portal
    2. the primary IdP credential provider
    3. the execution role (inline policy first -- IAM refuses otherwise)

Deliberately does NOT delete, because none of these are this script's to own:

    - the gateway or its targets  -> cleanup_targets.py, then
      cleanup_gateway_entra.py
    - the GitHub outbound credential provider -> cleanup_targets.py --github
    - the IdP *resource* app (ENTRA_RESOURCE) -- it is the gateway's audience,
      the portal's login client (PORTAL_CLIENT_ID == ENTRA_RESOURCE under Entra),
      and shared with every other client of its scope. cleanup_gateway_entra.py
      leaves it too; deleting it is yours to run once the gateway is gone.

Takes the same required --<idp> flag as deploy_portal.py, and for the same
reason: the names it deletes are derived from the profile, so a guessed profile
would delete the wrong portal.

There is no --all: one gateway has one portal here, and nothing to fan out over.

Usage:
    uv run python scripts/cleanup_portal.py --entra
"""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError
from idp_config import (
    idp_provider_name,
    portal_name,
    portal_role_name,
    resource_app_delete_hint_lines,
    select_idp,
)
from mcp_config import load_env


def resolve_portal_id(control, name):
    """Find the portal id, preferring .env but falling back to a name lookup.

    The fallback matters: .env is gitignored and easy to lose, and without it the
    portal would be left running with no obvious way to find it again.
    """
    portal_id = os.environ.get("PORTAL_ID")
    if portal_id:
        return portal_id

    kwargs = {"maxResults": 50}
    while True:
        page = control.list_consent_portals(**kwargs)
        for item in page.get("consentPortals", []):
            if item.get("name") == name:
                print(f"  PORTAL_ID not in .env; found {name} by name")
                return item["consentPortalId"]
        token = page.get("nextToken")
        if not token:
            return None
        kwargs["nextToken"] = token


def delete_portal(control, portal_id):
    print("--- Step 1: consent portal ---")
    if not portal_id:
        print("  none found, skipping")
        return

    try:
        control.delete_consent_portal(consentPortalIdentifier=portal_id)
        print(f"  deleting: {portal_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"  already gone: {portal_id}")
        return

    # Wait it out rather than returning immediately: the portal holds a reference
    # to both the IdP provider and the execution role, and deleting those from
    # under a still-DELETING portal is how you get a stuck delete.
    for _ in range(30):
        try:
            status = control.get_consent_portal(consentPortalIdentifier=portal_id)[
                "status"
            ]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print("  deleted")
                return
            raise
        print(f"    status: {status}")
        time.sleep(10)

    print("ERROR: portal still present after waiting; not touching its")
    print("  execution role or IdP provider while it may still be in use.")
    print("  Re-run this script once the delete finishes.")
    sys.exit(1)


def delete_idp_provider(control, name):
    print("\n--- Step 2: IdP credential provider ---")
    try:
        control.delete_oauth2_credential_provider(name=name)
        print(f"  deleted: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        print(f"  not found, skipping: {name}")


def delete_role(iam, role_name):
    print("\n--- Step 3: execution role ---")
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="ConsentPortalAccess")
        print("  deleted inline policy: ConsentPortalAccess")
    except iam.exceptions.NoSuchEntityException:
        print("  inline policy not found, skipping")

    try:
        iam.delete_role(RoleName=role_name)
        print(f"  deleted: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  not found, skipping: {role_name}")


def main():
    idp = select_idp(__doc__)
    load_env()

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam", region_name=region)

    name = portal_name(idp)
    portal_id = resolve_portal_id(control, name)

    delete_portal(control, portal_id)
    delete_idp_provider(control, idp_provider_name(idp))
    delete_role(iam, portal_role_name(idp))

    print("\n--- Left in place (on purpose) ---")
    print("  the gateway and its targets -- use cleanup_targets.py, then")
    print("    cleanup_gateway_entra.py")
    print("  the GitHub outbound credential provider -- cleanup_targets.py")
    for line in resource_app_delete_hint_lines(idp):
        print(line)

    print("\n  scripts/.env still holds the deleted resources' ids. Remove the")
    print("  PORTAL_* , IDP_PROVIDER_ARN and EXECUTION_ROLE_ARN lines, or")
    print("  delete the file, before deploying again.")


if __name__ == "__main__":
    main()
