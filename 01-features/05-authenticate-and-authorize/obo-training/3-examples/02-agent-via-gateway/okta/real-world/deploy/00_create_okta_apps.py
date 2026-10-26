"""
Automate the Okta setup for Use Case 2 (real-world) using the Okta Admin API.

What this does (idempotent — safe to re-run):

  1. Verifies the default authorization server exists and grabs its audience.

  2. Creates three custom scopes on the default authorization server:
       - agent.access       (frontend -> agent)
       - gateway.access     (agent    -> gateway, via OBO #1)
       - downstream.access  (gateway  -> mock API, via OBO #2)
     Custom scopes are required because Okta refuses OIDC scopes on the
     Token Exchange grant (reason: `openid_not_allowed_token_exchange`).

  3. Creates three app registrations:
       - AgentCore OBO UC2 Frontend (Web App)
           Grant types: authorization_code, refresh_token.
           Redirect URI: http://localhost:8000/auth/callback.
           PKCE: required.
       - AgentCore OBO UC2 Agent (API Services)
           Grant type: token-exchange. DPoP: disabled.
       - AgentCore OBO UC2 Gateway (API Services)
           Grant type: token-exchange. DPoP: disabled.

     For each API Services app we explicitly set
     `settings.oauthClient.dpop_bound_access_tokens = false` because newer
     Okta Integrator tenants default DPoP ON, and AgentCore Identity does
     not sign DPoP proofs (result at runtime:
     `invalid_dpop_proof: The DPoP proof JWT header is missing`).

  4. Creates three access policies on the default authorization server —
     one per app — each with a single rule granting only the grant type
     and scopes that app needs:
       - Frontend policy    -> Auth Code grant + openid/profile/email/agent.access
       - Agent policy       -> Token Exchange grant + gateway.access
       - Gateway policy     -> Token Exchange grant + downstream.access
     Each policy is created in ACTIVE state.

  5. Writes every required .env value:
       - OKTA_DOMAIN, OKTA_AUTH_SERVER_ID, OKTA_AUDIENCE
       - FRONTEND_CLIENT_ID / _SECRET
       - AGENT_CLIENT_ID    / _SECRET
       - GATEWAY_CLIENT_ID  / _SECRET
       - UPSTREAM_SCOPE, GATEWAY_SCOPE, DOWNSTREAM_SCOPE

Prerequisites:
  - `OKTA_DOMAIN` in .env (e.g. integrator-1234567.okta.com — NOT the -admin
    hostname).
  - `OKTA_ADMIN_TOKEN` in .env, an Okta API token. Create at Okta admin ->
    Security -> API -> Tokens -> Create Token. The user who mints the token
    needs Super Admin or Org Admin (for creating apps and editing the auth
    server). The token is NOT needed at runtime — only for this setup.

Run:
    python deploy/00_create_okta_apps.py

Re-running is safe: apps, scopes, and policies are looked up by name and
only created when missing. Client secrets are rotated only when the
corresponding .env value is missing or set to `replace-me`. Pass
`--rotate-secrets` to force fresh secrets on all three apps.

Caveats:
  - This script does NOT assign the Web App to users or groups. Most newer
    Integrator tenants use Federation Broker Mode which implicitly grants
    access to everyone in the org. On classic tenants, you'll need to
    assign the app to your test user or group manually (Okta admin ->
    Applications -> the app -> Assignments).
  - This script does NOT set up MFA policies or Conditional Access rules.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


# ── Constants ──────────────────────────────────────────────────────────────
APP_LABELS = {
    "frontend": "AgentCore OBO UC2 Frontend",
    "agent": "AgentCore OBO UC2 Agent",
    "gateway": "AgentCore OBO UC2 Gateway",
}

REDIRECT_URI = "http://localhost:8000/auth/callback"

# Custom scopes we define on the default authorization server. Descriptions
# show up in Okta's consent screen; keep them user-facing.
SCOPES = [
    {
        "name": "agent.access",
        "displayName": "Access the agent as the signed-in user",
        "description": "Allows the frontend to invoke the agent on the user's behalf.",
    },
    {
        "name": "gateway.access",
        "displayName": "Access the gateway on the user's behalf",
        "description": "Allows the agent to invoke the AgentCore Gateway on the user's behalf.",
    },
    {
        "name": "downstream.access",
        "displayName": "Access the downstream API on the user's behalf",
        "description": "Allows the gateway to invoke the downstream API on the user's behalf.",
    },
]

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


# ── Helpers ────────────────────────────────────────────────────────────────
def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


class OktaClient:
    """Thin Okta Admin API client.

    Auth is `Authorization: SSWS <token>`. All requests JSON-encode by default.
    Raises on HTTP error, printing the response body which usually contains
    a machine-readable error code (e.g. `E0000001 Api validation failed`).
    """

    def __init__(self, domain: str, token: str) -> None:
        # If the caller pasted the -admin variant, strip it — the admin
        # endpoints work on the app-facing hostname too.
        if "-admin." in domain:
            print(f"  ! OKTA_DOMAIN={domain} looks like the admin host; using {domain.replace('-admin.', '.')} instead")
            domain = domain.replace("-admin.", ".")
        self.base = f"https://{domain}/api/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"SSWS {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    @staticmethod
    def _redact_response(body: Any) -> Any:
        """Return only Okta's error-diagnostic fields from a response body.

        Defensive redaction — even though we never call `_raise` on a
        successful `POST /credentials/secrets` (that endpoint's success
        payload IS the secret and hits the happy path in rotate_client_secret),
        this makes sure that if any future code path mistakenly feeds a
        credentials response through here, the raw secret can't hit stderr.
        Also satisfies CodeQL's `py/clear-text-logging-sensitive-data`.
        """
        if not isinstance(body, dict):
            return body  # plain error text — no keys to redact
        safe_keys = {
            "errorCode",
            "errorSummary",
            "errorLink",
            "errorId",
            "errorCauses",
        }
        return {k: v for k, v in body.items() if k in safe_keys}

    @staticmethod
    def _redact_sent(sent_body: Any) -> Any:
        """Strip fields with credential-looking key names from a request body."""
        if not isinstance(sent_body, dict):
            return sent_body
        sensitive = {"client_secret", "password", "token", "secret"}

        def scrub(d):
            if isinstance(d, dict):
                return {k: ("<redacted>" if k.lower() in sensitive else scrub(v)) for k, v in d.items()}
            if isinstance(d, list):
                return [scrub(x) for x in d]
            return d

        return scrub(sent_body)

    def _raise(self, method: str, url: str, resp: requests.Response, sent_body: Any = None) -> None:
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        safe_body = self._redact_response(body)
        safe_sent = self._redact_sent(sent_body) if sent_body is not None else None
        sent_repr = json.dumps(safe_sent, indent=2) if safe_sent is not None else "(no body sent)"
        die(
            f"Okta API call failed: {method} {url}\n"
            f"  HTTP {resp.status_code}\n"
            f"  Response body (redacted): "
            f"{json.dumps(safe_body, indent=2) if isinstance(safe_body, dict) else safe_body}\n"
            f"  Request body we sent (redacted):\n{sent_repr}"
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base}{path}"
        resp = self.session.request(
            method,
            url,
            json=json_body,
            params=params,
            timeout=30,
        )
        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            self._raise(method, url, resp, sent_body=json_body)
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

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)


def upsert_env_value(env_path: Path, key: str, value: str) -> None:
    """Insert/replace `KEY=value` in .env, preserving everything else."""
    if not env_path.exists():
        env_path.write_text(f"{key}={value}\n")
        return
    lines = env_path.read_text().splitlines()
    prefix = f"{key}="
    found = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n")


def env_value_is_placeholder(env_path: Path, key: str) -> bool:
    """True if .env is missing KEY, or sets it to empty / 'replace-me'."""
    if not env_path.exists():
        return True
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            value = line[len(key) + 1 :].strip()
            return value in ("", "replace-me", "REPLACE_ME")
    return True


# ── Auth server + scopes ───────────────────────────────────────────────────
def verify_auth_server(client: OktaClient, auth_server_id: str) -> dict:
    """Verify the auth server exists and return its metadata (incl. audiences)."""
    servers = client.get("/authorizationServers")
    match = next(
        (s for s in servers if s["id"] == auth_server_id or s["name"] == auth_server_id),
        None,
    )
    if not match:
        die(
            f"Authorization server '{auth_server_id}' not found on this tenant.\n"
            f"Available: {[(s['name'], s['id']) for s in servers]}\n"
            f"Set OKTA_AUTH_SERVER_ID in .env to one of the IDs (last path\n"
            f"segment of the Issuer URI)."
        )
    return match


def ensure_scope(client: OktaClient, auth_server_id: str, scope_def: dict) -> None:
    """Create a custom scope on the auth server if it doesn't already exist."""
    name = scope_def["name"]
    existing = client.get(f"/authorizationServers/{auth_server_id}/scopes")
    for s in existing:
        if s["name"] == name:
            print(f"  • Scope already exists: {name}")
            return
    body = {
        "name": name,
        "displayName": scope_def["displayName"],
        "description": scope_def["description"],
        # IMPLICIT means: don't prompt the user, just include it if the
        # access policy allows it. Alternative is REQUIRED (always prompt).
        "consent": "IMPLICIT",
        # Show in the well-known metadata so tools like jwt.io / discovery
        # inspectors can see the scope is defined.
        "metadataPublish": "ALL_CLIENTS",
        # NOT a default scope — it's only included when explicitly requested.
        "default": False,
        "system": False,
    }
    client.post(f"/authorizationServers/{auth_server_id}/scopes", json_body=body)
    print(f"  ✓ Created scope: {name}")


