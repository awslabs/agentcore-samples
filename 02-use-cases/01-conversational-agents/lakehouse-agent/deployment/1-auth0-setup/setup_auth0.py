#!/usr/bin/env python3
"""
Auth0 Setup for Health Lakehouse Data
Creates Auth0 applications + API + roles + test users for RFC 8693 OBO token exchange.
Writes configuration to SSM Parameter Store.

This script mirrors setup_okta.py and creates:
1. Regular Web Application - for user login (authorization_code flow)
2. Custom API Client (Resource Server) - for OBO token exchange (RFC 8693)
3. API (Resource Server) - defines audience and scopes
4. Roles - policyholders, adjusters, administrators
5. Test Users - assigned to roles
6. User-Delegated Client Grant - enables OBO between Custom API Client and downstream API

Auth0 OBO Token Exchange (RFC 8693):
- Auth0 supports OBO via Custom Token Exchange
- Requires a "Custom API Client" (app_type=resource_server) that shares an identifier with the API
- The Custom API Client performs the token exchange, receiving the user's access token
  as subject_token and returning a new token with the `act` claim
- User-Delegated Client Grant authorizes the exchange

Usage:
    python setup_auth0.py

Prerequisites:
    - Auth0 tenant with admin permissions
    - Auth0 Management API credentials (AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET)
      stored in env or .env file. These are for the Management API M2M app, NOT the
      user-login app created by this script.
    - AWS credentials configured (for SSM Parameter Store writes)

References:
    - https://auth0.com/docs/secure/call-apis-on-users-behalf/on-behalf-of-token-exchange
    - https://auth0.com/docs/authenticate/custom-token-exchange
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import boto3
from auth0.management import Auth0

# The sample root, so `utils` is importable when this script is run directly.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from utils.env_file import load_env_file

# Single source of truth for the resource-server audience (used as JWT 'aud'
# claim value). Same logical identifier as Okta path.
RESOURCE_SERVER_AUDIENCE = "api://lakehouse-api"

# Names assigned to created Auth0 resources (idempotency keys).
AUTH0_USER_APP_NAME = "lakehouse-agent-app"
AUTH0_API_NAME = "lakehouse-agent-api"
# The Custom API Client for OBO exchange. This is a special app_type=resource_server
# application that shares its identifier with the API, enabling it to perform
# RFC 8693 token exchange.
AUTH0_OBO_CLIENT_NAME = "lakehouse-obo-exchange-client"

# Scopes for the API (same as Okta path).
API_SCOPES = [
    {"value": "claims.query", "description": "Query claims"},
    {"value": "claims.submit", "description": "Submit claims"},
    {"value": "claims.update", "description": "Update claims"},
    {"value": "claims.approve", "description": "Approve/deny claims"},
    {"value": "opensearch.search", "description": "Search free-text claim notes via OpenSearch"},
]

# Role definitions (Auth0 uses roles, not groups like Okta).
ROLES = [
    {"name": "policyholders", "description": "Policy holders role"},
    {"name": "adjusters", "description": "Claims adjusters role"},
    {"name": "administrators", "description": "Administrators role"},
]

# Permission matrix: which permissions each role should have.
# With RBAC enabled, only permissions assigned to a user's role are included in tokens.
ROLE_PERMISSIONS = {
    "policyholders": ["claims.query", "claims.submit"],
    "adjusters": ["claims.query", "claims.submit", "claims.update", "opensearch.search"],
    "administrators": ["claims.query", "claims.submit", "claims.update", "claims.approve", "opensearch.search"],
}

# Test users (same as Okta path).
TEST_USERS = [
    {"email": "policyholder001@example.com", "name": "Policyholder 001", "role": "policyholders"},
    {"email": "policyholder002@example.com", "name": "Policyholder 002", "role": "policyholders"},
    {"email": "adjuster001@example.com", "name": "Adjuster 001", "role": "adjusters"},
    {"email": "adjuster002@example.com", "name": "Adjuster 002", "role": "adjusters"},
    {"email": "admin@example.com", "name": "Admin User", "role": "administrators"},
]


class Auth0Setup:
    def __init__(self):
        """Initialize Auth0 setup with domain and Management API credentials."""
        # Get Auth0 Management API credentials from env.
        self.domain = os.environ.get("AUTH0_DOMAIN")
        if not self.domain:
            raise RuntimeError(
                "AUTH0_DOMAIN not set. Add it to .env or export it before running this script. "
                "Example: your-tenant.auth0.com (no scheme)."
            )

        # Normalize domain (strip scheme if present).
        if self.domain.startswith("https://"):
            self.domain = self.domain.replace("https://", "")
        if self.domain.startswith("http://"):
            self.domain = self.domain.replace("http://", "")
        self.domain = self.domain.rstrip("/")

        # Management API M2M app credentials (NOT the user-login app).
        self.mgmt_client_id = os.environ.get("AUTH0_CLIENT_ID")
        self.mgmt_client_secret = os.environ.get("AUTH0_CLIENT_SECRET")
        if not self.mgmt_client_id or not self.mgmt_client_secret:
            raise RuntimeError(
                "AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET not set. These are the credentials "
                "for your Auth0 Management API M2M application. Create one in Auth0 Dashboard -> "
                "Applications -> APIs -> Auth0 Management API -> Machine to Machine Applications."
            )

        # Initialize Auth0 Management client.
        # auth0-python v4+ uses GetToken for client credentials flow
        from auth0.authentication import GetToken
        get_token = GetToken(self.domain, self.mgmt_client_id, client_secret=self.mgmt_client_secret)
        token = get_token.client_credentials(f"https://{self.domain}/api/v2/")
        self.auth0 = Auth0(tenant_domain=self.domain, token=token["access_token"])

        # AWS clients for SSM persistence.
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.env_file = Path(__file__).parent.parent / ".env"

        print("Initialized Auth0 setup")
        print(f"   Domain: {self.domain}")
        print(f"   AWS region (for SSM): {self.region}")
        print(f"   Resource server audience: {RESOURCE_SERVER_AUDIENCE}")

    # ─────────────────────────────────────────────────────────────────
    # Discovery helpers — find existing resources by name (idempotency)
    # ─────────────────────────────────────────────────────────────────

    def find_existing_client(self, name: str) -> Optional[dict]:
        """Find existing Auth0 application by name."""
        try:
            response = self.auth0.clients.list()
            clients = response.clients if hasattr(response, 'clients') else response
            for client in clients:
                client_dict = client if isinstance(client, dict) else client.__dict__
                if client_dict.get("name") == name:
                    print(f"ℹ️  Found existing Auth0 client: {client_dict['client_id']}")
                    return client_dict
        except Exception as e:
            print(f"⚠️  Error searching for client: {e}")
        return None

    def find_existing_api(self, identifier: str) -> Optional[dict]:
        """Find existing Auth0 API (resource server) by identifier."""
        try:
            response = self.auth0.resource_servers.list()
            apis = response.resource_servers if hasattr(response, 'resource_servers') else response
            for api in apis:
                api_dict = api if isinstance(api, dict) else api.__dict__
                if api_dict.get("identifier") == identifier:
                    print(f"ℹ️  Found existing Auth0 API: {api_dict['id']}")
                    return api_dict
        except Exception as e:
            print(f"⚠️  Error searching for API: {e}")
        return None

    def find_existing_role(self, name: str) -> Optional[dict]:
        """Find existing Auth0 role by name."""
        try:
            response = self.auth0.roles.list()
            roles = response.roles if hasattr(response, 'roles') else response
            for role in roles:
                role_dict = role if isinstance(role, dict) else role.__dict__
                if role_dict.get("name") == name:
                    return role_dict
        except Exception as e:
            print(f"⚠️  Error searching for role {name}: {e}")
        return None

    def find_existing_user(self, email: str) -> Optional[dict]:
        """Find existing Auth0 user by email."""
        try:
            response = self.auth0.users.list(q=f'email:"{email}"')
            users = response.users if hasattr(response, 'users') else response
            for user in users:
                user_dict = user if isinstance(user, dict) else user.__dict__
                if user_dict.get("email") == email:
                    return user_dict
        except Exception as e:
            print(f"⚠️  Error searching for user {email}: {e}")
        return None

    # ─────────────────────────────────────────────────────────────────
    # SSM persistence
    # ─────────────────────────────────────────────────────────────────

    def store_parameters_in_ssm(self, config: dict):
        """
        Store Auth0 configuration in SSM Parameter Store under
        /app/lakehouse-agent/auth0-*.

        Args:
            config: Dictionary with domain, client IDs, secrets, API info, etc.
        """
        print("\n💾 Storing configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/auth0-domain",
                "value": config["domain"],
                "description": "Auth0 tenant domain",
            },
            {
                "name": "/app/lakehouse-agent/auth0-client-id",
                "value": config["app_client_id"],
                "description": "Auth0 Regular Web Application client ID (user login)",
            },
            {
                "name": "/app/lakehouse-agent/auth0-obo-client-id",
                "value": config["obo_client_id"],
                "description": "Auth0 Custom API Client ID for OBO token exchange (RFC 8693)",
            },
            {
                "name": "/app/lakehouse-agent/auth0-audience",
                "value": config["audience"],
                "description": "Auth0 API audience (resource server identifier)",
            },
            {
                "name": "/app/lakehouse-agent/auth0-discovery-url",
                "value": config["discovery_url"],
                "description": "Auth0 OpenID Connect discovery URL",
            },
            {
                "name": "/app/lakehouse-agent/auth0-policyholders-role-id",
                "value": config["policyholders_role_id"],
                "description": "Auth0 role ID for policyholders",
            },
            {
                "name": "/app/lakehouse-agent/auth0-adjusters-role-id",
                "value": config["adjusters_role_id"],
                "description": "Auth0 role ID for adjusters",
            },
            {
                "name": "/app/lakehouse-agent/auth0-administrators-role-id",
                "value": config["administrators_role_id"],
                "description": "Auth0 role ID for administrators",
            },
        ]

        # Store SecureString secrets.
        secure_params = [
            (
                "/app/lakehouse-agent/auth0-client-secret",
                config.get("app_client_secret"),
                "Auth0 Regular Web Application client secret (SecureString)",
            ),
            (
                "/app/lakehouse-agent/auth0-obo-client-secret",
                config.get("obo_client_secret"),
                "Auth0 Custom API Client secret for OBO exchange (SecureString)",
            ),
        ]
        for name, value, description in secure_params:
            if not value:
                continue
            try:
                self.ssm.put_parameter(
                    Name=name,
                    Value=value,
                    Description=description,
                    Type="SecureString",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter (SecureString): {name}")
            except Exception as e:
                print(f"❌ Error storing {name}: {e}")
                raise

        # Store String parameters.
        for param in parameters:
            try:
                self.ssm.put_parameter(
                    Name=param["name"],
                    Value=param["value"],
                    Description=param["description"],
                    Type="String",
                    Overwrite=True,
                )
                print(f"✅ Stored parameter: {param['name']} = {param['value']}")
            except Exception as e:
                print(f"❌ Error storing parameter {param['name']}: {e}")
                raise

    # ─────────────────────────────────────────────────────────────────
    # Resource creation — API, apps, roles, users
    # ─────────────────────────────────────────────────────────────────

    def create_api(self) -> dict:
        """
        Create or find the Auth0 API (Resource Server).

        This defines the audience and scopes for the lakehouse-agent.
        """
        existing = self.find_existing_api(RESOURCE_SERVER_AUDIENCE)
        if existing:
            print(f"ℹ️  Reusing existing API: {existing['id']}")
            # Update scopes if needed.
            existing_scope_values = {s.get("value") if isinstance(s, dict) else getattr(s, "value", None) for s in existing.get("scopes", [])}
            missing_scopes = [s for s in API_SCOPES if s["value"] not in existing_scope_values]
            if missing_scopes:
                all_scopes = list(existing.get("scopes", [])) + missing_scopes
                self.auth0.resource_servers.update(
                    id=existing["id"],
                    scopes=all_scopes,
                )
                print(f"   ✅ Added missing scopes: {[s['value'] for s in missing_scopes]}")
            return {
                "api_id": existing["id"],
                "identifier": existing["identifier"],
                "status": "reused",
            }

        # Create new API using keyword arguments (auth0-python v6.x).
        response = self.auth0.resource_servers.create(
            name=AUTH0_API_NAME,
            identifier=RESOURCE_SERVER_AUDIENCE,
            scopes=API_SCOPES,
            signing_alg="RS256",
            token_lifetime=86400,
            enforce_policies=True,
            token_dialect="access_token_authz",
        )
        api = response if isinstance(response, dict) else response.__dict__
        print(f"✅ API created: {api['id']}")
        return {
            "api_id": api["id"],
            "identifier": api["identifier"],
            "status": "created",
        }

    def create_user_app(self) -> dict:
        """
        Create or find the Regular Web Application for user login.

        This app uses authorization_code flow with PKCE for user authentication.
        """
        existing = self.find_existing_client(AUTH0_USER_APP_NAME)
        if existing:
            print(f"ℹ️  Reusing existing user app: {existing['client_id']}")
            # Get client secret (need to fetch full client details).
            response = self.auth0.clients.get(existing["client_id"])
            client = response if isinstance(response, dict) else response.__dict__
            return {
                "app_id": client["client_id"],
                "app_client_id": client["client_id"],
                "app_client_secret": client.get("client_secret"),
                "status": "reused",
            }

        # Create new Regular Web Application using keyword arguments (auth0-python v6.x).
        response = self.auth0.clients.create(
            name=AUTH0_USER_APP_NAME,
            app_type="regular_web",
            callbacks=["http://localhost:8501/"],
            allowed_logout_urls=["http://localhost:8501/"],
            web_origins=["http://localhost:8501"],
            grant_types=[
                "authorization_code",
                "refresh_token",
                "client_credentials",
            ],
            token_endpoint_auth_method="client_secret_post",
            oidc_conformant=True,
            jwt_configuration={
                "alg": "RS256",
                "lifetime_in_seconds": 36000,
            },
        )
        client = response if isinstance(response, dict) else response.__dict__
        print(f"✅ User app created: {client['client_id']}")
        return {
            "app_id": client["client_id"],
            "app_client_id": client["client_id"],
            "app_client_secret": client.get("client_secret"),
            "status": "created",
        }

    def create_obo_exchange_client(self) -> dict:
        """
        Create or find the Custom API Client for OBO token exchange.

        Auth0's RFC 8693 OBO requires a "Custom API Client" which is an application
        with app_type=resource_server that shares an identifier with an API.
        This client performs the token exchange, receiving the user's access token
        as subject_token and issuing a new token with the `act` claim.

        References:
            - https://auth0.com/docs/secure/call-apis-on-users-behalf/on-behalf-of-token-exchange
        """
        existing = self.find_existing_client(AUTH0_OBO_CLIENT_NAME)
        if existing:
            print(f"ℹ️  Reusing existing OBO exchange client: {existing['client_id']}")
            response = self.auth0.clients.get(existing["client_id"])
            client = response if isinstance(response, dict) else response.__dict__
            # Ensure client is authorized for the API (may have been skipped previously).
            self._authorize_client_for_api(client["client_id"])
            return {
                "app_id": client["client_id"],
                "obo_client_id": client["client_id"],
                "obo_client_secret": client.get("client_secret"),
                "status": "reused",
            }

        # Create Custom API Client using keyword arguments (auth0-python v6.x).
        # Note: token-exchange grant type may not be available on all tenants.
        # Start with client_credentials only, then try to enable token-exchange.
        response = self.auth0.clients.create(
            name=AUTH0_OBO_CLIENT_NAME,
            app_type="non_interactive",
            grant_types=[
                "client_credentials",
            ],
            token_endpoint_auth_method="client_secret_basic",
            oidc_conformant=True,
            jwt_configuration={
                "alg": "RS256",
                "lifetime_in_seconds": 36000,
            },
        )
        client = response if isinstance(response, dict) else response.__dict__
        print(f"✅ OBO exchange client created: {client['client_id']}")

        # Authorize this client for the API.
        self._authorize_client_for_api(client["client_id"])

        return {
            "app_id": client["client_id"],
            "obo_client_id": client["client_id"],
            "obo_client_secret": client.get("client_secret"),
            "status": "created",
        }

    def _authorize_client_for_api(self, client_id: str):
        """
        Create a client grant authorizing the OBO exchange client for the API.

        This is required for the client_credentials flow and as a prerequisite
        for the token-exchange flow.
        """
        try:
            # Check if grant already exists (auth0-python v6.x uses list() instead of all()).
            response = self.auth0.client_grants.list(audience=RESOURCE_SERVER_AUDIENCE)
            grants = response.client_grants if hasattr(response, 'client_grants') else response
            for grant in grants:
                grant_dict = grant if isinstance(grant, dict) else grant.__dict__
                if grant_dict.get("client_id") == client_id:
                    print(f"   ℹ️  Client grant already exists for {client_id}")
                    return

            # Create new grant using keyword arguments (auth0-python v6.x).
            self.auth0.client_grants.create(
                client_id=client_id,
                audience=RESOURCE_SERVER_AUDIENCE,
                scope=[s["value"] for s in API_SCOPES],
            )
            print(f"   ✅ Client grant created for {client_id}")
        except Exception as e:
            # Some tenants may not allow this or grant may already exist.
            print(f"   ⚠️  Could not create client grant: {e}")

    def enable_obo_token_exchange(self, obo_client_id: str):
        """
        Enable On-Behalf-Of token exchange for the Custom API Client.

        This configures the client to accept urn:ietf:params:oauth:grant-type:token-exchange
        and issue tokens with the `act` claim for delegation tracking.

        Note: Auth0's OBO feature may require tenant-level feature flags or
        enterprise features. If this fails, the tenant may need OBO enabled
        by Auth0 support.
        """
        print(f"\n🔐 Configuring OBO token exchange for client {obo_client_id}...")

        try:
            # Update the client to ensure token-exchange grant is enabled (auth0-python v6.x).
            self.auth0.clients.update(
                id=obo_client_id,
                grant_types=[
                    "client_credentials",
                    "urn:ietf:params:oauth:grant-type:token-exchange",
                ],
            )
            print("   ✅ Token-exchange grant type enabled on OBO client")
        except Exception as e:
            print(f"   ⚠️  Could not update grant types: {e}")
            print("       OBO token exchange may require tenant-level configuration.")
            print("       Contact Auth0 support if 'token-exchange' is not available.")

        # Create or update User-Delegated Client Grant.
        # This grant authorizes the OBO client to exchange tokens on behalf of users
        # for the downstream API.
        try:
            # The Management API v2 may not directly expose User-Delegated Client Grants.
            # They are typically configured via the Auth0 Dashboard or specialized APIs.
            # For now, we document the manual step.
            print("\n   📝 Manual Step Required for OBO:")
            print("      1. Go to Auth0 Dashboard -> Applications -> APIs")
            print(f"      2. Select '{AUTH0_API_NAME}' ({RESOURCE_SERVER_AUDIENCE})")
            print("      3. Go to 'Machine to Machine Applications' tab")
            print(f"      4. Authorize '{AUTH0_OBO_CLIENT_NAME}' with all scopes")
            print("      5. Go to 'Permissions' tab and enable 'Allow User-Delegated Access'")
            print("      6. Under User-Delegated Client Grants, add:")
            print(f"         - Source Client: {AUTH0_USER_APP_NAME}")
            print(f"         - Delegated Client: {AUTH0_OBO_CLIENT_NAME}")
            print("         - Scopes: All scopes")
        except Exception as e:
            print(f"   ⚠️  Error configuring OBO: {e}")

    def create_roles(self) -> dict[str, str]:
        """
        Create the three roles matching the demo's archetypes.
        Returns dict mapping role name to role ID.
        """
        result = {}
        self.reused_roles = set()

        for role_config in ROLES:
            role_name = role_config["name"]
            existing = self.find_existing_role(role_name)
            if existing:
                print(f"ℹ️  Role already exists: {role_name} ({existing['id']})")
                result[role_name] = existing["id"]
                self.reused_roles.add(role_name)
                continue

            try:
                # auth0-python v6.x uses keyword arguments
                response = self.auth0.roles.create(
                    name=role_name,
                    description=role_config["description"],
                )
                role = response if isinstance(response, dict) else response.__dict__
                result[role_name] = role["id"]
                print(f"✅ Role created: {role_name} ({role['id']})")
            except Exception as e:
                print(f"⚠️  Error creating role {role_name}: {e}")
                raise

        return result

    def create_test_users(self, role_ids: dict[str, str]) -> list[dict]:
        """
        Create the 5 test users and assign them to roles.
        Returns list of user dicts (email, sub, role).
        """
        results = []

        for user_config in TEST_USERS:
            email = user_config["email"]
            existing = self.find_existing_user(email)

            if existing:
                print(f"ℹ️  Test user already exists: {email} (sub: {existing['user_id']})")
                self._assign_user_to_role(existing["user_id"], role_ids[user_config["role"]], user_config["role"])
                results.append({
                    "email": email,
                    "sub": existing["user_id"],
                    "role": user_config["role"],
                    "status": "reused",
                })
                continue

            try:
                # auth0-python v6.x uses keyword arguments
                response = self.auth0.users.create(
                    email=email,
                    name=user_config["name"],
                    password="TempPass123!",
                    connection="Username-Password-Authentication",
                    email_verified=True,
                )
                user = response if isinstance(response, dict) else response.__dict__
                print(f"✅ Test user created: {email} (sub: {user['user_id']})")
                self._assign_user_to_role(user["user_id"], role_ids[user_config["role"]], user_config["role"])
                results.append({
                    "email": email,
                    "sub": user["user_id"],
                    "role": user_config["role"],
                    "status": "created",
                })
            except Exception as e:
                print(f"⚠️  Error creating user {email}: {e}")

        # Seed per-user subject key for OpenSearch notes RLS.
        print("\n🔑 Seeding auth0-user-<label>-sub keys for notes RLS...")
        for u in results:
            # Auth0's `sub` claim is the user_id (e.g., auth0|xxx).
            sub_value = u["sub"]
            label = u["email"].split("@")[0]
            param_name = f"/app/lakehouse-agent/auth0-user-{label}-sub"
            self.ssm.put_parameter(
                Name=param_name,
                Value=sub_value,
                Description=f"Auth0 user_id (sub) for test user {u['email']} — notes RLS owner_user_sub",
                Type="String",
                Overwrite=True,
            )
            print(f"✅ Stored parameter: {param_name} = {sub_value}")

        return results

    def _assign_user_to_role(self, user_id: str, role_id: str, role_name: str):
        """Assign a user to a role. Idempotent."""
        try:
            # auth0-python v6.x uses users.roles.assign(id, roles=[...])
            self.auth0.users.roles.assign(user_id, roles=[role_id])
            print(f"   ✅ Assigned to role: {role_name}")
        except Exception as e:
            if "already" in str(e).lower():
                print(f"   ℹ️  Already in role: {role_name}")
            else:
                print(f"   ⚠️  Error assigning to role {role_name}: {e}")

    def assign_permissions_to_roles(self, role_ids: dict[str, str]):
        """
        Assign permissions to roles based on the ROLE_PERMISSIONS matrix.
        
        With RBAC enabled on the API, Auth0 only includes permissions in the access token
        that are explicitly assigned to the user's role. This function automates that assignment.
        
        Args:
            role_ids: Dictionary mapping role name to Auth0 role ID
        """
        print("\n🔐 Assigning permissions to roles (RBAC)...")
        
        for role_name, permissions in ROLE_PERMISSIONS.items():
            role_id = role_ids.get(role_name)
            if not role_id:
                print(f"   ⚠️  Role ID not found for {role_name}, skipping permissions")
                continue
            
            # Build the permissions payload for the Auth0 API
            # Each permission needs the resource_server_identifier (API audience) and permission_name
            permissions_payload = [
                {
                    "resource_server_identifier": RESOURCE_SERVER_AUDIENCE,
                    "permission_name": perm,
                }
                for perm in permissions
            ]
            
            try:
                # Check existing permissions on the role
                existing_response = self.auth0.roles.permissions.list(role_id)
                existing_perms = existing_response.permissions if hasattr(existing_response, 'permissions') else existing_response
                existing_perm_names = set()
                for p in existing_perms:
                    p_dict = p if isinstance(p, dict) else p.__dict__
                    if p_dict.get("resource_server_identifier") == RESOURCE_SERVER_AUDIENCE:
                        existing_perm_names.add(p_dict.get("permission_name"))
                
                # Only add permissions that are missing
                missing_perms = [
                    p for p in permissions_payload 
                    if p["permission_name"] not in existing_perm_names
                ]
                
                if not missing_perms:
                    print(f"   ℹ️  Role '{role_name}' already has all permissions: {permissions}")
                    continue
                
                # auth0-python v6.x: roles.permissions.add(role_id, permissions=[...])
                self.auth0.roles.permissions.add(role_id, permissions=missing_perms)
                added_names = [p["permission_name"] for p in missing_perms]
                print(f"   ✅ Role '{role_name}' assigned permissions: {added_names}")
                
            except Exception as e:
                print(f"   ⚠️  Error assigning permissions to role {role_name}: {e}")
                print(f"       Permissions to assign: {permissions}")
                # Continue with other roles even if one fails

    def authorize_user_app_for_api(self, app_client_id: str):
        """
        Authorize the user-login app for the API.

        This allows the app to request tokens with the API's audience and scopes.
        Auth0 requires a client grant even for authorization_code flows to allow
        the app to request API-specific scopes like claims.query.
        """
        print(f"\n🔐 Authorizing user app {app_client_id} for API...")
        self._authorize_client_for_api(app_client_id)

    def configure_roles_in_tokens(self):
        """
        Configure Auth0 to include roles in access tokens.

        Auth0 requires an Action or Rule to add roles/groups to tokens.
        We'll document the manual step to add an Action.
        """
        print("\n📝 Manual Step Required for Role Claims:")
        print("   Auth0 requires an Action to include roles in BOTH access tokens AND ID tokens.")
        print("   The REQUEST interceptor reads the 'groups' claim from the ID token.")
        print("")
        print("   1. Go to Auth0 Dashboard -> Actions -> Library -> Build Custom")
        print("   2. Name: 'Add Roles to Tokens'")
        print("   3. Trigger: 'Login / Post Login'")
        print("   4. Add this code:")
        print("""
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://lakehouse-agent/';
  if (event.authorization) {
    const roles = event.authorization.roles || [];
    // Add to ACCESS token (for API calls)
    api.accessToken.setCustomClaim(namespace + 'roles', roles);
    api.accessToken.setCustomClaim('groups', roles);
    // Add to ID token (for REQUEST interceptor - REQUIRED)
    api.idToken.setCustomClaim(namespace + 'roles', roles);
    api.idToken.setCustomClaim('groups', roles);
  }
};
""")
        print("   5. Deploy the Action and drag it into the Login flow")
        print("   6. Click 'Apply' to activate the flow")
        print("")
        print("   ⚠️  Without the 'groups' claim in the ID token, the REQUEST interceptor")
        print("      cannot match users to roles in DynamoDB, and requests will fail.")

    # ─────────────────────────────────────────────────────────────────
    # Top-level flow
    # ─────────────────────────────────────────────────────────────────

    def setup(self) -> dict:
        """Run the complete Auth0 setup flow."""
        # 1. Create / reuse the API (resource server).
        api_result = self.create_api()

        # 2. Create / reuse the Regular Web Application for user login.
        app_result = self.create_user_app()

        # 3. Create / reuse the Custom API Client for OBO token exchange.
        obo_result = self.create_obo_exchange_client()

        # 4. Enable OBO token exchange on the Custom API Client.
        self.enable_obo_token_exchange(obo_result["obo_client_id"])

        # 5. Authorize user app for the API.
        self.authorize_user_app_for_api(app_result["app_client_id"])

        # 6. Create the three roles.
        role_ids = self.create_roles()

        # 7. Assign permissions to roles (RBAC).
        # This is critical: with RBAC enabled, tokens only include permissions assigned to roles.
        self.assign_permissions_to_roles(role_ids)

        # 8. Create the five test users and assign roles.
        users = self.create_test_users(role_ids)

        # 9. Configure role claims in tokens.
        self.configure_roles_in_tokens()

        # 10. Aggregate config and persist to SSM.
        discovery_url = f"https://{self.domain}/.well-known/openid-configuration"
        config = {
            "domain": self.domain,
            "app_client_id": app_result["app_client_id"],
            "app_client_secret": app_result.get("app_client_secret"),
            "obo_client_id": obo_result["obo_client_id"],
            "obo_client_secret": obo_result.get("obo_client_secret"),
            "audience": RESOURCE_SERVER_AUDIENCE,
            "discovery_url": discovery_url,
            "policyholders_role_id": role_ids["policyholders"],
            "adjusters_role_id": role_ids["adjusters"],
            "administrators_role_id": role_ids["administrators"],
            "users": users,
            "api_status": api_result["status"],
            "app_status": app_result["status"],
            "obo_app_status": obo_result["status"],
            "reused_roles": sorted(getattr(self, "reused_roles", set())),
        }
        self.store_parameters_in_ssm(config)

        return config


def main():
    # Load .env before anything reads os.environ.
    load_env_file()

    setup = Auth0Setup()
    config = setup.setup()

    # Don't echo secrets to stdout.
    safe = {k: v for k, v in config.items() if "secret" not in k.lower()}
    print(f"\n📝 Configuration (secrets redacted):\n{json.dumps(safe, indent=2, default=str)}")

    print("\n💾 SSM Parameters Stored:")
    print("   • /app/lakehouse-agent/auth0-domain")
    print("   • /app/lakehouse-agent/auth0-client-id")
    print("   • /app/lakehouse-agent/auth0-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/auth0-obo-client-id")
    print("   • /app/lakehouse-agent/auth0-obo-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/auth0-audience")
    print("   • /app/lakehouse-agent/auth0-discovery-url")
    print("   • /app/lakehouse-agent/auth0-policyholders-role-id")
    print("   • /app/lakehouse-agent/auth0-adjusters-role-id")
    print("   • /app/lakehouse-agent/auth0-administrators-role-id")

    # Created-vs-reused summary.
    created_users = [u for u in config["users"] if u.get("status") == "created"]
    reused_users = [u for u in config["users"] if u.get("status") == "reused"]
    print(f"\n👥 Test Users: {len(created_users)} created, {len(reused_users)} reused")
    for u in config["users"]:
        status = u.get("status", "unknown")
        note = "  (password: TempPass123!)" if status == "created" else ""
        print(f"   • {u['email']} → {u['role']} [{status}] (sub: {u['sub']}){note}")

    reused_roles = set(config.get("reused_roles") or [])
    n_reused_roles = len(reused_roles)
    print(f"\n👥 Auth0 Roles: {3 - n_reused_roles} created, {n_reused_roles} reused")
    for name, key in (
        ("policyholders", "policyholders_role_id"),
        ("adjusters", "adjusters_role_id"),
        ("administrators", "administrators_role_id"),
    ):
        status = "reused" if name in reused_roles else "created"
        print(f"   • {name:<15} ({config[key]}) [{status}]")

    print("\n🔑 Auth0 Configuration:")
    print(f"   • User App Client ID: {config['app_client_id']} [{config.get('app_status', 'unknown')}]")
    print(f"   • OBO Exchange Client ID: {config['obo_client_id']} [{config.get('obo_app_status', 'unknown')}]")
    print(f"   • API: {AUTH0_API_NAME} [{config.get('api_status', 'unknown')}]")
    print(f"   • Audience: {config['audience']}")
    print(f"   • Discovery URL: {config['discovery_url']}")
    print("   • Scopes: claims.query, claims.submit, claims.update, claims.approve, opensearch.search")

    print("\n⚠️  IMPORTANT: Manual steps required for full OBO support:")
    print("   1. Configure User-Delegated Client Grant in Auth0 Dashboard")
    print("   2. Deploy the 'Add Roles to Tokens' Action")
    print("   See the printed instructions above for details.")


if __name__ == "__main__":
    main()
