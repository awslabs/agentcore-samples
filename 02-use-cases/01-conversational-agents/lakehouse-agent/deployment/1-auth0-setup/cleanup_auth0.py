#!/usr/bin/env python3
"""
Cleanup Auth0 Resources for Health Lakehouse Data

This script removes all Auth0 resources and SSM parameters created by setup_auth0.py:
- Test users
- Roles (policyholders, adjusters, administrators)
- Applications (user-login app, OBO exchange client)
- API resource server
- SSM parameters

Prerequisites:
    - AUTH0_DOMAIN: Your Auth0 tenant domain
    - AUTH0_CLIENT_ID: Management API application client ID
    - AUTH0_CLIENT_SECRET: Management API application client secret
    - AWS credentials configured

Usage:
    python cleanup_auth0.py [--dry-run] [--force]

Options:
    --dry-run    Show what would be deleted without actually deleting
    --force      Skip confirmation prompt
"""

import argparse
import os
import sys

import boto3
from auth0.management import Auth0

# The sample root, so `utils` is importable when this script is run directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from utils.env_file import load_env_file

SSM_PREFIX = "/app/lakehouse-agent/"

# SSM parameters created by setup_auth0.py
AUTH0_SSM_PARAMS = [
    "auth0-domain",
    "auth0-client-id",
    "auth0-client-secret",
    "auth0-obo-client-id",
    "auth0-obo-client-secret",
    "auth0-audience",
    "auth0-discovery-url",
    "auth0-policyholders-role-id",
    "auth0-adjusters-role-id",
    "auth0-administrators-role-id",
]

# User SSM params pattern
USER_SSM_PATTERN = "auth0-user-"

# Expected resource names (for identification)
API_IDENTIFIER = "api://lakehouse-api"
USER_APP_NAME = "Health Lakehouse User Login"
OBO_CLIENT_NAME = "Health Lakehouse OBO Exchange"
ROLE_NAMES = ["policyholders", "adjusters", "administrators"]
TEST_USER_EMAILS = [
    "policyholder001@example.com",
    "policyholder002@example.com",
    "adjuster001@example.com",
    "adjuster002@example.com",
    "admin@example.com",
]


