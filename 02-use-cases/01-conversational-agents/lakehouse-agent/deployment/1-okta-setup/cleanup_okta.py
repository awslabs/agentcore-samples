#!/usr/bin/env python3
"""
Cleanup Okta Resources

Deletes:
- Okta OIDC application (user-login app)
- Okta OBO token-exchange service application (dedicated exchange client)
- Okta custom authorization server (and its scopes/claims)
- Okta groups (policyholders, adjusters, administrators)
- Okta test users (5 users matching the original demo's user list)
- SSM parameters under /app/lakehouse-agent/okta-*

Cleanup ownership note: this script OWNS teardown of the OBO exchange app and
the okta-obo-client-* SSM keys (both Okta-side resources created by
setup_okta.py). The AWS-side OBO substrate (provider / gateway / target / AOSS
+ obo-* SSM keys) is owned by
deployment/5b-obo-gateway-setup/06_cleanup_obo_gateway.py — no double ownership.

Usage:
    python cleanup_okta.py [--keep-ssm]
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

        print("🧹 Okta Cleanup")
        print(f"   Org URL: {self.org_url}")
        print(f"   Region:  {self.region}")

    async def _find_app(self, label: str = OKTA_APP_NAME):
        """Return Okta app object matching `label`, or None."""
        try:
            apps, _, _ = await self.okta.list_applications()
            for a in apps or []:
                if a.label == label:
                    return a
        except Exception as e:
            print(f"⚠️  Error listing apps: {e}")
        return None

    async def _find_auth_server(self):
        try:
            servers, _, _ = await self.okta.list_authorization_servers()
            for s in servers or []:
                if s.name == OKTA_AUTH_SERVER_NAME:
                    return s
        except Exception as e:
            print(f"⚠️  Error listing auth servers: {e}")
        return None

    async def _find_group(self, name: str):
        try:
            groups, _, _ = await self.okta.list_groups(query_params={"q": name})
            for g in groups or []:
                if g.profile.name == name:
                    return g
        except Exception as e:
            print(f"⚠️  Error listing groups: {e}")
        return None

    async def _find_user(self, login: str):
        try:
            user, _, _ = await self.okta.get_user(login)
            return user
        except Exception:
            return None

    async def delete_app(self, label: str = OKTA_APP_NAME):
        print(f"\n🗑️  Deleting Okta application: {label}")
        app = await self._find_app(label)
        if not app:
            print(f"   ⏭️  App not found: {label}")
            return
        try:
            # Okta requires deactivation before deletion.
            await self.okta.deactivate_application(app.id)
            await self.okta.delete_application(app.id)
            print(f"   ✅ Deleted app: {label}")
        except Exception as e:
            print(f"   ❌ Error deleting app: {e}")

    async def delete_auth_server(self):
        print("\n🗑️  Deleting Okta custom authorization server...")
        server = await self._find_auth_server()
        if not server:
            print(f"   ⏭️  Auth server not found: {OKTA_AUTH_SERVER_NAME}")
            return
        try:
            await self.okta.deactivate_authorization_server(server.id)
            await self.okta.delete_authorization_server(server.id)
            print(f"   ✅ Deleted auth server: {OKTA_AUTH_SERVER_NAME}")
        except Exception as e:
            print(f"   ❌ Error deleting auth server: {e}")

    async def delete_groups(self):
        print("\n🗑️  Deleting Okta groups...")
        for name in GROUP_NAMES:
            g = await self._find_group(name)
            if not g:
                print(f"   ⏭️  Group not found: {name}")
                continue
            try:
                await self.okta.delete_group(g.id)
                print(f"   ✅ Deleted group: {name}")
            except Exception as e:
                print(f"   ❌ Error deleting group {name}: {e}")

    async def delete_users(self):
        print("\n🗑️  Deleting Okta test users...")
        for login in USER_LOGINS:
            u = await self._find_user(login)
            if not u:
                print(f"   ⏭️  User not found: {login}")
                continue
            try:
                # Okta requires deactivation before deletion.
                await self.okta.deactivate_or_delete_user(u.id)
                await self.okta.deactivate_or_delete_user(u.id)
                print(f"   ✅ Deleted user: {login}")
            except Exception as e:
                print(f"   ❌ Error deleting user {login}: {e}")

    def delete_ssm_parameters(self):
        if self.keep_ssm:
            print("\n⏭️  Keeping SSM parameters (--keep-ssm)")
            return
        print("\n🗑️  Deleting SSM parameters...")
        params = [
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
        for p in params:
            full = f"/app/lakehouse-agent/{p}"
            try:
                self.ssm.delete_parameter(Name=full)
                print(f"   ✅ Deleted: {full}")
            except self.ssm.exceptions.ParameterNotFound:
                print(f"   ⏭️  Not found: {full}")
            except Exception as e:
                print(f"   ❌ Error deleting {full}: {e}")

    async def run(self):
        await self.delete_app(OKTA_APP_NAME)
        # Dedicated OBO token-exchange service app (owned here, per the
        # cleanup-ownership note at the top of this file).
        await self.delete_app(OKTA_EXCHANGE_APP_NAME)
        await self.delete_auth_server()
        await self.delete_groups()
        await self.delete_users()
        self.delete_ssm_parameters()
        print("\n✨ Okta cleanup complete!")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Okta resources")
    parser.add_argument("--keep-ssm", action="store_true", help="Keep SSM parameters")
    args = parser.parse_args()

    # Same reason as setup_okta.py: this script reads OKTA_ORG_URL /
    # OKTA_API_TOKEN from os.environ and exits 1 without them, but nothing on the
    # CLI path ever loaded .env. Already-exported variables still win.
    load_env_file()

    cleanup = OktaCleanup(keep_ssm=args.keep_ssm)
    asyncio.run(cleanup.run())


if __name__ == "__main__":
    main()
