#!/usr/bin/env python3
"""
Create the notes gateway (GW2) and its target, branched on IDP_PROVIDER.

Run by notebook 05b-deploy-notes-gateway.ipynb, Step 7. Needs the OpenSearch
MCP runtime (agentcore_cli_deploy.py opensearch-mcp) and, on Okta, the OBO
credential provider from 03_create_oauth_provider.py; on Cognito, the notes
REQUEST interceptor Lambda from 5a-gateway-setup/interceptor-notes/deploy.sh.

[OKTA] The OBO_Gateway has NO Lambda interceptor. Identity propagation is
performed natively by AgentCore Identity via on-behalf-of TOKEN_EXCHANGE
(RFC 8693). Request path: the agent calls the gateway with the user's Okta
access token; the gateway's customJWTAuthorizer validates it (Okta discovery
URL, allowedAudience = the resource-server audience); when the gateway calls
the target it uses the bound OBO credential provider to exchange that token
at Okta for a user-scoped token (scope opensearch.search) and sends it to the
OpenSearch MCP runtime, which filters notes by the token's subject.

[COGNITO] The same gateway name and target name, but with a REQUEST
interceptor that forwards the caller's sub, and a Cognito M2M
client_credentials provider for the gateway-to-runtime leg.

This script does, in order:

  1. Create or update the IAM role agentcore-lakehouse-notes-gateway-role,
     assumed by bedrock-agentcore.amazonaws.com. It grants runtime invoke,
     workload-identity token and OAuth2 token-vault access, which the
     gateway needs to run the credential provider. An existing role is
     updated in place (trust policy + GatewayExecutionPolicy inline policy).
  2. Create the gateway (CUSTOM_JWT authorizer, MCP protocol,
     supportedVersions=['2025-11-25']). An existing gateway is reused only
     if it was deployed for the same IdP (pre-flight IdP-mismatch guard).
  3. Wait for the gateway to be ACTIVE; targets cannot be added before that.
  4. Create the target pointing at the OpenSearch MCP runtime's invocation
     URL, bound to the credential provider (Okta: TOKEN_EXCHANGE grant).
  5. Wait for the target to be READY: only then has the gateway fetched the
     server's tool list, so the agent sees the tools. Re-syncs once on FAILED.
  6. Store the gateway id, ARN, URL and name in SSM for notebook 06.

In the CreateGatewayTarget request, the OBO grant is requested at the
target level under

    credentialProviderConfigurations[].credentialProvider
    .oauthCredentialProvider.grantType = 'TOKEN_EXCHANGE'

The OAuthGrantType enum is {CLIENT_CREDENTIALS, AUTHORIZATION_CODE,
TOKEN_EXCHANGE}; TOKEN_EXCHANGE is the OBO value per RFC 8693.

MCP protocol version: the gateway advertises ['2025-11-25']. If create_gateway
rejects it, the error is raised as-is; there is no fallback to another version.

SSM keys (under /app/lakehouse-agent/):
  Reads:  the IdP flag (utils.idp_config), opensearch-mcp-runtime-arn;
          Okta: okta-discovery-url, okta-resource-server-audience,
                obo-credential-provider-arn;
          Cognito: cognito-user-pool-arn, cognito-app-client-{id,secret},
                cognito-domain, cognito-m2m-client-{id,secret},
                notes-interceptor-lambda-arn
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

# Scope requested in the OBO exchange: the smallest scope the OpenSearch
# server needs. It must exist on the Okta authorization server.
OBO_TARGET_SCOPES = ["opensearch.search"]

# Name used in this script's output. Okta builds the OBO gateway; Cognito builds the
# same notes gateway with a REQUEST interceptor and no OBO token exchange.
GATEWAY_LABELS = {"okta": "OBO_Gateway", "cognito": "Notes_Gateway"}

# Default MCP protocol version advertised by the gateway.
MCP_SUPPORTED_VERSIONS = ["2025-11-25"]

SSM_PREFIX = "/app/lakehouse-agent/"


class SSMConfig:
    """Load configuration from SSM Parameter Store."""

    def __init__(self, idp_provider: str | None = None):
        session = boto3.Session()
        self.region = session.region_name
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.account_id = self.sts.get_caller_identity()["Account"]

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # IdP selector — read once. GW2 flips auth by IdP: Okta =
        # OBO (TOKEN_EXCHANGE, no interceptor); Cognito = REQUEST interceptor +
        # Cognito M2M provider. Load only the active IdP's keys.
        self.idp_provider = idp_provider or get_idp_provider(self.ssm)

        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.opensearch_mcp_runtime_arn = self._get(f"{SSM_PREFIX}opensearch-mcp-runtime-arn")

        if self.idp_provider == "okta":
            # [OKTA] OBO path: authorizer settings + the OBO provider from 03.
            self.okta_discovery_url = self._get(f"{SSM_PREFIX}okta-discovery-url")
            self.okta_resource_server_audience = self._get(f"{SSM_PREFIX}okta-resource-server-audience")
            self.obo_credential_provider_arn = self._get(f"{SSM_PREFIX}obo-credential-provider-arn")
            print(f"   ✅ Okta Discovery URL: {self.okta_discovery_url}")
            print(f"   ✅ Okta Resource Server Audience: {self.okta_resource_server_audience}")
            print(f"   ✅ OpenSearch MCP Runtime ARN: {self.opensearch_mcp_runtime_arn}")
            print(f"   ✅ OBO Credential Provider ARN: {self.obo_credential_provider_arn}")
        else:  # cognito
            # [COGNITO] interceptor path: Cognito authorizer + notes REQUEST
            # interceptor + Cognito M2M provider for the gateway→runtime leg.
            self.cognito_user_pool_arn = self._get(f"{SSM_PREFIX}cognito-user-pool-arn")
            self.cognito_app_client_id = self._get(f"{SSM_PREFIX}cognito-app-client-id")
            self.cognito_app_client_secret = self._get(f"{SSM_PREFIX}cognito-app-client-secret", secure=True)
            self.cognito_domain = self._get(f"{SSM_PREFIX}cognito-domain")
            self.notes_interceptor_lambda_arn = self._get(f"{SSM_PREFIX}notes-interceptor-lambda-arn")
            # M2M client for the gateway→runtime leg — REQUIRED. setup_cognito
            # (notebook 01) always creates the dedicated M2M client, so require it
            # (fail-fast via _get) rather than silently falling back to the USER app
            # client — consistent with deployment/agentcore_cli_deploy.py.
            self.cognito_m2m_client_id = self._get(f"{SSM_PREFIX}cognito-m2m-client-id")
            self.cognito_m2m_client_secret = self._get(f"{SSM_PREFIX}cognito-m2m-client-secret", secure=True)
            print(f"   ✅ OpenSearch MCP Runtime ARN: {self.opensearch_mcp_runtime_arn}")
            print(f"   ✅ Cognito User Pool ARN: {self.cognito_user_pool_arn}")
            print(f"   ✅ Notes Interceptor Lambda ARN: {self.notes_interceptor_lambda_arn}")
            print("   ✅ M2M client: dedicated")

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
        """Persist OBO gateway info to SSM (net-new keys)."""
        noun = "OBO gateway" if self.idp_provider == "okta" else "notes gateway"
        print(f"\n💾 Storing {noun} configuration in SSM Parameter Store...")
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
    token-vault access). If the role already exists it is updated in place.
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
    # Lambda invoke is harmless surplus (kept for symmetry).
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

    NO interceptorConfigurations. Identity propagation is
    native via AgentCore Identity TOKEN_EXCHANGE (configured at target
    level, not gateway level).

    The MCP protocol version gate fires here on protocolConfiguration.mcp.supportedVersions.
    """
    print(f"\n🔧 Creating OBO_Gateway: {GATEWAY_NAME}")
    print(f"   Protocol: MCP, supportedVersions={MCP_SUPPORTED_VERSIONS}")
    print("   Authorizer: customJWTAuthorizer (Okta)")
    print(f"      discoveryUrl: {config.okta_discovery_url}")
    print(f"      allowedAudience: ['{config.okta_resource_server_audience}']")
    print("   No interceptorConfigurations (OBO path is interceptor-less)")

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
            description="OBO_Gateway for OpenSearch_MCP_Server (RFC 8693 TOKEN_EXCHANGE)",
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
                    # Pre-flight IdP-mismatch guard: refuse to reuse a gateway deployed for the
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


