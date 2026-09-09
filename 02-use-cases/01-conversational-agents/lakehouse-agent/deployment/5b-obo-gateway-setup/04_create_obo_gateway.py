#!/usr/bin/env python3
"""
Create the OBO_Gateway and TOKEN_EXCHANGE target.

Per design §7d: the OBO_Gateway is the architectural centerpiece of this
demo. It has NO Lambda interceptor — identity propagation is performed
natively by AgentCore Identity via on-behalf-of TOKEN_EXCHANGE (RFC 8693).
This is the structural contrast to the Interceptor_Gateway path.

This script does three things, in order:

  1. Create an IAM role agentcore-lakehouse-notes-gateway-role with the same
     permissions as the interceptor gateway's role (workload-identity +
     OAuth2 token vault access). Mirrors the interceptor side's defect of
     unconditionally deleting-and-recreating the role on already-exists
     (preserved here per R8.5).
  2. Create the OBO_Gateway with customJWTAuthorizer against Okta and
     protocolConfiguration.mcp.supportedVersions=['2025-11-25'].
     Crucially, NO interceptorConfigurations is supplied — the OBO
     path is interceptor-less by design.
  3. Create the gateway target pointing at the OpenSearch_MCP_Server
     runtime, with oauthCredentialProvider.grantType='TOKEN_EXCHANGE'
     referencing the OBO credential provider. The scopes field is non-empty
     and uses the OpenSearch-side scope 'opensearch.search'.

Per botocore probe of CreateGatewayTarget, the OBO grant is requested at
the target level under

    credentialProviderConfigurations[].credentialProvider
    .oauthCredentialProvider.grantType = 'TOKEN_EXCHANGE'

The OAuthGrantType enum is {CLIENT_CREDENTIALS, AUTHORIZATION_CODE,
TOKEN_EXCHANGE}; TOKEN_EXCHANGE is the OBO value per RFC 8693.

MCP protocol version gate:
  Default protocolConfiguration.mcp.supportedVersions = ['2025-11-25'].
  - PASS expected: gateway create succeeds; record empirically.
  - If gateway create rejects '2025-11-25' for TOKEN_EXCHANGE targets,
    surface the error and document. Do NOT silently fall back to a
    different version.

SSM keys:
  Reads:  okta-discovery-url, okta-resource-server-audience,
          opensearch-mcp-runtime-arn, obo-credential-provider-arn
  Writes: notes-gateway-id, notes-gateway-arn, notes-gateway-url, notes-gateway-name

Usage:
    python 04_create_obo_gateway.py
"""

import json
import os
import sys
import time
from typing import Any

import boto3

# Make the repo's utils/ importable (idp_config lives there).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import assert_gateway_idp_matches, get_idp_provider

# Pattern naming over data-store naming.
GATEWAY_NAME = "lakehouse-notes-gateway"
TARGET_NAME = "lakehouse-obo-target"

# Separate role for OBO gateway, mirroring interceptor's
# create_gateway_role() naming convention (`agentcore-{gateway-name}-role`).
GATEWAY_ROLE_NAME = f"agentcore-{GATEWAY_NAME}-role"

# Non-empty scopes; smallest-scope-that-works
# is the OBO-side scope. Verified against the demo's auth server /scopes
# inventory (5 scopes total; opensearch.search is one of them).
OBO_TARGET_SCOPES = ["opensearch.search"]

# Default MCP protocol version advertised by the gateway.
MCP_SUPPORTED_VERSIONS = ["2025-11-25"]

SSM_PREFIX = "/app/lakehouse-agent/"


