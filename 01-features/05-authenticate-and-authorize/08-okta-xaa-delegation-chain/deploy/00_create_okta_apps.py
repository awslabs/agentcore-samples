"""Provision everything in Okta that has a Management API, idempotently.

Creates, reusing by name on a re-run:

  1. AS 1 "XAA AgentCore" (audience api://agentcore) -- the agent chain.
     Scopes: agent.access (T_user, may invoke the runtime) and tools.access
     (T_gateway, may call the gateway; minted by the OBO exchange).

  2. AS 2 "XAA Todo Resource" (audience api://todo) -- the resource.
     Scope: todos.read. Redeems the ID-JAG at leg 2 and mints T_tool. Its access
     policy allows the `jwt-bearer` grant, which is what leg 2 uses.
     Kept separate from AS 1 deliberately: the todo API trusts only this issuer,
     which is what makes the agent's own tokens unusable against it.

  3. Login app -- OIDC web app, authorization_code + refresh_token, the BFF's
     confidential client. The user signs in here.

  4. Agent app -- API Services app with the Token Exchange grant, used by
     AgentCore Identity for the OBO exchange at hop C. DPoP stays off because
     AgentCore Identity does not sign DPoP proofs.

  5. Access policies + ACTIVE rules for each app on the right server.

Writes AGENTCORE_AS_*, RESOURCE_AS_*, LOGIN_CLIENT_*, AGENT_APP_CLIENT_* to .env.

NOT created here: the AI Agent (wlp...). Okta exposes no create API for workload
principals -- it is an Admin Console step, and it needs AS 2 to exist first for
its Resource Connection. Run this script, then follow IDP_SETUP_OKTA.md.

    python deploy/00_create_okta_apps.py
    python deploy/00_create_okta_apps.py --rotate-secrets
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    OktaAdmin,
    env,
    load_env,
    must_env,
    okta_org_url,
    save_env,
)

AS1_NAME = "XAA AgentCore"
AS2_NAME = "XAA Todo Resource"
LOGIN_LABEL = "XAA Todo Login"
AGENT_LABEL = "XAA Todo Agent App"
# OAuth grant URIs, not credentials.
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


# ── authorization servers ────────────────────────────────────────────────────


def ensure_as(okta: OktaAdmin, name: str, audience: str, description: str) -> dict:
    for server in okta.get("/authorizationServers?limit=200"):
        if server["name"] == name:
            print(f"  • reusing AS {name} (id={server['id']}, audiences={server.get('audiences')})")
            return server
    server = okta.post(
        "/authorizationServers",
        {
            "name": name,
            "description": description,
            "audiences": [audience],
            "issuerMode": "ORG_URL",
        },
    )
    print(f"  ✓ created AS {name} (id={server['id']}, audience={audience})")
    return server


def ensure_scope(okta: OktaAdmin, as_id: str, name: str, description: str) -> None:
    for scope in okta.get(f"/authorizationServers/{as_id}/scopes?limit=200"):
        if scope["name"] == name:
            print(f"      • scope {name} exists")
            return
    okta.post(
        f"/authorizationServers/{as_id}/scopes",
        {
            "name": name,
            "description": description,
            # IMPLICIT: no per-user consent prompt. The authorization that matters
            # in this sample is Okta's delegation + the Cedar policy, not a consent
            # screen on an internal scope.
            "consent": "IMPLICIT",
            "metadataPublish": "ALL_CLIENTS",
        },
    )
    print(f"      ✓ scope {name}")


def ensure_policy(okta: OktaAdmin, as_id: str, name: str, client_id: str, grants: list[str], scopes: list[str]) -> None:
    """One access policy + one ACTIVE rule, scoped to a single client.

    A brand-new authorization server has NO policy, and a policy created via the
    API can land Inactive -- an inactive policy is silently skipped during
    evaluation, so /authorize fails with a policy error that names nothing.
    """
    policies = okta.get(f"/authorizationServers/{as_id}/policies?limit=200")
    existing = next((p for p in policies if p["name"] == name), None)
    body = {
        "type": "OAUTH_AUTHORIZATION_POLICY",
        "status": "ACTIVE",
        "name": name,
        "description": f"Managed by the XAA delegation-chain sample ({name}).",
        "priority": 1,
        "conditions": {"clients": {"include": [client_id]}},
    }
    if existing:
        policy = okta.put(f"/authorizationServers/{as_id}/policies/{existing['id']}", body)
        print(f"      • policy {name} updated")
    else:
        policy = okta.post(f"/authorizationServers/{as_id}/policies", body)
        print(f"      ✓ policy {name}")

    rule_body = {
        "type": "RESOURCE_ACCESS",
        "name": "default",
        "status": "ACTIVE",
        "priority": 1,
        "conditions": {
            "people": {"users": {"include": [], "exclude": []}, "groups": {"include": ["EVERYONE"]}},
            "grantTypes": {"include": grants},
            "scopes": {"include": scopes},
        },
        "actions": {
            "token": {
                "accessTokenLifetimeMinutes": 60,
                "refreshTokenLifetimeMinutes": 0,
                "refreshTokenWindowMinutes": 10080,
            }
        },
    }
    rules = okta.get(f"/authorizationServers/{as_id}/policies/{policy['id']}/rules")
    existing_rule = next((r for r in rules if r["name"] == "default"), None)
    if existing_rule:
        okta.put(
            f"/authorizationServers/{as_id}/policies/{policy['id']}/rules/{existing_rule['id']}",
            rule_body,
        )
        print("        • rule updated (ACTIVE)")
    else:
        okta.post(f"/authorizationServers/{as_id}/policies/{policy['id']}/rules", rule_body)
        print("        ✓ rule created (ACTIVE)")


# ── apps ─────────────────────────────────────────────────────────────────────


def find_app(okta: OktaAdmin, label: str) -> dict | None:
    for app in okta.get("/apps?limit=200"):
        if app.get("label") == label:
            return app
    return None


def ensure_login_app(okta: OktaAdmin, redirect_uri: str) -> dict:
    existing = find_app(okta, LOGIN_LABEL)
    if existing:
        print(f"  • reusing app {LOGIN_LABEL}")
        return existing
    app = okta.post(
        "/apps",
        {
            "name": "oidc_client",
            "label": LOGIN_LABEL,
            "signOnMode": "OPENID_CONNECT",
            "credentials": {"oauthClient": {"token_endpoint_auth_method": "client_secret_basic"}},
            "settings": {
                "oauthClient": {
                    "application_type": "web",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "redirect_uris": [redirect_uri],
                }
            },
        },
    )
    print(f"  ✓ created app {LOGIN_LABEL}")
    return app


def ensure_agent_app(okta: OktaAdmin) -> dict:
    """API Services app used by AgentCore Identity for the OBO exchange."""
    existing = find_app(okta, AGENT_LABEL)
    if existing:
        print(f"  • reusing app {AGENT_LABEL}")
        return existing
    app = okta.post(
        "/apps",
        {
            "name": "oidc_client",
            "label": AGENT_LABEL,
            "signOnMode": "OPENID_CONNECT",
            "credentials": {"oauthClient": {"token_endpoint_auth_method": "client_secret_basic"}},
            "settings": {
                "oauthClient": {
                    "application_type": "service",
                    # Okta rejects a service app unless client_credentials is
                    # present ("'grant_types' must contain 'client_credentials'
                    # when 'application_type' is 'service'"). Enabling it on the
                    # app is not the same as authorizing it: the access policy
                    # below admits ONLY the token-exchange grant, so this client
                    # cannot actually mint an M2M token.
                    "grant_types": ["client_credentials", TOKEN_EXCHANGE],
                    "response_types": ["token"],
                }
            },
        },
    )
    print(f"  ✓ created app {AGENT_LABEL}")
    return app


def assign_everyone(okta: OktaAdmin, app_id: str, label: str) -> None:
    """Okta issues no token to a user who is not assigned to the app."""
    groups = okta.get("/groups?q=Everyone&limit=10")
    everyone = next((g for g in groups if (g.get("profile") or {}).get("name") == "Everyone"), None)
    if not everyone:
        print(f"    ⚠ 'Everyone' group not found; assign users to {label} manually")
        return
    try:
        okta.put(f"/apps/{app_id}/groups/{everyone['id']}", {})
        print(f"    ✓ assigned {label} to Everyone")
    except RuntimeError as exc:
        # Some newer tenants use Federation Broker Mode, where all users reach all
        # apps implicitly and this call is a no-op or rejected. Harmless.
        print(f"    • group assign skipped for {label}: {str(exc)[:80]}")


def client_secret(okta: OktaAdmin, app: dict, env_key: str, rotate: bool) -> str | None:
    """Return a usable client secret, or None to leave .env untouched.

    Okta returns client_secret only in the CREATE response. On a re-run the app is
    fetched with GET, which omits it -- so if .env has no secret yet we must mint a
    new one explicitly, or the chain is left with an unusable app.
    """
    creds = (app.get("credentials") or {}).get("oauthClient") or {}
    have_in_env = bool(env(env_key))
    if creds.get("client_secret") and not rotate:
        return creds["client_secret"]
    if rotate or not have_in_env:
        fresh = okta.post(f"/apps/{app['id']}/credentials/secrets", {})
        return fresh.get("secret") or fresh.get("client_secret")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rotate-secrets", action="store_true", help="Mint fresh client secrets.")
    args = ap.parse_args()

    load_env()
    org = okta_org_url()
    okta = OktaAdmin(org, must_env("OKTA_API_TOKEN"))
    redirect_uri = env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")
    agentcore_aud = env("AGENTCORE_AUDIENCE", "api://agentcore")
    resource_aud = env("RESOURCE_AUDIENCE", "api://todo")
    scope_agent = env("SCOPE_AGENT_ACCESS", "agent.access")
    scope_tools = env("SCOPE_TOOLS_ACCESS", "tools.access")
    resource_scope = env("RESOURCE_SCOPE", "todos.read")

    print(f"Okta org: {org}\n")

    print("[1/5] Authorization server 1 -- the agent chain")
    as1 = ensure_as(okta, AS1_NAME, agentcore_aud, "XAA sample: user + agent tokens (T_user, T_gateway).")
    ensure_scope(okta, as1["id"], scope_agent, "Invoke the AgentCore runtime as the signed-in user.")
    ensure_scope(okta, as1["id"], scope_tools, "Call the AgentCore gateway on behalf of the user.")

    print("\n[2/5] Authorization server 2 -- the resource")
    as2 = ensure_as(okta, AS2_NAME, resource_aud, "XAA sample: redeems the ID-JAG and mints the tool token.")
    ensure_scope(okta, as2["id"], resource_scope, "Read the signed-in user's todo items.")

    # Persist immediately: a failure later in the run must not lose the ids of
    # servers that already exist in the tenant.
    save_env(
        AGENTCORE_AS_ID=as1["id"],
        AGENTCORE_AS_ISSUER=f"{org}/oauth2/{as1['id']}",
        AGENTCORE_AUDIENCE=agentcore_aud,
        RESOURCE_AS_ID=as2["id"],
        RESOURCE_AS_ISSUER=f"{org}/oauth2/{as2['id']}",
        RESOURCE_AUDIENCE=resource_aud,
        RESOURCE_SCOPE=resource_scope,
    )

    print("\n[3/5] Apps")
    login = ensure_login_app(okta, redirect_uri)
    agent_app = ensure_agent_app(okta)
    assign_everyone(okta, login["id"], LOGIN_LABEL)

    print("\n[4/5] Client secrets")
    login_secret = client_secret(okta, login, "LOGIN_CLIENT_SECRET", args.rotate_secrets)
    agent_secret = client_secret(okta, agent_app, "AGENT_APP_CLIENT_SECRET", args.rotate_secrets)
    print(f"  login secret:  {'minted/read' if login_secret else 'unchanged (already in .env)'}")
    print(f"  agent secret:  {'minted/read' if agent_secret else 'unchanged (already in .env)'}")

    ids = {
        "LOGIN_CLIENT_ID": login["credentials"]["oauthClient"]["client_id"],
        "AGENT_APP_CLIENT_ID": agent_app["credentials"]["oauthClient"]["client_id"],
    }
    if login_secret:
        ids["LOGIN_CLIENT_SECRET"] = login_secret
    if agent_secret:
        ids["AGENT_APP_CLIENT_SECRET"] = agent_secret
    save_env(**ids)

    print("\n[5/5] Access policies (sleeping 3s for propagation)")
    time.sleep(3)
    login_id = login["credentials"]["oauthClient"]["client_id"]
    agent_id = agent_app["credentials"]["oauthClient"]["client_id"]
    ensure_policy(
        okta,
        as1["id"],
        "XAA sample - Login app",
        login_id,
        # refresh_token is NOT valid in a policy rule -- Okta rejects it and lists
        # the allowed set. Refresh tokens are governed by the app's grant_types plus
        # the offline_access scope, not by this condition.
        ["authorization_code"],
        ["openid", "profile", "email", "offline_access", scope_agent],
    )
    ensure_policy(
        okta,
        as1["id"],
        "XAA sample - Agent OBO",
        agent_id,
        [TOKEN_EXCHANGE],
        [scope_tools],
    )
    # Leg 2 of the ID-JAG flow is a jwt-bearer grant presented by the AI Agent.
    # Its wlp... client id does not exist yet, so the rule admits the grant and
    # IDP_SETUP_OKTA.md has you add the client to this policy after registration.
    ensure_policy(
        okta,
        as2["id"],
        "XAA sample - Resource jwt-bearer",
        agent_id,
        [JWT_BEARER, TOKEN_EXCHANGE],
        [resource_scope],
    )

    updates = {
        "AGENTCORE_AS_ID": as1["id"],
        "AGENTCORE_AS_ISSUER": f"{org}/oauth2/{as1['id']}",
        "AGENTCORE_AUDIENCE": agentcore_aud,
        "RESOURCE_AS_ID": as2["id"],
        "RESOURCE_AS_ISSUER": f"{org}/oauth2/{as2['id']}",
        "RESOURCE_AUDIENCE": resource_aud,
        "RESOURCE_SCOPE": resource_scope,
        "LOGIN_CLIENT_ID": login_id,
        "AGENT_APP_CLIENT_ID": agent_id,
    }
    if login_secret:
        updates["LOGIN_CLIENT_SECRET"] = login_secret
    if agent_secret:
        updates["AGENT_APP_CLIENT_SECRET"] = agent_secret
    save_env(**updates)
    print("\n  ✓ wrote ids, issuers and scopes to .env")

    print(
        "\n"
        "Next, the one manual step -- register the AI Agent (wlp...):\n"
        "  1. python scripts/gen_keypair.py\n"
        "  2. Okta Admin -> Directory -> AI Agents -> Register AI Agent -> manually\n"
        "     - Credentials: Public key / Private key; paste scripts/keys/okta_public_jwk.json\n"
        "     - Delegations -> Add caller: caller = '" + LOGIN_LABEL + "',\n"
        "       on behalf of = User, authorization server = the ORG server\n"
        "       (NOT a custom AS -- only the org server mints an ID-JAG)\n"
        f"     - Resource connections -> Add: Authorization server = '{AS2_NAME}',\n"
        f"       scope {resource_scope}\n"
        "     - Actions -> Activate  (a STAGED agent fails every call: invalid_client)\n"
        "  3. Add the agent's wlp... client id to the 'XAA sample - Resource jwt-bearer'\n"
        f"     policy on '{AS2_NAME}' (its client allowlist)\n"
        "  4. Put the wlp... id in AI_AGENT_CLIENT_ID and the key's kid in AI_AGENT_KEY_KID\n"
        "\nThen: python scripts/verify_ai_agent.py"
    )


if __name__ == "__main__":
    main()
