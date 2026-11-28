#!/usr/bin/env python3
"""
Cleanup Okta Resources

Okta path only. Run by 09-optional-cleanup.ipynb ([OKTA] step) as
``python cleanup_okta.py --yes`` from this directory, and only when that
cell's RUN_OKTA_CLEANUP switch is True. Needs OKTA_ORG_URL and OKTA_API_TOKEN
(exported, or read from .env); exits 1 without them.

Deletes nothing unless ``--yes`` is passed. Without it, the script prints what
it would delete and exits with status 2, so running it by accident is harmless.

DESTRUCTIVE for the Okta org it points at: it calls the Okta management API
with your API token and deletes, by exact name or login, the objects
setup_okta.py created. Any object in that org that happens to share one of
these names is deleted too, so point it only at the tenant you used for the
sample.

Deletes, in run() order:
- Okta OIDC application lakehouse-agent-app (user-login app)
- Okta OBO token-exchange service application lakehouse-obo-exchange-client
- Okta custom authorization server lakehouse-agent (its scopes, claims and
  access policies go with it)
- Okta groups (policyholders, adjusters, administrators)
- Okta test users: the 5 logins in USER_LOGINS
- A fixed list of 12 okta-* SSM parameters under /app/lakehouse-agent/
  (skipped with --keep-ssm). The okta-user-<label>-sub keys are not in that
  list; the notebook's final SSM sweep removes them.

Apps and the auth server are deactivated before they are deleted, because
Okta refuses to delete an active one.

Cleanup ownership note: this script OWNS teardown of the OBO exchange app and
the okta-obo-client-* SSM keys (both Okta-side resources created by
setup_okta.py). The AWS-side OBO substrate (provider / gateway / target / AOSS
+ obo-* SSM keys) is owned by
deployment/5b-obo-gateway-setup/06_cleanup_obo_gateway.py — no double ownership.

Usage:
    python cleanup_okta.py              # print what would be deleted; delete nothing
    python cleanup_okta.py --yes [--keep-ssm]
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

# Constants matching setup_okta.py
OKTA_APP_NAME = "lakehouse-agent-app"
OKTA_EXCHANGE_APP_NAME = "lakehouse-obo-exchange-client"
OKTA_AUTH_SERVER_NAME = "lakehouse-agent"
GROUP_NAMES = ["policyholders", "adjusters", "administrators"]
USER_LOGINS = [
    "policyholder001@example.com",
    "policyholder002@example.com",
    "adjuster001@example.com",
    "adjuster002@example.com",
    "admin@example.com",
]
SSM_PREFIX = "/app/lakehouse-agent/"
SSM_PARAMS = [
    "okta-org-url",
    "okta-auth-server-id",
    "okta-app-client-id",
    "okta-app-client-secret",
    "okta-obo-client-id",
    "okta-obo-client-secret",
    "okta-api-token",
    "okta-resource-server-audience",
    "okta-discovery-url",
    "okta-policyholders-group-id",
    "okta-adjusters-group-id",
    "okta-administrators-group-id",
]

# Returned by the _find_* helpers when the lookup itself failed, which is not the same as "not found".
_LOOKUP_FAILED = object()


def _is_not_found(err) -> bool:
    """True only for an Okta HTTP 404 (errorCode E0000007, resource not found).

    The Okta SDK returns errors instead of raising them (its raiseException option
    defaults to False), and both of its error types, OktaAPIError and HTTPError,
    carry the HTTP status as `.status`.
    """
    return getattr(err, "status", None) == 404


def _err_text(err) -> str:
    """The SDK's error types define `.message` but no __str__."""
    return getattr(err, "message", None) or str(err)


