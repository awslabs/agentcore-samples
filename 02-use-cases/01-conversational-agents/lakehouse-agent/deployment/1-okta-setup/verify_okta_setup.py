#!/usr/bin/env python3
"""
Verify the Okta two-client OBO topology (read-only).

Confirms that the Okta resources the OBO_Gateway path depends on are present
and correctly shaped — the two-app topology, the token-exchange grant on the
dedicated exchange app, the auth-server access policy/rule, and the
scopes/groups/users. This is the verification companion to README.md in this
directory; each ❌ points back to that README's troubleshooting table.

This script is READ-ONLY and idempotent: it inspects Okta + SSM and reports a
✅/❌ checklist. It never creates, updates, or deletes anything. Safe to run
repeatedly.

Default mode (Okta-only) — runnable right after notebook 01-deploy-idp, with
no dependency on the AgentCore gateways:
  • both apps exist (by label) and both SSM client-id/secret pairs are present
  • the exchange app carries the token-exchange grant type
  • the auth-server access policy includes the exchange client in clients.include
  • the policy rule's grantTypes.include lists the token-exchange grant
  • the 5 expected scopes (incl. opensearch.search) + the groups claim exist
  • the 3 groups and 5 test users exist

Optional --check-provider mode — cross-checks the 5b OBO credential provider is
wired to the exchange client (okta-obo-client-*). Skips with a note if the 5b
substrate has not been deployed yet.

Usage:
    python verify_okta_setup.py
    python verify_okta_setup.py --check-provider

Prerequisites:
    - OKTA_ORG_URL + OKTA_API_TOKEN in env (.env), as for setup_okta.py
    - AWS credentials configured (for SSM reads)
"""

import argparse
import asyncio
import os
import sys

import boto3
from okta.client import Client as OktaClient

# The sample root, so `utils` is importable when this script is run directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from utils.env_file import load_env_file

# Mirror setup_okta.py's resource names (idempotency keys).
OKTA_APP_NAME = "lakehouse-agent-app"
OKTA_EXCHANGE_APP_NAME = "lakehouse-obo-exchange-client"
AUTH_POLICY_NAME = "lakehouse-agent-default-policy"
AUTH_RULE_NAME = "lakehouse-agent-default-rule"

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"

EXPECTED_SCOPES = {
    "claims.query",
    "claims.submit",
    "claims.update",
    "claims.approve",
    "opensearch.search",
}
EXPECTED_GROUPS = {"policyholders", "adjusters", "administrators"}
EXPECTED_USERS = {
    "policyholder001@example.com",
    "policyholder002@example.com",
    "adjuster001@example.com",
    "adjuster002@example.com",
    "admin@example.com",
}

SSM_PREFIX = "/app/lakehouse-agent/"


class Checklist:
    """Accumulates ✅/❌ results without ever raising; reports at the end."""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, label: str, detail: str = ""):
        self.passed += 1
        suffix = f" — {detail}" if detail else ""
        print(f"   ✅ {label}{suffix}")

    def fail(self, label: str, fix: str):
        self.failed += 1
        print(f"   ❌ {label}")
        print(f"      → {fix}")

    def note(self, label: str, detail: str = ""):
        suffix = f" — {detail}" if detail else ""
        print(f"   ℹ️  {label}{suffix}")

    def api_error(self, label: str, detail: str):
        """An API/auth/transient error — verification was INCONCLUSIVE, not a
        confirmed-missing resource. Counts as failed (can't assert green) but is
        labeled distinctly so a transient Okta error is not read as broken setup."""
        self.failed += 1
        print(f"   ⚠️  API/auth error: {label}")
        print(f"      → {detail}")
        print(
            "      → transient/auth/network issue — NOT necessarily a missing "
            "resource; re-check OKTA creds/connectivity and re-run."
        )

    def summary(self) -> int:
        print("\n" + "=" * 70)
        if self.failed == 0:
            print(f"✅ All checks passed ({self.passed} ok).")
        else:
            print(f"❌ {self.failed} check(s) failed, {self.passed} ok.")
            print("   See deployment/1-okta-setup/README.md → 'the four token-exchange gates' for fixes.")
        print("=" * 70)
        return 0 if self.failed == 0 else 1


def get_ssm(ssm, name: str, secure: bool = False):
    """Read an SSM parameter; return value or None (never raises)."""
    try:
        resp = ssm.get_parameter(Name=name, WithDecryption=secure)
        return resp["Parameter"]["Value"]
    except Exception:
        return None


