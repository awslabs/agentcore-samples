"""
Create the Okta Web application used for user 3LO sign-in in the OBO
demo, mirroring the obo-training pattern but with PRIVATE_KEY_JWT
client authentication (instead of a client secret).

The OBO flow needs a user access token as the subject_token. That token
is obtained via AgentCore Identity's USER_FEDERATION flow, which
requires an Okta app with:
  - authorization_code grant
  - a redirect URI that points at AgentCore's managed callback (set
    later by setup/03b, after the credential provider is created)
  - client authentication configured for private_key_jwt with the same
    JWK we registered on the service app in setup/01

What this script does:
  1. Reads SIGNING_KMS_KEY_ARN from .env and re-derives the JWK from
     the KMS public key (deterministic kid = SHA-256[:16] of DER, same
     as setup/01).
  2. Calls Okta's Dynamic Client Registration endpoint
     (POST /oauth2/v1/clients per RFC 7591) with:
       - client_name        = OKTA_LOGIN_APP_LABEL
       - grant_types        = [authorization_code, refresh_token]
       - response_types     = [code]
       - token_endpoint_auth_method = private_key_jwt
       - application_type   = web
       - redirect_uris      = [placeholder]  ← setup/03b overwrites this
                                              with AgentCore's callback URL
       - jwks.keys          = [same JWK as setup/01]
  3. Adds the Web app's client_id to the Custom AS Access Policy's
     clients.include (the same policy setup/01a created).
  4. Writes OKTA_LOGIN_APP_ID and OKTA_LOGIN_APP_CLIENT_ID to .env.

Idempotent: if an app with OKTA_LOGIN_APP_LABEL already exists, it's
reused.

Prerequisites:
  - setup/00 (KMS key)
  - setup/01a (Access Policy exists - this script adds the Web app to
    its clients.include list)

Note about the placeholder redirect_uri: Okta requires at least one
redirect_uri to be registered when the app is created. We use a
non-routable placeholder here - setup/03b replaces it with AgentCore
Identity's real managed callback URL after that URL is known.

Usage:
    python setup/01b_create_okta_login_web_app.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import boto3
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_LABEL = "AgentCore Identity Private Key JWT Login App"
DEFAULT_POLICY_NAME = "AgentCore Identity Private Key JWT Sample"
# Placeholder Okta requires at DCR time. setup/03b will replace this
# with AgentCore Identity's actual managed callback URL.
PLACEHOLDER_REDIRECT_URI = "https://placeholder.example.com/set-by-setup-03b"


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
    if resp.status_code >= 400:
        print(f"✗ {action} → HTTP {resp.status_code}", file=sys.stderr)
        print(f"  {resp.text[:600]}", file=sys.stderr)
        resp.raise_for_status()


def b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def kms_public_key_to_jwk(der_public_key: bytes) -> dict:
    pubkey = load_der_public_key(der_public_key)
    if not isinstance(pubkey, rsa.RSAPublicKey):
        raise TypeError("This sample only supports RSA keys (RS256).")
    numbers = pubkey.public_numbers()
    kid = hashlib.sha256(der_public_key).hexdigest()[:16]
    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": b64url_uint(numbers.n),
        "e": b64url_uint(numbers.e),
    }


def get_kms_public_key_der(kms, key_arn: str) -> bytes:
    resp = kms.get_public_key(KeyId=key_arn)
    if resp.get("KeyUsage") != "SIGN_VERIFY":
        raise RuntimeError(f"KMS key {key_arn} has KeyUsage={resp.get('KeyUsage')!r}, expected SIGN_VERIFY.")
    return resp["PublicKey"]


def find_app_by_label(base_url: str, api_token: str, label: str) -> dict | None:
    resp = requests.get(
        f"{base_url}/api/v1/apps",
        headers=okta_headers(api_token),
        params={"q": label, "limit": 50},
        timeout=15,
    )
    raise_for_status(resp, "GET /apps")
    for app in resp.json():
        if app.get("label") == label:
            return app
    return None


def create_web_app_via_dcr(base_url: str, api_token: str, label: str, jwk: dict) -> dict:
    """Create a Web app with authorization_code grant + PRIVATE_KEY_JWT via DCR."""
    payload = {
        "client_name": label,
        "response_types": ["code"],
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "private_key_jwt",
        "application_type": "web",
        "redirect_uris": [PLACEHOLDER_REDIRECT_URI],
        "jwks": {"keys": [jwk]},
    }
    resp = requests.post(
        f"{base_url}/oauth2/v1/clients",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=30,
    )
    raise_for_status(resp, "POST /oauth2/v1/clients (Web login app)")
    return resp.json()


def set_federation_broker_mode(base_url: str, api_token: str, app_id: str, enabled: bool) -> None:
    """Toggle settings.implicitAssignment on the app.

    Okta's DCR endpoint doesn't accept the implicitAssignment field, so we
    fetch the app after DCR creation and PUT it back with the flag set.

    Federation Broker Mode (implicitAssignment=true) implicitly assigns
    every org user to the app, so no explicit user/group assignment is
    needed. BUT Okta refuses explicit user assignments while
    implicitAssignment is true. When we're going to assign a specific
    user via OKTA_ASSIGN_USER_LOGIN, this must be false. When we're not,
    turning it on is a convenient way to grant access to the whole org.
    """
    action = "Enable" if enabled else "Disable"
    get = requests.get(
        f"{base_url}/api/v1/apps/{app_id}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(get, f"GET /apps/{app_id}")
    app = get.json()

    settings = app.setdefault("settings", {})
    current = bool(settings.get("implicitAssignment"))
    if current == enabled:
        state = "enabled" if enabled else "disabled"
        print(f"• Federation Broker Mode already {state} on the Web app")
        return
    settings["implicitAssignment"] = enabled

    put = requests.put(
        f"{base_url}/api/v1/apps/{app_id}",
        headers=okta_headers(api_token),
        data=json.dumps(app),
        timeout=15,
    )
    raise_for_status(put, f"PUT /apps/{app_id} ({action.lower()} Federation Broker Mode)")
    print(f"✓ {action}d Federation Broker Mode on the Web app")


def find_user_by_login(base_url: str, api_token: str, login: str) -> str | None:
    """Return the id of the Okta user with this login, or None if missing."""
    resp = requests.get(
        f"{base_url}/api/v1/users/{requests.utils.quote(login, safe='')}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code == 404:
        return None
    raise_for_status(resp, f"GET /users/{login}")
    return resp.json().get("id")


def assign_user_to_app(base_url: str, api_token: str, app_id: str, login: str) -> None:
    """Assign a specific Okta user to the app so they can complete 3LO sign-in.

    Some Okta tenants refuse the built-in 'Everyone' group assignment via
    API for DCR-created apps (E0000001 / GroupAppAssignment). Explicit
    user assignment always works, at the cost of one env var (OKTA_ASSIGN_USER_LOGIN).
    """
    user_id = find_user_by_login(base_url, api_token, login)
    if not user_id:
        print(
            f"⚠ Okta user with login {login!r} not found. Check "
            f"OKTA_ASSIGN_USER_LOGIN in .env - it must be an existing Okta user.",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = {
        "id": user_id,
        "scope": "USER",
        "credentials": {"userName": login},
    }
    resp = requests.post(
        f"{base_url}/api/v1/apps/{app_id}/users",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    if resp.status_code in (200, 201):
        print(f"✓ Assigned user {login} to Web app (user_id={user_id})")
    elif resp.status_code == 409:
        print(f"• User {login} already assigned to Web app")
    else:
        print(
            f"⚠ Assign user returned HTTP {resp.status_code}: {resp.text[:200]}",
            file=sys.stderr,
        )


def find_auth_policy_by_name(base_url: str, api_token: str, name: str) -> dict | None:
    """Return the ACCESS_POLICY (app authentication policy) with this name."""
    resp = requests.get(
        f"{base_url}/api/v1/policies",
        headers=okta_headers(api_token),
        params={"type": "ACCESS_POLICY"},
        timeout=15,
    )
    raise_for_status(resp, "GET /policies?type=ACCESS_POLICY")
    for policy in resp.json():
        if policy.get("name") == name:
            return policy
    return None


def create_permissive_auth_policy(base_url: str, api_token: str, name: str) -> dict:
    """Create an Okta Authentication Policy (ACCESS_POLICY) with an
    unconditional allow rule, so any assigned user can sign in with just
    a password (no MFA challenge).
    """
    payload = {
        "name": name,
        "type": "ACCESS_POLICY",
        "description": (
            "Permissive authentication policy for the AgentCore Identity "
            "Private Key JWT sample login app. Any user assigned to the "
            "app may sign in with a password."
        ),
    }
    resp = requests.post(
        f"{base_url}/api/v1/policies",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    raise_for_status(resp, "POST /policies (create ACCESS_POLICY)")
    policy = resp.json()
    print(f"✓ Created authentication policy '{name}' (id={policy['id']})")
    return policy


def ensure_allow_rule(base_url: str, api_token: str, policy_id: str) -> None:
    """Add an unconditional ALLOW rule to the auth policy if not already present."""
    get = requests.get(
        f"{base_url}/api/v1/policies/{policy_id}/rules",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(get, f"GET /policies/{policy_id}/rules")
    existing = get.json()

    for rule in existing:
        actions = rule.get("actions") or {}
        access = (actions.get("appSignOn") or {}).get("access")
        if access == "ALLOW":
            print(f"• Auth policy already has an ALLOW rule ({rule.get('name')!r})")
            return

    payload = {
        "name": "Allow assigned users",
        "type": "ACCESS_POLICY",
        "priority": 1,
        "actions": {
            "appSignOn": {
                "access": "ALLOW",
                "verificationMethod": {
                    "type": "ASSURANCE",
                    "factorMode": "1FA",
                    "reauthenticateIn": "PT2H",
                },
            }
        },
    }
    resp = requests.post(
        f"{base_url}/api/v1/policies/{policy_id}/rules",
        headers=okta_headers(api_token),
        data=json.dumps(payload),
        timeout=15,
    )
    raise_for_status(resp, f"POST /policies/{policy_id}/rules")
    print("✓ Added ALLOW rule (1FA / password) to auth policy")


def bind_app_to_auth_policy(base_url: str, api_token: str, app_id: str, policy_id: str) -> None:
    """Bind the Web app to a specific authentication policy."""
    resp = requests.put(
        f"{base_url}/api/v1/apps/{app_id}/policies/{policy_id}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code in (200, 204):
        print(f"✓ Bound Web app to authentication policy {policy_id}")
    else:
        raise_for_status(resp, f"PUT /apps/{app_id}/policies/{policy_id}")


DEFAULT_AUTH_POLICY_NAME = "AgentCore Identity Private Key JWT Sample Auth"


def ensure_permissive_auth_policy(base_url: str, api_token: str, app_id: str) -> None:
    """Idempotently create/reuse a permissive auth policy and bind it to the app."""
    policy_name = os.environ.get("OKTA_AUTH_POLICY_NAME") or DEFAULT_AUTH_POLICY_NAME
    policy = find_auth_policy_by_name(base_url, api_token, policy_name)
    if policy:
        print(f"• Auth policy '{policy_name}' already exists (id={policy['id']})")
    else:
        policy = create_permissive_auth_policy(base_url, api_token, policy_name)

    ensure_allow_rule(base_url, api_token, policy["id"])
    bind_app_to_auth_policy(base_url, api_token, app_id, policy["id"])


def add_client_to_policy(
    base_url: str,
    api_token: str,
    as_id: str,
    policy_name: str,
    client_id: str,
) -> None:
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies",
        headers=okta_headers(api_token),
        timeout=15,
    )
    raise_for_status(resp, "GET /policies")
    target = next((p for p in resp.json() if p.get("name") == policy_name), None)
    if not target:
        print(f"⚠ Could not find policy named {policy_name!r} on AS {as_id!r}. Run setup/01a first.")
        return

    policy_id = target["id"]
    include = target.get("conditions", {}).get("clients", {}).get("include", [])
    if client_id in include:
        print(f"• Login app already in policy '{policy_name}' clients.include")
        return

    merged = [*include, client_id]
    put_body = {
        "type": target.get("type", "OAUTH_AUTHORIZATION_POLICY"),
        "name": target.get("name"),
        "status": target.get("status", "ACTIVE"),
        "description": target.get("description", ""),
        "priority": target.get("priority", 1),
        "conditions": {"clients": {"include": merged}},
    }
    put_resp = requests.put(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{policy_id}",
        headers=okta_headers(api_token),
        data=json.dumps(put_body),
        timeout=15,
    )
    raise_for_status(put_resp, f"PUT policies/{policy_id}")
    print(f"✓ Added login app to policy '{policy_name}' clients.include")


def write_env_var(name: str, value: str) -> None:
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")
    key_arn = must_env("SIGNING_KMS_KEY_ARN")
    domain = must_env("OKTA_DOMAIN")
    api_token = must_env("OKTA_API_TOKEN")
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID") or "default"
    label = os.environ.get("OKTA_LOGIN_APP_LABEL") or DEFAULT_LABEL
    policy_name = os.environ.get("OKTA_POLICY_NAME") or DEFAULT_POLICY_NAME

    if "-admin." in domain:
        print(
            f"✗ OKTA_DOMAIN={domain!r} looks like the Okta admin host.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = f"https://{domain}"
    print(f"KMS key:  {key_arn}")
    print(f"Okta:     {base_url}")
    print(f"AS:       {as_id}")
    print(f"Label:    {label}")
    print()

    kms = boto3.client("kms", region_name=region)
    der = get_kms_public_key_der(kms, key_arn)
    jwk = kms_public_key_to_jwk(der)
    print(f"✓ Built JWK from KMS public key (kid={jwk['kid']})")

    existing = find_app_by_label(base_url, api_token, label)
    if existing:
        app_id = existing["id"]
        client_id = existing.get("credentials", {}).get("oauthClient", {}).get("client_id")
        print(f"• Login app already exists: {app_id}")
    else:
        print()
        print("→ Creating Okta Web app via Dynamic Client Registration...")
        created = create_web_app_via_dcr(base_url, api_token, label, jwk)
        client_id = created.get("client_id")
        app_id = client_id  # DCR-created client_id serves as the appId
        print(f"✓ Created Web app: {app_id}")

    print(f"  Client ID: {client_id}")

    # Grant sign-in access to the Web app. Two supported paths:
    #
    # 1. OKTA_ASSIGN_USER_LOGIN is set (recommended for the demo): we
    #    disable Federation Broker Mode and explicitly assign that user.
    #    Okta rejects explicit assignments while implicitAssignment=true
    #    with "It is not possible to assign users to an application in
    #    Federation Broker mode", so the toggle has to come first.
    #
    # 2. OKTA_ASSIGN_USER_LOGIN is not set: we enable Federation Broker
    #    Mode (implicitAssignment=true), which implicitly assigns every
    #    org user. Some tenants ignore this in the App Sign-On Policy -
    #    if 3LO sign-in fails, use path 1 instead.
    assign_login = os.environ.get("OKTA_ASSIGN_USER_LOGIN")
    if assign_login:
        print()
        print("→ Disabling Federation Broker Mode (required for explicit user assignment)...")
        set_federation_broker_mode(base_url, api_token, app_id, enabled=False)

        print()
        print(f"→ Assigning Okta user {assign_login!r} to the Web app...")
        assign_user_to_app(base_url, api_token, app_id, assign_login)
    else:
        print()
        print("→ Enabling Federation Broker Mode (no OKTA_ASSIGN_USER_LOGIN set)...")
        set_federation_broker_mode(base_url, api_token, app_id, enabled=True)
        print(
            "• Note: some Okta tenants don't fully honor Federation Broker "
            "Mode. If sign-in later fails with 'You are not allowed to "
            "access this app', set OKTA_ASSIGN_USER_LOGIN in .env to "
            "your Okta login and re-run this script."
        )

    # DCR-created apps get a default Authentication Policy that often
    # requires MFA factors the sample user hasn't enrolled. Bind the app
    # to a permissive policy so 3LO sign-in works with just a password.
    print()
    print("→ Ensuring permissive Authentication Policy on the Web app...")
    ensure_permissive_auth_policy(base_url, api_token, app_id)

    print()
    add_client_to_policy(base_url, api_token, as_id, policy_name, client_id)

    print()
    write_env_var("OKTA_LOGIN_APP_ID", app_id)
    write_env_var("OKTA_LOGIN_APP_CLIENT_ID", client_id)

    print()
    print("=" * 70)
    print("  Summary - step 01b: Okta Web app for user 3LO (OBO)")
    print("=" * 70)
    print("  What was created / updated:")
    print(f"    Label                     : {label}")
    print(f"    App / client ID           : {client_id}")
    print("    Application type          : web")
    print("    Grant types               : authorization_code, refresh_token")
    print("    Client auth method        : private_key_jwt (same KMS-hosted JWK")
    print(f"                                as the service app, kid={jwk['kid']})")
    if assign_login:
        print("    Access model              : explicit user assignment")
        print(f"    User assigned             : {assign_login}")
    else:
        print("    Access model              : Federation Broker Mode (implicitAssignment=true)")
    print(f"    Authentication Policy     : '{DEFAULT_AUTH_POLICY_NAME}'")
    print("                                (permissive 1FA / password only)")
    print("    redirect_uris             : [<placeholder>] - setup/03b will")
    print("                                replace this with the AgentCore-")
    print("                                managed callback URL")
    print()
    print("  Where to inspect it in the Okta admin console:")
    print(f"    Applications → Applications → search {label!r}")
    print("    General tab → Client Credentials → Public Keys")
    print(f"    Assignments tab (should list {assign_login or 'no explicit users'})")
    print(f"    Security → Authentication Policies → '{DEFAULT_AUTH_POLICY_NAME}'")
    print("    Direct URL:")
    print(f"      {base_url}/admin/app/oidc_client/instance/{app_id}")
    print()
    print("  Written to .env:")
    print(f"    OKTA_LOGIN_APP_ID={app_id}")
    print(f"    OKTA_LOGIN_APP_CLIENT_ID={client_id}")
    print()
    print("  Why this step matters:")
    print("    OBO needs a user access token as the subject_token. We mint")
    print("    that token by running the AgentCore-mediated USER_FEDERATION")
    print("    3LO flow (get_okta_user_jwt.py). That flow signs the user")
    print("    into this Web app; the code-exchange leg then uses")
    print("    PRIVATE_KEY_JWT with this app's JWK.")
    print()
    print("  Next steps (run in order):")
    print("    python setup/02_create_provider_m2m.py")
    print("    python setup/03_create_provider_obo.py")
    print("    python setup/03b_create_provider_client.py")


if __name__ == "__main__":
    main()
