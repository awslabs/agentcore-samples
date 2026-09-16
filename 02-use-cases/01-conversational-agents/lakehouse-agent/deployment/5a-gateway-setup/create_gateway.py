#!/usr/bin/env python3
"""
Create AgentCore Gateway for Health Lakehouse Data

This script creates and configures an AgentCore Gateway that:
1. Connects to the MCP Athena server (running on AgentCore Runtime)
2. Uses the Gateway interceptor for JWT validation
3. Enforces fine-grained access control
4. Propagates user identity to the MCP server

Prerequisites:
- MCP server deployed to AgentCore Runtime
- Interceptor Lambda function deployed
- Cognito User Pool configured
- Configuration in SSM Parameter Store

Usage:
    python create_gateway.py
"""

import json
import os
import sys
from typing import Any

import boto3

# Make the repo's utils/ importable (idp_config lives there) when this script
# runs from its own deployment subdir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.idp_config import assert_gateway_idp_matches, get_idp_provider


class SSMConfig:
    """Load configuration from SSM Parameter Store."""

    def __init__(self):
        """Initialize and load configuration from SSM."""
        # Get region from boto3 session
        session = boto3.Session()
        self.region = session.region_name

        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)

        # Get account ID
        self.account_id = self.sts.get_caller_identity()["Account"]

        print("✅ Using AWS configuration")
        print(f"   Region: {self.region}")
        print(f"   Account: {self.account_id}")

        # IdP selector — read once (DR-8 flag-branch convention).
        self.idp_provider = get_idp_provider(self.ssm)

        # Load configuration from SSM
        print("\n🔍 Loading configuration from SSM Parameter Store...")
        self.mcp_server_runtime_arn = self._get_parameter("/app/lakehouse-agent/mcp-server-runtime-arn")
        self.interceptor_lambda_arn = self._get_parameter("/app/lakehouse-agent/interceptor-lambda-arn")
        # RESPONSE-point interceptor Lambda (pure-delta A1, both paths): attached
        # alongside REQUEST so tools/list is filtered per persona, not just
        # call-time gated. Upstream ships + stores this ARN but leaves it unattached.
        self.response_interceptor_lambda_arn = self._get_parameter(
            "/app/lakehouse-agent/response-interceptor-lambda-arn"
        )

        if self.idp_provider == "cognito":
            # [COGNITO] user-pool authorizer + dedicated M2M provider (DR-17)
            self.cognito_user_pool_arn = self._get_parameter("/app/lakehouse-agent/cognito-user-pool-arn")
            self.cognito_app_client_id = self._get_parameter("/app/lakehouse-agent/cognito-app-client-id")
            self.cognito_domain = self._get_parameter("/app/lakehouse-agent/cognito-domain")

            # M2M client for the Gateway-to-Runtime leg — REQUIRED (DR-17). setup_cognito
            # (notebook 01) unconditionally creates the dedicated M2M client, so require it
            # (fail-fast via _get_parameter, which sys.exit(1)s on ParameterNotFound) rather
            # than silently falling back to the USER app client — symmetric with
            # 5b-obo-gateway-setup/04_create_obo_gateway.py and 4b-.../deploy_runtime.py.
            self.cognito_m2m_client_id = self._get_parameter("/app/lakehouse-agent/cognito-m2m-client-id")
            self.cognito_m2m_client_secret = self._get_parameter(
                "/app/lakehouse-agent/cognito-m2m-client-secret", secure=True
            )
            print(f"   ✅ M2M Client ID: {self.cognito_m2m_client_id}")
            print("   ✅ M2M Client Secret: ****** (loaded)")

            print(f"   ✅ MCP Server Runtime ARN: {self.mcp_server_runtime_arn}")
            print(f"   ✅ Interceptor Lambda ARN: {self.interceptor_lambda_arn}")
            print(f"   ✅ Cognito User Pool ARN: {self.cognito_user_pool_arn}")
            print(f"   ✅ Cognito App Client ID: {self.cognito_app_client_id}")
            print(f"   ✅ Cognito Domain: {self.cognito_domain}")
        else:  # okta
            # [OKTA] custom-auth-server authorizer + single-client provider (canonical §6)
            self.okta_org_url = self._get_parameter("/app/lakehouse-agent/okta-org-url")
            self.okta_auth_server_id = self._get_parameter("/app/lakehouse-agent/okta-auth-server-id")
            self.okta_app_client_id = self._get_parameter("/app/lakehouse-agent/okta-app-client-id")
            self.okta_app_client_secret = self._get_parameter(
                "/app/lakehouse-agent/okta-app-client-secret", secure=True
            )
            self.okta_resource_server_audience = self._get_parameter(
                "/app/lakehouse-agent/okta-resource-server-audience"
            )
            self.okta_discovery_url = self._get_parameter("/app/lakehouse-agent/okta-discovery-url")

            print(f"   ✅ MCP Server Runtime ARN: {self.mcp_server_runtime_arn}")
            print(f"   ✅ Interceptor Lambda ARN: {self.interceptor_lambda_arn}")
            print(f"   ✅ Response Interceptor Lambda ARN: {self.response_interceptor_lambda_arn}")
            print(f"   ✅ Okta Org URL: {self.okta_org_url}")
            print(f"   ✅ Okta Auth Server ID: {self.okta_auth_server_id}")
            print(f"   ✅ Okta App Client ID: {self.okta_app_client_id}")
            print("   ✅ Okta App Client Secret: ****** (loaded)")
            print(f"   ✅ Okta Resource Server Audience: {self.okta_resource_server_audience}")
            print(f"   ✅ Okta Discovery URL: {self.okta_discovery_url}")

    def _get_parameter(self, parameter_name: str, secure: bool = False) -> str:
        """Get parameter value from SSM Parameter Store."""
        try:
            response = self.ssm.get_parameter(Name=parameter_name, WithDecryption=secure)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            print(f"❌ SSM parameter {parameter_name} not found")
            print("   Please run the setup scripts first")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error retrieving parameter {parameter_name}: {e}")
            sys.exit(1)

    def store_gateway_parameters(self, gateway_id: str, gateway_arn: str, gateway_url: str, gateway_name: str):
        """Store Gateway information in SSM Parameter Store."""
        print("\n💾 Storing gateway configuration in SSM Parameter Store...")

        parameters = [
            {
                "name": "/app/lakehouse-agent/gateway-id",
                "value": gateway_id,
                "description": "AgentCore Gateway ID",
            },
            {
                "name": "/app/lakehouse-agent/gateway-arn",
                "value": gateway_arn,
                "description": "AgentCore Gateway ARN",
            },
            {
                "name": "/app/lakehouse-agent/gateway-url",
                "value": gateway_url,
                "description": "AgentCore Gateway URL",
            },
            {
                "name": "/app/lakehouse-agent/gateway-name",
                "value": gateway_name,
                "description": "AgentCore Gateway Name",
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


class GatewaySetup:
    def __init__(self, config: SSMConfig):
        """
        Initialize Gateway setup.

        Args:
            config: SSM configuration object
        """
        self.config = config
        self.client = boto3.client("bedrock-agentcore-control", region_name=config.region)

    def create_gateway_role(self, gateway_name: str) -> str:
        """
        Create IAM role for Gateway.

        Args:
            gateway_name: Name for the gateway

        Returns:
            Role ARN
        """
        iam = boto3.client("iam", region_name=self.config.region)

        role_name = f"agentcore-{gateway_name}-role"

        # Trust policy for AgentCore Gateway
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        # Policy document with all required permissions
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
                    "Resource": f"arn:aws:lambda:{self.config.region}:{self.config.account_id}:function:*",
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
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "OAuth2Credentials",
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:token-vault/default",
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:token-vault/*/oauth2credentialprovider/*",
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "SecretsManagerAccess",
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": f"arn:aws:secretsmanager:{self.config.region}:{self.config.account_id}:secret:*",
                },
            ],
        }

        try:
            print(f"🔑 Creating IAM role: {role_name}")
            response = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="IAM role for AgentCore Gateway",
                Tags=[
                    {"Key": "Application", "Value": "lakehouse-agent"},
                    {"Key": "Purpose", "Value": "claims-gateway-role"},
                ],
            )
            role_arn = response["Role"]["Arn"]
            print(f"✅ Created IAM role: {role_arn}")

            # Attach policy
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="GatewayExecutionPolicy",
                PolicyDocument=json.dumps(policy_document),
            )
            print("✅ Attached execution policy to role")

            return role_arn

        except iam.exceptions.EntityAlreadyExistsException:
            # In-place idempotent update (DR-8 Flag-3, both paths): re-assert the
            # script-owned trust + inline policy while preserving any out-of-band
            # attachments. No detach-all / delete-recreate / sleep.
            print(f"ℹ️  Role {role_name} already exists — updating in place (preserving out-of-band attachments)")

            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

            # Repair the trust policy in place (no delete).
            iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust_policy))

            # Upsert ONLY our known inline policy; put_role_policy overwrites in place.
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="GatewayExecutionPolicy",
                PolicyDocument=json.dumps(policy_document),
            )

            print(f"✅ Updated existing IAM role in place: {role_arn}")
            return role_arn

        except Exception as e:
            print(f"❌ Error creating role: {e}")
            raise

    def create_gateway(self, gateway_name: str = "lakehouse-gateway") -> dict[str, Any]:
        """
        Create an AgentCore Gateway with JWT authentication.

        Args:
            gateway_name: Name for the gateway

        Returns:
            Gateway creation response
        """
        try:
            print(f"\n🔧 Creating AgentCore Gateway: {gateway_name}")

            # Create IAM role for gateway
            role_arn = self.create_gateway_role(gateway_name)

            # JWT authorizer differs by IdP (DR-8): Cognito validates by client_id
            # (Cognito access tokens carry no 'aud'); Okta validates by audience.
            if self.config.idp_provider == "cognito":
                # [COGNITO] upstream verbatim
                user_pool_id = self.config.cognito_user_pool_arn.split("/")[-1]
                issuer = f"https://cognito-idp.{self.config.region}.amazonaws.com/{user_pool_id}"
                discovery_url = f"{issuer}/.well-known/openid-configuration"
                auth_config = {
                    "customJWTAuthorizer": {
                        "discoveryUrl": discovery_url,
                        "allowedClients": [self.config.cognito_app_client_id],
                        # Cognito access tokens carry no 'aud'; validate via client_id.
                    }
                }
            else:  # okta
                # [OKTA] custom-auth-server discovery + audience (canonical §6 names)
                auth_config = {
                    "customJWTAuthorizer": {
                        "discoveryUrl": self.config.okta_discovery_url,
                        "allowedAudience": [self.config.okta_resource_server_audience],
                    }
                }

            # Interceptor config (pure-delta A1, both paths): TWO entries — a REQUEST
            # interceptor (call-time 403 gate + identity extraction) and a RESPONSE
            # interceptor (filters tools/list per persona). RESPONSE must set
            # passRequestHeaders so it can resolve the caller's group from the bearer.
            interceptor_config = [
                {
                    "interceptor": {"lambda": {"arn": self.config.interceptor_lambda_arn}},
                    "interceptionPoints": ["REQUEST"],
                    "inputConfiguration": {"passRequestHeaders": True},
                },
                {
                    "interceptor": {"lambda": {"arn": self.config.response_interceptor_lambda_arn}},
                    "interceptionPoints": ["RESPONSE"],
                    "inputConfiguration": {"passRequestHeaders": True},
                },
            ]

            # Create gateway
            response = self.client.create_gateway(
                name=gateway_name,
                roleArn=role_arn,
                protocolType="MCP",
                protocolConfiguration={
                    "mcp": {
                        "supportedVersions": ["2025-03-26", "2025-06-18"],
                        "searchType": "SEMANTIC",
                    }
                },
                authorizerType="CUSTOM_JWT",
                authorizerConfiguration=auth_config,
                interceptorConfigurations=interceptor_config,
                description="Gateway for Lakehouse Data MCP Server with OAuth-based access control",
                tags={"Application": "lakehouse-agent", "Purpose": "claims-gateway"},
            )

            gateway_id = response["gatewayId"]
            gateway_url = response["gatewayUrl"]
            gateway_arn = (
                f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:gateway/{gateway_id}"
            )

            print("✅ Gateway created successfully!")
            print(f"   Gateway ID: {gateway_id}")
            print(f"   Gateway URL: {gateway_url}")
            print(f"   Gateway ARN: {gateway_arn}")

            return {
                "gatewayId": gateway_id,
                "gatewayUrl": gateway_url,
                "gatewayArn": gateway_arn,
                "gatewayName": gateway_name,
            }

        except Exception as e:
            if "already exists" in str(e):
                # In-place converge (DR-8 Flag-3, both paths): round-trip the LIVE
                # authorizer + protocol (update_gateway is a full-replace PUT — omitting
                # them would drop auth / reset SEMANTIC search) and re-apply the
                # REQUEST+RESPONSE interceptor config so re-runs attach RESPONSE.
                print(f"ℹ️  Gateway {gateway_name} already exists — updating in place to converge interceptors...")
                response = self.client.list_gateways()
                for gateway in response.get("items", []):
                    if gateway["name"] == gateway_name:
                        gateway_id = gateway["gatewayId"]
                        live = self.client.get_gateway(gatewayIdentifier=gateway_id)
                        # DR-11 pre-flight: refuse to converge a gateway deployed
                        # for the other IdP (flag-switch without teardown).
                        assert_gateway_idp_matches(live, self.config.idp_provider, gateway_name)
                        gateway_url = live["gatewayUrl"]
                        gateway_arn = f"arn:aws:bedrock-agentcore:{self.config.region}:{self.config.account_id}:gateway/{gateway_id}"

                        update_kwargs = {
                            "gatewayIdentifier": gateway_id,
                            "name": live["name"],
                            "roleArn": live["roleArn"],
                            "protocolType": live["protocolType"],
                            "protocolConfiguration": live["protocolConfiguration"],
                            "authorizerType": "CUSTOM_JWT",
                            "authorizerConfiguration": live["authorizerConfiguration"],
                            "interceptorConfigurations": interceptor_config,
                        }
                        if live.get("description"):
                            update_kwargs["description"] = live["description"]

                        print(f"   ↻ update_gateway {gateway_id} (adding REQUEST+RESPONSE interceptors)")
                        self.client.update_gateway(**update_kwargs)

                        if not self.wait_for_gateway_active(gateway_id):
                            raise RuntimeError(f"Gateway {gateway_id} did not return to READY after update_gateway")

                        print(f"✅ Updated existing gateway in place: {gateway_id}")
                        return {
                            "gatewayId": gateway_id,
                            "gatewayUrl": gateway_url,
                            "gatewayArn": gateway_arn,
                            "gatewayName": gateway_name,
                        }
            print(f"❌ Error creating gateway: {e!s}")
            raise

    def create_oauth_provider(
        self,
        provider_name: str,
        client_id: str,
        client_secret: str,
        cognito_token_endpoint: str | None = None,
        cognito_issuer: str | None = None,
    ) -> str:
        """
        Create an OAuth2 credential provider in AgentCore Identity for Cognito.

        This provider is used by the Gateway to authenticate to the MCP server on Runtime.
        The Gateway uses client_credentials flow (M2M) to obtain tokens from Cognito,
        then includes those tokens when invoking the MCP server.

        Authentication Flow:
        1. User → Gateway: User's JWT token (from Cognito user authentication)
        2. Gateway validates user's token with JWT authorizer
        3. Gateway → Cognito: Request M2M token using client_credentials
        4. Cognito → Gateway: M2M access token
        5. Gateway → MCP Runtime: MCP request with M2M token in Authorization header
        6. MCP Runtime validates M2M token with its JWT authorizer
        7. MCP Runtime → Gateway: MCP response

        Args:
            provider_name: Name for the OAuth provider
            cognito_client_id: Cognito App Client ID (M2M client preferred)
            cognito_client_secret: Cognito App Client Secret
            cognito_token_endpoint: Cognito token endpoint URL
            cognito_issuer: Cognito issuer URL

        Returns:
            OAuth provider ARN
        """
        try:
            print(f"\n🔐 Creating OAuth2 credential provider: {provider_name}")

            # Provider config differs by IdP (DR-8): Cognito has no OIDC discovery
            # endpoint for its token endpoint → pass authorization-server metadata
            # directly; Okta exposes /.well-known on its custom auth server → use
            # the discoveryUrl form.
            if self.config.idp_provider == "cognito":
                # [COGNITO] upstream verbatim
                oauth2_config = {
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
                }
            else:  # okta
                # [OKTA] discovery-URL form (canonical §6 okta-discovery-url)
                oauth2_config = {
                    "customOauth2ProviderConfig": {
                        "oauthDiscovery": {"discoveryUrl": self.config.okta_discovery_url},
                        "clientId": client_id,
                        "clientSecret": client_secret,
                    }
                }

            response = self.client.create_oauth2_credential_provider(
                name=provider_name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput=oauth2_config,
                tags={"Application": "lakehouse-agent", "Purpose": "claims-mcp-oauth-provider"},
            )

            # Debug: print response to see actual structure
            print(f"   Debug - Response keys: {list(response.keys())}")

            # Try different possible key names
            if "oauth2CredentialProviderArn" in response:
                provider_arn = response["oauth2CredentialProviderArn"]
            elif "arn" in response:
                provider_arn = response["arn"]
            elif "credentialProviderArn" in response:
                provider_arn = response["credentialProviderArn"]
            else:
                print(f"   Debug - Full response: {response}")
                raise KeyError(f"Could not find ARN in response. Available keys: {list(response.keys())}")

            print(f"✅ OAuth2 provider created: {provider_arn}")  # codeql[py/clear-text-logging-sensitive-data]
            return provider_arn

        except Exception as e:
            if "already exists" in str(e).lower() or "AlreadyExistsException" in str(e):
                print(f"ℹ️  OAuth2 provider {provider_name} already exists, retrieving ARN...")
                try:
                    # List providers and find the one with matching name
                    response = self.client.list_oauth2_credential_providers()
                    print(f"   Debug - List response keys: {list(response.keys())}")

                    # Try different possible key names for the list
                    providers = response.get(
                        "oauth2CredentialProviders",
                        response.get("credentialProviders", response.get("items", [])),
                    )

                    for provider in providers:
                        if provider.get("name") == provider_name:
                            # Try different possible ARN key names
                            provider_arn = (
                                provider.get("oauth2CredentialProviderArn")
                                or provider.get("arn")
                                or provider.get("credentialProviderArn")
                            )
                            if provider_arn:
                                print(
                                    f"✅ Using existing provider: {provider_arn}"
                                )  # codeql[py/clear-text-logging-sensitive-data]
                                return provider_arn

                    print(f"   ⚠️  Provider {provider_name} not found in list")
                except Exception as list_error:
                    print(f"❌ Error listing providers: {list_error}")

            print(f"❌ Error creating OAuth2 provider: {e}")
            raise

    def create_gateway_target(
        self,
        gateway_id: str,
        target_name: str,
        mcp_server_url: str,
        oauth_provider_arn: str,
        scopes: list | None = None,
    ) -> dict[str, Any]:
        """
        Create a gateway target pointing to the MCP server runtime with OAuth authentication.

        Args:
            gateway_id: Gateway ID
            target_name: Name for the target
            mcp_server_url: URL of the MCP server runtime
            oauth_provider_arn: ARN of the OAuth credential provider

        Returns:
            Target creation response
        """
        try:
            print(f"\n🎯 Creating gateway target: {target_name}")
            print(f"   MCP Server URL: {mcp_server_url}")
            print("   Authentication: OAuth2 Client Credentials")
            print(f"   Provider ARN: {oauth_provider_arn}")  # codeql[py/clear-text-logging-sensitive-data]

            response = self.client.create_gateway_target(
                name=target_name,
                gatewayIdentifier=gateway_id,
                targetConfiguration={"mcp": {"mcpServer": {"endpoint": mcp_server_url}}},
                credentialProviderConfigurations=[
                    {
                        "credentialProviderType": "OAUTH",
                        "credentialProvider": {
                            "oauthCredentialProvider": {
                                "providerArn": oauth_provider_arn,
                                # Cognito: [] (client_credentials); Okta: non-empty
                                # scope required by its auth server (DR-8).
                                "scopes": scopes or [],
                            }
                        },
                    }
                ],
            )

            print("✅ Gateway target created successfully with OAuth2 authentication!")
            return response

        except Exception as e:
            if "already exists" in str(e):
                print(f"ℹ️  Target {target_name} already exists")
                return {}
            print(f"❌ Error creating gateway target: {e!s}")
            raise

    def wait_for_gateway_active(self, gateway_id: str, max_wait_seconds: int = 300) -> bool:
        """
        Wait for gateway to be in ACTIVE or READY status.

        Args:
            gateway_id: Gateway ID
            max_wait_seconds: Maximum time to wait in seconds

        Returns:
            True if gateway is active/ready, False if timeout
        """
        import time

        print("\n⏳ Checking gateway status...")
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            try:
                response = self.client.get_gateway(gatewayIdentifier=gateway_id)
                status = response.get("status", "UNKNOWN").strip().upper()

                print(f"   Status: {status}")

                if status in ["ACTIVE", "READY"]:
                    print(f"✅ Gateway is ready (status: {status})!")
                    return True
                elif status in ["FAILED", "DELETING", "DELETED", "UPDATE_UNSUCCESSFUL"]:
                    print(f"❌ Gateway is in {status} status")
                    return False

                print("   Waiting for gateway to be ready...")
                time.sleep(10)

            except Exception as e:
                print(f"⚠️  Error checking gateway status: {e}")
                time.sleep(10)

        print("⚠️  Timeout waiting for gateway to be active")
        return False


def get_runtime_mcp_url(runtime_arn: str, region: str) -> str:
    """
    Get the MCP endpoint URL for an AgentCore Runtime.

    For MCP servers deployed on AgentCore Runtime, the endpoint is:
    https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded-arn}/invocations?qualifier=DEFAULT

    Args:
        runtime_arn: Runtime ARN
        region: AWS region

    Returns:
        MCP endpoint URL
    """
    try:
        # Encode the runtime ARN (replace : with %3A and / with %2F)
        encoded_arn = runtime_arn.replace(":", "%3A").replace("/", "%2F")

        # Construct the MCP endpoint URL
        mcp_url = (
            f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
        )

        print(f"✅ MCP Endpoint URL: {mcp_url}")
        return mcp_url

    except Exception as e:
        print(f"❌ Error constructing MCP URL: {e}")
        return ""


def main():
    """Main gateway creation function."""
    print("=" * 70)
    print("AgentCore Gateway Setup")
    print("=" * 70)

    # Load configuration from SSM
    config = SSMConfig()

    # Create gateway setup instance
    setup = GatewaySetup(config)

    # Gateway name
    gateway_name = "lakehouse-gateway"

    print("\n📋 Configuration:")
    print(f"   Gateway Name: {gateway_name}")
    print(f"   MCP Server: {config.mcp_server_runtime_arn}")
    print(f"   Interceptor: {config.interceptor_lambda_arn}")
    if config.idp_provider == "cognito":
        print(f"   Cognito User Pool: {config.cognito_user_pool_arn}")
        print(f"   Client ID: {config.cognito_app_client_id}")
    else:  # okta
        print(f"   Okta Discovery URL: {config.okta_discovery_url}")
        print(f"   Okta App Client ID: {config.okta_app_client_id}")

    try:
        # Create gateway
        gateway_response = setup.create_gateway(gateway_name=gateway_name)

        # Wait for gateway to be active before creating target
        if setup.wait_for_gateway_active(gateway_response["gatewayId"]):
            # Get MCP endpoint URL for the runtime
            mcp_url = get_runtime_mcp_url(config.mcp_server_runtime_arn, config.region)

            # OAuth2 provider for the Gateway→MCP-runtime M2M leg differs by IdP
            # (DR-8): Cognito needs a manual auth-server-metadata envelope + the
            # dedicated M2M client (DR-17); Okta uses discovery + its single app client.
            # Target scopes: Cognito [] (client_credentials); Okta ['claims.query'].
            if config.idp_provider == "cognito":
                # [COGNITO] upstream verbatim
                user_pool_id = config.cognito_user_pool_arn.split("/")[-1]
                cognito_issuer = f"https://cognito-idp.{config.region}.amazonaws.com/{user_pool_id}"
                cognito_token_endpoint = f"{config.cognito_domain}/oauth2/token"

                # M2M client is REQUIRED (DR-17) — the user-app-client hybrid fallback
                # (provider "lakehouse-mcp-oauth-provider") has been removed; always use
                # the dedicated M2M provider, symmetric with GW2.
                print("\n🔐 Using M2M client for Gateway-to-Runtime authentication")
                auth_client_id = config.cognito_m2m_client_id
                auth_client_secret = config.cognito_m2m_client_secret
                provider_name = "lakehouse-mcp-m2m-oauth-provider"

                oauth_provider_arn = setup.create_oauth_provider(
                    provider_name=provider_name,
                    client_id=auth_client_id,
                    client_secret=auth_client_secret,
                    cognito_token_endpoint=cognito_token_endpoint,
                    cognito_issuer=cognito_issuer,
                )
                target_scopes = []
            else:  # okta
                # [OKTA] single Okta app client handles client_credentials directly
                # (canonical §6 provider name)
                print("\n🔐 Using Okta app client for Gateway-to-Runtime authentication")
                provider_name = "lakehouse-mcp-okta-oauth-provider"
                oauth_provider_arn = setup.create_oauth_provider(
                    provider_name=provider_name,
                    client_id=config.okta_app_client_id,
                    client_secret=config.okta_app_client_secret,
                )
                target_scopes = ["claims.query"]

            # Create gateway target with OAuth authentication
            if mcp_url and oauth_provider_arn:
                setup.create_gateway_target(
                    gateway_id=gateway_response["gatewayId"],
                    target_name="lakehouse-mcp-target",
                    mcp_server_url=mcp_url,
                    oauth_provider_arn=oauth_provider_arn,
                    scopes=target_scopes,
                )
        else:
            print("\n⚠️  Gateway not active yet. You can create the target later.")

        # Store gateway configuration in SSM
        config.store_gateway_parameters(
            gateway_response["gatewayId"],
            gateway_response["gatewayArn"],
            gateway_response["gatewayUrl"],
            gateway_response["gatewayName"],
        )

        print("\n" + "=" * 70)
        print("Gateway Setup Complete!")
        print("=" * 70)

        print("\n✅ Gateway configuration stored in SSM Parameter Store:")
        print("   /app/lakehouse-agent/gateway-id")
        print("   /app/lakehouse-agent/gateway-arn")
        print("   /app/lakehouse-agent/gateway-url")
        print("   /app/lakehouse-agent/gateway-name")

        print("\n📋 Next Steps:")
        print("   1. Deploy the Lakehouse Agent (Step 8)")
        print("   2. Test the system end-to-end")

        print("\n" + "=" * 70)

    except Exception as e:
        print(f"\n❌ Gateway creation failed: {e!s}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