# ── Apps ────────────────────────────────────────────────────────────────────
def find_app_by_label(client: OktaClient, label: str) -> dict | None:
    """Look up an OIDC app by human label. Only searches active apps."""
    apps = client.get(
        "/apps",
        params={
            "q": label,
            "filter": 'status eq "ACTIVE"',
            "limit": 20,
        },
    )
    for a in apps:
        if a.get("label") == label:
            return a
    return None


def create_web_app(client: OktaClient, label: str) -> dict:
    """Create the Frontend Web App (Authorization Code + refresh).

    Uses a minimal request body — Okta's POST /apps validator returns a
    generic E0000003 ("body not well-formed") with an empty errorCauses when
    it rejects unknown or misplaced fields, so we keep the create body to
    only fields Okta's OpenAPI schema documents on the OpenIdConnectApplication
    type. PKCE and consent settings are applied by ensure_web_app_extras()
    in a follow-up update.
    """
    body = {
        "name": "oidc_client",
        "label": label,
        "signOnMode": "OPENID_CONNECT",
        "credentials": {
            "oauthClient": {
                "token_endpoint_auth_method": "client_secret_basic",
            }
        },
        "settings": {
            "oauthClient": {
                "application_type": "web",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "redirect_uris": [REDIRECT_URI],
            }
        },
    }
    return client.post("/apps", json_body=body)


