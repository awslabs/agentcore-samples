"""
Delete the Okta artifacts created by deploy/00_create_okta_apps.py.

Removes (in dependency order):
  - The three access policies on the default authorization server
    (frontend, agent OBO, gateway OBO) — must go before scopes because a
    policy that references a scope pins that scope in place.
  - The three custom scopes (agent.access, gateway.access, downstream.access).
  - The three app registrations (frontend, agent, gateway).

Idempotent — missing resources are silently skipped.

Optionally clears deploy-populated .env values (client IDs + secrets + scopes)
so a re-run of 00_create_okta_apps.py starts clean.

Run:
    python deploy/00_delete_okta_apps.py --yes           # delete apps + policies + scopes
    python deploy/00_delete_okta_apps.py --yes --clean-env  # + clear .env values
    python deploy/00_delete_okta_apps.py --dry-run       # show what would be deleted

Requires:
  - OKTA_DOMAIN and OKTA_ADMIN_TOKEN in .env (same token used to create).

Does NOT touch:
  - AgentCore Identity resources (workload, credential providers).
    Use `python deploy/teardown.py` for those.
  - The AWS Gateway or the deployed agent Runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Reuse helpers from the create script — same folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module  # noqa: E402

_create = import_module("00_create_okta_apps")
OktaClient = _create.OktaClient
APP_LABELS = _create.APP_LABELS
SCOPES = _create.SCOPES
find_app_by_label = _create.find_app_by_label
find_policy_by_name = _create.find_policy_by_name
upsert_env_value = _create.upsert_env_value


POLICY_NAMES = [
    "AgentCore OBO UC2 - Frontend",
    "AgentCore OBO UC2 - Agent OBO",
    "AgentCore OBO UC2 - Gateway OBO",
]

DEPLOY_POPULATED_KEYS = [
    "FRONTEND_CLIENT_ID",
    "FRONTEND_CLIENT_SECRET",
    "AGENT_CLIENT_ID",
    "AGENT_CLIENT_SECRET",
    "GATEWAY_CLIENT_ID",
    "GATEWAY_CLIENT_SECRET",
    "UPSTREAM_SCOPE",
    "GATEWAY_SCOPE",
    "DOWNSTREAM_SCOPE",
    "OKTA_AUDIENCE",
]


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def deactivate_and_delete_policy(client: OktaClient, auth_server_id: str, policy: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"  would delete policy: {policy['name']} (id={policy['id']})")
        return
    # Deactivate first — active policies sometimes refuse deletion.
    try:
        client.post(f"/authorizationServers/{auth_server_id}/policies/{policy['id']}/lifecycle/deactivate")
    except SystemExit:
        pass
    try:
        client.delete(f"/authorizationServers/{auth_server_id}/policies/{policy['id']}")
        print(f"  ✓ Deleted policy: {policy['name']}")
    except SystemExit as e:
        # Log & continue — other cleanups may still succeed.
        print(f"  ✗ Failed to delete policy {policy['name']}: {e}", file=sys.stderr)


def delete_scope(client: OktaClient, auth_server_id: str, scope_name: str, dry_run: bool) -> None:
    scopes = client.get(f"/authorizationServers/{auth_server_id}/scopes")
    match = next((s for s in scopes if s["name"] == scope_name), None)
    if not match:
        print(f"  • Scope already gone: {scope_name}")
        return
    if dry_run:
        print(f"  would delete scope: {scope_name} (id={match['id']})")
        return
    try:
        client.delete(f"/authorizationServers/{auth_server_id}/scopes/{match['id']}")
        print(f"  ✓ Deleted scope: {scope_name}")
    except SystemExit as e:
        print(f"  ✗ Failed to delete scope {scope_name}: {e}", file=sys.stderr)


def deactivate_and_delete_app(client: OktaClient, app: dict, dry_run: bool) -> None:
    label = app["label"]
    if dry_run:
        print(f"  would delete app: {label} (id={app['id']})")
        return
    # Okta requires DEACTIVATE before DELETE on OIDC apps.
    try:
        client.post(f"/apps/{app['id']}/lifecycle/deactivate")
    except SystemExit:
        pass
    try:
        client.delete(f"/apps/{app['id']}")
        print(f"  ✓ Deleted app: {label}")
    except SystemExit as e:
        print(f"  ✗ Failed to delete app {label}: {e}", file=sys.stderr)


def clean_env_values(env_path: Path) -> None:
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        for key in DEPLOY_POPULATED_KEYS:
            if line.startswith(f"{key}="):
                lines[i] = f"{key}="
                changed = True
                print(f"  ✓ Cleared .env value: {key}")
                break
    if changed:
        env_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without doing it.",
    )
    parser.add_argument(
        "--clean-env",
        action="store_true",
        help="Also clear deploy-populated values from .env.",
    )
    args = parser.parse_args()

    real_world_root = Path(__file__).resolve().parent.parent
    env_path = real_world_root / ".env"
    load_dotenv(env_path, override=True)

    okta_domain = os.environ.get("OKTA_DOMAIN", "").strip()
    okta_token = os.environ.get("OKTA_ADMIN_TOKEN", "").strip()
    auth_server_id = os.environ.get("OKTA_AUTH_SERVER_ID", "default").strip() or "default"

    if not okta_domain:
        die("OKTA_DOMAIN is not set in .env.")
    if not okta_token:
        die("OKTA_ADMIN_TOKEN is not set in .env.")

    if not args.yes and not args.dry_run:
        print(f"About to delete Okta artifacts on {okta_domain}:")
        print(f"  - 3 access policies on '{auth_server_id}': {POLICY_NAMES}")
        print(f"  - 3 custom scopes on '{auth_server_id}': {[s['name'] for s in SCOPES]}")
        print(f"  - 3 app registrations: {list(APP_LABELS.values())}")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    client = OktaClient(okta_domain, okta_token)

    # 1) Policies (must go before scopes).
    print("\n[1/3] Deleting access policies…")
    for name in POLICY_NAMES:
        p = find_policy_by_name(client, auth_server_id, name)
        if not p:
            print(f"  • Policy already gone: {name}")
            continue
        deactivate_and_delete_policy(client, auth_server_id, p, args.dry_run)

    # 2) Custom scopes.
    print("\n[2/3] Deleting custom scopes…")
    for scope_def in SCOPES:
        delete_scope(client, auth_server_id, scope_def["name"], args.dry_run)

    # 3) Apps.
    print("\n[3/3] Deleting app registrations…")
    for label in APP_LABELS.values():
        app = find_app_by_label(client, label)
        if not app:
            print(f"  • App already gone: {label}")
            continue
        deactivate_and_delete_app(client, app, args.dry_run)

    if args.clean_env and not args.dry_run:
        print("\n[clean-env] Clearing deploy-populated values from .env…")
        clean_env_values(env_path)

    print()
    print("✓ Okta cleanup complete." if not args.dry_run else "✓ Dry run complete (no changes made).")


if __name__ == "__main__":
    main()
