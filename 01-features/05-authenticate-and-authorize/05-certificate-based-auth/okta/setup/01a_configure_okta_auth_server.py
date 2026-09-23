"""
Configure the Okta Custom Authorization Server so that the sample's
service app can obtain M2M tokens for the requested scope.

Okta's Custom AS enforces two things at token-grant time:
  1. The requested scope must be registered on the AS.
  2. An Access Policy must be assigned to the requesting client AND
     contain a rule whose conditions (grant type, scopes, user) match
     the request.

Setup step 01 creates the service app itself. This script (01a)
configures the AS so the service app's token requests actually match
a policy + rule.

What this script does:
  1. Ensures the OAuth scope named in M2M_SCOPE exists on the Custom AS
     (POST /api/v1/authorizationServers/{as}/scopes).
  2. Ensures an Access Policy exists whose clients.include contains
     the service app's client_id. If a matching policy already exists,
     it's updated; otherwise a fresh policy is created.
  3. Ensures a rule exists on that policy allowing:
       - Grant types: CLIENT_CREDENTIALS (M2M) and TOKEN_EXCHANGE (OBO)
       - Scopes: the M2M_SCOPE
       - User condition: EVERYONE (M2M has no user, so this must not
         gate on user assignment)

Idempotent: safe to re-run after failed attempts. Existing scopes,
policies, and rules matching by name are updated in place.

Usage:
    python setup/01a_configure_okta_auth_server.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

POLICY_NAME = "AgentCore Identity Private Key JWT Sample"
RULE_NAME = "pkjwt-sample-client-credentials"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def okta_headers(api_token: str) -> dict:
    return {
        "Authorization": f"SSWS {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def raise_for_status(resp: requests.Response, action: str) -> None:
    """Raise with body content so Okta error codes are visible."""
    if resp.status_code >= 400:
        print(f"✗ {action} → HTTP {resp.status_code}", file=sys.stderr)
        print(f"  {resp.text[:600]}", file=sys.stderr)
        resp.raise_for_status()


def ensure_scope(base_url: str, api_token: str, as_id: str, scope_name: str) -> None:
    """Check-then-create the scope on the AS.

    Different Okta tenants return different codes for duplicate scopes
    (409 on some, 400 with 'name must be unique' on others). We list
    scopes first and only POST when missing, so the outcome doesn't
    depend on which duplicate-response Okta returns.
    """
    list_resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/scopes",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(list_resp, "GET /scopes")
    for existing in list_resp.json():
        if existing.get("name") == scope_name:
            print(f"• Scope already exists: {scope_name}")
            return

    resp = requests.post(
        f"{base_url}/api/v1/authorizationServers/{as_id}/scopes",
        headers=okta_headers(api_token),
        data=json.dumps(
            {
                "name": scope_name,
                "description": "Sample scope for AgentCore Identity PRIVATE_KEY_JWT test",
                "consent": "IMPLICIT",
                "default": False,
                "metadataPublish": "ALL_CLIENTS",
            }
        ),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        print(f"✓ Registered scope: {scope_name}")
        return
    # Defensive: even after the pre-check, tolerate the "duplicate"
    # signals in case another process registered it in the meantime.
    if resp.status_code == 409:
        print(f"• Scope already exists: {scope_name}")
        return
    if resp.status_code == 400 and "must be unique" in resp.text:
        print(f"• Scope already exists: {scope_name}")
        return
    raise_for_status(resp, f"POST /scopes for {scope_name}")


def find_policy(base_url: str, api_token: str, as_id: str, name: str) -> dict | None:
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(resp, "GET /policies")
    for pol in resp.json():
        if pol.get("name") == name:
            return pol
    return None


def build_policy_payload(name: str, client_id: str) -> dict:
    return {
        "type": "OAUTH_AUTHORIZATION_POLICY",
        "status": "ACTIVE",
        "name": name,
        "description": "Auto-created by the PRIVATE_KEY_JWT sample. Grants the sample service app permission to use client_credentials + token_exchange for the sample scope.",
        "priority": 1,
        "conditions": {
            "clients": {
                "include": [client_id],
            }
        },
    }


def upsert_policy(
    base_url: str,
    api_token: str,
    as_id: str,
    client_id: str,
    name: str,
) -> str:
    """Create-or-update a policy whose clients.include contains client_id.

    Returns the policy's id.
    """
    existing = find_policy(base_url, api_token, as_id, name)
    payload = build_policy_payload(name, client_id)

    if existing:
        policy_id = existing["id"]
        # Merge in the existing include list plus our client_id so we
        # don't clobber other clients an admin has added.
        existing_include = existing.get("conditions", {}).get("clients", {}).get("include", [])
        merged = list(dict.fromkeys([*existing_include, client_id]))
        payload["conditions"]["clients"]["include"] = merged

        resp = requests.put(
            f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}",
            headers=okta_headers(api_token),
            data=json.dumps(payload),
            timeout=15,
        )
        raise_for_status(resp, f"PUT /policies/{policy_id}")
        print(f"✓ Updated policy '{name}' (id={policy_id}, clients={merged})")
        return policy_id

    resp = requests.post(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    raise_for_status(resp, "POST /policies")
    created = resp.json()
    print(f"✓ Created policy '{name}' (id={created['id']}, clients=[{client_id}])")
    return created["id"]


def find_rule(base_url: str, api_token: str, as_id: str, policy_id: str, name: str) -> dict | None:
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}/rules",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(resp, f"GET /policies/{policy_id}/rules")
    for rule in resp.json():
        if rule.get("name") == name:
            return rule
    return None


def build_rule_payload(name: str, scope_name: str) -> dict:
    return {
        "type": "RESOURCE_ACCESS",
        "name": name,
        "status": "ACTIVE",
        "priority": 1,
        "conditions": {
            "people": {
                "users": {"include": [], "exclude": []},
                "groups": {"include": ["EVERYONE"], "exclude": []},
            },
            # All grant types needed across the sample:
            #   client_credentials  → M2M demo
            #   token-exchange      → OBO demo
            #   authorization_code  → 3LO user login (setup/01b Web app,
            #                         mediated by AgentCore Identity
            #                         USER_FEDERATION via setup/03b)
            "grantTypes": {
                "include": [
                    "client_credentials",
                    "authorization_code",
                    "urn:ietf:params:oauth:grant-type:token-exchange",
                ]
            },
            # Scopes granted to the sample's clients. The M2M flow only
            # needs the custom sample scope; the 3LO/OBO flow additionally
            # asks for standard OIDC scopes so it can identify the end
            # user. If any requested scope isn't listed here (and isn't
            # covered by another rule on this AS), Okta returns
            # app.oauth2.as.authorize_failure and the browser shows
            # "You are not allowed to access this app".
            "scopes": {
                "include": [
                    scope_name,
                    "openid",
                    "profile",
                    "email",
                    "offline_access",
                ]
            },
        },
        # Access + refresh token lifetimes must satisfy Okta's constraints:
        #   0 < refreshTokenWindowMinutes < refreshTokenLifetimeMinutes.
        # These are irrelevant for client_credentials (which doesn't issue
        # refresh tokens) but Okta validates them for all rules. Using
        # Okta's admin-console defaults: 60 min / 90 days / 7 days idle.
        "actions": {
            "token": {
                "accessTokenLifetimeMinutes": 60,
                "refreshTokenLifetimeMinutes": 90 * 24 * 60,
                "refreshTokenWindowMinutes": 7 * 24 * 60,
                "inlineHook": None,
            }
        },
    }


def upsert_rule(
    base_url: str,
    api_token: str,
    as_id: str,
    policy_id: str,
    name: str,
    scope_name: str,
) -> None:
    payload = build_rule_payload(name, scope_name)
    existing = find_rule(base_url, api_token, as_id, policy_id, name)

    if existing:
        rule_id = existing["id"]
        resp = requests.put(
            f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}/rules/{rule_id}",
            headers=okta_headers(api_token),
            data=json.dumps(payload),
            timeout=15,
        )
        raise_for_status(resp, f"PUT rules/{rule_id}")
        print(f"✓ Updated rule '{name}' (id={rule_id})")
        return

    resp = requests.post(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}/rules",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    raise_for_status(resp, "POST rules")
    created = resp.json()
    print(f"✓ Created rule '{name}' (id={created['id']})")


def verify_final_state(
    base_url: str,
    api_token: str,
    as_id: str,
    client_id: str,
    scope_name: str,
    policy_id: str,
) -> None:
    """Fetch the just-configured policy and print a summary."""
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(resp, "GET final policy")
    pol = resp.json()

    rules_resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}/rules",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(rules_resp, "GET final rules")
    rules = rules_resp.json()

    print()
    print("=== Verified state ===")
    print(f"Policy: {pol.get('name')} (status={pol.get('status')})")
    include = pol.get("conditions", {}).get("clients", {}).get("include", [])
    ok_client = client_id in include
    print(f"  clients.include = {include}  {'✓' if ok_client else '✗ MISSING CLIENT'}")
    required_scopes = {scope_name, "openid", "profile", "email"}
    for rule in rules:
        conds = rule.get("conditions", {})
        gt = conds.get("grantTypes", {}).get("include", [])
        sc = conds.get("scopes", {}).get("include", [])
        pp = conds.get("people", {}).get("groups", {}).get("include", [])
        ok_ccg = "client_credentials" in gt
        missing = sorted(required_scopes - set(sc))
        ok_scope = not missing
        ok_users = "EVERYONE" in pp
        print(f"  Rule '{rule['name']}' (status={rule.get('status')}):")
        print(f"    grantTypes = {gt}  {'✓' if ok_ccg else '✗ MISSING client_credentials'}")
        print(f"    scopes     = {sc}  {'✓' if ok_scope else '✗ MISSING ' + ','.join(missing)}")
        print(f"    people     = groups={pp}  {'✓' if ok_users else '✗ EVERYONE not included'}")


def main() -> None:
    load_dotenv(ENV_FILE)

    domain = must_env("OKTA_DOMAIN")
    api_token = must_env("OKTA_API_TOKEN")
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID") or "default"
    client_id = must_env("OKTA_SERVICE_APP_CLIENT_ID")
    scope_name = os.environ.get("M2M_SCOPE") or "api:access"

    if "-admin." in domain:
        print(
            f"✗ OKTA_DOMAIN={domain!r} looks like the Okta admin host.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = f"https://{domain}"
    print(f"Okta:       {base_url}")
    print(f"AS:         {as_id}")
    print(f"Client:     {client_id}")
    print(f"Scope:      {scope_name}")
    print(f"Policy:     {POLICY_NAME}")
    print(f"Rule:       {RULE_NAME}")
    print()

    ensure_scope(base_url, api_token, as_id, scope_name)
    policy_id = upsert_policy(base_url, api_token, as_id, client_id, POLICY_NAME)
    upsert_rule(base_url, api_token, as_id, policy_id, RULE_NAME, scope_name)
    verify_final_state(base_url, api_token, as_id, client_id, scope_name, policy_id)

    print()
    print("=" * 70)
    print("  Summary - step 01a: Okta Custom Authorization Server")
    print("=" * 70)
    print(f"  What was created / updated on AS {as_id!r}:")
    print(f"    Scope       : {scope_name}")
    print(f"    Access Policy: '{POLICY_NAME}' (id={policy_id})")
    print(f"      clients.include  : [{client_id}]")
    print(f"    Rule        : '{RULE_NAME}'")
    print("      grantTypes.include:")
    print("        client_credentials             (M2M demo)")
    print("        authorization_code             (3LO via login web app)")
    print("        urn:ietf:params:oauth:grant-type:token-exchange (OBO)")
    print(f"      scopes.include : {scope_name}, openid, profile, email,")
    print("                       offline_access")
    print("      people         : Everyone")
    print()
    print("  Where to inspect it in the Okta admin console:")
    print(f"    Security → API → Authorization Servers → {as_id}")
    print(f"    Scopes tab      : confirms {scope_name} is present")
    print(f"    Access Policies : {POLICY_NAME!r} → your service app under Clients")
    print("    Direct URL:")
    print(f"      {base_url}/admin/oauth2/as/{as_id}/policies")
    print()
    print("  Written to .env:")
    print("    (no new keys - this step configures Okta only)")
    print()
    print("  Why this step matters:")
    print("    Okta's Custom AS refuses to issue a token unless the request")
    print("    matches an Access Policy AND a rule inside it. Missing scopes")
    print("    on this rule cause the browser 3LO flow to show 'You are not")
    print("    allowed to access this app' - Okta's user-facing rendering of")
    print("    'no matching policy'.")
    print()
    print("  Next step:")
    print("    M2M-only path:  python setup/02_create_provider_m2m.py")
    print("    OBO path:       python setup/01b_create_okta_login_web_app.py")
    print("                    (creates the Web app used for user 3LO sign-in)")


if __name__ == "__main__":
    main()