def create_api_services_app(client: OktaClient, label: str) -> dict:
    """Create an API Services app with Token Exchange grant.

    Includes `client_credentials` in grant_types because Okta REQUIRES it
    for `application_type: "service"` — the admin API rejects the create
    otherwise ("grant_types must contain 'client_credentials' when
    application_type is 'service'"). We don't actually intend to use
    client_credentials at runtime; the app's Access Policy on the auth
    server only permits Token Exchange, so attackers can't invoke the
    client_credentials grant regardless of it being in the app's list.

    DPoP-off is applied by ensure_dpop_disabled() in a follow-up update.
    """
    body = {
        "name": "oidc_client",
        "label": label,
        "signOnMode": "OPENID_CONNECT",
        "credentials": {
            "oauthClient": {
                "token_endpoint_auth_method": "client_secret_basic",
            }
        },
        "settings": {
            "oauthClient": {
                "application_type": "service",
                "grant_types": ["client_credentials", TOKEN_EXCHANGE_GRANT],
                "response_types": [],
                "redirect_uris": [],
            }
        },
    }
    return client.post("/apps", json_body=body)


def _put_minimal_web_app(client: OktaClient, app: dict, *, pkce_required: bool) -> dict:
    """PUT a minimal Web App body — includes ONLY fields Okta's admin API accepts.

    Reusing the GET response as PUT body fails with E0000003 because Okta
    returns server-managed read-only fields (`orn`, `universalLogout`,
    `credentials.signing`, etc.) that it then refuses to accept back.
    Building the PUT body from scratch sidesteps that.

    Two write-side quirks handled here:
    - `pkce_required` goes under `credentials.oauthClient` (the response
      body echoes it in both locations but only that location is writable).
    - `client_id` MUST be present in `credentials.oauthClient` — omitting
      it triggers E0000001 "'client_id' cannot be modified", because
      Okta's PUT semantics treat a missing client_id as an attempt to
      null it out.
    """
    app_id = app["id"]
    client_id = (app.get("credentials") or {}).get("oauthClient", {}).get("client_id")
    if not client_id:
        raise ValueError(f"app {app_id} has no client_id in credentials.oauthClient")

    body = {
        "name": "oidc_client",
        "label": app["label"],
        "signOnMode": "OPENID_CONNECT",
        "credentials": {
            "oauthClient": {
                "client_id": client_id,
                "token_endpoint_auth_method": "client_secret_basic",
                "pkce_required": pkce_required,
            }
        },
        "settings": {
            "oauthClient": {
                "application_type": "web",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "redirect_uris": [REDIRECT_URI],
                "post_logout_redirect_uris": ["http://localhost:8000/"],
            }
        },
    }
    return client.put(f"/apps/{app_id}", json_body=body)