async def find_app_by_label(okta, label: str):
    """Find an Okta app by EXACT label, narrowing server-side and paginating.

    Returns (app, error):
      • (app, None)   — the app exists (exact-label match).
      • (None, None)  — the app is genuinely ABSENT (lookup succeeded, no match).
      • (None, detail)— an API/auth/transient error occurred; absence is
                        UNCONFIRMED (caller must NOT report this as "missing").

    Uses the `q` query filter (server-side narrow by name/label, like the
    groups lookup) + cursor pagination so it is reliable on tenants with many
    apps and does not depend on a single unbounded page.
    """
    try:
        apps, resp, err = await okta.list_applications(query_params={"q": label, "limit": 200})
        if err:
            return None, str(err)
        while True:
            for app in apps or []:
                if app.label == label:
                    return app, None
            if resp is not None and resp.has_next():
                apps, err = await resp.next()
                if err:
                    return None, str(err)
            else:
                break
    except Exception as e:
        return None, str(e)
    return None, None


def app_grant_types(app) -> list:
    """Best-effort extraction of an app's OAuth grant_types list."""
    try:
        return list(app.settings.oauth_client.grant_types or [])
    except Exception:
        return []


async def check_apps(okta, ssm, cl: Checklist):
    """Both apps exist (by label) + both SSM key pairs present."""
    print("\n🔑 Okta applications + SSM keys")

    login_app, login_err = await find_app_by_label(okta, OKTA_APP_NAME)
    if login_app:
        cl.ok(f"User-login app exists: {OKTA_APP_NAME}", login_app.id)
    elif login_err:
        cl.api_error(f"Could not verify user-login app: {OKTA_APP_NAME}", login_err)
    else:
        cl.fail(f"User-login app missing: {OKTA_APP_NAME}", "Run setup_okta.py (notebook 01-deploy-idp).")

    exchange_app, exchange_err = await find_app_by_label(okta, OKTA_EXCHANGE_APP_NAME)
    if exchange_app:
        cl.ok(f"OBO exchange app exists: {OKTA_EXCHANGE_APP_NAME}", exchange_app.id)
    elif exchange_err:
        cl.api_error(f"Could not verify OBO exchange app: {OKTA_EXCHANGE_APP_NAME}", exchange_err)
    else:
        cl.fail(
            f"OBO exchange app missing: {OKTA_EXCHANGE_APP_NAME}",
            "Run setup_okta.py — the dedicated exchange app is required (gate 4: unsupported_token_exchange_flow).",
        )

    # SSM key pairs.
    pairs = [
        ("okta-app-client-id", "okta-app-client-secret", "user-login app"),
        ("okta-obo-client-id", "okta-obo-client-secret", "OBO exchange app"),
    ]
    for id_key, secret_key, who in pairs:
        cid = get_ssm(ssm, f"{SSM_PREFIX}{id_key}")
        csecret = get_ssm(ssm, f"{SSM_PREFIX}{secret_key}", secure=True)
        if cid and csecret:
            cl.ok(f"SSM keys present for {who}", id_key)
        else:
            missing = id_key if not cid else secret_key
            cl.fail(f"SSM key missing for {who}: {missing}", "Re-run setup_okta.py to repopulate SSM.")

    # Exchange app must carry the token-exchange grant (gate 1, app side).
    if exchange_app:
        grants = app_grant_types(exchange_app)
        if TOKEN_EXCHANGE_GRANT in grants:
            cl.ok("Exchange app has the token-exchange grant")
        else:
            cl.fail(
                "Exchange app missing the token-exchange grant",
                f"Gate 1 (unauthorized_client): add '{TOKEN_EXCHANGE_GRANT}' to the exchange app's grant types.",
            )

    return login_app, exchange_app


