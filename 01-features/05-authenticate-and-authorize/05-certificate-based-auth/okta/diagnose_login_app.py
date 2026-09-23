"""
Diagnostic: report the current Okta configuration for the login Web app.

Reads .env and prints:
  1. App metadata (label, signOnMode, implicitAssignment)
  2. Users assigned directly to the app (looking for OKTA_ASSIGN_USER_LOGIN)
  3. Group assignments on the app
  4. Which authentication policy the app is bound to + its rules
  5. Redirect URIs on the app (vs AGENTCORE_MANAGED_CALLBACK_URL)

Run:
    .venv/bin/python diagnose_login_app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"


def okta_headers(api_token: str) -> dict:
    return {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set in .env", file=sys.stderr)
        sys.exit(1)
    return value


def get_json(url: str, headers: dict) -> dict | list | None:
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        print(f"✗ GET {url} → HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


def main() -> None:
    load_dotenv(ENV_FILE)
    domain = must_env("OKTA_DOMAIN")
    api_token = must_env("OKTA_API_TOKEN")
    app_id = must_env("OKTA_LOGIN_APP_ID")
    expected_user = os.environ.get("OKTA_ASSIGN_USER_LOGIN", "")
    expected_callback = os.environ.get("AGENTCORE_MANAGED_CALLBACK_URL", "")

    base_url = f"https://{domain}"
    headers = okta_headers(api_token)

    print(f"Okta:      {base_url}")
    print(f"App id:    {app_id}")
    print(f"Expected user:     {expected_user or '(not set)'}")
    print(f"Expected callback: {expected_callback or '(not set)'}")
    print()

    # 1. App metadata
    print("=== App metadata ===")
    app = get_json(f"{base_url}/api/v1/apps/{app_id}", headers)
    if not app:
        print(f"App {app_id} not found. Check OKTA_LOGIN_APP_ID.")
        sys.exit(1)
    print(f"  label         = {app.get('label')}")
    print(f"  status        = {app.get('status')}")
    print(f"  signOnMode    = {app.get('signOnMode')}")
    print(f"  name          = {app.get('name')}")
    settings = app.get("settings", {}) or {}
    print(f"  implicitAssignment = {settings.get('implicitAssignment')}")
    oauth = settings.get("oauthClient") or {}
    redirect_uris = oauth.get("redirect_uris") or []
    print(f"  grant_types   = {oauth.get('grant_types')}")
    print(f"  application_type = {oauth.get('application_type')}")
    print(f"  token_endpoint_auth_method = {oauth.get('token_endpoint_auth_method')}")
    print(f"  redirect_uris = {redirect_uris}")
    if expected_callback:
        match = expected_callback in redirect_uris
        state = "✓ matches AGENTCORE_MANAGED_CALLBACK_URL" if match else "✗ NOT in redirect_uris"
        print(f"    AgentCore callback registered? {state}")
    print()

    # 2. User assignments
    print("=== Direct user assignments ===")
    users = get_json(f"{base_url}/api/v1/apps/{app_id}/users?limit=200", headers) or []
    if not users:
        print("  (none)")
    else:
        for u in users:
            name = u.get("credentials", {}).get("userName") or u.get("id")
            print(f"  - {name}  (id={u.get('id')}, scope={u.get('scope')}, status={u.get('status')})")
    if expected_user:
        found = any((u.get("credentials", {}).get("userName") == expected_user) for u in users)
        print(f"  {expected_user!r} assigned? {'✓ yes' if found else '✗ NO'}")
    print()

    # 3. Group assignments
    print("=== Group assignments ===")
    groups = get_json(f"{base_url}/api/v1/apps/{app_id}/groups", headers) or []
    if not groups:
        print("  (none)")
    else:
        for g in groups:
            print(f"  - group_id={g.get('id')}, priority={g.get('priority')}")
    print()

    # 4. Authentication Policy bound to the app
    print("=== Authentication Policy bound to app ===")
    # Undocumented but stable: the app resource embeds a _links.accessPolicy href
    # For older tenants, we fetch the app's assigned policy via GET /apps/{id}
    # accessPolicy link is at _links.accessPolicy.href
    links = app.get("_links", {}) or {}
    ap_link = (links.get("accessPolicy") or {}).get("href")
    print(f"  accessPolicy link = {ap_link}")
    if ap_link:
        # Extract policy id from href
        policy_id = ap_link.rstrip("/").split("/")[-1]
        policy = get_json(ap_link, headers)
        if policy:
            print(f"  policy.name        = {policy.get('name')}")
            print(f"  policy.status      = {policy.get('status')}")
            print(f"  policy.description = {(policy.get('description') or '')[:120]}")
            rules = get_json(f"{base_url}/api/v1/policies/{policy_id}/rules", headers) or []
            print(f"  rules ({len(rules)}):")
            for r in rules:
                actions = r.get("actions", {}) or {}
                access = (actions.get("appSignOn") or {}).get("access")
                vm = (actions.get("appSignOn") or {}).get("verificationMethod") or {}
                conds = r.get("conditions", {}) or {}
                people = conds.get("people", {}) or {}
                print(
                    f"    - name={r.get('name')!r} priority={r.get('priority')} "
                    f"status={r.get('status')} access={access} "
                    f"factorMode={vm.get('factorMode')}"
                )
                if people:
                    print(f"        people = {json.dumps(people)[:200]}")
                elif conds:
                    print(f"        conditions = {json.dumps(conds)[:200]}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
