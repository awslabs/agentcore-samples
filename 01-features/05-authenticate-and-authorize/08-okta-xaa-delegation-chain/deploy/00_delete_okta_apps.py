"""Delete the Okta objects deploy/00_create_okta_apps.py created.

Dry-run by default. The two authorization servers are only removed with
--include-servers, because an authorization server is often shared and deleting one
takes its scopes and policies with it.

NOT deleted: the **AI Agent** (workload principal). Okta exposes no delete API for it --
remove it in the Admin Console under Directory -> AI Agents. Its linked OIDC app goes
with it.

    python deploy/00_delete_okta_apps.py                        # preview
    python deploy/00_delete_okta_apps.py --yes
    python deploy/00_delete_okta_apps.py --yes --include-servers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OktaAdmin, load_env, must_env, okta_org_url

APP_LABELS = ("XAA Todo Login", "XAA Todo Agent App")
SERVER_NAMES = ("XAA AgentCore", "XAA Todo Resource")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="Actually delete.")
    ap.add_argument("--include-servers", action="store_true", help="Also delete the two authorization servers.")
    args = ap.parse_args()
    load_env()
    okta = OktaAdmin(okta_org_url(), must_env("OKTA_API_TOKEN"))

    apps = [a for a in okta.get("/apps?limit=200") if a.get("label") in APP_LABELS]
    servers = (
        [s for s in okta.get("/authorizationServers?limit=200") if s["name"] in SERVER_NAMES]
        if args.include_servers
        else []
    )

    if not apps and not servers:
        print("  Nothing to delete.")
        return
    print(f"  {len(apps) + len(servers)} object(s) would be deleted:")
    for a in apps:
        print(f"    app                  {a['label']}  ({a['id']}, {a.get('status')})")
    for s in servers:
        print(f"    authorization server {s['name']}  ({s['id']})")
    if not args.include_servers:
        print("    (authorization servers kept; pass --include-servers to remove them)")

    if not args.yes:
        print("\n  Dry run. Re-run with --yes to delete.")
        return

    print("\n--- deleting ---")
    for a in apps:
        # Okta requires an app to be deactivated before it can be deleted.
        if a.get("status") == "ACTIVE":
            okta.post(f"/apps/{a['id']}/lifecycle/deactivate", {})
            print(f"  deactivated {a['label']}")
        okta.delete(f"/apps/{a['id']}")
        print(f"  deleted app {a['label']}")
    for s in servers:
        okta.post(f"/authorizationServers/{s['id']}/lifecycle/deactivate", {})
        okta.delete(f"/authorizationServers/{s['id']}")
        print(f"  deleted authorization server {s['name']}")

    print(
        "\n  Remaining manual step: the AI Agent has no delete API.\n"
        "    Okta Admin Console -> Directory -> AI Agents -> your agent -> Actions -> Delete\n"
        "    (its linked OIDC app is removed with it)"
    )


if __name__ == "__main__":
    main()