def wait_for_gateway_active(client, gateway_id: str, max_wait_seconds: int = 300, label: str = "OBO_Gateway") -> bool:
    """
    Wait for gateway to reach ACTIVE / READY status.
    Mirrors wait_for_gateway_active() in 5a-gateway-setup/create_gateway.py.
    """
    print(f"\n⏳ Checking {label} status...")
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
# [COGNITO] notes gateway with a REQUEST interceptor. There is no OBO token
# exchange: the interceptor Lambda (5a-gateway-setup/interceptor-notes) forwards
# the caller sub, and the gateway→runtime leg authenticates with a Cognito M2M
# OAuth2 credential provider. Same shape as the claims gateway, pointed at the
# OpenSearch MCP runtime.
# ─────────────────────────────────────────────────────────────────────────

COGNITO_M2M_PROVIDER_NAME = "lakehouse-notes-cognito-oauth-provider"


def create_notes_cognito_provider(client, config: SSMConfig) -> str:
    """Create (or reuse) the Cognito M2M OAuth2 provider for the gateway→runtime leg.

    create_oauth2_credential_provider stores the client id and secret in AgentCore
    Identity; the gateway uses it to fetch a client_credentials token when it
    calls the runtime. Authorization-server metadata (issuer, token endpoint) is
    passed directly, as in 5a-gateway-setup/create_gateway.py's Cognito branch.
    Uses the dedicated M2M app client that SSMConfig reads from SSM
    (cognito-m2m-client-id / -secret). If the provider already exists, its ARN is
    looked up and reused.
    """
    provider_name = COGNITO_M2M_PROVIDER_NAME
    user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
    cognito_issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
    cognito_token_endpoint = f"{config.cognito_domain}/oauth2/token"
    client_id = config.cognito_m2m_client_id
    client_secret = config.cognito_m2m_client_secret

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
    """Create the notes gateway (Cognito): customJWTAuthorizer + REQUEST interceptor.

    Uses the same gateway name and SSM keys as the Okta OBO path; only the auth
    mechanism differs. Inbound JWTs are checked against the user pool's OIDC
    discovery URL and must come from the app client. The notes interceptor Lambda
    (ARN from SSM notes-interceptor-lambda-arn) runs at the REQUEST point with
    request headers passed in; there is no RESPONSE interceptor. If the gateway
    already exists it is reused after an IdP-mismatch check.
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
            description="GW2 notes gateway (Cognito REQUEST interceptor)",
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
                    # Pre-flight IdP-mismatch guard: refuse to reuse a gateway deployed for the
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

    The target's OAuth credential provider uses client_credentials with no scopes
    and no TOKEN_EXCHANGE grant. The caller's identity reaches the server through
    the interceptor's params.arguments.context, not through this token.
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


# ─────────────────────────────────────────────────────────────────────────
# Gateway target readiness. Creating a target returns at once, but the
# gateway then connects to the MCP server and fetches its tool list; if that
# fetch fails the target goes FAILED and the gateway serves no tools. So wait
# for READY, re-synchronize once if it fails, and stop loudly if it still
# fails. Every TargetStatus value is mapped to "ready", "failed" or "wait".
# ─────────────────────────────────────────────────────────────────────────
TARGET_READY_TIMEOUT_SECONDS = 180
TARGET_POLL_SECONDS = 10
TARGET_FAILED_STATUSES = ("FAILED", "SYNCHRONIZE_UNSUCCESSFUL", "UPDATE_UNSUCCESSFUL", "DELETING")
# Everything else is a wait state: CREATING, UPDATING, SYNCHRONIZING and the
# three *_PENDING_AUTH states (which cannot be synchronized until they finish).


def find_target_id(client, gateway_id: str, target_name: str) -> str:
    """Return the id of the named target on the gateway, or raise if absent."""
    token = None
    while True:
        kwargs = {"gatewayIdentifier": gateway_id}
        if token:
            kwargs["nextToken"] = token
        page = client.list_gateway_targets(**kwargs)
        for item in page.get("items", []):
            if item.get("name") == target_name:
                return item["targetId"]
        token = page.get("nextToken")
        if not token:
            raise RuntimeError(f"Gateway target {target_name} not found on gateway {gateway_id}")


def _poll_target(client, gateway_id: str, target_id: str, target_name: str, timeout_seconds: int):
    """Poll until READY, a failed status, or the timeout. Returns (outcome, status, reasons)."""
    start = time.time()
    while True:
        target = client.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = (target.get("status") or "UNKNOWN").strip().upper()
        reasons = target.get("statusReasons") or []
        elapsed = int(time.time() - start)
        print(f"   Target {target_name} status: {status} ({elapsed}s elapsed)")
        if status == "READY":
            return "ready", status, reasons
        if status in TARGET_FAILED_STATUSES:
            return "failed", status, reasons
        if time.time() - start >= timeout_seconds:
            return "timeout", status, reasons
        time.sleep(TARGET_POLL_SECONDS)


def wait_for_target_ready(
    client, gateway_id: str, target_id: str, target_name: str, timeout_seconds: int = TARGET_READY_TIMEOUT_SECONDS
) -> None:
    """Wait for the target to be READY; re-synchronize once if it fails; raise if it cannot get there."""
    print(f"\n⏳ Waiting for gateway target {target_name} to reach READY (up to {timeout_seconds}s)...")
    outcome, status, reasons = _poll_target(client, gateway_id, target_id, target_name, timeout_seconds)
    if outcome == "ready":
        print(f"✅ Gateway target {target_name} is READY")
        return
    if outcome == "timeout":
        raise RuntimeError(
            f"Gateway target {target_name} not ready after {timeout_seconds}s (last status: {status}). "
            "It may still finish on its own; check it with "
            f"`aws bedrock-agentcore-control get-gateway-target --gateway-identifier {gateway_id} "
            f"--target-id {target_id}` and re-run this step once it is READY."
        )

    print(f"⚠️  WARNING: gateway target {target_name} is {status}. Reasons reported by AWS: {reasons}")
    print("   Re-synchronizing the target once (the gateway fetches the MCP server's tool list again)...")
    synchronize = getattr(client, "synchronize_gateway_targets", None)
    if synchronize is None:
        raise RuntimeError(
            "This boto3 version has no synchronize_gateway_targets. Upgrade it (pip install -U boto3) "
            f"and re-run this step. Target {target_name} is {status}: {reasons}"
        )
    synchronize(gatewayIdentifier=gateway_id, targetIdList=[target_id])

    outcome, status, reasons = _poll_target(client, gateway_id, target_id, target_name, timeout_seconds)
    if outcome == "ready":
        print(f"✅ Gateway target {target_name} is READY after a re-sync (the first tool-list fetch failed).")
        return
    if outcome == "timeout":
        raise RuntimeError(
            f"Gateway target {target_name} not ready after {timeout_seconds}s following a re-sync "
            f"(last status: {status})."
        )
    raise RuntimeError(f"Gateway target {target_name} is still {status} after a re-sync. Reasons: {reasons}")


def main():
    # Read the IdP first so the banner can name the right gateway; SSMConfig reuses it.
    idp_provider = get_idp_provider(boto3.client("ssm", region_name=boto3.Session().region_name))
    label = GATEWAY_LABELS[idp_provider]
    print("=" * 70)
    print(f"{label} Setup")
    print("=" * 70)

    config = SSMConfig(idp_provider=idp_provider)
    client = boto3.client("bedrock-agentcore-control", region_name=config.region)

    print("\n📋 Configuration:")
    print(f"   IdP: {config.idp_provider}")
    print(f"   Gateway Name: {GATEWAY_NAME}")
    print(f"   Target Name: {TARGET_NAME}")
    print(f"   OpenSearch MCP Runtime ARN: {config.opensearch_mcp_runtime_arn}")

    try:
        # Step 1: Gateway IAM role
        print("\n" + "=" * 70)
        print(f"Step 1: {label} IAM role")
        print("=" * 70)
        role_arn = create_obo_gateway_role(config)

        # Step 2: Create the GW2 gateway (auth flip by IdP)
        print("\n" + "=" * 70)
        print("Step 2: Create GW2 gateway")
        print("=" * 70)
        if config.idp_provider == "okta":
            gateway = create_obo_gateway(client, config, role_arn)
        else:  # cognito
            gateway = create_notes_interceptor_gateway(client, config, role_arn)

        # Wait for gateway ACTIVE before creating target
        if not wait_for_gateway_active(client, gateway["gatewayId"], label=label):
            print("\n⚠️  Gateway not active; cannot create target. Re-run after gateway reaches ACTIVE.")
            sys.exit(1)

        # Step 3: Create the GW2 target
        print("\n" + "=" * 70)
        print("Step 3: Create GW2 target")
        print("=" * 70)
        if config.idp_provider == "okta":
            # [OKTA] OBO target bound to the TOKEN_EXCHANGE provider
            create_obo_target(client, config, gateway["gatewayId"])
        else:  # cognito
            # [COGNITO] Cognito M2M provider + client_credentials target (identity via interceptor)
            provider_arn = create_notes_cognito_provider(client, config)
            create_notes_target(client, config, gateway["gatewayId"], provider_arn)

        # Both paths: the target must be READY (tool list fetched) before the agent uses it.
        target_id = find_target_id(client, gateway["gatewayId"], TARGET_NAME)
        wait_for_target_ready(client, gateway["gatewayId"], target_id, TARGET_NAME)

        # Step 4: Persist gateway info to SSM (read by notebook 06 / the agent)
        print("\n" + "=" * 70)
        print(f"Step 4: Persist {label} info to SSM")
        print("=" * 70)
        config.store_gateway_parameters(
            gateway["gatewayId"],
            gateway["gatewayArn"],
            gateway["gatewayUrl"],
            gateway["gatewayName"],
        )

        # Summary
        print("\n" + "=" * 70)
        print(f"{label} Setup Complete!")
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
        if config.idp_provider == "okta":
            print("   - The agent deliberately holds NO OBO grant — the GW2 gateway role")
            print("     performs the RFC 8693 exchange.")
        else:  # cognito
            print("   - The notes REQUEST interceptor forwards the caller's sub; the gateway")
            print("     calls the OpenSearch MCP runtime with the Cognito M2M client.")
        print("   - Deploy the agent (06-deploy-agent.ipynb) with its two MCP clients:")
        print(f"     claims/* via Interceptor_Gateway, notes/* via {label}.")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ {label} setup failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
