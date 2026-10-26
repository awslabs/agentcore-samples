"""
Tear down everything this sample created, in one shot.

By default this deletes:

  AgentCore identity side (bedrock-agentcore-control):
    - Client (USER_FEDERATION) credential provider  → CLIENT_PROVIDER_NAME
    - OBO credential provider                       → OBO_PROVIDER_NAME
    - M2M credential provider                       → M2M_PROVIDER_NAME
    - Workload identity                             → WORKLOAD_NAME

  Okta side (via SSWS admin token):
    - Web app used for user 3LO sign-in             → label OKTA_LOGIN_APP_LABEL
                                                      (default: "AgentCore
                                                       Identity Private Key
                                                       JWT Login App")
    - Service app used for M2M + OBO token requests → label OKTA_SERVICE_APP_LABEL
                                                      (default: "AgentCore
                                                       Identity Private Key
                                                       JWT Sample")
    - Custom AS Access Policy                       → name OKTA_POLICY_NAME
                                                      (default: "AgentCore
                                                       Identity Private Key
                                                       JWT Sample")
    - Custom scope on the Custom AS                 → name M2M_SCOPE
    - App Authentication Policy                     → name OKTA_AUTH_POLICY_NAME
                                                      (default: "AgentCore
                                                       Identity Private Key
                                                       JWT Sample Auth")

  Local side:
    - Blanks setup-populated keys in .env so the next setup run starts
      from a clean slate.

Explicitly KEPT by default:
    - The AWS KMS signing key and its alias (KMS_KEY_ALIAS). Reusing the
      same key across runs is deliberate: it saves the 7-day pending
      deletion window and lets you re-run the setup pipeline immediately.
      Pass --delete-kms to schedule the key for deletion.

The script walks ALL apps whose label matches the sample's labels - so
if previous failed runs left orphan apps in the tenant they get swept
too, not just the one whose id is recorded in .env.

Usage:
    python cleanup.py               # normal cleanup, keeps KMS
    python cleanup.py --dry-run     # show what would be deleted
    python cleanup.py --delete-kms  # also schedule KMS key for deletion
    python cleanup.py --keep-env    # don't blank .env values
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"

# Names / labels this sample uses if not overridden by env vars.
DEFAULT_POLICY_NAME = "AgentCore Identity Private Key JWT Sample"
DEFAULT_SERVICE_APP_LABEL = "AgentCore Identity Private Key JWT Sample"
DEFAULT_LOGIN_APP_LABEL = "AgentCore Identity Private Key JWT Login App"
DEFAULT_AUTH_POLICY_NAME = "AgentCore Identity Private Key JWT Sample Auth"

# .env keys that setup steps populate. Cleared by cleanup unless
# --keep-env is passed. User-supplied keys (OKTA_DOMAIN, OKTA_API_TOKEN,
# WORKLOAD_NAME, KMS_KEY_ALIAS, M2M_PROVIDER_NAME, OBO_PROVIDER_NAME,
# OKTA_ASSIGN_USER_LOGIN, endpoint overrides) are left untouched.
ENV_KEYS_TO_BLANK = (
    "SIGNING_KMS_KEY_ARN",  # kept iff --keep-kms (default)
    "OKTA_SERVICE_APP_ID",
    "OKTA_SERVICE_APP_CLIENT_ID",
    "SIGNING_KID",
    "OKTA_LOGIN_APP_ID",
    "OKTA_LOGIN_APP_CLIENT_ID",
    "CLIENT_PROVIDER_NAME",
    "AGENTCORE_MANAGED_CALLBACK_URL",
    "OKTA_USER_JWT",
)


# ─────────────────────────── helpers ────────────────────────────────


def maybe_env(name: str) -> str | None:
    return os.environ.get(name) or None


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


def _print_section(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 60 - len(title) - 4))


def _mark(prefix: str, msg: str) -> None:
    print(f"  {prefix} {msg}")


def blank_env(name: str) -> None:
    """Set NAME= in .env in place (preserving order, comments, blank lines)."""
    if not ENV_FILE.exists():
        return
    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, f"{name}=", text, flags=re.MULTILINE)
        ENV_FILE.write_text(new_text)


# ─────────────────────────── AgentCore ──────────────────────────────


def delete_provider(control, name: str | None, dry_run: bool) -> None:
    if not name:
        return
    if dry_run:
        _mark("[dry-run]", f"would delete credential provider: {name}")
        return
    try:
        control.delete_oauth2_credential_provider(name=name)
        _mark("✓", f"deleted credential provider: {name}")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in {"ResourceNotFoundException", "NotFoundException"}:
            _mark("•", f"credential provider already absent: {name}")
        else:
            _mark("⚠", f"delete_oauth2_credential_provider({name}) failed: {code}")


def delete_workload(control, name: str | None, dry_run: bool) -> None:
    if not name:
        return
    if dry_run:
        _mark("[dry-run]", f"would delete workload identity: {name}")
        return
    try:
        control.delete_workload_identity(name=name)
        _mark("✓", f"deleted workload identity: {name}")
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code in {"ResourceNotFoundException", "NotFoundException"}:
            _mark("•", f"workload identity already absent: {name}")
        else:
            _mark("⚠", f"delete_workload_identity({name}) failed: {code}")


# ─────────────────────────── Okta ───────────────────────────────────


def list_apps_by_label(base_url: str, api_token: str, label: str) -> list[dict]:
    resp = requests.get(
        f"{base_url}/api/v1/apps",
        headers=okta_headers(api_token),
        params={"q": label, "limit": 50},
        timeout=15,
    )
    if resp.status_code >= 400:
        _mark("⚠", f"GET /apps for {label!r}: HTTP {resp.status_code}")
        return []
    return [app for app in resp.json() if app.get("label") == label]


def delete_app(base_url: str, api_token: str, app_id: str, label: str, dry_run: bool) -> None:
    if dry_run:
        _mark("[dry-run]", f"would delete Okta app {label!r} (id={app_id})")
        return

    # Okta requires apps to be deactivated first.
    deact = requests.post(
        f"{base_url}/api/v1/apps/{app_id}/lifecycle/deactivate",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if deact.status_code not in (200, 204, 404):
        _mark("⚠", f"deactivate {app_id} → HTTP {deact.status_code}")

    resp = requests.delete(
        f"{base_url}/api/v1/apps/{app_id}",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code in (200, 204):
        _mark("✓", f"deleted Okta app {label!r} (id={app_id})")
    elif resp.status_code == 404:
        _mark("•", f"Okta app already absent: {label!r} (id={app_id})")
    else:
        _mark("⚠", f"delete {app_id} → HTTP {resp.status_code}: {resp.text[:200]}")


def delete_as_access_policy(base_url: str, api_token: str, as_id: str, name: str, dry_run: bool) -> None:
    """Delete the Custom AS Access Policy (and its rules) by name."""
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/policies",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code >= 400:
        _mark("⚠", f"GET /policies: HTTP {resp.status_code}")
        return
    matches = [p for p in resp.json() if p.get("name") == name]
    if not matches:
        _mark("•", f"no AS Access Policy named {name!r} found")
        return
    for pol in matches:
        pid = pol["id"]
        if dry_run:
            _mark("[dry-run]", f"would delete AS Access Policy {name!r} (id={pid})")
            continue
        d = requests.delete(
            f"{base_url}/api/v1/authorizationServers/{as_id}/policies/{pid}",
            headers=okta_headers(api_token),
            timeout=15,
        )
        if d.status_code in (200, 204):
            _mark("✓", f"deleted AS Access Policy {name!r} (id={pid})")
        else:
            _mark("⚠", f"delete policy {pid} → HTTP {d.status_code}: {d.text[:200]}")


def delete_as_scope(base_url: str, api_token: str, as_id: str, name: str | None, dry_run: bool) -> None:
    if not name:
        return
    resp = requests.get(
        f"{base_url}/api/v1/authorizationServers/{as_id}/scopes",
        headers=okta_headers(api_token),
        timeout=15,
    )
    if resp.status_code >= 400:
        _mark("⚠", f"GET /scopes: HTTP {resp.status_code}")
        return
    matches = [s for s in resp.json() if s.get("name") == name]
    if not matches:
        _mark("•", f"no AS scope named {name!r} found")
        return
    for sc in matches:
        sid = sc["id"]
        if dry_run:
            _mark("[dry-run]", f"would delete AS scope {name!r} (id={sid})")
            continue
        d = requests.delete(
            f"{base_url}/api/v1/authorizationServers/{as_id}/scopes/{sid}",
            headers=okta_headers(api_token),
            timeout=15,
        )
        if d.status_code in (200, 204):
            _mark("✓", f"deleted AS scope {name!r} (id={sid})")
        else:
            _mark("⚠", f"delete scope {sid} → HTTP {d.status_code}: {d.text[:200]}")


def delete_app_auth_policy(base_url: str, api_token: str, name: str, dry_run: bool) -> None:
    """Delete an App Authentication Policy (type=ACCESS_POLICY) by name."""
    resp = requests.get(
        f"{base_url}/api/v1/policies",
        headers=okta_headers(api_token),
        params={"type": "ACCESS_POLICY"},
        timeout=15,
    )
    if resp.status_code >= 400:
        _mark("⚠", f"GET /policies?type=ACCESS_POLICY: HTTP {resp.status_code}")
        return
    matches = [p for p in resp.json() if p.get("name") == name]
    if not matches:
        _mark("•", f"no App Authentication Policy named {name!r} found")
        return
    for pol in matches:
        pid = pol["id"]
        if dry_run:
            _mark("[dry-run]", f"would delete Authentication Policy {name!r} (id={pid})")
            continue
        d = requests.delete(
            f"{base_url}/api/v1/policies/{pid}",
            headers=okta_headers(api_token),
            timeout=15,
        )
        if d.status_code in (200, 204):
            _mark("✓", f"deleted Authentication Policy {name!r} (id={pid})")
        elif d.status_code == 400:
            # 400 usually means "policy is still bound to one or more
            # apps". Report and move on - the user can rerun the sample
            # cleanup after deleting the apps in question.
            _mark(
                "⚠",
                f"Authentication Policy {name!r} not deletable yet (HTTP 400). "
                f"Likely still bound to an app that wasn't part of this sample. "
                f"Detail: {d.text[:200]}",
            )
        else:
            _mark("⚠", f"delete policy {pid} → HTTP {d.status_code}: {d.text[:200]}")


# ─────────────────────────── KMS (optional) ─────────────────────────


def delete_kms(kms, alias_name: str | None, dry_run: bool, days: int = 7) -> None:
    if not alias_name:
        return
    try:
        described = kms.describe_key(KeyId=alias_name)
        key_id = described["KeyMetadata"]["KeyId"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            _mark("•", f"KMS alias already absent: {alias_name}")
            return
        raise

    if dry_run:
        _mark("[dry-run]", f"would schedule KMS key {key_id} for deletion in {days}d")
        _mark("[dry-run]", f"would delete KMS alias {alias_name}")
        return

    try:
        kms.delete_alias(AliasName=alias_name)
        _mark("✓", f"deleted KMS alias: {alias_name}")
    except ClientError as e:
        _mark("⚠", f"delete_alias({alias_name}) failed: {e.response['Error'].get('Code')}")

    try:
        kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=days)
        _mark("✓", f"scheduled KMS key {key_id} for deletion in {days} days")
    except ClientError as e:
        _mark("⚠", f"schedule_key_deletion({key_id}) failed: {e.response['Error'].get('Code')}")


# ─────────────────────────── main ───────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Tear down PRIVATE_KEY_JWT Okta sample resources.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without actually deleting anything.",
    )
    parser.add_argument(
        "--delete-kms",
        action="store_true",
        help="Also schedule the KMS signing key for deletion (7-day window).",
    )
    parser.add_argument(
        "--keep-env",
        action="store_true",
        help="Don't blank setup-populated keys in .env.",
    )
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")

    # Names / identifiers
    workload = maybe_env("WORKLOAD_NAME")
    m2m_provider = maybe_env("M2M_PROVIDER_NAME")
    obo_provider = maybe_env("OBO_PROVIDER_NAME")
    client_provider = maybe_env("CLIENT_PROVIDER_NAME")
    kms_alias = maybe_env("KMS_KEY_ALIAS")

    okta_domain = maybe_env("OKTA_DOMAIN")
    okta_api_token = maybe_env("OKTA_API_TOKEN")
    as_id = os.environ.get("OKTA_AUTH_SERVER_ID") or "default"
    m2m_scope = maybe_env("M2M_SCOPE")

    service_label = maybe_env("OKTA_SERVICE_APP_LABEL") or DEFAULT_SERVICE_APP_LABEL
    login_label = maybe_env("OKTA_LOGIN_APP_LABEL") or DEFAULT_LOGIN_APP_LABEL
    as_policy_name = maybe_env("OKTA_POLICY_NAME") or DEFAULT_POLICY_NAME
    app_auth_policy_name = maybe_env("OKTA_AUTH_POLICY_NAME") or DEFAULT_AUTH_POLICY_NAME

    print(f"Region:       {region}")
    print(f"Okta:         {okta_domain or '(no OKTA_DOMAIN in .env)'}")
    print(f"Dry run:      {args.dry_run}")
    print(f"Delete KMS:   {args.delete_kms}")
    print(f"Blank .env:   {not args.keep_env}")

    # 1. AgentCore side (delete providers first, then workload, so
    #    provider deletes don't cascade-fail on missing workload)
    _print_section("AgentCore credential providers + workload")
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    delete_provider(control, client_provider, args.dry_run)
    delete_provider(control, obo_provider, args.dry_run)
    delete_provider(control, m2m_provider, args.dry_run)
    delete_workload(control, workload, args.dry_run)

    # 2. Okta apps - always look up by label so orphans from failed
    #    setup runs get swept too.
    if okta_domain and okta_api_token:
        base_url = f"https://{okta_domain}"

        _print_section("Okta apps (login + service)")
        for label in (login_label, service_label):
            apps = list_apps_by_label(base_url, okta_api_token, label)
            if not apps:
                _mark("•", f"no Okta app with label {label!r} found")
                continue
            for app in apps:
                delete_app(
                    base_url,
                    okta_api_token,
                    app["id"],
                    app.get("label", "?"),
                    args.dry_run,
                )

        # 3. AS access policy
        _print_section(f"Custom AS Access Policy on AS {as_id!r}")
        delete_as_access_policy(base_url, okta_api_token, as_id, as_policy_name, args.dry_run)

        # 4. AS scope
        _print_section(f"Custom AS scope on AS {as_id!r}")
        delete_as_scope(base_url, okta_api_token, as_id, m2m_scope, args.dry_run)

        # 5. App Authentication Policy (must come AFTER apps are gone,
        #    Okta refuses to delete a policy that still has apps bound)
        _print_section("App Authentication Policy (Security → Authentication Policies)")
        delete_app_auth_policy(base_url, okta_api_token, app_auth_policy_name, args.dry_run)
    else:
        _print_section("Okta side")
        _mark(
            "•",
            "OKTA_DOMAIN / OKTA_API_TOKEN not set - skipping Okta cleanup entirely.",
        )

    # 6. KMS (opt-in only)
    _print_section("AWS KMS signing key")
    if args.delete_kms:
        kms = boto3.client("kms", region_name=region)
        delete_kms(kms, kms_alias, args.dry_run)
    else:
        _mark(
            "•",
            f"KMS alias {kms_alias or '(unset)'} left in place (pass --delete-kms to schedule deletion).",
        )

    # 7. .env - only after actual (not dry-run) cleanup
    _print_section(".env")
    if args.dry_run:
        _mark("•", "dry run: not touching .env")
    elif args.keep_env:
        _mark("•", "--keep-env: leaving .env values in place")
    else:
        for key in ENV_KEYS_TO_BLANK:
            if key == "SIGNING_KMS_KEY_ARN" and not args.delete_kms:
                # KMS is being kept; keep its ARN so the next setup run
                # picks up the same key without having to look it up.
                continue
            blank_env(key)
        _mark("✓", "blanked setup-populated keys in .env")

    print()
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to actually delete.")
    else:
        print("Cleanup complete. To rebuild:")
        print("  python setup/00_provision_signing_key.py")
        print("  python setup/01_create_okta_service_app.py")
        print("  python setup/01a_configure_okta_auth_server.py")
        print("  python setup/01b_create_okta_login_web_app.py")
        print("  python setup/02_create_provider_m2m.py")
        print("  python setup/03_create_provider_obo.py")
        print("  python setup/03b_create_provider_client.py")


if __name__ == "__main__":
    main()
