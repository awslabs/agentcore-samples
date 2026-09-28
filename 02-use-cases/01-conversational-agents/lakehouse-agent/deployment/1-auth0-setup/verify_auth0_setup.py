#!/usr/bin/env python3
"""
Verify Auth0 Setup for Health Lakehouse Data

This script verifies:
1. All SSM parameters are present and valid
2. Auth0 API and applications exist
3. OBO token exchange is configured
4. Test users can authenticate
5. Optional: Test OBO token exchange flow

Usage:
    python verify_auth0_setup.py [--test-obo]

Options:
    --test-obo    Test the OBO token exchange flow (requires user interaction)
"""

import argparse
import json
import os
import sys
from typing import Optional

import boto3
import requests
from auth0.management import Auth0

# The sample root, so `utils` is importable when this script is run directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from utils.env_file import load_env_file

SSM_PREFIX = "/app/lakehouse-agent/"

# Expected SSM parameters from setup_auth0.py
EXPECTED_PARAMS = [
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


class Auth0Verifier:
    def __init__(self):
        """Initialize verifier with AWS and Auth0 clients."""
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)

        # Load config from SSM first
        self.config = {}
        self.errors = []
        self.warnings = []

        print("=" * 70)
        print("Auth0 Setup Verification")
        print("=" * 70)
        print(f"AWS Region: {self.region}")

    def verify_ssm_parameters(self) -> bool:
        """Verify all expected SSM parameters exist."""
        print("\n📋 Verifying SSM Parameters...")
        all_present = True

        for param_name in EXPECTED_PARAMS:
            full_name = f"{SSM_PREFIX}{param_name}"
            try:
                is_secret = "secret" in param_name.lower()
                response = self.ssm.get_parameter(Name=full_name, WithDecryption=is_secret)
                value = response["Parameter"]["Value"]
                self.config[param_name] = value

                if is_secret:
                    print(f"   ✅ {full_name}: ****** (SecureString)")
                else:
                    display_value = value[:50] + "..." if len(value) > 50 else value
                    print(f"   ✅ {full_name}: {display_value}")
            except self.ssm.exceptions.ParameterNotFound:
                print(f"   ❌ {full_name}: NOT FOUND")
                self.errors.append(f"Missing SSM parameter: {full_name}")
                all_present = False
            except Exception as e:
                print(f"   ❌ {full_name}: Error - {e}")
                self.errors.append(f"Error reading {full_name}: {e}")
                all_present = False

        return all_present

    def verify_auth0_connectivity(self) -> bool:
        """Verify Auth0 discovery endpoint is reachable."""
        print("\n🔗 Verifying Auth0 Connectivity...")

        discovery_url = self.config.get("auth0-discovery-url")
        if not discovery_url:
            print("   ❌ Discovery URL not available")
            return False

        try:
            response = requests.get(discovery_url, timeout=10)
            response.raise_for_status()
            oidc_config = response.json()

            print(f"   ✅ Discovery URL reachable: {discovery_url}")
            print(f"   ✅ Issuer: {oidc_config.get('issuer')}")
            print(f"   ✅ Token endpoint: {oidc_config.get('token_endpoint')}")

            # Check for token-exchange grant type support
            grants = oidc_config.get("grant_types_supported", [])
            if "urn:ietf:params:oauth:grant-type:token-exchange" in grants:
                print("   ✅ Token exchange grant type supported")
            else:
                print("   ⚠️  Token exchange grant type not listed (may still work)")
                self.warnings.append("Token exchange not listed in discovery (may require tenant config)")

            return True
        except Exception as e:
            print(f"   ❌ Error connecting to Auth0: {e}")
            self.errors.append(f"Auth0 connectivity error: {e}")
            return False

    def verify_auth0_resources(self) -> bool:
        """Verify Auth0 API and applications exist using Management API."""
        print("\n🔍 Verifying Auth0 Resources...")

        # Need Management API credentials from env
        domain = self.config.get("auth0-domain")
        mgmt_client_id = os.environ.get("AUTH0_CLIENT_ID")
        mgmt_client_secret = os.environ.get("AUTH0_CLIENT_SECRET")

        if not mgmt_client_id or not mgmt_client_secret:
            print("   ⚠️  Management API credentials not in env, skipping resource verification")
            print("      Set AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET to verify resources")
            self.warnings.append("Could not verify Auth0 resources (no Management API credentials)")
            return True  # Not a failure, just can't verify

        try:
            auth0 = Auth0(domain, mgmt_client_id, mgmt_client_secret)

            # Verify API exists
            audience = self.config.get("auth0-audience")
            apis = auth0.resource_servers.all()
            api_found = any(api.get("identifier") == audience for api in apis)
            if api_found:
                print(f"   ✅ API exists with audience: {audience}")
            else:
                print(f"   ❌ API not found with audience: {audience}")
                self.errors.append(f"API not found: {audience}")

            # Verify user app exists
            app_client_id = self.config.get("auth0-client-id")
            try:
                client = auth0.clients.get(app_client_id)
                print(f"   ✅ User app exists: {client.get('name')} ({app_client_id})")
            except Exception:
                print(f"   ❌ User app not found: {app_client_id}")
                self.errors.append(f"User app not found: {app_client_id}")

            # Verify OBO exchange client exists
            obo_client_id = self.config.get("auth0-obo-client-id")
            try:
                obo_client = auth0.clients.get(obo_client_id)
                print(f"   ✅ OBO exchange client exists: {obo_client.get('name')} ({obo_client_id})")

                # Check grant types
                grant_types = obo_client.get("grant_types", [])
                if "urn:ietf:params:oauth:grant-type:token-exchange" in grant_types:
                    print("   ✅ OBO client has token-exchange grant type")
                else:
                    print("   ⚠️  OBO client missing token-exchange grant type")
                    self.warnings.append("OBO client may need token-exchange grant type configured")
            except Exception:
                print(f"   ❌ OBO exchange client not found: {obo_client_id}")
                self.errors.append(f"OBO exchange client not found: {obo_client_id}")

            # Verify roles exist
            for role_name in ["policyholders", "adjusters", "administrators"]:
                role_id = self.config.get(f"auth0-{role_name}-role-id")
                if role_id:
                    try:
                        role = auth0.roles.get(role_id)
                        print(f"   ✅ Role exists: {role.get('name')} ({role_id})")
                    except Exception:
                        print(f"   ❌ Role not found: {role_name} ({role_id})")
                        self.errors.append(f"Role not found: {role_name}")

            return len(self.errors) == 0

        except Exception as e:
            print(f"   ❌ Error verifying Auth0 resources: {e}")
            self.errors.append(f"Auth0 resource verification error: {e}")
            return False

    def verify_test_users(self) -> bool:
        """Verify test users exist in Auth0."""
        print("\n👥 Verifying Test Users...")

        domain = self.config.get("auth0-domain")
        mgmt_client_id = os.environ.get("AUTH0_CLIENT_ID")
        mgmt_client_secret = os.environ.get("AUTH0_CLIENT_SECRET")

        if not mgmt_client_id or not mgmt_client_secret:
            print("   ⚠️  Skipping (no Management API credentials)")
            return True

        test_emails = [
            "policyholder001@example.com",
            "policyholder002@example.com",
            "adjuster001@example.com",
            "adjuster002@example.com",
            "admin@example.com",
        ]

        try:
            auth0 = Auth0(domain, mgmt_client_id, mgmt_client_secret)
            users_found = 0

            for email in test_emails:
                users = auth0.users.list(q=f'email:"{email}"')
                if users.get("users"):
                    user = users["users"][0]
                    print(f"   ✅ {email} (sub: {user['user_id']})")
                    users_found += 1
                else:
                    print(f"   ❌ {email}: NOT FOUND")
                    self.errors.append(f"Test user not found: {email}")

            print(f"\n   Found {users_found}/{len(test_emails)} test users")
            return users_found == len(test_emails)

        except Exception as e:
            print(f"   ❌ Error verifying users: {e}")
            self.errors.append(f"User verification error: {e}")
            return False

    def test_client_credentials_flow(self) -> bool:
        """Test client_credentials flow with OBO client."""
        print("\n🔐 Testing Client Credentials Flow (OBO Client)...")

        domain = self.config.get("auth0-domain")
        obo_client_id = self.config.get("auth0-obo-client-id")
        obo_client_secret = self.config.get("auth0-obo-client-secret")
        audience = self.config.get("auth0-audience")

        if not all([domain, obo_client_id, obo_client_secret, audience]):
            print("   ❌ Missing required configuration")
            return False

        token_url = f"https://{domain}/oauth/token"

        try:
            response = requests.post(
                token_url,
                json={
                    "grant_type": "client_credentials",
                    "client_id": obo_client_id,
                    "client_secret": obo_client_secret,
                    "audience": audience,
                },
                timeout=10,
            )

            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                if access_token:
                    print("   ✅ Client credentials flow successful")
                    print(f"   ✅ Access token received (length: {len(access_token)})")

                    # Decode and show some claims (without verification)
                    import base64
                    payload = access_token.split(".")[1]
                    # Add padding if needed
                    payload += "=" * (4 - len(payload) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(payload))
                    print(f"   ✅ Token audience: {claims.get('aud')}")
                    print(f"   ✅ Token issuer: {claims.get('iss')}")
                    return True
            else:
                print(f"   ❌ Token request failed: {response.status_code}")
                print(f"      Response: {response.text[:200]}")
                self.errors.append(f"Client credentials flow failed: {response.status_code}")
                return False

        except Exception as e:
            print(f"   ❌ Error testing client credentials: {e}")
            self.errors.append(f"Client credentials test error: {e}")
            return False

    def test_obo_token_exchange(self, subject_token: str) -> bool:
        """
        Test RFC 8693 OBO token exchange.

        Args:
            subject_token: A valid access token from the user-login app.
        """
        print("\n🔄 Testing OBO Token Exchange (RFC 8693)...")

        domain = self.config.get("auth0-domain")
        obo_client_id = self.config.get("auth0-obo-client-id")
        obo_client_secret = self.config.get("auth0-obo-client-secret")
        audience = self.config.get("auth0-audience")

        if not all([domain, obo_client_id, obo_client_secret, audience]):
            print("   ❌ Missing required configuration")
            return False

        token_url = f"https://{domain}/oauth/token"

        try:
            # RFC 8693 token exchange request
            response = requests.post(
                token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": obo_client_id,
                    "client_secret": obo_client_secret,
                    "subject_token": subject_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "audience": audience,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )

            if response.status_code == 200:
                token_data = response.json()
                access_token = token_data.get("access_token")
                if access_token:
                    print("   ✅ OBO token exchange successful!")

                    # Decode and check for `act` claim
                    import base64
                    payload = access_token.split(".")[1]
                    payload += "=" * (4 - len(payload) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(payload))

                    print(f"   ✅ New token audience: {claims.get('aud')}")
                    print(f"   ✅ Subject (sub): {claims.get('sub')}")

                    if "act" in claims:
                        print(f"   ✅ Delegation chain (act): {json.dumps(claims['act'], indent=6)}")
                    else:
                        print("   ⚠️  No 'act' claim in token (delegation tracking)")
                        self.warnings.append("OBO token missing 'act' claim")

                    return True
            else:
                print(f"   ❌ OBO exchange failed: {response.status_code}")
                print(f"      Response: {response.text[:300]}")

                # Common errors
                error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                error_code = error_data.get("error")
                if error_code == "unauthorized_client":
                    print("\n   💡 Hint: The OBO client may need User-Delegated Client Grant configured")
                    print("      See setup_auth0.py output for manual configuration steps")
                elif error_code == "invalid_grant":
                    print("\n   💡 Hint: The subject_token may be invalid or expired")
                elif error_code == "unsupported_grant_type":
                    print("\n   💡 Hint: Token exchange may not be enabled on this tenant")
                    print("      Contact Auth0 support to enable OBO token exchange")

                self.errors.append(f"OBO exchange failed: {error_code or response.status_code}")
                return False

        except Exception as e:
            print(f"   ❌ Error testing OBO exchange: {e}")
            self.errors.append(f"OBO exchange test error: {e}")
            return False

    def print_summary(self):
        """Print verification summary."""
        print("\n" + "=" * 70)
        print("Verification Summary")
        print("=" * 70)

        if self.errors:
            print(f"\n❌ {len(self.errors)} Error(s):")
            for error in self.errors:
                print(f"   • {error}")

        if self.warnings:
            print(f"\n⚠️  {len(self.warnings)} Warning(s):")
            for warning in self.warnings:
                print(f"   • {warning}")

        if not self.errors and not self.warnings:
            print("\n✅ All verifications passed!")
        elif not self.errors:
            print("\n✅ Core setup verified with warnings")
        else:
            print("\n❌ Setup verification failed")
            print("\n📋 Next Steps:")
            print("   1. Run setup_auth0.py to create missing resources")
            print("   2. Check Auth0 Dashboard for manual configuration steps")
            print("   3. Re-run this verification script")

        return len(self.errors) == 0


def main():
    parser = argparse.ArgumentParser(description="Verify Auth0 setup for lakehouse-agent")
    parser.add_argument(
        "--test-obo",
        action="store_true",
        help="Test OBO token exchange (requires a subject token)",
    )
    parser.add_argument(
        "--subject-token",
        type=str,
        help="Subject token for OBO test (user's access token)",
    )
    args = parser.parse_args()

    # Load .env
    load_env_file()

    verifier = Auth0Verifier()

    # Run verifications
    verifier.verify_ssm_parameters()
    verifier.verify_auth0_connectivity()
    verifier.verify_auth0_resources()
    verifier.verify_test_users()
    verifier.test_client_credentials_flow()

    # Optional OBO test
    if args.test_obo:
        if args.subject_token:
            verifier.test_obo_token_exchange(args.subject_token)
        else:
            print("\n⚠️  --test-obo requires --subject-token")
            print("   Get a user token via the user-login app's authorization_code flow")
            print("   Example: python decode_token.py to get a test user token")

    success = verifier.print_summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