class Auth0Cleaner:
    def __init__(self, dry_run: bool = False):
        """Initialize cleanup with Auth0 and AWS clients."""
        self.dry_run = dry_run
        self.deleted = []
        self.errors = []

        # Load environment
        load_env_file()

        # Auth0 credentials
        self.domain = os.environ.get("AUTH0_DOMAIN")
        self.client_id = os.environ.get("AUTH0_CLIENT_ID")
        self.client_secret = os.environ.get("AUTH0_CLIENT_SECRET")

        if not all([self.domain, self.client_id, self.client_secret]):
            print("❌ Missing Auth0 credentials in environment")
            print("   Required: AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET")
            sys.exit(1)

        # Initialize clients
        self.auth0 = Auth0(self.domain, self.client_id, self.client_secret)

        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)

        mode = "DRY RUN" if dry_run else "LIVE"
        print("=" * 70)
        print(f"Auth0 Cleanup ({mode})")
        print("=" * 70)
        print(f"Auth0 Domain: {self.domain}")
        print(f"AWS Region: {self.region}")

    def delete_test_users(self):
        """Delete test users from Auth0."""
        print("\n👥 Deleting Test Users...")

        for email in TEST_USER_EMAILS:
            try:
                users = self.auth0.users.list(q=f'email:"{email}"')
                if users.get("users"):
                    user = users["users"][0]
                    user_id = user["user_id"]

                    if self.dry_run:
                        print(f"   [DRY RUN] Would delete user: {email} ({user_id})")
                    else:
                        self.auth0.users.delete(user_id)
                        print(f"   ✅ Deleted user: {email}")
                    self.deleted.append(f"User: {email}")
                else:
                    print(f"   ⏭️  User not found: {email}")
            except Exception as e:
                print(f"   ❌ Error deleting user {email}: {e}")
                self.errors.append(f"User {email}: {e}")

    def delete_roles(self):
        """Delete roles from Auth0."""
        print("\n🏷️  Deleting Roles...")

        for role_name in ROLE_NAMES:
            try:
                # Find role by name
                roles = self.auth0.roles.list()
                role = next((r for r in roles.get("roles", []) if r["name"] == role_name), None)

                if role:
                    role_id = role["id"]
                    if self.dry_run:
                        print(f"   [DRY RUN] Would delete role: {role_name} ({role_id})")
                    else:
                        self.auth0.roles.delete(role_id)
                        print(f"   ✅ Deleted role: {role_name}")
                    self.deleted.append(f"Role: {role_name}")
                else:
                    print(f"   ⏭️  Role not found: {role_name}")
            except Exception as e:
                print(f"   ❌ Error deleting role {role_name}: {e}")
                self.errors.append(f"Role {role_name}: {e}")

    def delete_applications(self):
        """Delete Auth0 applications."""
        print("\n📱 Deleting Applications...")

        app_names = [USER_APP_NAME, OBO_CLIENT_NAME]

        for app_name in app_names:
            try:
                # List all clients and find by name
                clients = self.auth0.clients.all()
                client = next((c for c in clients if c.get("name") == app_name), None)

                if client:
                    client_id = client["client_id"]
                    if self.dry_run:
                        print(f"   [DRY RUN] Would delete app: {app_name} ({client_id})")
                    else:
                        self.auth0.clients.delete(client_id)
                        print(f"   ✅ Deleted app: {app_name}")
                    self.deleted.append(f"Application: {app_name}")
                else:
                    print(f"   ⏭️  Application not found: {app_name}")
            except Exception as e:
                print(f"   ❌ Error deleting app {app_name}: {e}")
                self.errors.append(f"Application {app_name}: {e}")

    def delete_api(self):
        """Delete Auth0 API resource server."""
        print("\n🔌 Deleting API Resource Server...")

        try:
            # Find API by identifier
            apis = self.auth0.resource_servers.all()
            api = next((a for a in apis if a.get("identifier") == API_IDENTIFIER), None)

            if api:
                api_id = api["id"]
                if self.dry_run:
                    print(f"   [DRY RUN] Would delete API: {API_IDENTIFIER} ({api_id})")
                else:
                    self.auth0.resource_servers.delete(api_id)
                    print(f"   ✅ Deleted API: {API_IDENTIFIER}")
                self.deleted.append(f"API: {API_IDENTIFIER}")
            else:
                print(f"   ⏭️  API not found: {API_IDENTIFIER}")
        except Exception as e:
            print(f"   ❌ Error deleting API: {e}")
            self.errors.append(f"API {API_IDENTIFIER}: {e}")

    def delete_ssm_parameters(self):
        """Delete SSM parameters."""
        print("\n🗄️  Deleting SSM Parameters...")

        # Fixed parameters
        for param_name in AUTH0_SSM_PARAMS:
            full_name = f"{SSM_PREFIX}{param_name}"
            self._delete_ssm_param(full_name)

        # User sub parameters (dynamic)
        try:
            paginator = self.ssm.get_paginator("describe_parameters")
            for page in paginator.paginate(
                ParameterFilters=[
                    {"Key": "Name", "Option": "Contains", "Values": [f"{SSM_PREFIX}{USER_SSM_PATTERN}"]}
                ]
            ):
                for param in page.get("Parameters", []):
                    self._delete_ssm_param(param["Name"])
        except Exception as e:
            print(f"   ❌ Error listing user SSM params: {e}")
            self.errors.append(f"SSM user params listing: {e}")

    def _delete_ssm_param(self, param_name: str):
        """Delete a single SSM parameter."""
        try:
            # Check if exists
            self.ssm.get_parameter(Name=param_name)

            if self.dry_run:
                print(f"   [DRY RUN] Would delete: {param_name}")
            else:
                self.ssm.delete_parameter(Name=param_name)
                print(f"   ✅ Deleted: {param_name}")
            self.deleted.append(f"SSM: {param_name}")
        except self.ssm.exceptions.ParameterNotFound:
            print(f"   ⏭️  Not found: {param_name}")
        except Exception as e:
            print(f"   ❌ Error deleting {param_name}: {e}")
            self.errors.append(f"SSM {param_name}: {e}")

    def print_summary(self):
        """Print cleanup summary."""
        print("\n" + "=" * 70)
        print("Cleanup Summary")
        print("=" * 70)

        if self.dry_run:
            print(f"\n📋 Would delete {len(self.deleted)} resources:")
        else:
            print(f"\n✅ Deleted {len(self.deleted)} resources:")

        for item in self.deleted:
            print(f"   • {item}")

        if self.errors:
            print(f"\n❌ {len(self.errors)} error(s):")
            for error in self.errors:
                print(f"   • {error}")

        if self.dry_run:
            print("\n💡 Run without --dry-run to actually delete resources")

        print("\n⚠️  Manual Cleanup Required:")
        print("   • Auth0 Actions (if created): Delete 'Add Roles to Tokens' action")
        print("   • Auth0 User-Delegated Client Grants: Remove from Auth0 Dashboard")
        print("   • Management API Application: NOT deleted (used for this script)")

        return len(self.errors) == 0

    def run(self):
        """Run the full cleanup."""
        # Order matters: users first (they reference roles), then roles,
        # then apps (which reference API), then API, finally SSM params
        self.delete_test_users()
        self.delete_roles()
        self.delete_applications()
        self.delete_api()
        self.delete_ssm_parameters()
        return self.print_summary()


def main():
    parser = argparse.ArgumentParser(description="Cleanup Auth0 resources for lakehouse-agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    cleaner = Auth0Cleaner(dry_run=args.dry_run)

    if not args.force and not args.dry_run:
        print("\n⚠️  WARNING: This will permanently delete Auth0 resources!")
        print("   Run with --dry-run first to see what would be deleted.")
        response = input("\nType 'yes' to continue: ")
        if response.lower() != "yes":
            print("Cancelled.")
            sys.exit(0)

    success = cleaner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