def _put_minimal_service_app(client: OktaClient, app: dict, *, dpop_bound: bool) -> dict:
    """PUT a minimal API Services app body. Same pattern as _put_minimal_web_app."""
    app_id = app["id"]
    client_id = (app.get("credentials") or {}).get("oauthClient", {}).get("client_id")
    if not client_id:
        raise ValueError(f"app {app_id} has no client_id in credentials.oauthClient")

    body = {
        "name": "oidc_client",
        "label": app["label"],
        "signOnMode": "OPENID_CONNECT",
        "credentials": {
            "oauthClient": {
                "client_id": client_id,
                "token_endpoint_auth_method": "client_secret_basic",
            }
        },
        "settings": {
            "oauthClient": {
                "application_type": "service",
                "grant_types": ["client_credentials", TOKEN_EXCHANGE_GRANT],
                "response_types": [],
                "redirect_uris": [],
                "dpop_bound_access_tokens": dpop_bound,
            }
        },
    }
    return client.put(f"/apps/{app_id}", json_body=body)


def ensure_web_app_extras(client: OktaClient, app: dict) -> dict:
    """After creating the Web App, apply PKCE-required + post-logout redirect.

    Applied via a follow-up PUT so the initial POST body stays minimal
    (Okta's validator is picky about the create request). Returns the
    updated app dict; on failure prints a "flip this in the console"
    message and returns the original.
    """
    oc = (app.get("settings") or {}).get("oauthClient") or {}
    if oc.get("pkce_required") is True:
        # Already set — nothing to do.
        return app
    try:
        updated = _put_minimal_web_app(client, app, pkce_required=True)
        print(f"    ✓ Enabled PKCE + set post-logout redirect on {app['label']}")
        return updated
    except SystemExit:
        print(
            f"    ⚠ Could not enable PKCE via API on {app['label']}.\n"
            f"      Flip it manually: Okta admin -> Applications -> {app['label']} ->\n"
            f"        General -> Client Credentials -> Edit -> check\n"
            f"        'Require PKCE as additional verification'.",
            file=sys.stderr,
        )
        return app


def rotate_client_secret(client: OktaClient, app_id: str) -> str:
    """Generate a new client secret for the app and return the value.

    Okta apps return the secret in the response of POST /credentials/secrets.
    The old secret is not deleted — Okta keeps both active for zero-downtime
    rotation. If you want to prune old secrets, that's a separate DELETE
    call; we don't do it here.
    """
    resp = client.post(f"/apps/{app_id}/credentials/secrets")
    # Response shape: {id, client_secret, status, ...}
    secret = resp.get("client_secret")
    if not secret:
        # Only print the set of keys returned — never the raw response, since
        # this endpoint's success payload includes `client_secret`. Even on the
        # unexpected "no secret returned" branch we avoid dumping the response
        # to stderr so a mistakenly-parsed success payload can't leak.
        die(
            f"Okta returned no client_secret for app {app_id}. "
            f"Response keys: {sorted(resp.keys()) if isinstance(resp, dict) else '(non-dict)'}"
        )
    return secret