class SSMConfig:
    """Load configuration from SSM Parameter Store."""

    def __init__(self):
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.account_id = self.sts.get_caller_identity()["Account"]

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # IdP selector — read once (DR-8). GW2 flips auth by IdP (DR-9): Okta =
        # OBO (TOKEN_EXCHANGE, no interceptor); Cognito = REQUEST interceptor +
        # Cognito M2M provider. Load only the active IdP's keys.
        self.idp_provider = get_idp_provider(self.ssm)

        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.opensearch_mcp_runtime_arn = self._get(f"{SSM_PREFIX}opensearch-mcp-runtime-arn")

        if self.idp_provider == "okta":
            # [OKTA] OBO path (verbatim)
            self.okta_discovery_url = self._get(f"{SSM_PREFIX}okta-discovery-url")
            self.okta_resource_server_audience = self._get(f"{SSM_PREFIX}okta-resource-server-audience")
            self.obo_credential_provider_arn = self._get(f"{SSM_PREFIX}obo-credential-provider-arn")
            print(f"   ✅ Okta Discovery URL: {self.okta_discovery_url}")
            print(f"   ✅ Okta Resource Server Audience: {self.okta_resource_server_audience}")
            print(f"   ✅ OpenSearch MCP Runtime ARN: {self.opensearch_mcp_runtime_arn}")
            print(f"   ✅ OBO Credential Provider ARN: {self.obo_credential_provider_arn}")
        else:  # cognito
            # [COGNITO] interceptor path (DR-9): Cognito authorizer + notes REQUEST
            # interceptor + Cognito M2M provider for the gateway→runtime leg.
            self.cognito_user_pool_arn = self._get(f"{SSM_PREFIX}cognito-user-pool-arn")
            self.cognito_app_client_id = self._get(f"{SSM_PREFIX}cognito-app-client-id")
            self.cognito_app_client_secret = self._get(f"{SSM_PREFIX}cognito-app-client-secret", secure=True)
            self.cognito_domain = self._get(f"{SSM_PREFIX}cognito-domain")
            self.notes_interceptor_lambda_arn = self._get(f"{SSM_PREFIX}notes-interceptor-lambda-arn")
            # M2M client for the gateway→runtime leg — REQUIRED (R4/m3). setup_cognito
            # (notebook 01) always creates the dedicated M2M client, so require it
            # (fail-fast via _get) rather than silently falling back to the USER app
            # client — consistent with 4b-mcp-opensearch-server/deploy_runtime.py.
            self.cognito_m2m_client_id = self._get(f"{SSM_PREFIX}cognito-m2m-client-id")
            self.cognito_m2m_client_secret = self._get(f"{SSM_PREFIX}cognito-m2m-client-secret", secure=True)
            self.has_m2m_client = True
            print(f"   ✅ OpenSearch MCP Runtime ARN: {self.opensearch_mcp_runtime_arn}")
            print(f"   ✅ Cognito User Pool ARN: {self.cognito_user_pool_arn}")
            print(f"   ✅ Notes Interceptor Lambda ARN: {self.notes_interceptor_lambda_arn}")
            print(f"   ✅ M2M client: {'dedicated' if self.has_m2m_client else 'app-client fallback'}")

    def _get(self, name: str, secure: bool = False) -> str:
        try:
            response = self.ssm.get_parameter(Name=name, WithDecryption=secure)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            print(f"❌ SSM parameter {name} not found")
            print("   Please run the prerequisite setup scripts first")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error retrieving parameter {name}: {e}")
            sys.exit(1)

    def store_gateway_parameters(self, gateway_id: str, gateway_arn: str, gateway_url: str, gateway_name: str):
        """Persist OBO gateway info to SSM (per design §8b net-new keys)."""
        print("\n💾 Storing OBO gateway configuration in SSM Parameter Store...")
        parameters = [
            {
                "name": f"{SSM_PREFIX}notes-gateway-id",
                "value": gateway_id,
                "description": "AgentCore OBO_Gateway ID",
            },
            {
                "name": f"{SSM_PREFIX}notes-gateway-arn",
                "value": gateway_arn,
                "description": "AgentCore OBO_Gateway ARN",
            },
            {
                "name": f"{SSM_PREFIX}notes-gateway-url",
                "value": gateway_url,
                "description": "AgentCore OBO_Gateway URL",
            },
            {
                "name": f"{SSM_PREFIX}notes-gateway-name",
                "value": gateway_name,
                "description": "AgentCore OBO_Gateway Name",
            },
        ]
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


