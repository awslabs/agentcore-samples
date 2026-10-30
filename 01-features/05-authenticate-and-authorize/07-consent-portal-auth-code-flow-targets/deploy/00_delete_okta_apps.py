"""Delete the two Okta apps this sample created (and optionally its AS policies).

Run this LAST — after `deploy/teardown.py`.

Okta requires an app to be deactivated before it can be deleted, so each app
gets a POST .../lifecycle/deactivate followed by a DELETE.

Deletes by label:
  - AgentCore Consent GitHub Frontend
  - agentcore-consent-portal-login

Leaves alone by default: the `access_as_user` scope and the two access
policies on the authorization server, because the authorization server is
usually shared. Pass --policies to remove the two policies this sample added
(named "Consent sample - …"); the scope is left either way.

Needs OKTA_DOMAIN and OKTA_ADMIN_TOKEN in .env.

Run from the sample root:
    python deploy/00_delete_okta_apps.py --yes
    python deploy/00_delete_okta_apps.py --yes --policies
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_env, okta_domain

APP_LABELS = [
    "AgentCore Consent GitHub Frontend",
    "agentcore-consent-portal-login",
]
POLICY_NAMES = [
    "Consent sample - Frontend",
    "Consent sample - Portal login",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="Delete without prompting.")
    parser.add_argument(
        "--policies",
        action="store_true",
        help="Also delete the two access policies this sample added to the authorization server.",
    )
    args = parser.parse_args()

    load_env()
    domain = okta_domain() if os.environ.get("OKTA_DOMAIN", "").strip() else ""
    token = os.environ.get("OKTA_ADMIN_TOKEN", "").strip()
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID", "default").strip() or "default"
    if not domain or not token:
        print("ERROR: OKTA_DOMAIN and OKTA_ADMIN_TOKEN must be set in .env.", file=sys.stderr)
        sys.exit(1)

    base = f"https://{domain}/api/v1"
    headers = {
        "Authorization": f"SSWS {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    found: list[tuple[str, str]] = []
    for label in APP_LABELS:
        resp = requests.get(
            f"{base}/apps",
            headers=headers,
            params={"q": label, "limit": 20},
            timeout=30,
        )
        if resp.status_code >= 400:
            print(f"  ⚠ Could not list apps matching {label} (HTTP {resp.status_code})", file=sys.stderr)
            continue
        for app in resp.json():
            if app.get("label") == label:
                found.append((label, app["id"]))

    policies_to_delete: list[tuple[str, str]] = []
    if args.policies:
        resp = requests.get(f"{base}/authorizationServers/{as_id}/policies", headers=headers, timeout=30)
        if resp.status_code < 400:
            for p in resp.json():
                if p.get("name") in POLICY_NAMES:
                    policies_to_delete.append((p["name"], p["id"]))

    if not found and not policies_to_delete:
        print("Nothing to delete — no matching Okta apps or policies found.")
        return

    print("Will delete:")
    for label, app_id in found:
        print(f"  - app: {label} ({app_id})")
    for name, pid in policies_to_delete:
        print(f"  - policy: {name} ({pid}) on authorization server {as_id}")
    if not args.yes and input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return

    for label, app_id in found:
        # Okta refuses to delete an ACTIVE app.
        requests.post(f"{base}/apps/{app_id}/lifecycle/deactivate", headers=headers, timeout=30)
        resp = requests.delete(f"{base}/apps/{app_id}", headers=headers, timeout=30)
        if resp.status_code < 400:
            print(f"  ✓ Deleted app: {label}")
        else:
            print(f"  ⚠ Failed to delete app {label} (HTTP {resp.status_code})", file=sys.stderr)

    for name, pid in policies_to_delete:
        resp = requests.delete(f"{base}/authorizationServers/{as_id}/policies/{pid}", headers=headers, timeout=30)
        if resp.status_code < 400:
            print(f"  ✓ Deleted policy: {name}")
        else:
            print(f"  ⚠ Failed to delete policy {name} (HTTP {resp.status_code})", file=sys.stderr)

    print()
    print(f"Left in place: the `access_as_user` scope on authorization server {as_id}.")
    print("Remove it yourself if nothing else uses it.")


if __name__ == "__main__":
    main()