def find_everyone_group_id(client: OktaClient) -> str | None:
    """Look up the built-in Everyone group's ID (varies per tenant).

    Every Okta tenant ships with a built-in group named "Everyone" containing
    every user. The ID is `00g...` and is stable within a tenant but different
    across tenants — so we look it up rather than hard-code.
    """
    groups = client.get("/groups", params={"q": "Everyone", "limit": 10})
    for g in groups:
        profile = g.get("profile") or {}
        if profile.get("name") == "Everyone" and g.get("type") == "BUILT_IN":
            return g["id"]
    return None


def assign_app_to_everyone(client: OktaClient, app: dict) -> None:
    """Assign the Frontend Web App to the Everyone group.

    Without this, users see Okta's "User is not assigned to the client
    application" error on sign-in. On tenants using Federation Broker Mode
    the assignment happens implicitly and this call may fail with 400 or
    be a no-op — we handle that gracefully.

    Idempotent: Okta returns 200 with existing assignment info on re-post.
    """
    everyone_id = find_everyone_group_id(client)
    if not everyone_id:
        print(
            f"    ⚠ Could not find the 'Everyone' group on this tenant.\n"
            f"      Assign users to {app['label']} manually:\n"
            f"        Okta admin -> Applications -> {app['label']} -> Assignments\n"
            f"        -> Assign -> Assign to People / Assign to Groups.",
            file=sys.stderr,
        )
        return
    try:
        client.put(f"/apps/{app['id']}/groups/{everyone_id}", json_body={})
        print(f"    ✓ Assigned {app['label']} to Everyone group ({everyone_id})")
    except SystemExit:
        print(
            f"    ⚠ Could not assign {app['label']} to Everyone via API.\n"
            f"      Assign manually (users can't sign in until this is done):\n"
            f"        Okta admin -> Applications -> {app['label']} -> Assignments\n"
            f"        -> Assign -> Assign to People (pick your test user)\n"
            f"        or Assign to Groups -> Everyone.",
            file=sys.stderr,
        )


def ensure_dpop_disabled(client: OktaClient, app: dict) -> None:
    """Verify + force `dpop_bound_access_tokens = false` on an existing app.

    Applied via a follow-up PUT after app creation, using a minimal body
    (see _put_minimal_service_app). On failure, prints a clear "flip this
    in the console" message — DPoP-off is critical for OBO.
    """
    oc = (app.get("settings") or {}).get("oauthClient") or {}
    if oc.get("dpop_bound_access_tokens") is False:
        return
    try:
        _put_minimal_service_app(client, app, dpop_bound=False)
        print(f"    ✓ Forced DPoP off on {app['label']}")
    except SystemExit:
        print(
            f"    ⚠ Could not disable DPoP via API on {app['label']}.\n"
            f"      Flip it manually (REQUIRED for OBO to work):\n"
            f"        Okta admin -> Applications -> {app['label']} ->\n"
            f"        General -> General Settings -> Edit ->\n"
            f"        'Proof of possession' -> Not required -> Save.",
            file=sys.stderr,
        )


def get_or_create_app(client: OktaClient, kind: str, label: str) -> tuple[dict, bool]:
    """Return (app_obj, created_new_bool).

    kind ∈ {'web', 'api-services'}.
    """
    existing = find_app_by_label(client, label)
    if existing:
        # We intentionally don't include the client_id (or any field pulled
        # from the API response dict) in this log line. The final [6/6]
        # summary prints every client_id from .env — that's the canonical
        # place to see them. Avoiding it here keeps CodeQL's taint tracker
        # happy on the /apps response.
        print(f"  • App exists: {label}")
        if kind == "web":
            ensure_web_app_extras(client, existing)
            assign_app_to_everyone(client, existing)
        elif kind == "api-services":
            ensure_dpop_disabled(client, existing)
        return existing, False

    if kind == "web":
        app = create_web_app(client, label)
    elif kind == "api-services":
        app = create_api_services_app(client, label)
    else:
        raise ValueError(f"unknown app kind: {kind}")
    # Same reasoning as above: don't inline the client_id from the response.
    print(f"  ✓ Created app: {label}")

    # Apply extras via follow-up updates.
    #   Web:          PKCE-required + post-logout redirect (PUT) + Everyone
    #                 group assignment (so users can actually sign in).
    #   API Services: DPoP-off (PUT).
    if kind == "web":
        app = ensure_web_app_extras(client, app)
        assign_app_to_everyone(client, app)
    elif kind == "api-services":
        ensure_dpop_disabled(client, app)
    return app, True