async def check_auth_server(okta, ssm, cl: Checklist, login_app, exchange_app):
    """Auth-server policy client-list + rule grant types + scopes + groups claim."""
    print("\n🛡️  Authorization server: policy, rule, scopes")

    auth_server_id = get_ssm(ssm, f"{SSM_PREFIX}okta-auth-server-id")
    if not auth_server_id:
        cl.fail("Auth-server ID not in SSM (okta-auth-server-id)", "Run setup_okta.py.")
        return
    cl.ok("Auth-server ID found in SSM", auth_server_id)

    # Scopes (incl. opensearch.search).
    try:
        scopes, _, _ = await okta.list_o_auth_2_scopes(auth_server_id)
        names = {s.name for s in (scopes or [])}
        missing = EXPECTED_SCOPES - names
        if not missing:
            cl.ok("All expected scopes present", ", ".join(sorted(EXPECTED_SCOPES)))
        else:
            cl.fail(
                f"Missing scopes: {', '.join(sorted(missing))}",
                "Re-run setup_okta.py to create the auth-server scopes.",
            )
    except Exception as e:
        cl.fail(
            f"Could not list auth-server scopes: {e}",
            "Verify OKTA_API_TOKEN has read access and the auth server exists.",
        )

    # groups claim.
    try:
        claims, _, _ = await okta.list_o_auth_2_claims(auth_server_id)
        if any(c.name == "groups" for c in (claims or [])):
            cl.ok("`groups` claim configured on auth server")
        else:
            cl.fail(
                "`groups` claim missing on auth server",
                "Re-run setup_okta.py — the interceptor maps the groups claim to tenant roles.",
            )
    except Exception as e:
        cl.fail(f"Could not list auth-server claims: {e}", "Verify OKTA_API_TOKEN access.")

    # Access policy + rule.
    try:
        policies, _, _ = await okta.list_authorization_server_policies(auth_server_id)
        policy = next((p for p in (policies or []) if p.name == AUTH_POLICY_NAME), None)
    except Exception as e:
        cl.fail(f"Could not list auth-server policies: {e}", "Verify OKTA_API_TOKEN access.")
        return

    if not policy:
        cl.fail(
            f"Access policy missing: {AUTH_POLICY_NAME}",
            "Okta does not auto-create one; run setup_okta.py (without it, every token request returns access_denied).",
        )
        return
    cl.ok(f"Access policy exists: {AUTH_POLICY_NAME}")

    # Exchange client in clients.include.
    include = []
    try:
        include = list(policy.conditions.clients.include or [])
    except Exception:
        include = []

    exchange_client_id = get_ssm(ssm, f"{SSM_PREFIX}okta-obo-client-id")
    if exchange_client_id and exchange_client_id in include:
        cl.ok("Policy client-list includes the exchange client")
    elif exchange_client_id:
        cl.fail(
            "Exchange client not in policy clients.include",
            "Gate 1/4: add the exchange client to the auth-server policy's "
            "client include-list (setup_okta.py reconciles this).",
        )
    else:
        cl.note("Skipped policy client-list check (okta-obo-client-id not in SSM)")

    # Rule grant types include token-exchange.
    try:
        rules, _, _ = await okta.list_authorization_server_policy_rules(auth_server_id, policy.id)
        rule = next((r for r in (rules or []) if r.name == AUTH_RULE_NAME), None)
    except Exception as e:
        cl.fail(f"Could not list policy rules: {e}", "Verify OKTA_API_TOKEN access.")
        return

    if not rule:
        cl.fail(f"Policy rule missing: {AUTH_RULE_NAME}", "Run setup_okta.py.")
        return

    rule_grants = []
    try:
        rule_grants = list(rule.conditions.grant_types.include or [])
    except Exception:
        rule_grants = []

    if TOKEN_EXCHANGE_GRANT in rule_grants:
        cl.ok("Policy rule permits the token-exchange grant")
    else:
        cl.fail(
            "Policy rule does not permit the token-exchange grant",
            "Gate 1 (unauthorized_client): add the token-exchange grant to the "
            "rule's grantTypes.include (enforced at BOTH app and rule).",
        )


async def check_groups_and_users(okta, cl: Checklist):
    """3 groups + 5 test users exist."""
    print("\n👥 Groups + test users")

    try:
        found_groups = set()
        for name in EXPECTED_GROUPS:
            groups, _, _ = await okta.list_groups(query_params={"q": name})
            if any(g.profile.name == name for g in (groups or [])):
                found_groups.add(name)
        missing = EXPECTED_GROUPS - found_groups
        if not missing:
            cl.ok("All 3 groups present", ", ".join(sorted(EXPECTED_GROUPS)))
        else:
            cl.fail(f"Missing groups: {', '.join(sorted(missing))}", "Re-run setup_okta.py.")
    except Exception as e:
        cl.fail(f"Could not list groups: {e}", "Verify OKTA_API_TOKEN access.")

    missing_users = set()
    for login in EXPECTED_USERS:
        try:
            user, _, err = await okta.get_user(login)
            if err or not user:
                missing_users.add(login)
        except Exception:
            missing_users.add(login)
    if not missing_users:
        cl.ok(f"All {len(EXPECTED_USERS)} test users present")
    else:
        cl.fail(f"Missing test users: {', '.join(sorted(missing_users))}", "Re-run setup_okta.py.")


