#!/usr/bin/env python3
"""
Okta Setup for Health Lakehouse Data
Creates Okta OIDC application + custom authorization server + scopes + groups + test users
Writes configuration to SSM Parameter Store

Usage:
    python setup_okta.py

Prerequisites:
    - Okta tenant with admin permissions (free Okta Developer account at
      developer.okta.com/signup is sufficient)
    - Okta API token issued by an admin (Okta admin console → Security → API
      → Tokens). Stored in env as OKTA_API_TOKEN, then persisted to SSM as
      SecureString.
    - OKTA_ORG_URL set (e.g. dev-12345678.okta.com or your tenant's org URL)
    - AWS credentials configured (for SSM Parameter Store writes)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import boto3

# Okta management SDK (python-okta-sdk)
from okta.client import Client as OktaClient
from okta.errors.okta_api_error import OktaAPIError

# The sample root, so `utils` is importable when this script is run directly
# (the documented `python setup_okta.py` path has no notebook to set this up).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from utils.env_file import load_env_file

# Single source of truth for the resource-server audience (used as JWT 'aud'
# claim value). The original demo used 'lakehouse-api'; we keep the same
# logical identifier and prefix it with 'api://' per Okta convention so the
# audience is unambiguously a custom-resource-server URI.
RESOURCE_SERVER_AUDIENCE = "api://lakehouse-api"

# Names assigned to created Okta resources (idempotency keys).
OKTA_APP_NAME = "lakehouse-agent-app"
OKTA_AUTH_SERVER_NAME = "lakehouse-agent"

# Dedicated OBO token-exchange service app. Okta's RFC 8693 TOKEN_EXCHANGE
# flow requires the EXCHANGING client to be distinct from the subject-token
# issuer (the user-login OKTA_APP_NAME app), so the OBO leg gets its own
# API-Services (service) client. Empirically proven: a single app acting
# as both issuer and exchanger is rejected with
# `unsupported_token_exchange_flow`. (Okta-specific — Entra's
# JWT_AUTHORIZATION_GRANT is single-client.)
OKTA_EXCHANGE_APP_NAME = "lakehouse-obo-exchange-client"


class OktaSetup:
    def __init__(self):
        """Initialize Okta setup with org URL and API-token credentials."""
        # Get org URL and API token from env (loaded via .env or shell)
        self.org_url = os.environ.get("OKTA_ORG_URL")
        if not self.org_url:
            raise RuntimeError(
                "OKTA_ORG_URL not set. Add it to .env or export it before "
                "running this script. Example: dev-12345678.okta.com (no scheme)."
            )

        # Reject the admin-console hostname. It is the URL you were just looking at
        # to mint the API token, so it is an easy mistake, but the Okta management
        # API is not served there — every call fails in a way that reads like a
        # permissions problem rather than a wrong-host problem.
        bare_host = self.org_url.split("://", 1)[-1].strip("/")
        if "-admin." in bare_host:
            raise RuntimeError(
                f"OKTA_ORG_URL points at the Okta admin console ({bare_host}), which "
                "does not serve the management API. Drop the '-admin' segment: "
                f"OKTA_ORG_URL={bare_host.replace('-admin.', '.', 1)}"
            )
        if "/" in bare_host:
            raise RuntimeError(
                f"OKTA_ORG_URL must be a host with no path ({bare_host}). Use OKTA_ORG_URL={bare_host.split('/', 1)[0]}"
            )

        self.api_token = os.environ.get("OKTA_API_TOKEN")
        if not self.api_token:
            raise RuntimeError(
                "OKTA_API_TOKEN not set. Issue one in the Okta admin console "
                "under Security → API → Tokens, then add to .env."
            )

        # Normalize to a full URL with scheme (Okta SDK is happiest this way).
        if not self.org_url.startswith("http"):
            self.org_url_full = f"https://{self.org_url}"
        else:
            self.org_url_full = self.org_url
            # Strip scheme back out for the bare org-URL we persist to SSM.
            self.org_url = self.org_url.replace("https://", "").replace("http://", "")

        # Initialize Okta SDK client.
        self.okta = OktaClient(
            {
                "orgUrl": self.org_url_full,
                "token": self.api_token,
            }
        )

        # AWS clients for SSM persistence.
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.env_file = Path(__file__).parent.parent / ".env"

        print("Initialized Okta setup")
        print(f"   Org URL: {self.org_url}")
        print(f"   AWS region (for SSM): {self.region}")
        print(f"   Resource server audience: {RESOURCE_SERVER_AUDIENCE}")

    # ─────────────────────────────────────────────────────────────────
    # Discovery helpers — find existing resources by name (idempotency)
    # ─────────────────────────────────────────────────────────────────

    async def find_existing_app(self, app_label: str):
        """Find existing Okta application by label."""
        try:
            apps, _, _ = await self.okta.list_applications()
            for app in apps or []:
                if app.label == app_label:
                    print(f"ℹ️  Found existing Okta App: {app.id}")
                    return app
        except Exception as e:
            print(f"⚠️  Error searching for app: {e}")
        return None

    async def find_existing_auth_server(self, name: str):
        """Find existing Okta custom authorization server by name."""
        try:
            servers, _, _ = await self.okta.list_authorization_servers()
            for s in servers or []:
                if s.name == name:
                    print(f"ℹ️  Found existing Okta auth server: {s.id}")
                    return s
        except Exception as e:
            print(f"⚠️  Error searching for auth server: {e}")
        return None

    async def find_existing_group(self, group_name: str):
        """Find existing Okta group by profile.name."""
        try:
            groups, _, _ = await self.okta.list_groups(query_params={"q": group_name})
            for g in groups or []:
                if g.profile.name == group_name:
                    return g
        except Exception as e:
            print(f"⚠️  Error searching for group {group_name}: {e}")
        return None

    async def find_existing_user(self, login: str):
        """Find existing Okta user by login (email)."""
        try:
            user, _, _ = await self.okta.get_user(login)
            return user
        except OktaAPIError:
            return None
        except Exception as e:
            print(f"⚠️  Error searching for user {login}: {e}")
        return None

    # ─────────────────────────────────────────────────────────────────
    # SSM persistence (mirrors setup_cognito.store_parameters_in_ssm)
    # ─────────────────────────────────────────────────────────────────

    def store_parameters_in_ssm(self, config: dict):
        """
        Store Okta configuration in SSM Parameter Store under
        /app/lakehouse-agent/okta-* per design §8b.

        Args:
            config: Dictionary with org_url, app_client_id, app_client_secret,
                    obo_client_id, obo_client_secret, auth_server_id,
                    resource_server_audience, discovery_url, api_token,
                    group IDs.
        """
        print("\n💾 Storing configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/okta-org-url",
                "value": config["org_url"],
                "description": "Okta tenant org URL (e.g. dev-12345678.okta.com)",
            },
            {
                "name": "/app/lakehouse-agent/okta-auth-server-id",
                "value": config["auth_server_id"],
                "description": "Okta custom authorization server ID",
            },
            {
                "name": "/app/lakehouse-agent/okta-app-client-id",
                "value": config["app_client_id"],
                "description": "Okta OIDC application client ID",
            },
            {
                "name": "/app/lakehouse-agent/okta-obo-client-id",
                "value": config["obo_client_id"],
                "description": "Okta OBO token-exchange service app client ID "
                "(dedicated exchange client, distinct from the "
                "user-login app per Okta RFC 8693 two-client rule)",
            },
            {
                "name": "/app/lakehouse-agent/okta-resource-server-audience",
                "value": config["resource_server_audience"],
                "description": "Custom authorization server audience (JWT aud claim)",
            },
            {
                "name": "/app/lakehouse-agent/okta-discovery-url",
                "value": config["discovery_url"],
                "description": "Okta authorization server OpenID Connect discovery URL",
            },
            {
                "name": "/app/lakehouse-agent/okta-policyholders-group-id",
                "value": config["policyholders_group_id"],
                "description": "Okta group ID for policyholders",
            },
            {
                "name": "/app/lakehouse-agent/okta-adjusters-group-id",
                "value": config["adjusters_group_id"],
                "description": "Okta group ID for adjusters",
            },
            {
                "name": "/app/lakehouse-agent/okta-administrators-group-id",
                "value": config["administrators_group_id"],
                "description": "Okta group ID for administrators",
            },
        ]

        # Store SecureString secrets.
        secure_params = [
            (
                "/app/lakehouse-agent/okta-app-client-secret",
                config.get("app_client_secret"),
                "Okta OIDC application client secret (SecureString)",
            ),
            (
                "/app/lakehouse-agent/okta-obo-client-secret",
                config.get("obo_client_secret"),
                "Okta OBO token-exchange service app client secret (SecureString)",
            ),
            (
                "/app/lakehouse-agent/okta-api-token",
                config.get("api_token"),
                "Okta management API token (SecureString)",
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
    # Resource creation — app, auth server, scopes, groups, users
    # ─────────────────────────────────────────────────────────────────

    async def create_oidc_app(self, app_label: str) -> dict[str, str]:
        """
        Create or find the Okta OIDC application. Returns dict with
        app_id, app_client_id, app_client_secret.
        """
        existing = await self.find_existing_app(app_label)
        if existing:
            # Refetch to ensure we have credentials.
            print(f"ℹ️  Reusing existing OIDC app: {existing.id}")
            app = existing
            app_status = "reused"
        else:
            from okta.models import (
                OAuthGrantType,
                OAuthResponseType,
                OpenIdConnectApplication,
                OpenIdConnectApplicationConsentMethod,
                OpenIdConnectApplicationIssuerMode,
                OpenIdConnectApplicationSettings,
                OpenIdConnectApplicationSettingsClient,
                OpenIdConnectApplicationType,
            )

            # NOTE: Okta SDK 2.9.x model classes read config via camelCase keys
            # (mirroring the Okta REST API JSON envelope). Snake_case keys are
            # silently ignored — fields land as None and the API rejects with
            # "Missing app settings". Pin SDK to <3 (3.x has separate breaking
            # changes documented in requirements.txt).
            app_settings_client = OpenIdConnectApplicationSettingsClient(
                {
                    "clientUri": "",
                    "logoUri": "",
                    "redirectUris": ["http://localhost:8501/"],  # Streamlit default
                    "postLogoutRedirectUris": ["http://localhost:8501/"],
                    "responseTypes": [
                        OAuthResponseType("code"),
                    ],
                    "grantTypes": [
                        OAuthGrantType("authorization_code"),
                        OAuthGrantType("refresh_token"),
                        OAuthGrantType("client_credentials"),
                        # `password` grant is required by deployment/1-okta-setup/decode_token.py
                        # (ROPC inspection of test-user tokens). The custom auth server may
                        # also need an explicit password-grant access-policy rule — surfaces
                        # during the first ROPC mint if so.
                        OAuthGrantType("password"),
                        # NOTE: token-exchange (RFC 8693) is intentionally NOT on the
                        # user-login app. This app only ISSUES the subject token via
                        # authorization_code/PKCE (plus client_credentials/password for
                        # the M2M and ROPC-inspection paths); it never performs the OBO
                        # exchange. The exchange leg is carried by a DEDICATED service
                        # app (see create_exchange_app below) because Okta's
                        # TOKEN_EXCHANGE flow requires the exchanging client to DIFFER
                        # from the subject-token issuer (a single app acting as both
                        # issuer and exchanger is rejected with
                        # `unsupported_token_exchange_flow`).
                        #
                        # Source-vs-live drift (benign): the first fix was applied to
                        # THIS app live before the two-client root cause was understood,
                        # so the live user-login
                        # app still carries an unused token-exchange grant. It is
                        # harmless (never exercised) and left in place; source stays
                        # clean — the grant belongs on the exchange app only.
                    ],
                    "applicationType": OpenIdConnectApplicationType("web"),
                    # TRUSTED skips the per-user consent dialog (matches the original
                    # Cognito demo's no-consent behavior). Demo's v1 Streamlit UI does
                    # not yet surface a consent prompt; v2 may revisit per R15.1.b.
                    "consentMethod": OpenIdConnectApplicationConsentMethod("TRUSTED"),
                    # ORG_URL: the app uses the standard tenant URL; the *custom auth
                    # server* (created below) carries the unique `api://lakehouse-api`
                    # audience and issuer URL. CUSTOM_URL would require per-app DNS
                    # configuration that's outside this demo's scope.
                    "issuerMode": OpenIdConnectApplicationIssuerMode("ORG_URL"),
                }
            )
            app_settings = OpenIdConnectApplicationSettings(
                {
                    "oauthClient": app_settings_client,
                }
            )

            new_app = OpenIdConnectApplication(
                {
                    "label": app_label,
                    # signOnMode: OpenIdConnectApplication.__init__ hard-codes this
                    # to OPENID_CONNECT regardless; key is here for completeness.
                    "signOnMode": "OPENID_CONNECT",
                    "settings": app_settings,
                }
            )
            app, _, err = await self.okta.create_application(new_app)
            if err:
                raise RuntimeError(f"Failed to create OIDC app: {err}")
            print(f"✅ OIDC app created: {app.id}")
            app_status = "created"

        # Fetch credentials (Okta returns these via the create response, but
        # idempotent path needs an explicit fetch).
        client_id = app.credentials.oauth_client.client_id
        client_secret = app.credentials.oauth_client.client_secret

        # On the idempotent reuse path, list_applications() omits the
        # client_secret. Fall back to list_client_secrets_for_application
        # which returns the active secret value. (Okta only exposes the
        # secret on the create response and via this dedicated endpoint.)
        if not client_secret:
            secrets, _, err = await self.okta.list_client_secrets_for_application(app.id)
            if err:
                raise RuntimeError(f"Failed to list client secrets for app {app.id}: {err}")
            active = next(
                (s for s in (secrets or []) if getattr(s, "status", None) == "ACTIVE"),
                None,
            )
            if not active or not getattr(active, "client_secret", None):
                raise RuntimeError(
                    f"No ACTIVE client secret retrievable for app {app.id}. "
                    "Generate a new one in the Okta admin console under "
                    "Applications -> the app -> General -> Client Credentials."
                )
            client_secret = active.client_secret
            print("ℹ️  Recovered client_secret via list_client_secrets_for_application")

        return {
            "app_id": app.id,
            "app_client_id": client_id,
            "app_client_secret": client_secret,
            "status": app_status,
        }

    async def create_exchange_app(self, app_label: str) -> dict[str, str]:
        """
        Create or find the dedicated OBO token-exchange service application.

        This is a SECOND Okta app, distinct from the user-login app created by
        create_oidc_app. Okta's RFC 8693 TOKEN_EXCHANGE requires the exchanging
        client to differ from the subject-token issuer, so the OBO_Gateway's
        AgentCore credential provider authenticates as THIS service client when
        it performs the on-behalf-of exchange.

        Configuration (empirically proven):
          - application_type = 'service'  (API Services / M2M app)
          - grant_types = ['client_credentials',
                           'urn:ietf:params:oauth:grant-type:token-exchange']
          - token_endpoint_auth_method = 'client_secret_basic'

        Returns dict with app_id, exchange_client_id, exchange_client_secret.
        Idempotent: reuse-by-label, mirroring create_oidc_app.
        """
        existing = await self.find_existing_app(app_label)
        if existing:
            print(f"ℹ️  Reusing existing OBO exchange app: {existing.id}")
            app = existing
            app_status = "reused"
        else:
            from okta.models import (
                OAuthGrantType,
                OpenIdConnectApplication,
                OpenIdConnectApplicationConsentMethod,
                OpenIdConnectApplicationIssuerMode,
                OpenIdConnectApplicationSettings,
                OpenIdConnectApplicationSettingsClient,
                OpenIdConnectApplicationType,
            )

            # camelCase keys per the Okta SDK 2.9.x note in create_oidc_app.
            app_settings_client = OpenIdConnectApplicationSettingsClient(
                {
                    # A service (M2M) app has no interactive redirect; no redirectUris.
                    "responseTypes": [],
                    "grantTypes": [
                        OAuthGrantType("client_credentials"),
                        # The OBO leg. Required on BOTH this app's grant_types AND the
                        # auth-server access-policy rule (see
                        # create_auth_server_access_policy, where this app's client_id
                        # is added to the rule's client include-list). Missing either
                        # gate surfaces as `unauthorized_client` from /v1/token.
                        OAuthGrantType("urn:ietf:params:oauth:grant-type:token-exchange"),
                    ],
                    # 'service' = API Services / M2M app (no user interaction).
                    "applicationType": OpenIdConnectApplicationType("service"),
                    "consentMethod": OpenIdConnectApplicationConsentMethod("TRUSTED"),
                    "issuerMode": OpenIdConnectApplicationIssuerMode("ORG_URL"),
                }
            )
            app_settings = OpenIdConnectApplicationSettings(
                {
                    "oauthClient": app_settings_client,
                }
            )

            # client_secret_basic: the AgentCore credential provider sends the
            # client_secret via HTTP Basic auth on the token request (matches
            # 03_create_oauth_provider.py
            # clientAuthenticationMethod='CLIENT_SECRET_BASIC'). The
            # token_endpoint_auth_method lives on credentials.oauthClient (NOT on
            # the settings-client — Okta SDK shape).
            from okta.models import (
                ApplicationCredentialsOAuthClient,
                OAuthApplicationCredentials,
                OAuthEndpointAuthenticationMethod,
            )

            app_credentials = OAuthApplicationCredentials(
                {
                    "oauthClient": ApplicationCredentialsOAuthClient(
                        {
                            "tokenEndpointAuthMethod": OAuthEndpointAuthenticationMethod("client_secret_basic"),
                            "autoKeyRotation": True,
                        }
                    ),
                }
            )

            new_app = OpenIdConnectApplication(
                {
                    "label": app_label,
                    "signOnMode": "OPENID_CONNECT",
                    "settings": app_settings,
                    "credentials": app_credentials,
                }
            )
            app, _, err = await self.okta.create_application(new_app)
            if err:
                raise RuntimeError(f"Failed to create OBO exchange app: {err}")
            print(f"✅ OBO exchange app created: {app.id}")
            app_status = "created"

        client_id = app.credentials.oauth_client.client_id
        client_secret = app.credentials.oauth_client.client_secret

        # Idempotent reuse path: list_applications() omits client_secret —
        # recover it via the dedicated endpoint (same as create_oidc_app).
        if not client_secret:
            secrets, _, err = await self.okta.list_client_secrets_for_application(app.id)
            if err:
                raise RuntimeError(f"Failed to list client secrets for exchange app {app.id}: {err}")
            active = next(
                (s for s in (secrets or []) if getattr(s, "status", None) == "ACTIVE"),
                None,
            )
            if not active or not getattr(active, "client_secret", None):
                raise RuntimeError(
                    f"No ACTIVE client secret retrievable for exchange app {app.id}. "
                    "Generate a new one in the Okta admin console under "
                    "Applications -> the app -> General -> Client Credentials."
                )
            client_secret = active.client_secret
            print("ℹ️  Recovered exchange client_secret via list_client_secrets_for_application")

        return {
            "app_id": app.id,
            "exchange_client_id": client_id,
            "exchange_client_secret": client_secret,
            "status": app_status,
        }

    async def create_auth_server(self) -> dict[str, str]:
        """
        Create or find the custom authorization server with audience
        api://lakehouse-api. Returns dict with auth_server_id and the
        discovery URL.
        """
        existing = await self.find_existing_auth_server(OKTA_AUTH_SERVER_NAME)
        if existing:
            print(f"ℹ️  Reusing existing auth server: {existing.id}")
            server = existing
            server_status = "reused"
        else:
            from okta.models import AuthorizationServer

            new_server = AuthorizationServer(
                {
                    "name": OKTA_AUTH_SERVER_NAME,
                    "description": "Custom authorization server for lakehouse-agent",
                    "audiences": [RESOURCE_SERVER_AUDIENCE],
                }
            )
            server, _, err = await self.okta.create_authorization_server(new_server)
            if err:
                raise RuntimeError(f"Failed to create auth server: {err}")
            print(f"✅ Auth server created: {server.id}")
            server_status = "created"

        # Create scopes on the auth server (idempotent: skip if already present).
        scope_definitions = [
            ("claims.query", "Query claims"),
            ("claims.submit", "Submit claims"),
            ("claims.update", "Update claims"),
            ("claims.approve", "Approve/deny claims"),
            ("opensearch.search", "Search free-text claim notes via OpenSearch"),
        ]
        existing_scopes, _, _ = await self.okta.list_o_auth_2_scopes(server.id)
        existing_names = {s.name for s in (existing_scopes or [])}

        from okta.models import OAuth2Scope

        for name, description in scope_definitions:
            if name in existing_names:
                print(f"ℹ️  Scope already exists: {name}")
                continue
            scope = OAuth2Scope(
                {
                    "name": name,
                    "description": description,
                    "consent": "IMPLICIT",
                    "metadataPublish": "ALL_CLIENTS",
                }
            )
            _, _, err = await self.okta.create_o_auth_2_scope(server.id, scope)
            if err:
                print(f"⚠️  Error creating scope {name}: {err}")
            else:
                print(f"✅ Scope created: {name}")

        # Create a `groups` claim on the auth server (per design §7a — the
        # interceptor's claim-extraction priority list expects `groups`).
        from okta.models import OAuth2Claim

        existing_claims, _, _ = await self.okta.list_o_auth_2_claims(server.id)
        if not any(c.name == "groups" for c in (existing_claims or [])):
            groups_claim = OAuth2Claim(
                {
                    "name": "groups",
                    "status": "ACTIVE",
                    "claimType": "RESOURCE",
                    "valueType": "GROUPS",
                    "value": ".*",
                    "groupFilterType": "REGEX",
                    "alwaysIncludeInToken": True,
                }
            )
            _, _, err = await self.okta.create_o_auth_2_claim(server.id, groups_claim)
            if err:
                print(f"⚠️  Error creating groups claim: {err}")
            else:
                print("✅ Created `groups` claim on auth server")
        else:
            print("ℹ️  `groups` claim already exists on auth server")

        discovery_url = f"{self.org_url_full}/oauth2/{server.id}/.well-known/openid-configuration"

        return {
            "auth_server_id": server.id,
            "discovery_url": discovery_url,
            "status": server_status,
        }

    async def create_auth_server_access_policy(self, server_id: str, app_client_id: str, exchange_client_id: str):
        """
        Create the auth server's access policy + permissive rule.

        Okta does not auto-create an access policy when a custom authorization
        server is created. Without one, every token request returns
        `access_denied: Policy evaluation failed`. This is a SEMANTIC gap
        between API success (server created) and operational readiness (server
        usable for token issuance) — captured in design.md §6c.

        v1 ships a single permissive rule covering all 5 configured scopes for
        all assigned client applications and the standard grant types
        (authorization_code, client_credentials, password, refresh_token,
        token-exchange). Production deployments would have more granular
        per-client-per-scope rules. (v2 enhancement note.)

        Both Okta apps are added to the policy's client include-list:
          - app_client_id      : the user-login app (subject-token issuer)
          - exchange_client_id : the dedicated OBO exchange service app
        The OBO TOKEN_EXCHANGE flow evaluates the policy against the EXCHANGING
        client (the exchange app), so it must appear here or the exchange is
        rejected with `unauthorized_client` (enforced at BOTH this rule's
        client-list AND the exchange app's own grant_types allowlist).
        """
        from okta.models import AuthorizationServerPolicy, AuthorizationServerPolicyRule

        policy_name = "lakehouse-agent-default-policy"
        rule_name = "lakehouse-agent-default-rule"

        # Both apps must be covered by the policy (user-login issues the subject
        # token; exchange app performs the OBO leg).
        policy_client_ids = [app_client_id, exchange_client_id]

        # ─── Policy ────────────────────────────────────────────────
        existing_policies, _, _ = await self.okta.list_authorization_server_policies(server_id)
        policy = next((p for p in (existing_policies or []) if p.name == policy_name), None)

        if policy:
            print(f"ℹ️  Auth-server access policy already exists: {policy_name}")
            # Idempotent reconcile: ensure BOTH apps are in the client
            # include-list even if the policy predates the exchange app
            # (e.g. a setup run from before the two-client OBO fix).
            try:
                current = (
                    policy.conditions.clients.include if policy.conditions and policy.conditions.clients else None
                ) or []
            except AttributeError:
                current = []
            missing = [cid for cid in policy_client_ids if cid not in current]
            if missing:
                merged = list(current) + missing
                # Build a fresh model for the PUT rather than mutating the
                # fetched one (avoids mixed model/dict serialization issues in
                # the SDK's as_dict()).
                updated_policy = AuthorizationServerPolicy(
                    {
                        "type": "OAUTH_AUTHORIZATION_POLICY",
                        "name": policy_name,
                        "description": "Default policy for lakehouse-agent demo",
                        "priority": 1,
                        "status": "ACTIVE",
                        "conditions": {"clients": {"include": merged}},
                    }
                )
                _, _, err = await self.okta.update_authorization_server_policy(server_id, policy.id, updated_policy)
                if err:
                    print(f"⚠️  Could not reconcile policy client-list: {err}")
                else:
                    print(f"   ✅ Added to policy client-list: {missing}")
            else:
                print("   ℹ️  Policy client-list already includes both apps")
        else:
            new_policy = AuthorizationServerPolicy(
                {
                    "type": "OAUTH_AUTHORIZATION_POLICY",
                    "name": policy_name,
                    "description": "Default policy for lakehouse-agent demo",
                    "priority": 1,
                    "status": "ACTIVE",
                    # Apply this policy to BOTH the user-login app and the OBO
                    # exchange app by client id.
                    "conditions": {
                        "clients": {"include": policy_client_ids},
                    },
                }
            )
            policy, _, err = await self.okta.create_authorization_server_policy(server_id, new_policy)
            if err:
                raise RuntimeError(f"Failed to create auth-server policy: {err}")
            print(f"✅ Auth-server access policy created: {policy_name}")

        # ─── Rule ──────────────────────────────────────────────────
        existing_rules, _, _ = await self.okta.list_authorization_server_policy_rules(server_id, policy.id)
        rule = next((r for r in (existing_rules or []) if r.name == rule_name), None)

        if rule:
            print(f"ℹ️  Auth-server policy rule already exists: {rule_name}")
            return

        new_rule = AuthorizationServerPolicyRule(
            {
                "type": "RESOURCE_ACCESS",
                "name": rule_name,
                "priority": 1,
                "status": "ACTIVE",
                # TUTORIAL SIMPLIFICATION: this single rule grants the EVERYONE
                # built-in group access to ALL scopes (`*`) under all the listed
                # grant types. It keeps the demo's auth-server setup to one rule so
                # readers can focus on the OBO mechanics rather than Okta policy
                # authoring. A production deployment would tighten this to
                # per-client / per-scope / per-group least-privilege rules — flagged
                # as a least-privilege tightening candidate.
                "conditions": {
                    # Allow EVERYONE built-in group → covers M2M (no user) +
                    # all assigned users via client-credentials, ROPC, auth-code.
                    "people": {
                        "users": {"include": [], "exclude": []},
                        "groups": {"include": ["EVERYONE"], "exclude": []},
                    },
                    "grantTypes": {
                        "include": [
                            "authorization_code",
                            "client_credentials",
                            "password",
                            # The auth-server-side enabling for the OBO_Gateway path.
                            "urn:ietf:params:oauth:grant-type:token-exchange",
                            # NOTE: `refresh_token` is NOT a valid grant type for an
                            # auth-server policy rule (it controls only the *initial*
                            # token-mint grant types; refresh is granted automatically
                            # for grants that include `offline_access` scope at the
                            # OIDC-app level).
                        ],
                    },
                    # `*` = match all scopes configured on this auth server
                    # (claims.{query,submit,update,approve}, opensearch.search).
                    "scopes": {"include": ["*"]},
                },
                "actions": {
                    "token": {
                        "accessTokenLifetimeMinutes": 60,
                        "refreshTokenLifetimeMinutes": 0,
                        "refreshTokenWindowMinutes": 10080,
                    },
                },
            }
        )
        _, _, err = await self.okta.create_authorization_server_policy_rule(server_id, policy.id, new_rule)
        if err:
            raise RuntimeError(f"Failed to create auth-server policy rule: {err}")
        print(f"✅ Auth-server policy rule created: {rule_name}")

    async def create_groups(self) -> dict[str, str]:
        """
        Create three groups matching the original demo's archetypes.
        Returns dict mapping group name to group ID.
        """
        from okta.models import Group, GroupProfile

        groups_config = [
            ("policyholders", "Policy holders group"),
            ("adjusters", "Claims adjusters group"),
            ("administrators", "Administrators group"),
        ]
        result = {}
        # Track reuse so the summary can report created-vs-reused honestly.
        self.reused_groups = set()
        for group_name, description in groups_config:
            existing = await self.find_existing_group(group_name)
            if existing:
                print(f"ℹ️  Group already exists: {group_name} ({existing.id})")
                result[group_name] = existing.id
                self.reused_groups.add(group_name)
                continue

            group = Group(
                {
                    "profile": GroupProfile(
                        {
                            "name": group_name,
                            "description": description,
                        }
                    ),
                }
            )
            try:
                created, _, err = await self.okta.create_group(group)
                if err:
                    raise RuntimeError(f"Failed to create group {group_name}: {err}")
                result[group_name] = created.id
                print(f"✅ Group created: {group_name} ({created.id})")
            except Exception as e:
                print(f"⚠️  Error creating group {group_name}: {e}")
                raise

        return result

    async def create_test_users(self, group_ids: dict[str, str]) -> list[dict]:
        """
        Create the 5 test users matching the original demo's user list.
        Each user is created with a temporary password and assigned to its
        archetype group. Returns list of user dicts (login, sub, group_name).
        """
        from okta.models import PasswordCredential, User, UserCredentials, UserProfile

        # Test users — same list as the original demo. Okta's free Developer
        # accounts allow @example.com email addresses for test users.
        test_users = [
            {"login": "policyholder001@example.com", "first": "Policyholder", "last": "001", "group": "policyholders"},
            {"login": "policyholder002@example.com", "first": "Policyholder", "last": "002", "group": "policyholders"},
            {"login": "adjuster001@example.com", "first": "Adjuster", "last": "001", "group": "adjusters"},
            {"login": "adjuster002@example.com", "first": "Adjuster", "last": "002", "group": "adjusters"},
            {"login": "admin@example.com", "first": "Admin", "last": "User", "group": "administrators"},
        ]

        results = []
        for u in test_users:
            existing = await self.find_existing_user(u["login"])
            if existing:
                print(f"ℹ️  Test user already exists: {u['login']} (sub: {existing.id})")
                await self._add_user_to_group(existing.id, group_ids[u["group"]], u["group"])
                results.append({"login": u["login"], "sub": existing.id, "group": u["group"], "status": "reused"})
                continue

            user_profile = UserProfile(
                {
                    "firstName": u["first"],
                    "lastName": u["last"],
                    "email": u["login"],
                    "login": u["login"],
                }
            )
            password = PasswordCredential({"value": "TempPass123!"})
            user_credentials = UserCredentials({"password": password})
            new_user = User(
                {
                    "profile": user_profile,
                    "credentials": user_credentials,
                }
            )
            try:
                created, _, err = await self.okta.create_user(new_user, query_params={"activate": "true"})
                if err:
                    raise RuntimeError(f"Failed to create user {u['login']}: {err}")
                print(f"✅ Test user created: {u['login']} (sub: {created.id})")
                await self._add_user_to_group(created.id, group_ids[u["group"]], u["group"])
                results.append({"login": u["login"], "sub": created.id, "group": u["group"], "status": "created"})
            except Exception as e:
                print(f"⚠️  Error creating user {u['login']}: {e}")

        # Seed the per-user subject key for the OpenSearch notes RLS
        # (FINDING-1: load_sample_opensearch_data.py in 05b consumes
        # okta-user-<label>-sub; setup_okta is the PRODUCER, mirroring the
        # Cognito seed_cognito_user_subs.py convention). CRITICAL: the Okta
        # access-token `sub` claim is the user's EMAIL (login), NOT created.id
        # (the 00u… Okta user id) — seeding the id would make owner_user_sub
        # match nothing at query time (vacuous-pass; fork Finding 16). The
        # created.id capture above (results["sub"]) is left as-is for the
        # summary print and is intentionally NOT reused here.
        print("\n🔑 Seeding okta-user-<label>-sub keys for notes RLS...")
        for u in results:
            sub_value = u["login"]  # email == access-token `sub` claim
            # Fail-fast guard against a future regression to created.id.
            assert "@" in sub_value, (
                f"okta-user sub must be the email (access-token `sub` claim), got {sub_value!r}; "
                "do NOT seed created.id (the 00u… Okta user id)."
            )
            label = sub_value.split("@")[0]
            param_name = f"/app/lakehouse-agent/okta-user-{label}-sub"
            self.ssm.put_parameter(
                Name=param_name,
                Value=sub_value,
                Description=f"Okta access-token `sub` (email) for test user {u['login']} — notes RLS owner_user_sub",
                Type="String",
                Overwrite=True,
            )
            print(f"✅ Stored parameter: {param_name} = {sub_value}")

        return results

    async def _add_user_to_group(self, user_id: str, group_id: str, group_name: str):
        """Add a user to a group. Idempotent on already-member."""
        try:
            _, err = await self.okta.add_user_to_group(group_id, user_id)
            if err and "already" not in str(err).lower():
                print(f"   ⚠️  Error adding to group {group_name}: {err}")
            else:
                print(f"   ✅ Added to group: {group_name}")
        except Exception as e:
            if "already" in str(e).lower():
                print(f"   ℹ️  Already in group: {group_name}")
            else:
                print(f"   ⚠️  Error adding to group {group_name}: {e}")

    async def assign_app_to_groups(self, app_id: str, group_ids: dict[str, str]):
        """Assign all three groups to the OIDC application so users in those
        groups can authenticate against it."""
        from okta.models import ApplicationGroupAssignment

        for group_name, group_id in group_ids.items():
            try:
                assignment = ApplicationGroupAssignment({})
                _, _, err = await self.okta.create_application_group_assignment(app_id, group_id, assignment)
                if err and "already" not in str(err).lower():
                    print(f"   ⚠️  Error assigning group {group_name} to app: {err}")
                else:
                    print(f"   ✅ Group {group_name} assigned to app")
            except Exception as e:
                if "already" in str(e).lower():
                    print(f"   ℹ️  Group {group_name} already assigned to app")
                else:
                    print(f"   ⚠️  Error assigning group {group_name}: {e}")

    # ─────────────────────────────────────────────────────────────────
    # Top-level flow (mirrors setup_cognito.setup)
    # ─────────────────────────────────────────────────────────────────

    async def setup(self) -> dict:
        """Run the complete Okta setup flow."""
        # 1. Create / reuse the OIDC application.
        app_result = await self.create_oidc_app(OKTA_APP_NAME)

        # 1b. Create / reuse the dedicated OBO token-exchange service app. Okta
        # requires the exchanging client to differ from the subject-token
        # issuer, so the OBO leg gets its own
        # service client distinct from the user-login app in step 1.
        exchange_result = await self.create_exchange_app(OKTA_EXCHANGE_APP_NAME)

        # 2. Create / reuse the custom authorization server with audience + scopes.
        auth_server = await self.create_auth_server()

        # 2b. Create the auth server's access policy + permissive rule. Okta does
        # NOT auto-create these and without them every token request returns
        # access_denied. Must run after steps 1+1b so we have BOTH the
        # user-login app's and the exchange app's client_ids for the rule's
        # client include-list.
        await self.create_auth_server_access_policy(
            auth_server["auth_server_id"],
            app_result["app_client_id"],
            exchange_result["exchange_client_id"],
        )

        # 3. Create the three security groups.
        group_ids = await self.create_groups()

        # 4. Assign groups to the OIDC app so users can sign in.
        await self.assign_app_to_groups(app_result["app_id"], group_ids)

        # 5. Create the five test users and assign group memberships.
        users = await self.create_test_users(group_ids)

        # 6. Aggregate config and persist to SSM.
        config = {
            "org_url": self.org_url,
            "app_client_id": app_result["app_client_id"],
            "app_client_secret": app_result["app_client_secret"],
            "obo_client_id": exchange_result["exchange_client_id"],
            "obo_client_secret": exchange_result["exchange_client_secret"],
            "auth_server_id": auth_server["auth_server_id"],
            "resource_server_audience": RESOURCE_SERVER_AUDIENCE,
            "discovery_url": auth_server["discovery_url"],
            "policyholders_group_id": group_ids["policyholders"],
            "adjusters_group_id": group_ids["adjusters"],
            "administrators_group_id": group_ids["administrators"],
            "api_token": self.api_token,
            "users": users,
            # Created-vs-reused, so the summary below reports what actually
            # happened rather than asserting creation unconditionally. setup_okta
            # is idempotent, so a re-run legitimately reuses most objects.
            "app_status": app_result["status"],
            "obo_app_status": exchange_result["status"],
            "auth_server_status": auth_server["status"],
            "reused_groups": sorted(getattr(self, "reused_groups", set())),
        }
        self.store_parameters_in_ssm(config)

        return config


def main():
    # Load .env before anything reads os.environ. The notebook path gets this for
    # free via init_aws(); the documented `python setup_okta.py` path did not, so
    # OKTA_ORG_URL / OKTA_API_TOKEN had to be exported by hand. Already-exported
    # variables still win.
    load_env_file()

    setup = OktaSetup()
    config = asyncio.run(setup.setup())

    # Don't echo client secret or API token to stdout (already in SSM as SecureString).
    safe = {k: v for k, v in config.items() if "secret" not in k.lower() and "token" not in k.lower()}
    print(f"\n📝 Configuration (secrets redacted):\n{json.dumps(safe, indent=2)}")

    print("\n💾 SSM Parameters Stored:")
    print("   • /app/lakehouse-agent/okta-org-url")
    print("   • /app/lakehouse-agent/okta-auth-server-id")
    print("   • /app/lakehouse-agent/okta-app-client-id")
    print("   • /app/lakehouse-agent/okta-app-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/okta-obo-client-id")
    print("   • /app/lakehouse-agent/okta-obo-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/okta-api-token (SecureString)")
    print("   • /app/lakehouse-agent/okta-resource-server-audience")
    print("   • /app/lakehouse-agent/okta-discovery-url")
    print("   • /app/lakehouse-agent/okta-policyholders-group-id")
    print("   • /app/lakehouse-agent/okta-adjusters-group-id")
    print("   • /app/lakehouse-agent/okta-administrators-group-id")

    # Created-vs-reused, reported per object. This script is idempotent, so a
    # re-run legitimately reuses most objects — announcing everything as "created"
    # turns the summary into misleading evidence (it reads as proof that a fresh
    # object was made when nothing was touched). Only newly-created users get the
    # temporary-password note, because a reused user's password is whatever it
    # already was.
    created_users = [u for u in config["users"] if u.get("status") == "created"]
    reused_users = [u for u in config["users"] if u.get("status") == "reused"]
    print(f"\n👥 Test Users: {len(created_users)} created, {len(reused_users)} reused")
    for u in config["users"]:
        status = u.get("status", "unknown")
        note = "  (password: TempPass123!, must change on first login)" if status == "created" else ""
        print(f"   • {u['login']} → {u['group']} [{status}] (sub: {u['sub']}){note}")
    if reused_users:
        print("     Reused users kept their existing credentials and enrolled MFA factors.")

    reused_groups = set(config.get("reused_groups") or [])
    n_reused_groups = len(reused_groups)
    print(f"\n👥 Okta Groups: {3 - n_reused_groups} created, {n_reused_groups} reused")
    for name, key in (
        ("policyholders", "policyholders_group_id"),
        ("adjusters", "adjusters_group_id"),
        ("administrators", "administrators_group_id"),
    ):
        status = "reused" if name in reused_groups else "created"
        print(f"   • {name:<15} ({config[key]}) [{status}]")

    print("\n🔑 OIDC Application:")
    print(f"   • Client ID: {config['app_client_id']} [{config.get('app_status', 'unknown')}]")
    print(
        f"   • OBO Exchange Client ID: {config['obo_client_id']} "
        f"({OKTA_EXCHANGE_APP_NAME}) [{config.get('obo_app_status', 'unknown')}]"
    )
    print(f"   • Auth Server ID: {config['auth_server_id']} [{config.get('auth_server_status', 'unknown')}]")
    print(f"   • Audience: {config['resource_server_audience']}")
    print(f"   • Discovery URL: {config['discovery_url']}")
    print("   • Scopes: claims.query, claims.submit, claims.update, claims.approve, opensearch.search")
    print("   • Group claim emission: groups (with .* regex filter, always_include_in_token=True)")


if __name__ == "__main__":
    main()