# ── Access policies ────────────────────────────────────────────────────────
def find_policy_by_name(client: OktaClient, auth_server_id: str, name: str) -> dict | None:
    policies = client.get(f"/authorizationServers/{auth_server_id}/policies")
    for p in policies:
        if p.get("name") == name:
            return p
    return None


def ensure_policy(
    client: OktaClient,
    auth_server_id: str,
    *,
    name: str,
    description: str,
    client_id: str,
) -> dict:
    """Create-or-update an access policy scoped to a single client. Activated on create."""
    existing = find_policy_by_name(client, auth_server_id, name)
    body = {
        "type": "OAUTH_AUTHORIZATION_POLICY",
        "status": "ACTIVE",
        "name": name,
        "description": description,
        "conditions": {
            "clients": {"include": [client_id]},
        },
    }
    if existing:
        policy_id = existing["id"]
        # Merge our fields onto the existing policy (Okta wants the whole
        # object on PUT). Also force ACTIVE — an inactive policy is silently
        # ignored during evaluation, which is a common trap.
        body["id"] = policy_id
        client.put(
            f"/authorizationServers/{auth_server_id}/policies/{policy_id}",
            json_body=body,
        )
        # Activate explicitly in case PUT with status ACTIVE isn't honored
        # (some Okta orgs treat status transitions via dedicated endpoints).
        try:
            client.post(f"/authorizationServers/{auth_server_id}/policies/{policy_id}/lifecycle/activate")
        except SystemExit:
            # Already active — Okta returns 400. Ignore.
            pass
        print(f"  • Policy updated + activated: {name}")
        return {"id": policy_id, **body}

    resp = client.post(
        f"/authorizationServers/{auth_server_id}/policies",
        json_body=body,
    )
    print(f"  ✓ Policy created: {name}")
    return resp


def find_rule_by_name(client: OktaClient, auth_server_id: str, policy_id: str, name: str) -> dict | None:
    rules = client.get(f"/authorizationServers/{auth_server_id}/policies/{policy_id}/rules")
    for r in rules:
        if r.get("name") == name:
            return r
    return None