class OktaCleanup:
    def __init__(self, keep_ssm: bool = False):
        org_url = os.environ.get("OKTA_ORG_URL")
        api_token = os.environ.get("OKTA_API_TOKEN")
        if not org_url or not api_token:
            print("❌ OKTA_ORG_URL and OKTA_API_TOKEN must be set. Cannot proceed.")
            sys.exit(1)

        self.org_url = org_url.replace("https://", "").replace("http://", "")
        org_url_full = f"https://{self.org_url}"
        self.okta = OktaClient({"orgUrl": org_url_full, "token": api_token})

        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.keep_ssm = keep_ssm
        # Failed lookups and deletes are collected here; run() prints ✨ only when this
        # stays empty. "Not found" outcomes are not failures.
        self.errors = []

        print("🧹 Okta Cleanup")
        print(f"   Org URL: {self.org_url}")
        print(f"   Region:  {self.region}")

    def _lookup_failed(self, what: str, err):
        print(f"   ❌ Error listing {what}: {_err_text(err)}")
        self.errors.append(f"{what} not listed: {_err_text(err)}")
        return _LOOKUP_FAILED

    def _delete_failed(self, what: str, err):
        print(f"   ❌ Error deleting {what}: {_err_text(err)}")
        self.errors.append(f"{what}: {_err_text(err)}")

    async def _find_app(self, label: str = OKTA_APP_NAME):
        """Return Okta app object matching `label`, None if absent, or _LOOKUP_FAILED."""
        try:
            apps, _, err = await self.okta.list_applications()
        except Exception as e:
            apps, err = None, e
        if err:
            return self._lookup_failed("apps", err)
        for a in apps or []:
            if a.label == label:
                return a
        return None

    async def _find_auth_server(self):
        try:
            servers, _, err = await self.okta.list_authorization_servers()
        except Exception as e:
            servers, err = None, e
        if err:
            return self._lookup_failed("auth servers", err)
        for s in servers or []:
            if s.name == OKTA_AUTH_SERVER_NAME:
                return s
        return None

    async def _find_group(self, name: str):
        try:
            groups, _, err = await self.okta.list_groups(query_params={"q": name})
        except Exception as e:
            groups, err = None, e
        if err:
            return self._lookup_failed(f"groups (looking for {name})", err)
        for g in groups or []:
            if g.profile.name == name:
                return g
        return None

    async def _find_user(self, login: str):
        try:
            user, _, err = await self.okta.get_user(login)
        except Exception as e:
            user, err = None, e
        if err and not _is_not_found(err):
            return self._lookup_failed(f"user {login}", err)
        return user

    async def delete_app(self, label: str = OKTA_APP_NAME):
        print(f"\n🗑️  Deleting Okta application: {label}")
        app = await self._find_app(label)
        if app is _LOOKUP_FAILED:
            return
        if not app:
            print(f"   ⏭️  App not found: {label}")
            return
        try:
            # Okta requires deactivation before deletion. Both calls return
            # (response, error) rather than raising.
            _, err = await self.okta.deactivate_application(app.id)
            if not err:
                _, err = await self.okta.delete_application(app.id)
        except Exception as e:
            err = e
        if err and _is_not_found(err):
            print(f"   ⏭️  App not found: {label}")
        elif err:
            self._delete_failed(f"app {label}", err)
        else:
            print(f"   ✅ Deleted app: {label}")

    async def delete_auth_server(self):
        print("\n🗑️  Deleting Okta custom authorization server...")
        server = await self._find_auth_server()
        if server is _LOOKUP_FAILED:
            return
        if not server:
            print(f"   ⏭️  Auth server not found: {OKTA_AUTH_SERVER_NAME}")
            return
        try:
            _, err = await self.okta.deactivate_authorization_server(server.id)
            if not err:
                _, err = await self.okta.delete_authorization_server(server.id)
        except Exception as e:
            err = e
        if err and _is_not_found(err):
            print(f"   ⏭️  Auth server not found: {OKTA_AUTH_SERVER_NAME}")
        elif err:
            self._delete_failed(f"auth server {OKTA_AUTH_SERVER_NAME}", err)
        else:
            print(f"   ✅ Deleted auth server: {OKTA_AUTH_SERVER_NAME}")

    async def delete_groups(self):
        print("\n🗑️  Deleting Okta groups...")
        for name in GROUP_NAMES:
            g = await self._find_group(name)
            if g is _LOOKUP_FAILED:
                continue
            if not g:
                print(f"   ⏭️  Group not found: {name}")
                continue
            try:
                _, err = await self.okta.delete_group(g.id)
            except Exception as e:
                err = e
            if err and _is_not_found(err):
                print(f"   ⏭️  Group not found: {name}")
            elif err:
                self._delete_failed(f"group {name}", err)
            else:
                print(f"   ✅ Deleted group: {name}")

    async def delete_users(self):
        print("\n🗑️  Deleting Okta test users...")
        for login in USER_LOGINS:
            u = await self._find_user(login)
            if u is _LOOKUP_FAILED:
                continue
            if not u:
                print(f"   ⏭️  User not found: {login}")
                continue
            # Okta deletes only a DEPROVISIONED user; on any other status the first
            # call deactivates and the second deletes. A user left deactivated by an
            # earlier run gets one call, because a second call would 404.
            status = getattr(u, "status", None)
            calls = 1 if str(getattr(status, "value", status)).upper() == "DEPROVISIONED" else 2
            err = None
            try:
                for _ in range(calls):
                    _, err = await self.okta.deactivate_or_delete_user(u.id)
                    if err:
                        break
            except Exception as e:
                err = e
            if err and _is_not_found(err):
                print(f"   ⏭️  User not found: {login}")
            elif err:
                self._delete_failed(f"user {login}", err)
            else:
                print(f"   ✅ Deleted user: {login}")

    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        for p in SSM_PARAMS:
            full = f"{SSM_PREFIX}{p}"
            try:
                self.ssm.delete_parameter(Name=full)
                print(f"   ✅ Deleted: {full}")
            except self.ssm.exceptions.ParameterNotFound:
                print(f"   ⏭️  Not found: {full}")
            except Exception as e:
                print(f"   ❌ Error deleting {full}: {e}")
                self.errors.append(f"SSM {full}: {e}")

    async def run(self) -> int:
        """Run every delete step and return the process exit code (1 if any step failed)."""
        await self.delete_app(OKTA_APP_NAME)
        # Dedicated OBO token-exchange service app (owned here, per the
        # cleanup-ownership note at the top of this file).
        await self.delete_app(OKTA_EXCHANGE_APP_NAME)
        await self.delete_auth_server()
        await self.delete_groups()
        await self.delete_users()
        self.delete_ssm_parameters()
        if self.errors:
            print(f"\n❌ cleanup_okta.py finished with {len(self.errors)} error(s):")
            for err in self.errors:
                print(f"   • {err}")
            return 1
        print("\n✨ Okta cleanup complete!")
        return 0


