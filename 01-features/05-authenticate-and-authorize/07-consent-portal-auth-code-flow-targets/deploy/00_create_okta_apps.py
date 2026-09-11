"""Automate the Okta setup for the consent-portal sample (Okta Admin API).

Two OIDC web apps plus scope/policy work on a CUSTOM authorization server —
this sample uses passthrough (the agent forwards the user's token to the
gateway as-is), so there is no agent app:

  1. Frontend app (AgentCore Consent GitHub Frontend)
       Confidential web app, authorization_code + refresh_token, redirect
       http://localhost:8000/auth/callback, assigned to Everyone. "Require
       PKCE" is left off; the BFF sends S256 regardless, so turning it on
       later needs no code change.
  2. Portal login app (agentcore-consent-portal-login)
       A SEPARATE confidential web app for the consent portal's own sign-in.
       Okta's `sub` is a stable per-user identifier — the same user gets the
       same `sub` regardless of client app — so a dedicated portal client is
       safe (unlike Entra, where the portal must reuse the resource app).
       Created with a placeholder redirect; deploy/02_create_portal.py swaps
       in the real `<portalUrl>/callback` once the portal exists.

  On the authorization server (OKTA_AUTH_SERVER_ID, default "default"):
       - custom scope `access_as_user`
       - one access policy + rule per app admitting authorization_code with
         openid/profile/email/access_as_user. Every scope the portal requests
         — openid included — must be admitted by the rule, or /authorize
         fails with a policy-evaluation error.

  The portal and gateway require a CUSTOM authorization server (the org
  server issues opaque access tokens, which neither can validate). The
  built-in "default" custom AS exists on orgs with the API Access Management
  feature; its audience is api://default.

Writes to .env: OKTA_DOMAIN, OKTA_AUTH_SERVER_ID, PORTAL_APP_ID,
FRONTEND_CLIENT_ID/SECRET, PORTAL_CLIENT_ID/SECRET, IDP_DISCOVERY_URL,
IDP_AUDIENCE, GATEWAY_SCOPE, PORTAL_SCOPES, IDP=okta.

Prerequisites in .env (or exported): OKTA_DOMAIN (e.g.
integrator-1234567.okta.com) and OKTA_ADMIN_TOKEN (Okta admin -> Security ->
API -> Tokens; needs Org/Super Admin). The token is only used by this script
and by 02_create_portal.py's callback registration — never at runtime.

Run from the sample root:
    python deploy/00_create_okta_apps.py
Re-running is safe: apps/scopes/policies are reused by name; secrets are only
minted when .env is missing them (pass --rotate-secrets to force).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ENV_PATH, load_env, okta_domain, save_env

APP_LABELS = {
    "frontend": "AgentCore Consent GitHub Frontend",
    "portal": "agentcore-consent-portal-login",
}
REDIRECT_URI = "http://localhost:8000/auth/callback"
PORTAL_PLACEHOLDER_REDIRECT = "https://localhost/placeholder"
SCOPE_NAME = "access_as_user"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class OktaClient:
    """Thin Okta Admin API client (Authorization: SSWS <token>)."""

    def __init__(self, domain: str, token: str) -> None:
        if "-admin." in domain:
            domain = domain.replace("-admin.", ".")
            print(f"  ! OKTA_DOMAIN looked like the admin host; using {domain}")
        self.domain = domain
        self.base = f"https://{domain}/api/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"SSWS {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def request(self, method: str, path: str, *, json_body=None, params=None):
        url = f"{self.base}{path}"
        resp = self.session.request(method, url, json=json_body, params=params, timeout=30)
        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            # Redact everything except Okta's error-diagnostic fields.
            if isinstance(body, dict):
                body = {k: v for k, v in body.items() if k.startswith("error")}
            die(f"Okta API call failed: {method} {url}\n  HTTP {resp.status_code}\n  {body}")
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)


def env_value_is_placeholder(key: str) -> bool:
    if not ENV_PATH.exists():
        return True
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :].strip() in ("", "replace-me", "REPLACE_ME")
    return True


def find_app_by_label(client: OktaClient, label: str):
    apps = client.get("/apps", params={"q": label, "filter": 'status eq "ACTIVE"', "limit": 20})
    return next((a for a in apps if a.get("label") == label), None)


def create_web_app(client: OktaClient, label: str, redirect_uris: list[str]) -> dict:
    body = {
        "name": "oidc_client",
        "label": label,
        "signOnMode": "OPENID_CONNECT",
        "credentials": {"oauthClient": {"token_endpoint_auth_method": "client_secret_basic"}},
        "settings": {
            "oauthClient": {
                "application_type": "web",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "redirect_uris": redirect_uris,
            }
        },
    }
    return client.post("/apps", json_body=body)


def assign_app_to_everyone(client: OktaClient, app: dict) -> None:
    """Okta issues no token to a user who is not assigned to the app."""
    groups = client.get("/groups", params={"q": "Everyone", "limit": 10})
    everyone = next(
        (g for g in groups if (g.get("profile") or {}).get("name") == "Everyone" and g.get("type") == "BUILT_IN"),
        None,
    )
    if not everyone:
        print(
            f"    ⚠ 'Everyone' group not found; assign users to {app['label']} manually\n"
            f"      (Okta admin -> Applications -> {app['label']} -> Assignments).",
            file=sys.stderr,
        )
        return
    client.put(f"/apps/{app['id']}/groups/{everyone['id']}", json_body={})
    print(f"    ✓ Assigned {app['label']} to Everyone")


def get_or_create_app(client: OktaClient, label: str, redirect_uris: list[str]):
    existing = find_app_by_label(client, label)
    if existing:
        print(f"  • App exists: {label}")
        assign_app_to_everyone(client, existing)
        return existing, False
    app = create_web_app(client, label, redirect_uris)
    print(f"  ✓ Created app: {label}")
    assign_app_to_everyone(client, app)
    return app, True


def rotate_client_secret(client: OktaClient, app_id: str) -> str:
    resp = client.post(f"/apps/{app_id}/credentials/secrets")
    secret = resp.get("client_secret")
    if not secret:
        die(f"Okta returned no client_secret for app {app_id}.")
    return secret


def ensure_scope(client: OktaClient, as_id: str) -> None:
    for s in client.get(f"/authorizationServers/{as_id}/scopes"):
        if s["name"] == SCOPE_NAME:
            print(f"  • Scope already exists: {SCOPE_NAME}")
            return
    client.post(
        f"/authorizationServers/{as_id}/scopes",
        json_body={
            "name": SCOPE_NAME,
            "displayName": "Access AgentCore on the user's behalf",
            "description": "Allows calling the AgentCore runtime and gateway as the signed-in user.",
            "consent": "IMPLICIT",
            "metadataPublish": "ALL_CLIENTS",
            "default": False,
            "system": False,
        },
    )
    print(f"  ✓ Created scope: {SCOPE_NAME}")


def ensure_policy_and_rule(client: OktaClient, as_id: str, *, name: str, client_id: str, scopes: list[str]) -> None:
    policies = client.get(f"/authorizationServers/{as_id}/policies")
    existing = next((p for p in policies if p.get("name") == name), None)
    body = {
        "type": "OAUTH_AUTHORIZATION_POLICY",
        "status": "ACTIVE",
        "name": name,
        "description": f"Consent-portal sample policy for {name}",
        "conditions": {"clients": {"include": [client_id]}},
    }
    if existing:
        body["id"] = existing["id"]
        client.put(f"/authorizationServers/{as_id}/policies/{existing['id']}", json_body=body)
        policy_id = existing["id"]
        print(f"  • Policy updated: {name}")
    else:
        policy_id = client.post(f"/authorizationServers/{as_id}/policies", json_body=body)["id"]
        print(f"  ✓ Policy created: {name}")

    rule_name = "Auth code"
    rule_body = {
        "type": "RESOURCE_ACCESS",
        "name": rule_name,
        "status": "ACTIVE",
        "priority": 1,
        "conditions": {
            "people": {"users": {"include": [], "exclude": []}, "groups": {"include": ["EVERYONE"], "exclude": []}},
            "grantTypes": {"include": ["authorization_code"]},
            "scopes": {"include": scopes},
        },
        "actions": {
            "token": {
                "accessTokenLifetimeMinutes": 60,
                "refreshTokenLifetimeMinutes": 0,
                "refreshTokenWindowMinutes": 10080,
                "inlineHook": None,
            }
        },
    }
    rules = client.get(f"/authorizationServers/{as_id}/policies/{policy_id}/rules")
    existing_rule = next((r for r in rules if r.get("name") == rule_name), None)
    if existing_rule:
        rule_body["id"] = existing_rule["id"]
        client.put(
            f"/authorizationServers/{as_id}/policies/{policy_id}/rules/{existing_rule['id']}",
            json_body=rule_body,
        )
        print(f"    • Rule updated (scopes={scopes})")
    else:
        client.post(f"/authorizationServers/{as_id}/policies/{policy_id}/rules", json_body=rule_body)
        print(f"    ✓ Rule created (scopes={scopes})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rotate-secrets", action="store_true", help="Mint fresh client secrets.")
    args = parser.parse_args()

    load_env()
    domain = okta_domain() if os.environ.get("OKTA_DOMAIN", "").strip() else ""
    okta_token = os.environ.get("OKTA_ADMIN_TOKEN", "").strip()
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID", "default").strip() or "default"
    if not domain or domain.startswith("integrator-1234567"):
        die("OKTA_DOMAIN must be set to your Okta tenant (e.g. integrator-1234567.okta.com).")
    if not okta_token:
        die(
            "OKTA_ADMIN_TOKEN is not set. Create one at Okta admin -> Security ->\n"
            "API -> Tokens -> Create Token (needs Org/Super Admin), then add it to .env."
        )

    client = OktaClient(domain, okta_token)

    print("[1/4] Authorization server…")
    servers = client.get("/authorizationServers")
    server = next((s for s in servers if s["id"] == as_id or s["name"] == as_id), None)
    if not server:
        die(
            f"Authorization server '{as_id}' not found. This sample needs a CUSTOM\n"
            f"authorization server (API Access Management). Available: "
            f"{[(s['name'], s['id']) for s in servers]}"
        )
    # Okta accepts BOTH the literal id (aus…) and the alias "default" as the
    # path segment, on the Admin API and on the discovery endpoint alike, and
    # both discovery URLs report the same issuer. They are still not
    # interchangeable downstream: the consent portal compares the gateway
    # authorizer's discoveryUrl against the IdP credential provider's as
    # STRINGS, so a deployment that mixes the two forms fails closed with
    # `login_unavailable`. Keep the segment the operator configured and use it
    # for every URL, so all three legs agree by construction.
    as_url_segment = as_id
    as_api_id = server["id"]
    audiences = server.get("audiences") or []
    audience = audiences[0] if audiences else "api://default"
    print(f"  ✓ {server['name']} (id={as_api_id}, audience={audience})")
    if as_url_segment != as_api_id:
        print(f"    URLs will use the configured segment '{as_url_segment}' (same issuer).")
    ensure_scope(client, as_api_id)

    print("\n[2/4] App registrations…")
    frontend_app, frontend_new = get_or_create_app(client, APP_LABELS["frontend"], [REDIRECT_URI])
    portal_app, portal_new = get_or_create_app(client, APP_LABELS["portal"], [PORTAL_PLACEHOLDER_REDIRECT])

    frontend_client_id = frontend_app["credentials"]["oauthClient"]["client_id"]
    portal_client_id = portal_app["credentials"]["oauthClient"]["client_id"]

    def get_secret(app: dict, is_new: bool, env_key: str) -> str | None:
        if is_new:
            secret = (app.get("credentials") or {}).get("oauthClient", {}).get("client_secret")
            if secret:
                return secret
        if args.rotate_secrets or env_value_is_placeholder(env_key):
            return rotate_client_secret(client, app["id"])
        return None

    frontend_secret = get_secret(frontend_app, frontend_new, "FRONTEND_CLIENT_SECRET")
    portal_secret = get_secret(portal_app, portal_new, "PORTAL_CLIENT_SECRET")
    minted = sum(1 for s in (frontend_secret, portal_secret) if s)
    print(f"  ✓ Client secrets: {minted} freshly minted, {2 - minted} kept")

    print("\n[3/4] Access policies (sleeping 3s for propagation)…")
    time.sleep(3)
    ensure_policy_and_rule(
        client,
        as_api_id,
        name="Consent sample - Frontend",
        client_id=frontend_client_id,
        scopes=["openid", "profile", "email", "offline_access", SCOPE_NAME],
    )
    # The portal always requests openid plus PORTAL_SCOPES; every one of them
    # must be admitted here or /authorize fails with a policy error.
    ensure_policy_and_rule(
        client,
        as_api_id,
        name="Consent sample - Portal login",
        client_id=portal_client_id,
        scopes=["openid", "profile", "email", SCOPE_NAME],
    )

    print("\n[4/4] Writing .env…")
    env_writes = {
        "IDP": "okta",
        "OKTA_DOMAIN": client.domain,
        "OKTA_AUTH_SERVER_ID": as_url_segment,
        "PORTAL_APP_ID": portal_app["id"],
        "FRONTEND_CLIENT_ID": frontend_client_id,
        "PORTAL_CLIENT_ID": portal_client_id,
        "IDP_DISCOVERY_URL": (f"https://{client.domain}/oauth2/{as_url_segment}/.well-known/openid-configuration"),
        "IDP_AUDIENCE": audience,
        # Okta uses the short scope name end to end — no qualified form.
        "GATEWAY_SCOPE": SCOPE_NAME,
        "PORTAL_SCOPES": f"openid {SCOPE_NAME}",
    }
    if frontend_secret:
        env_writes["FRONTEND_CLIENT_SECRET"] = frontend_secret
    if portal_secret:
        env_writes["PORTAL_CLIENT_SECRET"] = portal_secret
    save_env(**env_writes)
    print("  ✓ Wrote IDs, discovery URL, audience, and scopes to .env")

    print()
    print("✓ Okta setup complete (frontend app + separate portal login app).")
    print("  Make sure your test user is assigned to BOTH apps.")
    print()
    print("Next: python deploy/01_create_gateway.py --okta")


if __name__ == "__main__":
    main()