def check_obo_provider(ssm, cl: Checklist):
    """
    Optional cross-check: the 5b OBO credential provider is wired to the
    exchange client. Read-only; skips with a note if 5b has not deployed.
    """
    print("\n🔗 OBO credential provider wiring (--check-provider)")

    provider_arn = get_ssm(ssm, f"{SSM_PREFIX}obo-credential-provider-arn")
    if not provider_arn:
        cl.note(
            "Skipped — OBO provider not deployed yet (run deployment/5b-obo-gateway-setup/03_create_oauth_provider.py)"
        )
        return

    session = boto3.Session()
    region = session.region_name
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        # Resolve provider name from ARN tail; list + match to read its config.
        providers = []
        try:
            resp = client.list_oauth2_credential_providers()
            providers = (
                resp.get("credentialProviders") or resp.get("oauth2CredentialProviders") or resp.get("items") or []
            )
        except Exception as e:
            cl.note(f"Skipped — could not list credential providers: {e}")
            return

        target = next(
            (p for p in providers if p.get("name") == "lakehouse-obo-okta-provider"),
            None,
        )
        if not target:
            cl.note("Skipped — 'lakehouse-obo-okta-provider' not found (5b not fully deployed)")
            return

        # The provider config does not echo the client secret; we confirm the
        # configured clientId matches the exchange client's SSM id.
        exchange_client_id = get_ssm(ssm, f"{SSM_PREFIX}okta-obo-client-id")
        configured_client_id = None
        try:
            detail = client.get_oauth2_credential_provider(name="lakehouse-obo-okta-provider")
            cfg = detail.get("oauth2ProviderConfigOutput") or detail.get("oauth2ProviderConfigInput") or {}
            custom = cfg.get("customOauth2ProviderConfig", {}) if isinstance(cfg, dict) else {}
            configured_client_id = custom.get("clientId")
        except Exception:
            configured_client_id = None

        if configured_client_id is None:
            cl.note(
                "Provider exists; clientId not exposed by the API (cannot confirm wiring non-destructively)",
                provider_arn,
            )
        elif exchange_client_id and configured_client_id == exchange_client_id:
            cl.ok("OBO provider wired to the exchange client (okta-obo-client-*)")
        else:
            cl.fail(
                "OBO provider NOT wired to the exchange client",
                "Gate 4: re-point lakehouse-obo-okta-provider to "
                "okta-obo-client-* (see 5b/03_create_oauth_provider.py).",
            )
    except Exception as e:
        cl.note(f"Skipped — provider cross-check error: {e}")


async def run(check_provider: bool) -> int:
    print("=" * 70)
    print("Verify Okta two-client OBO topology (read-only)")
    print("=" * 70)

    org_url = os.environ.get("OKTA_ORG_URL")
    api_token = os.environ.get("OKTA_API_TOKEN")
    if not org_url or not api_token:
        print("\n❌ OKTA_ORG_URL and OKTA_API_TOKEN must be set (see .env).")
        print("   These are the same credentials setup_okta.py uses.")
        return 1

    org_url_full = org_url if org_url.startswith("http") else f"https://{org_url}"
    okta = OktaClient({"orgUrl": org_url_full, "token": api_token})

    session = boto3.Session()
    ssm = boto3.client("ssm", region_name=session.region_name)

    cl = Checklist()

    login_app, exchange_app = await check_apps(okta, ssm, cl)
    await check_auth_server(okta, ssm, cl, login_app, exchange_app)
    await check_groups_and_users(okta, cl)

    if check_provider:
        check_obo_provider(ssm, cl)

    return cl.summary()


def main():
    parser = argparse.ArgumentParser(description="Read-only verification of the Okta two-client OBO topology.")
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help="Also cross-check the 5b OBO credential provider wiring (skips with a note if 5b is not deployed).",
    )
    args = parser.parse_args()

    # This script's documented prerequisite is "OKTA_ORG_URL + OKTA_API_TOKEN in
    # env (.env), as for setup_okta.py" — but nothing loaded .env, so the stated
    # prerequisite was only satisfiable by exporting by hand.
    load_env_file()

    exit_code = asyncio.run(run(args.check_provider))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
