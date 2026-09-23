"""
Tear down everything the Entra ID PRIVATE_KEY_JWT sample created, in
one shot.

By default this deletes:

  AgentCore Identity side (bedrock-agentcore-control):
    - Client (USER_FEDERATION) credential provider  → CLIENT_PROVIDER_NAME
    - OBO credential provider                       → OBO_PROVIDER_NAME
    - M2M credential provider                       → M2M_PROVIDER_NAME
    - Workload identity                             → WORKLOAD_NAME

  Entra ID side (via Microsoft Graph):
    - The login web app                             → ENTRA_LOGIN_APP_OBJECT_ID
    - The service app                               → ENTRA_SERVICE_APP_OBJECT_ID
    - Service principals for both apps (Graph deletes those with the app)

  Local:
    - entra_cert.pem + entra_cert.der (see --keep-cert)
    - Setup-populated keys in .env

Explicitly KEPT by default:
    - The AWS KMS signing key and its alias. Same reasoning as the Okta
      sample: keeping the key across re-runs saves the 7-day pending
      deletion window and lets you re-run the setup pipeline
      immediately. Pass --delete-kms to schedule deletion.

Graph auth follows the same pattern as the setup scripts: `az account
get-access-token`. If az isn't available, the Entra-side cleanup is
skipped and printed as a manual step.

Usage:
    python cleanup.py               # normal cleanup, keeps KMS
    python cleanup.py --dry-run     # show what would be deleted
    python cleanup.py --delete-kms  # also schedule KMS key for deletion
    python cleanup.py --keep-env    # don't blank .env values
    python cleanup.py --keep-cert   # leave entra_cert.pem + .der on disk
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CERT_PEM_PATH = ROOT / "entra_cert.pem"
CERT_DER_PATH = ROOT / "entra_cert.der"

# .env keys that setup steps populate. Cleared unless --keep-env. Left
# untouched: AWS_REGION, ENTRA_TENANT_ID (needed to talk to the tenant
# again), WORKLOAD_NAME, KMS_KEY_ALIAS, M2M_PROVIDER_NAME,
# OBO_PROVIDER_NAME, LOCAL_CALLBACK_URL - all user-supplied.
ENV_KEYS_TO_BLANK = (
    "SIGNING_KMS_KEY_ARN",  # kept iff --keep-kms (default)
    "X5T_S256_THUMBPRINT",
    "ENTRA_SERVICE_APP_OBJECT_ID",
    "ENTRA_SERVICE_CLIENT_ID",
    "ENTRA_SERVICE_SP_OBJECT_ID",
    "ENTRA_LOGIN_APP_OBJECT_ID",
    "ENTRA_LOGIN_CLIENT_ID",
    "CLIENT_PROVIDER_NAME",
    "AGENTCORE_MANAGED_CALLBACK_URL",
    "ENTRA_USER_JWT",
)


def _load_helper():
    path = ROOT / "setup" / "_entra_graph.py"
    spec = importlib.util.spec_from_file_location("_entra_graph", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def maybe_env(name: str) -> str | None:
    return os.environ.get(name) or None


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def _mark(prefix: str, msg: str) -> None:
    print(f"  {prefix} {msg}")


def _print_section(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 60 - len(title) - 4))


def blank_env(name: str) -> None:
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


# ─────────────────────────── Entra ──────────────────────────────────


def delete_application(graph, token: str, object_id: str, label: str, dry_run: bool) -> None:
    if not object_id:
        return
    if dry_run:
        _mark("[dry-run]", f"would delete Entra app {label!r} (objectId={object_id})")
        return
    status = graph.graph_delete(f"applications/{object_id}", token)
    if status == 404:
        _mark("•", f"Entra app already absent: {label!r} (objectId={object_id})")
    else:
        _mark("✓", f"deleted Entra app {label!r} (objectId={object_id})")


# ─────────────────────────── KMS (opt-in) ───────────────────────────


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
        _mark(
            "⚠",
            f"schedule_key_deletion({key_id}) failed: {e.response['Error'].get('Code')}",
        )


# ─────────────────────────── main ───────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Tear down PRIVATE_KEY_JWT Entra sample resources.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete-kms", action="store_true")
    parser.add_argument("--keep-env", action="store_true")
    parser.add_argument("--keep-cert", action="store_true")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    graph = _load_helper()
    region = must_env("AWS_REGION")

    workload = maybe_env("WORKLOAD_NAME")
    m2m_provider = maybe_env("M2M_PROVIDER_NAME")
    obo_provider = maybe_env("OBO_PROVIDER_NAME")
    client_provider = maybe_env("CLIENT_PROVIDER_NAME")
    kms_alias = maybe_env("KMS_KEY_ALIAS")

    service_object_id = maybe_env("ENTRA_SERVICE_APP_OBJECT_ID")
    login_object_id = maybe_env("ENTRA_LOGIN_APP_OBJECT_ID")

    service_label = "AgentCore Identity Private Key JWT Sample (service)"
    login_label = "AgentCore Identity Private Key JWT Login App"

    print(f"Region:      {region}")
    print(f"Dry run:     {args.dry_run}")
    print(f"Delete KMS:  {args.delete_kms}")
    print(f"Blank .env:  {not args.keep_env}")
    print(f"Delete cert: {not args.keep_cert}")

    # 1. AgentCore side (providers before workload so provider deletes
    #    don't cascade-fail on missing workload).
    _print_section("AgentCore credential providers + workload")
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    delete_provider(control, client_provider, args.dry_run)
    delete_provider(control, obo_provider, args.dry_run)
    delete_provider(control, m2m_provider, args.dry_run)
    delete_workload(control, workload, args.dry_run)

    # 2. Entra apps - best-effort via Graph. If az isn't available, print
    #    manual instructions.
    _print_section("Entra ID app registrations")
    if service_object_id or login_object_id:
        try:
            token = graph.get_graph_token()
        except SystemExit:
            token = None

        if token:
            if login_object_id:
                delete_application(graph, token, login_object_id, login_label, args.dry_run)
            if service_object_id:
                delete_application(graph, token, service_object_id, service_label, args.dry_run)
        else:
            _mark(
                "•",
                "az login unavailable - skipping Entra cleanup. Delete these manually in the Entra admin center:",
            )
            if login_object_id:
                _mark("  ", f"App registrations → objectId={login_object_id}")
            if service_object_id:
                _mark("  ", f"App registrations → objectId={service_object_id}")
    else:
        _mark("•", "No Entra app object IDs in .env - nothing to delete.")

    # 3. KMS (opt-in)
    _print_section("AWS KMS signing key")
    if args.delete_kms:
        kms = boto3.client("kms", region_name=region)
        delete_kms(kms, kms_alias, args.dry_run)
    else:
        _mark(
            "•",
            f"KMS alias {kms_alias or '(unset)'} left in place (pass --delete-kms to schedule deletion).",
        )

    # 4. Local cert files
    _print_section("Local certificate files")
    if args.keep_cert:
        _mark("•", "--keep-cert: leaving entra_cert.pem/.der in place")
    else:
        for p in (CERT_PEM_PATH, CERT_DER_PATH):
            if p.exists():
                if args.dry_run:
                    _mark("[dry-run]", f"would delete {p.name}")
                else:
                    p.unlink()
                    _mark("✓", f"deleted {p.name}")
            else:
                _mark("•", f"{p.name} already absent")

    # 5. .env - after actual (not dry-run) cleanup only
    _print_section(".env")
    if args.dry_run:
        _mark("•", "dry run: not touching .env")
    elif args.keep_env:
        _mark("•", "--keep-env: leaving .env values in place")
    else:
        for key in ENV_KEYS_TO_BLANK:
            if key == "SIGNING_KMS_KEY_ARN" and not args.delete_kms:
                continue  # keep the ARN so setup/00 reuses the same key
            blank_env(key)
        _mark("✓", "blanked setup-populated keys in .env")

    print()
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to actually delete.")
    else:
        print("Cleanup complete. To rebuild:")
        print("  python setup/00_provision_signing_key.py")
        print("  python setup/01_build_certificate.py")
        print("  python setup/02_create_entra_service_app.py")
        print("  python setup/02a_configure_entra_permissions.py")
        print("  python setup/02b_create_entra_login_web_app.py")
        print("  python setup/03_create_provider_m2m.py")
        print("  python setup/04_create_provider_obo.py")
        print("  python setup/04b_create_provider_client.py")


if __name__ == "__main__":
    main()