def print_plan(keep_ssm: bool) -> None:
    """Print what a run with --yes would delete. Makes no Okta or AWS call."""
    print("🧹 Okta Cleanup: DRY RUN, nothing deleted (pass --yes to delete)")
    print(f"   Org URL: {os.environ.get('OKTA_ORG_URL') or '(OKTA_ORG_URL not set)'}")
    print("   With --yes, this deletes from that Okta org, by exact name or login:")
    print(f"     - apps: {OKTA_APP_NAME}, {OKTA_EXCHANGE_APP_NAME}")
    print(f"     - authorization server: {OKTA_AUTH_SERVER_NAME}")
    print(f"     - groups: {', '.join(GROUP_NAMES)}")
    print(f"     - users: {', '.join(USER_LOGINS)}")
    if keep_ssm:
        print("     - SSM parameters: none (--keep-ssm)")
    else:
        print(f"     - SSM parameters under {SSM_PREFIX}: {', '.join(SSM_PARAMS)}")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Okta resources")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to delete anything. Without it, print what would be deleted and exit 2.",
    )
    args = parser.parse_args()

    # Same as setup_okta.py: this script reads OKTA_ORG_URL / OKTA_API_TOKEN
    # from os.environ and exits 1 without them, so load .env into the
    # environment first. Already-exported variables still win.
    load_env_file()

    # Opt-in: the deletes reach into your Okta org, so they need an explicit --yes.
    # No interactive prompt, because the notebook that calls this has no terminal.
    if not args.yes:
        print_plan(args.keep_ssm)
        sys.exit(2)

    cleanup = OktaCleanup(keep_ssm=args.keep_ssm)
    sys.exit(asyncio.run(cleanup.run()))


if __name__ == "__main__":
    main()