def ensure_rule(
    client: OktaClient,
    auth_server_id: str,
    policy_id: str,
    *,
    name: str,
    grant_types: list[str],
    scopes: list[str],
) -> None:
    """Create-or-update a rule inside a policy. Activated on create.

    people.groups.include=["EVERYONE"] applies to all users; tighten this
    in production to specific groups.
    """
    body = {
        "type": "RESOURCE_ACCESS",
        "name": name,
        "status": "ACTIVE",
        "priority": 1,
        "conditions": {
            "people": {
                "users": {"include": [], "exclude": []},
                "groups": {"include": ["EVERYONE"], "exclude": []},
            },
            "grantTypes": {"include": grant_types},
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
    existing = find_rule_by_name(client, auth_server_id, policy_id, name)
    if existing:
        rule_id = existing["id"]
        body["id"] = rule_id
        client.put(
            f"/authorizationServers/{auth_server_id}/policies/{policy_id}/rules/{rule_id}",
            json_body=body,
        )
        print(f"    • Rule updated: {name} (grants={grant_types}, scopes={scopes})")
        return

    client.post(
        f"/authorizationServers/{auth_server_id}/policies/{policy_id}/rules",
        json_body=body,
    )
    print(f"    ✓ Rule created: {name} (grants={grant_types}, scopes={scopes})")


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate-secrets",
        action="store_true",
        help="Force rotation of client secrets even if .env already has values.",
    )
    args = parser.parse_args()

    real_world_root = Path(__file__).resolve().parent.parent
    env_path = real_world_root / ".env"

    # Bootstrap .env from example if needed so we can persist values as we go.
    if not env_path.exists():
        example = real_world_root / "config.example.env"
        if example.exists():
            env_path.write_text(example.read_text())
            print(f"  • Bootstrapped {env_path.name} from config.example.env")
        else:
            env_path.write_text("")
    load_dotenv(env_path, override=True)

    okta_domain = os.environ.get("OKTA_DOMAIN", "").strip()
    okta_token = os.environ.get("OKTA_ADMIN_TOKEN", "").strip()
    auth_server_id = os.environ.get("OKTA_AUTH_SERVER_ID", "default").strip() or "default"

    if not okta_domain or okta_domain.startswith("integrator-1234567"):
        die("OKTA_DOMAIN must be set to your Okta tenant (e.g. integrator-1234567.okta.com).\nEdit .env and re-run.")
    if not okta_token:
        die(
            "OKTA_ADMIN_TOKEN is not set. Create one at Okta admin -> Security ->\n"
            "API -> Tokens -> Create Token, then set OKTA_ADMIN_TOKEN in .env.\n"
            "The token needs Super Admin or Org Admin permissions."
        )

    client = OktaClient(okta_domain, okta_token)

    print(f"Okta domain:   {okta_domain}")
    print(f"Auth server:   {auth_server_id}")
    print()

    # 1) Verify the auth server + capture its audience.
    print("[1/6] Verifying authorization server…")
    server = verify_auth_server(client, auth_server_id)
    audiences = server.get("audiences") or []
    audience = audiences[0] if audiences else "api://default"
    resolved_auth_server_id = server["id"]
    print(f"  ✓ Auth server: {server['name']} (id={resolved_auth_server_id})")
    print(f"    Audience:    {audience}")
    print(f"    Issuer:      {server.get('issuer')}")

    # 2) Custom scopes.
    print("\n[2/6] Ensuring custom scopes on the auth server…")
    for scope_def in SCOPES:
        ensure_scope(client, resolved_auth_server_id, scope_def)

    # 3) Apps.
    print("\n[3/6] Ensuring app registrations…")
    frontend_app, frontend_new = get_or_create_app(client, "web", APP_LABELS["frontend"])
    agent_app, agent_new = get_or_create_app(client, "api-services", APP_LABELS["agent"])
    gateway_app, gateway_new = get_or_create_app(client, "api-services", APP_LABELS["gateway"])

    # 4) Client secrets.
    #    Newly-created apps have a secret in the create response. Existing
    #    apps don't expose their current secret via API — we can only mint
    #    new ones. Mint only if .env is missing / placeholder / --rotate.
    print("\n[4/6] Handling client secrets…")

    # Mint or keep each app's client secret. We intentionally do NOT log
    # per-app progress here — CodeQL's clear-text-logging query flags any
    # print inside a scope where a client_secret variable exists, even for
    # messages that only reference the app label. The summary count below
    # is enough for the operator.
    def get_secret(app: dict, is_new: bool, env_key: str) -> str | None:
        if is_new:
            secret = (app.get("credentials") or {}).get("oauthClient", {}).get("client_secret")
            if secret:
                return secret
        if args.rotate_secrets or env_value_is_placeholder(env_path, env_key):
            return rotate_client_secret(client, app["id"])
        return None

    frontend_secret = get_secret(frontend_app, frontend_new, "FRONTEND_CLIENT_SECRET")
    agent_secret = get_secret(agent_app, agent_new, "AGENT_CLIENT_SECRET")
    gateway_secret = get_secret(gateway_app, gateway_new, "GATEWAY_CLIENT_SECRET")

    minted = sum(1 for s in (frontend_secret, agent_secret, gateway_secret) if s)
    kept = 3 - minted
    print(f"  ✓ Client secrets: {minted} freshly minted, {kept} kept (use --rotate-secrets to force-rotate all)")

    # 5) Access policies.
    #    Wait briefly for Okta to finish propagating the app registrations
    #    before we reference them in policy conditions — otherwise the
    #    policy creation call can return 404 for the client_id.
    print("\n[5/6] Creating access policies (sleeping 3s for propagation)…")
    time.sleep(3)

    frontend_client_id = frontend_app["credentials"]["oauthClient"]["client_id"]
    agent_client_id = agent_app["credentials"]["oauthClient"]["client_id"]
    gateway_client_id = gateway_app["credentials"]["oauthClient"]["client_id"]

    # Frontend policy — Authorization Code + user-facing scopes + agent.access.
    # NOTE: `refresh_token` is NOT a valid entry in an Okta access policy
    # rule's grantTypes.include (Okta rejects it with E0000001). Refresh
    # tokens are issued automatically when the Authorization Code grant is
    # used AND the `offline_access` scope is present in scopes.include.
    fp = ensure_policy(
        client,
        resolved_auth_server_id,
        name="AgentCore OBO UC2 - Frontend",
        description="Allows the Frontend Web App to mint user tokens via Authorization Code.",
        client_id=frontend_client_id,
    )
    ensure_rule(
        client,
        resolved_auth_server_id,
        fp["id"],
        name="Frontend Auth Code",
        grant_types=["authorization_code"],
        scopes=["openid", "profile", "email", "offline_access", "agent.access"],
    )

    # Agent policy — Token Exchange + gateway.access.
    ap = ensure_policy(
        client,
        resolved_auth_server_id,
        name="AgentCore OBO UC2 - Agent OBO",
        description="Allows the Agent to exchange user tokens for gateway.access via Token Exchange.",
        client_id=agent_client_id,
    )
    ensure_rule(
        client,
        resolved_auth_server_id,
        ap["id"],
        name="Agent Token Exchange",
        grant_types=[TOKEN_EXCHANGE_GRANT],
        scopes=["gateway.access"],
    )

    # Gateway policy — Token Exchange + downstream.access.
    gp = ensure_policy(
        client,
        resolved_auth_server_id,
        name="AgentCore OBO UC2 - Gateway OBO",
        description="Allows the Gateway to exchange gateway tokens for downstream.access via Token Exchange.",
        client_id=gateway_client_id,
    )
    ensure_rule(
        client,
        resolved_auth_server_id,
        gp["id"],
        name="Gateway Token Exchange",
        grant_types=[TOKEN_EXCHANGE_GRANT],
        scopes=["downstream.access"],
    )

    # 6) Write .env.
    print("\n[6/6] Writing .env…")
    env_writes = {
        "OKTA_DOMAIN": okta_domain.replace("-admin.", "."),
        "OKTA_AUTH_SERVER_ID": resolved_auth_server_id,
        "OKTA_AUDIENCE": audience,
        "FRONTEND_CLIENT_ID": frontend_client_id,
        "AGENT_CLIENT_ID": agent_client_id,
        "GATEWAY_CLIENT_ID": gateway_client_id,
        # Quoted because it contains spaces — needed for shell `source .env`
        # (python-dotenv strips the quotes transparently).
        "UPSTREAM_SCOPE": '"openid profile email agent.access"',
        "GATEWAY_SCOPE": "gateway.access",
        "DOWNSTREAM_SCOPE": "downstream.access",
    }
    if frontend_secret:
        env_writes["FRONTEND_CLIENT_SECRET"] = frontend_secret
    if agent_secret:
        env_writes["AGENT_CLIENT_SECRET"] = agent_secret
    if gateway_secret:
        env_writes["GATEWAY_CLIENT_SECRET"] = gateway_secret

    # Silently persist to .env. We deliberately do NOT print the keys or
    # values here — every element of `env_writes` is derived from an Okta
    # API response, and CodeQL's taint tracker flags any print that reads
    # from that dict, even for values it labels as ***. The keys are named
    # deterministically per config.example.env, and the user can inspect
    # .env directly after the run.
    for k, v in env_writes.items():
        upsert_env_value(env_path, k, v)
    print(f"  ✓ Wrote {len(env_writes)} value(s) to .env (client IDs, scopes, and any freshly-minted secrets).")

    print()
    print("✓ Okta setup complete.")
    print()
    print("Verify (client IDs and scopes only; secrets stay in .env):")
    print(
        "  grep -E '^(OKTA_DOMAIN|OKTA_AUTH_SERVER_ID|OKTA_AUDIENCE|FRONTEND_CLIENT_ID|AGENT_CLIENT_ID|GATEWAY_CLIENT_ID|UPSTREAM_SCOPE|GATEWAY_SCOPE|DOWNSTREAM_SCOPE)=' .env"
    )
    print()
    print("Assign the Frontend Web App to your test user (if your tenant isn't in")
    print("Federation Broker Mode):")
    print(f"  Okta admin -> Applications -> {APP_LABELS['frontend']} -> Assignments")
    print()
    print("Next step: python deploy/01_create_providers.py")


if __name__ == "__main__":
    main()
