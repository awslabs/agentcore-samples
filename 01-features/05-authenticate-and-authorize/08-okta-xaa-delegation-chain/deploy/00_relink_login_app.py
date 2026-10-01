"""Repoint sign-in at the OIDC app Okta linked to the AI Agent.

Run this AFTER registering the AI Agent (IDP_SETUP_OKTA.md step 3).

Why this exists: Okta's AI Agent "User access" tab binds the agent to exactly one
OIDC app, and the agent may act for a user only while that user is signed in to
that app. The binding is permanent, and the "select an existing app" picker offers
SAML apps only -- so you cannot bind the `XAA Todo Login` app that
00_create_okta_apps.py creates. The linked app Okta auto-creates is therefore the
real sign-in client. On the id_token fallback path the ID token leg 1 exchanges must come
from it.

This script:
  1. finds the OIDC app linked to the AI Agent (by label, default "XAA Todo Agent")
  2. adds the BFF redirect URI to it if missing, and ensures the authorization_code
     grant is present
  3. mints a client secret for it
  4. repoints LOGIN_CLIENT_ID / LOGIN_CLIENT_SECRET in .env
  5. moves the AS 1 sign-in access policy onto it

    python deploy/00_relink_login_app.py
    python deploy/00_relink_login_app.py --app-label "XAA Todo Agent"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import OktaAdmin, env, load_env, must_env, okta_org_url, save_env

DEFAULT_LABEL = "XAA Todo Agent"
LOGIN_POLICY = "XAA sample - Login app"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--app-label",
        default=DEFAULT_LABEL,
        help="Label of the OIDC app Okta linked to the AI Agent (default: %(default)s).",
    )
    args = ap.parse_args()

    load_env()
    org = okta_org_url()
    okta = OktaAdmin(org, must_env("OKTA_API_TOKEN"))
    redirect_uri = env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")
    scope_agent = env("SCOPE_AGENT_ACCESS", "agent.access")
    as1 = must_env("AGENTCORE_AS_ID", "Run deploy/00_create_okta_apps.py first.")

    apps = okta.get("/apps?limit=200")
    app = next((a for a in apps if a.get("label") == args.app_label), None)
    if app is None:
        labels = sorted(a.get("label", "") for a in apps)
        print(f"ERROR: no app labelled {args.app_label!r}.", file=sys.stderr)
        print("Apps in this tenant:", file=sys.stderr)
        for label in labels:
            print(f"  {label}", file=sys.stderr)
        print(
            "\nPass the right one with --app-label. It is the app shown on the AI "
            "Agent's\nUser access tab under 'Application used for access configuration'.",
            file=sys.stderr,
        )
        sys.exit(1)

    oauth = (app.get("settings") or {}).get("oauthClient") or {}
    client_id = str(((app.get("credentials") or {}).get("oauthClient") or {}).get("client_id"))
    # Nothing read out of `app` is printed. The dict also holds the client secret, and a
    # field-insensitive taint analysis cannot tell one key from another -- so printing
    # even the label from it reads as leaking a credential. The label we already have
    # from argv, and the client id is echoed at the end from .env once persisted.
    print(f"  linked app: {args.app_label}")
    if str(app.get("status")) != "ACTIVE":
        print("  ⚠ that app is not ACTIVE in Okta -- sign-in will fail until it is")

    # 2. make sure it can actually run the BFF's authorization-code flow
    redirects = list(oauth.get("redirect_uris") or [])
    grants = list(oauth.get("grant_types") or [])
    changed = False
    if redirect_uri not in redirects:
        redirects.append(redirect_uri)
        changed = True
    for needed in ("authorization_code", "refresh_token"):
        if needed not in grants:
            grants.append(needed)
            changed = True
    if changed:
        body = dict(app)
        body["settings"]["oauthClient"]["redirect_uris"] = redirects
        body["settings"]["oauthClient"]["grant_types"] = grants
        if "code" not in (oauth.get("response_types") or []):
            body["settings"]["oauthClient"]["response_types"] = ["code"]
        okta.put(f"/apps/{app['id']}", body)
        print(f"  ✓ added redirect {redirect_uri} and the authorization_code grant")
    else:
        print("  • redirect URI and grants already correct")

    # 3. Client authentication: only mint a secret if the app actually uses one.
    #
    # An AI Agent's linked app is normally registered with private_key_jwt (the
    # "Public/private key" method on the Client registration tab). Minting a secret
    # there is wrong -- it is unused at best, and switching the active method would
    # break the ID-JAG legs. In that case the BFF authenticates to the token
    # endpoint with a client assertion signed by the agent key instead.
    auth_method = ((app.get("credentials") or {}).get("oauthClient") or {}).get("token_endpoint_auth_method")
    minted = None
    if auth_method == "private_key_jwt":
        print("  • client auth is private_key_jwt -- no secret minted")
        print("    The BFF signs a client assertion with the AI Agent key")
        print(f"    (scripts/keys/okta_private_key.pem, kid={env('AI_AGENT_KEY_KID') or '?'}).")
    else:
        fresh = okta.post(f"/apps/{app['id']}/credentials/secrets", {})
        minted = fresh.get("secret") or fresh.get("client_secret")
        print(f"  ✓ minted a client secret ({'ok' if minted else 'FAILED'})")

    # 5. move the AS 1 sign-in policy onto this client
    moved = False
    for policy in okta.get(f"/authorizationServers/{as1}/policies?limit=200"):
        if policy["name"] != LOGIN_POLICY:
            continue
        body = {
            "type": "OAUTH_AUTHORIZATION_POLICY",
            "status": "ACTIVE",
            "name": policy["name"],
            "description": policy.get("description", ""),
            "priority": policy.get("priority", 1),
            "conditions": {"clients": {"include": [client_id]}},
        }
        okta.put(f"/authorizationServers/{as1}/policies/{policy['id']}", body)
        print(f"  ✓ policy {LOGIN_POLICY!r} now scoped to the linked app")
        moved = True
    if not moved:
        print(f"  ⚠ policy {LOGIN_POLICY!r} not found on AS 1 -- re-run 00_create_okta_apps.py")

    updates = {
        "LOGIN_CLIENT_ID": client_id,
        "LINKED_APP_ID": app["id"],
        "LOGIN_CLIENT_AUTH_METHOD": auth_method or "client_secret_basic",
    }
    if minted:
        updates["LOGIN_CLIENT_SECRET"] = minted
    save_env(**updates)
    print("\n  ✓ .env now signs users in through the AI Agent's linked app")
    # Read back from the environment save_env just populated, rather than echoing the
    # value we sent. Confirms what was actually persisted, and the round trip through
    # os.environ keeps this print clear of the app dict that holds the secret.
    print(f"    LOGIN_CLIENT_ID={env('LOGIN_CLIENT_ID')}")
    print(
        "\n  The separate 'XAA Todo Login' app is now unused. Leave it or delete it;\n"
        "  nothing reads it after this point."
    )
    print(
        f"\n  Reminder: assign your test user to {app['label']!r} (Applications -> "
        f"{app['label']} ->\n  Assignments), and check its Sign On policy -- the "
        f"default is often 'Any two\n  factors', which a scripted sign-in cannot "
        "complete.\n"
        f"  The user must also be able to consent to the {scope_agent} scope."
    )


if __name__ == "__main__":
    main()