def create_obo_gateway_role(config: SSMConfig) -> str:
    """
    Create IAM role for the OBO_Gateway.

    Mirrors 5a-gateway-setup/create_gateway.py:create_gateway_role(). Same
    trust policy + same permissions shape (workload-identity + OAuth2
    token-vault access). Per R8.5: preserves the original's defect of
    deleting-and-recreating the role on already-exists (not corrected
    here).
    """
    iam = boto3.client("iam", region_name=config.region)

    role_name = GATEWAY_ROLE_NAME

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole"}
        ],
    }

    # Mirrors interceptor gateway's policy. The OBO path uses
    # GetWorkloadAccessTokenForJWT + GetResourceOauth2Token under the hood;
    # Lambda invoke is harmless surplus (kept for symmetry / R8.1).
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeRuntimeTarget",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:InvokeAgentRuntimeForUser",
                    "bedrock-agentcore:InvokeGateway",
                ],
                "Resource": "*",
            },
            {
                "Sid": "InvokeLambda",
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": f"arn:aws:lambda:{config.region}:{config.account_id}:function:*",
            },
            {
                "Sid": "WorkloadIdentity",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                    "bedrock-agentcore:CreateWorkloadIdentity",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:workload-identity-directory/default/workload-identity/*",
                ],
            },
            {
                "Sid": "OAuth2Credentials",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:token-vault/default",
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:token-vault/*/oauth2credentialprovider/*",
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:workload-identity-directory/default/workload-identity/*",
                ],
            },
            {
                "Sid": "SecretsManagerAccess",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": f"arn:aws:secretsmanager:{config.region}:{config.account_id}:secret:*",
            },
        ],
    }

    try:
        print(f"🔑 Creating IAM role: {role_name}")
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for AgentCore OBO_Gateway",
            Tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "notes-gateway-role"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        print(f"✅ Created IAM role: {role_arn}")

        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="GatewayExecutionPolicy",
            PolicyDocument=json.dumps(policy_document),
        )
        print("✅ Attached execution policy to role")
        return role_arn

    except iam.exceptions.EntityAlreadyExistsException:
        # Idempotent in-place update — preserves any out-of-band attachments.
        # This re-asserts the script-owned trust policy + the
        # 'GatewayExecutionPolicy' inline policy (overwriting any hand-edits to
        # *those two* on re-run, by design), while leaving every OTHER attachment
        # untouched: other inline policies, managed-policy attachments, and
        # instance-profile memberships. No detach-all, no delete_role, no sleep.
        # (Kept in sync with 5a-gateway-setup/create_gateway.py:create_gateway_role.)
        print(f"ℹ️  Role {role_name} already exists — updating in place (preserving any out-of-band attachments)")

        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

        # Repair the trust policy in place (no delete).
        iam.update_assume_role_policy(
            RoleName=role_name,
            PolicyDocument=json.dumps(trust_policy),
        )

        # Upsert ONLY our known inline policy; put_role_policy overwrites an
        # existing PolicyName, so this is a safe in-place update.
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="GatewayExecutionPolicy",
            PolicyDocument=json.dumps(policy_document),
        )

        print(f"✅ Updated existing IAM role in place: {role_arn}")
        return role_arn


def create_obo_gateway(client, config: SSMConfig, role_arn: str) -> dict[str, Any]:
    """
    Create the OBO_Gateway with Okta customJWTAuthorizer + MCP protocol.

    Per design §7d: NO interceptorConfigurations. Identity propagation is
    native via AgentCore Identity TOKEN_EXCHANGE (configured at target
    level, not gateway level).

    The MCP protocol version gate fires here on protocolConfiguration.mcp.supportedVersions.
    """
    print(f"\n🔧 Creating OBO_Gateway: {GATEWAY_NAME}")
    print(f"   Protocol: MCP, supportedVersions={MCP_SUPPORTED_VERSIONS}")
    print("   Authorizer: customJWTAuthorizer (Okta)")
    print(f"      discoveryUrl: {config.okta_discovery_url}")
    print(f"      allowedAudience: ['{config.okta_resource_server_audience}']")
    print("   No interceptorConfigurations (OBO path is interceptor-less per §7d)")

    auth_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": config.okta_discovery_url,
            "allowedAudience": [config.okta_resource_server_audience],
        }
    }

    try:
        response = client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            protocolConfiguration={
                "mcp": {
                    "supportedVersions": MCP_SUPPORTED_VERSIONS,
                    # searchType intentionally omitted (semantic search OFF).
                    # Primary reason — tutorial clarity: enabling it auto-provisions
                    # the built-in x_amz_bedrock_agentcore_search meta-tool, which the
                    # agent surfaces (as notes_x_amz_bedrock_agentcore_search) alongside
                    # the real tools. That's noise that distracts from what this tutorial
                    # teaches — per-persona tool access and per-user data scope — so we
                    # keep it out of the access-control story.
                    # Secondary — cost-free here: this gateway fronts a single tool
                    # (search_claim_notes), so semantic tool-discovery adds no value anyway.
                    # Contrast: GW1 keeps SEMANTIC and strips the meta-tool via its
                    # response interceptor; GW2 has no interceptor by design, so we omit
                    # it at the source — same clean-toolset outcome, pattern-appropriate
                    # mechanism.
                }
            },
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            description="OBO_Gateway for OpenSearch_MCP_Server (RFC 8693 TOKEN_EXCHANGE; design §7d)",
            tags={"Application": "lakehouse-agent", "Purpose": "notes-gateway"},
        )
        gateway_id = response["gatewayId"]
        gateway_url = response["gatewayUrl"]
        gateway_arn = f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:gateway/{gateway_id}"
        print("✅ OBO_Gateway created successfully!")
        print(f"   Gateway ID: {gateway_id}")
        print(f"   Gateway URL: {gateway_url}")
        print(f"   Gateway ARN: {gateway_arn}")
        return {
            "gatewayId": gateway_id,
            "gatewayUrl": gateway_url,
            "gatewayArn": gateway_arn,
            "gatewayName": GATEWAY_NAME,
        }

    except Exception as e:
        if "already exists" in str(e):
            print(f"ℹ️  Gateway {GATEWAY_NAME} already exists, retrieving details...")
            response = client.list_gateways()
            for gateway in response.get("items", []):
                if gateway["name"] == GATEWAY_NAME:
                    gateway_id = gateway["gatewayId"]
                    detail = client.get_gateway(gatewayIdentifier=gateway_id)
                    # DR-11 pre-flight: refuse to reuse a gateway deployed for the
                    # other IdP (flag-switch without teardown).
                    assert_gateway_idp_matches(detail, config.idp_provider, GATEWAY_NAME)
                    gateway_url = detail["gatewayUrl"]
                    gateway_arn = f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:gateway/{gateway_id}"
                    print(f"✅ Using existing gateway: {gateway_id}")
                    return {
                        "gatewayId": gateway_id,
                        "gatewayUrl": gateway_url,
                        "gatewayArn": gateway_arn,
                        "gatewayName": GATEWAY_NAME,
                    }
        print(f"❌ Error creating OBO_Gateway: {e}")
        raise


def get_runtime_mcp_url(runtime_arn: str, region: str) -> str:
    """
    Construct the MCP endpoint URL for an AgentCore Runtime ARN.
    Mirrors get_runtime_mcp_url() in 5a-gateway-setup/create_gateway.py.
    """
    encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F")
    return f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"


def create_obo_target(client, config: SSMConfig, gateway_id: str) -> dict[str, Any]:
    """
    Create the OBO_Gateway target pointing at the OpenSearch_MCP_Server
    runtime, with TOKEN_EXCHANGE grant on the OBO credential provider.

    grantType='TOKEN_EXCHANGE' lives on
    credentialProvider.oauthCredentialProvider, alongside providerArn and
    scopes. OAuthGrantType enum: {CLIENT_CREDENTIALS, AUTHORIZATION_CODE,
    TOKEN_EXCHANGE}.

    The scopes list is non-empty (['opensearch.search']).
    """
    mcp_url = get_runtime_mcp_url(config.opensearch_mcp_runtime_arn, config.region)

    print(f"\n🎯 Creating OBO_Gateway target: {TARGET_NAME}")
    print(f"   MCP Server URL: {mcp_url}")
    print(f"   OAuth provider ARN: {config.obo_credential_provider_arn}")
    print("   Grant type: TOKEN_EXCHANGE  (RFC 8693 — the OBO pattern centerpiece)")
    print(f"   Scopes: {OBO_TARGET_SCOPES}  (non-empty; verified against the /scopes inventory)")

    try:
        response = client.create_gateway_target(
            name=TARGET_NAME,
            gatewayIdentifier=gateway_id,
            targetConfiguration={
                "mcp": {
                    "mcpServer": {
                        "endpoint": mcp_url,
                    }
                }
            },
            credentialProviderConfigurations=[
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": config.obo_credential_provider_arn,
                            "scopes": OBO_TARGET_SCOPES,
                            "grantType": "TOKEN_EXCHANGE",
                            # Okta's RFC 8693 token-exchange requires an
                            # `audience` body parameter identifying the target
                            # resource server; without it Okta rejects with
                            # `missing_token_request_parameter`. AgentCore forwards
                            # oauthCredentialProvider.customParameters as extra
                            # token-request body params. audience == the
                            # auth-server resource audience the runtime/gateway
                            # authorizers already validate (api://lakehouse-api).
                            #
                            # `subject_token_type` overrides AgentCore's default
                            # (urn:...:token-type:jwt), which Okta's OBO flow
                            # rejects with `invalid_subject_token_type`. Okta OBO
                            # requires the subject token be
                            # declared as an access_token. Per AWS OBO devguide,
                            # the default subject-token-type is overridable via
                            # the same customParameters body-param channel.
                            "customParameters": {
                                "audience": config.okta_resource_server_audience,
                                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                            },
                        }
                    },
                }
            ],
        )
        print("✅ OBO_Gateway target created successfully with TOKEN_EXCHANGE!")
        return response

    except Exception as e:
        if "already exists" in str(e):
            print(f"ℹ️  Target {TARGET_NAME} already exists")
            return {}
        print(f"❌ Error creating OBO_Gateway target: {e}")
        raise


def wait_for_gateway_active(client, gateway_id: str, max_wait_seconds: int = 300) -> bool:
    """
    Wait for gateway to reach ACTIVE / READY status.
    Mirrors wait_for_gateway_active() in 5a-gateway-setup/create_gateway.py.
    """
    print("\n⏳ Checking OBO_Gateway status...")
    start = time.time()
    while time.time() - start < max_wait_seconds:
        try:
            response = client.get_gateway(gatewayIdentifier=gateway_id)
            status = response.get("status", "UNKNOWN").strip().upper()
            print(f"   Status: {status}")
            if status in ("ACTIVE", "READY"):
                print(f"✅ Gateway is ready (status: {status})!")
                return True
            if status in ("FAILED", "DELETING", "DELETED"):
                print(f"❌ Gateway is in {status} status")
                return False
            time.sleep(10)
        except Exception as e:
            print(f"⚠️  Error checking gateway status: {e}")
            time.sleep(10)
    print("⚠️  Timeout waiting for gateway to be active")
    return False


# ─────────────────────────────────────────────────────────────────────────
# [COGNITO] GW2 interceptor path (DR-9). No OBO/TOKEN_EXCHANGE — a REQUEST
# interceptor forwards the caller sub; the gateway→runtime leg uses a Cognito
# M2M provider. Mirrors GW1's interceptor gateway, pointed at the OpenSearch MCP.
# ─────────────────────────────────────────────────────────────────────────

COGNITO_M2M_PROVIDER_NAME = "lakehouse-notes-cognito-oauth-provider"


def create_notes_cognito_provider(client, config: SSMConfig) -> str:
    """Create (or reuse) the Cognito M2M OAuth2 provider for the gateway→runtime leg.

    Cognito has no OIDC token-endpoint discovery, so pass authorization-server
    metadata directly (mirrors 5a-gateway-setup/create_gateway.py's Cognito branch).
    """
    provider_name = COGNITO_M2M_PROVIDER_NAME
    user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
    cognito_issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
    cognito_token_endpoint = f"{config.cognito_domain}/oauth2/token"
    client_id = config.cognito_m2m_client_id if config.has_m2m_client else config.cognito_app_client_id
    client_secret = config.cognito_m2m_client_secret if config.has_m2m_client else config.cognito_app_client_secret

    print(f"\n🔐 Creating Cognito M2M OAuth2 provider: {provider_name}")
    try:
        response = client.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "authorizationServerMetadata": {
                            "issuer": cognito_issuer,
                            "authorizationEndpoint": f"{cognito_issuer}/oauth2/authorize",
                            "tokenEndpoint": cognito_token_endpoint,
                            "tokenEndpointAuthMethods": ["client_secret_post"],
                        }
                    },
                    "clientId": client_id,
                    "clientSecret": client_secret,
                }
            },
            tags={"Application": "lakehouse-agent", "Purpose": "notes-cognito-oauth-provider"},
        )
        provider_arn = (
            response.get("oauth2CredentialProviderArn") or response.get("arn") or response.get("credentialProviderArn")
        )
        if not provider_arn:
            raise KeyError(f"No ARN in create response: {list(response.keys())}")
        print(f"✅ Created provider: {provider_arn}")
        return provider_arn
    except Exception as e:
        if "already exists" in str(e).lower() or "AlreadyExistsException" in str(e):
            print(f"ℹ️  Provider {provider_name} exists; retrieving ARN...")
            resp = client.list_oauth2_credential_providers()
            providers = (
                resp.get("credentialProviders") or resp.get("oauth2CredentialProviders") or resp.get("items") or []
            )
            for p in providers:
                if p.get("name") == provider_name:
                    arn = p.get("credentialProviderArn") or p.get("oauth2CredentialProviderArn") or p.get("arn")
                    if arn:
                        print(f"✅ Using existing provider: {arn}")
                        return arn
        print(f"❌ Error creating Cognito M2M provider: {e}")
        raise


def create_notes_interceptor_gateway(client, config: SSMConfig, role_arn: str) -> dict[str, Any]:
    """Create the GW2 notes gateway (Cognito): customJWTAuthorizer + REQUEST interceptor.

    Same GW2 name/keys as the OBO path (topology symmetric, DR-1) — only the auth
    mechanism differs. No interceptorConfigurations RESPONSE (notes has one tool);
    identity is forwarded by the thin notes REQUEST interceptor.
    """
    user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
    issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
    auth_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": f"{issuer}/.well-known/openid-configuration",
            "allowedClients": [config.cognito_app_client_id],
        }
    }
    interceptor_config = [
        {
            "interceptor": {"lambda": {"arn": config.notes_interceptor_lambda_arn}},
            "interceptionPoints": ["REQUEST"],
            "inputConfiguration": {"passRequestHeaders": True},
        }
    ]

    print(f"\n🔧 Creating notes Interceptor_Gateway (Cognito): {GATEWAY_NAME}")
    try:
        response = client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            protocolConfiguration={"mcp": {"supportedVersions": MCP_SUPPORTED_VERSIONS}},
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            interceptorConfigurations=interceptor_config,
            description="GW2 notes gateway (Cognito REQUEST interceptor; DR-9)",
            tags={"Application": "lakehouse-agent", "Purpose": "notes-gateway"},
        )
        gateway_id = response["gatewayId"]
        gateway_url = response["gatewayUrl"]
        gateway_arn = f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:gateway/{gateway_id}"
        print(f"✅ Notes gateway created: {gateway_id}")
        return {
            "gatewayId": gateway_id,
            "gatewayUrl": gateway_url,
            "gatewayArn": gateway_arn,
            "gatewayName": GATEWAY_NAME,
        }
    except Exception as e:
        if "already exists" in str(e):
            print(f"ℹ️  Gateway {GATEWAY_NAME} already exists, retrieving details...")
            for gateway in client.list_gateways().get("items", []):
                if gateway["name"] == GATEWAY_NAME:
                    gateway_id = gateway["gatewayId"]
                    detail = client.get_gateway(gatewayIdentifier=gateway_id)
                    # DR-11 pre-flight: refuse to reuse a gateway deployed for the
                    # other IdP (flag-switch without teardown).
                    assert_gateway_idp_matches(detail, config.idp_provider, GATEWAY_NAME)
                    gateway_arn = f"arn:aws:bedrock-agentcore:{config.region}:{config.account_id}:gateway/{gateway_id}"
                    return {
                        "gatewayId": gateway_id,
                        "gatewayUrl": detail["gatewayUrl"],
                        "gatewayArn": gateway_arn,
                        "gatewayName": GATEWAY_NAME,
                    }
        print(f"❌ Error creating notes gateway: {e}")
        raise


def create_notes_target(client, config: SSMConfig, gateway_id: str, provider_arn: str) -> dict[str, Any]:
    """Create the notes gateway target → OpenSearch MCP runtime (Cognito M2M provider).

    client_credentials (scopes []); NO grantType TOKEN_EXCHANGE. Identity reaches
    the server via the interceptor's body-context injection, not this leg.
    """
    mcp_url = get_runtime_mcp_url(config.opensearch_mcp_runtime_arn, config.region)
    print(f"\n🎯 Creating notes gateway target: {TARGET_NAME}")
    print(f"   MCP Server URL: {mcp_url}")
    try:
        response = client.create_gateway_target(
            name=TARGET_NAME,
            gatewayIdentifier=gateway_id,
            targetConfiguration={"mcp": {"mcpServer": {"endpoint": mcp_url}}},
            credentialProviderConfigurations=[
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {"oauthCredentialProvider": {"providerArn": provider_arn, "scopes": []}},
                }
            ],
        )
        print("✅ Notes gateway target created (Cognito M2M client_credentials).")
        return response
    except Exception as e:
        if "already exists" in str(e):
            print(f"ℹ️  Target {TARGET_NAME} already exists")
            return {}
        print(f"❌ Error creating notes target: {e}")
        raise


def main():
    print("=" * 70)
    print("OBO_Gateway Setup")
    print("=" * 70)

    config = SSMConfig()
    client = boto3.client("bedrock-agentcore-control", region_name=config.region)

    print("\n📋 Configuration:")
    print(f"   IdP: {config.idp_provider}")
    print(f"   Gateway Name: {GATEWAY_NAME}")
    print(f"   Target Name: {TARGET_NAME}")
    print(f"   OpenSearch MCP Runtime ARN: {config.opensearch_mcp_runtime_arn}")

    try:
        # Step 1: Gateway IAM role
        print("\n" + "=" * 70)
        print("Step 1: OBO_Gateway IAM role")
        print("=" * 70)
        role_arn = create_obo_gateway_role(config)

        # Step 2: Create the GW2 gateway (DR-9 auth flip by IdP)
        print("\n" + "=" * 70)
        print("Step 2: Create GW2 gateway")
        print("=" * 70)
        if config.idp_provider == "okta":
            gateway = create_obo_gateway(client, config, role_arn)
        else:  # cognito
            gateway = create_notes_interceptor_gateway(client, config, role_arn)

        # Step 3: Wait for gateway ACTIVE before creating target
        if not wait_for_gateway_active(client, gateway["gatewayId"]):
            print("\n⚠️  Gateway not active; cannot create target. Re-run after gateway reaches ACTIVE.")
            sys.exit(1)

        # Step 4: Create the GW2 target
        print("\n" + "=" * 70)
        print("Step 3: Create GW2 target")
        print("=" * 70)
        if config.idp_provider == "okta":
            # [OKTA] OBO target (TOKEN_EXCHANGE — locked shape)
            create_obo_target(client, config, gateway["gatewayId"])
        else:  # cognito
            # [COGNITO] Cognito M2M provider + client_credentials target (identity via interceptor)
            provider_arn = create_notes_cognito_provider(client, config)
            create_notes_target(client, config, gateway["gatewayId"], provider_arn)

        # Step 5: Persist gateway info to SSM
        print("\n" + "=" * 70)
        print("Step 4: Persist OBO_Gateway info to SSM")
        print("=" * 70)
        config.store_gateway_parameters(
            gateway["gatewayId"],
            gateway["gatewayArn"],
            gateway["gatewayUrl"],
            gateway["gatewayName"],
        )

        # Summary
        print("\n" + "=" * 70)
        print("OBO_Gateway Setup Complete!")
        print("=" * 70)
        print(f"\n✅ Gateway: {GATEWAY_NAME}")
        print(f"   ID:  {gateway['gatewayId']}")
        print(f"   URL: {gateway['gatewayUrl']}")
        print(f"   ARN: {gateway['gatewayArn']}")
        print(f"\n✅ Target: {TARGET_NAME}")
        if config.idp_provider == "okta":
            print("   Grant: TOKEN_EXCHANGE (RFC 8693)")
            print(f"   Scopes: {OBO_TARGET_SCOPES}")
        else:  # cognito
            print("   Auth: Cognito M2M (client_credentials) + REQUEST interceptor for identity")
        print("\n📋 Next Steps:")
        print("   - The agent deliberately holds NO OBO grant — the GW2 gateway role")
        print("     performs the RFC 8693 exchange (Finding 15).")
        print("   - Deploy the agent (06-deploy-agent.ipynb) with its two MCP clients:")
        print("     claims/* via Interceptor_Gateway, notes/* via OBO_Gateway.")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ OBO_Gateway setup failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
