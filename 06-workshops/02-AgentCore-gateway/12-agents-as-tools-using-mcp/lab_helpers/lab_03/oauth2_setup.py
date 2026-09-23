"""
Lab 03: OAuth2 Credential Provider and M2M Authentication Setup

Sets up machine-to-machine (M2M) authentication between Gateway and Runtime
using OAuth2 client credentials grant with Cognito.

Architecture:
- Gateway uses M2M client credentials to obtain access tokens from Cognito
- M2M tokens contain custom scopes for fine-grained authorization
- Runtime validates M2M tokens and only allows operations within authorized scopes
- OAuth2 credential provider manages credential storage in AWS Secrets Manager

Based on: gateway-to-runtime/07_connect_gateway_to_runtime.py
"""

import json
import boto3
import time
from typing import Dict, Optional
from botocore.exceptions import ClientError

from lab_helpers.config import AWS_REGION, AWS_PROFILE
from lab_helpers.parameter_store import get_parameter, put_parameter
from lab_helpers.constants import PARAMETER_PATHS


class OAuth2CredentialProviderSetup:
    """Manages OAuth2 credential provider for M2M authentication"""

    def __init__(self, region: str = AWS_REGION, profile: str = AWS_PROFILE):
        """Initialize OAuth2 setup helper"""
        self.session = boto3.Session(profile_name=profile, region_name=region)
        self.agentcore = self.session.client("bedrock-agentcore-control", region_name=region)
        self.iam = self.session.client("iam", region_name=region)
        self.ssm = self.session.client("ssm", region_name=region)
        self.sts = self.session.client("sts", region_name=region)

        self.region = region
        self.account_id = self.sts.get_caller_identity()["Account"]
        self.prefix = "aiml301"

    def create_oauth2_credential_provider(self) -> Dict:
        """
        Create OAuth2 credential provider for M2M authentication

        This provider manages M2M client credentials and enables the Gateway
        to authenticate with the Runtime using client credentials grant.

        Returns:
            Dict with provider_arn, secret_arn, and configuration
        """
        print("\n" + "=" * 70)
        print("CREATING OAUTH2 CREDENTIAL PROVIDER")
        print("=" * 70 + "\n")

        # Get M2M credentials from Cognito config (set up in Lab-01)
        try:
            m2m_client_id = get_parameter(PARAMETER_PATHS["cognito"]["m2m_client_id"])
            m2m_client_secret = get_parameter(PARAMETER_PATHS["cognito"]["m2m_client_secret"])
            user_pool_id = get_parameter(PARAMETER_PATHS["cognito"]["user_pool_id"])
        except Exception as e:
            print(f"❌ Failed to retrieve Cognito M2M credentials from SSM: {e}")
            print("   Ensure Lab-01 Cognito setup has been completed first")
            raise

        print("✅ Retrieved M2M credentials from Cognito")
        print(f"   - M2M Client ID: {m2m_client_id}")
        print("   - M2M Client Secret: ****")
        print(f"   - User Pool ID: {user_pool_id}")

        # Build discovery URL for OAuth2 discovery endpoint
        # This tells AgentCore where to find the Cognito OIDC configuration
        discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
        )

        provider_name = f"{self.prefix}-runtime-m2m-credentials"

        print(f"\nCreating OAuth2 credential provider: {provider_name}")
        print(f"Discovery URL: {discovery_url}\n")

        try:
            # Create OAuth2 credential provider
            # AgentCore will automatically:
            # 1. Store credentials in AWS Secrets Manager
            # 2. Manage credential rotation (if needed)
            # 3. Generate tokens using client credentials grant
            response = self.agentcore.create_oauth2_credential_provider(
                name=provider_name,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput={
                    "customOauth2ProviderConfig": {
                        "oauthDiscovery": {"discoveryUrl": discovery_url},
                        "clientId": m2m_client_id,
                        "clientSecret": m2m_client_secret,
                    }
                },
            )

            provider_arn = response["oAuth2CredentialProviderArn"]
            secret_arn = response.get("secretArn", "")

            print("✅ OAuth2 credential provider created")
            print(f"   - Provider ARN: {provider_arn}")  # codeql[py/clear-text-logging-sensitive-data]
            print(f"   - Secret ARN: {secret_arn}")  # codeql[py/clear-text-logging-sensitive-data]

            # Store configuration
            oauth2_config = {
                "provider_name": provider_name,
                "provider_arn": provider_arn,
                "secret_arn": secret_arn,
                "discovery_url": discovery_url,
                "m2m_client_id": m2m_client_id,
                "region": self.region,
                "account_id": self.account_id,
            }

            # Save to SSM
            put_parameter(f"/{self.prefix}/lab-03/oauth2-provider-arn", provider_arn)
            put_parameter(f"/{self.prefix}/lab-03/oauth2-secret-arn", secret_arn)
            put_parameter(f"/{self.prefix}/lab-03/oauth2-config", json.dumps(oauth2_config))

            print("\n✅ OAuth2 configuration saved to SSM Parameter Store")

            return oauth2_config

        except Exception as e:
            print(f"❌ Failed to create OAuth2 credential provider: {e}")
            raise

    def add_runtime_as_oauth2_target(
        self,
        gateway_id: str,
        runtime_arn: str,
        oauth2_provider_arn: Optional[str] = None,
    ) -> Dict:
        """
        Add Runtime as Gateway target with OAuth2 M2M authentication

        When Gateway receives a request to invoke Runtime, it will:
        1. Use the OAuth2 provider to get an M2M access token
        2. Include token in request: Authorization: Bearer {M2M_token}
        3. Runtime validates token and authorizes operation based on scopes

        Args:
            gateway_id: Gateway identifier
            runtime_arn: Runtime ARN to register as target
            oauth2_provider_arn: OAuth2 provider ARN (fetches from SSM if not provided)

        Returns:
            Dict with target configuration
        """
        print("\n" + "=" * 70)
        print("ADDING RUNTIME AS GATEWAY TARGET WITH OAUTH2")
        print("=" * 70 + "\n")

        # Get OAuth2 provider ARN if not provided
        if not oauth2_provider_arn:
            try:
                oauth2_provider_arn = get_parameter(f"/{self.prefix}/lab-03/oauth2-provider-arn")
                print(
                    f"✅ Retrieved OAuth2 provider ARN from SSM: {oauth2_provider_arn}"
                )  # codeql[py/clear-text-logging-sensitive-data]
            except Exception as e:
                print(f"❌ OAuth2 provider ARN not found in SSM: {e}")
                print("   Ensure OAuth2 credential provider has been created first")
                raise

        # Get resource server identifier for scopes
        try:
            resource_server_id = get_parameter(PARAMETER_PATHS["cognito"]["resource_server_identifier"])
        except Exception as e:
            print(f"❌ Failed to retrieve resource server identifier: {e}")
            raise

        # Define M2M scopes
        # These scopes will be included in M2M tokens and validated by Runtime
        scopes = [
            f"{resource_server_id}/mcp.invoke",
            f"{resource_server_id}/runtime.access",
        ]

        target_name = f"{self.prefix}-runtime-m2m-target"

        print("Creating Gateway target with OAuth2 M2M authentication:")
        print(f"  - Gateway ID: {gateway_id}")
        print(f"  - Runtime ARN: {runtime_arn}")
        print(f"  - Target Name: {target_name}")
        print(f"  - Scopes: {', '.join(scopes)}\n")

        try:
            # Create gateway target with OAuth2 credential provider
            response = self.agentcore.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=target_name,
                targetConfiguration={"mcp": {"mcpServer": {"runtimeArn": runtime_arn}}},
                credentialProviderConfigurations=[
                    {
                        "credentialProviderType": "OAUTH",
                        "credentialProvider": {
                            "oauthCredentialProvider": {
                                "providerArn": oauth2_provider_arn,
                                "scopes": scopes,
                            }
                        },
                    }
                ],
            )

            target_id = response["targetId"]

            print("✅ Runtime added as Gateway target with OAuth2 M2M auth")
            print(f"   - Target ID: {target_id}")
            print(f"   - Target Name: {target_name}")

            target_config = {
                "target_id": target_id,
                "target_name": target_name,
                "gateway_id": gateway_id,
                "runtime_arn": runtime_arn,
                "oauth2_provider_arn": oauth2_provider_arn,
                "scopes": scopes,
                "credential_type": "OAUTH",
            }

            # Save to SSM
            put_parameter(f"/{self.prefix}/lab-03/gateway-m2m-target", json.dumps(target_config))

            print("\n✅ Gateway M2M target configuration saved to SSM Parameter Store")

            return target_config

        except Exception as e:
            print(f"❌ Failed to add Runtime as Gateway target: {e}")
            raise

    def update_gateway_oauth2_permissions(self, gateway_role_arn: Optional[str] = None) -> None:
        """
        Update Gateway IAM role with permissions to access OAuth2 credentials

        Gateway role needs:
        - bedrock-agentcore:GetResourceOauth2Token
        - secretsmanager:GetSecretValue

        Args:
            gateway_role_arn: Gateway role ARN (fetches from SSM if not provided)
        """
        print("\n" + "=" * 70)
        print("UPDATING GATEWAY IAM ROLE WITH OAUTH2 PERMISSIONS")
        print("=" * 70 + "\n")

        # Get OAuth2 secret ARN
        try:
            secret_arn = get_parameter(f"/{self.prefix}/lab-03/oauth2-secret-arn")
            provider_arn = get_parameter(f"/{self.prefix}/lab-03/oauth2-provider-arn")
        except Exception as e:
            print(f"❌ Failed to retrieve OAuth2 configuration: {e}")
            raise

        # Get gateway role ARN if not provided
        if not gateway_role_arn:
            try:
                # Try to get from existing parameter first
                response = self.ssm.get_parameter(Name=f"/{self.prefix}/lab-03/gateway-role-arn")
                gateway_role_arn = response["Parameter"]["Value"]
                print(f"✅ Retrieved Gateway role ARN from SSM: {gateway_role_arn}")
            except ClientError:
                print("❌ Gateway role ARN not found in SSM")
                raise

        # Extract role name from ARN
        # ARN format: arn:aws:iam::ACCOUNT:role/ROLE_NAME
        role_name = gateway_role_arn.split("/")[-1]

        print(f"Updating IAM role: {role_name}\n")

        # Define OAuth2 permissions policy
        oauth2_permissions = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "GetResourceOauth2Token",
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                    "Resource": [provider_arn],
                },
                {
                    "Sid": "AccessSecretsManager",
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": [secret_arn],
                },
            ],
        }

        try:
            self.iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{self.prefix}-oauth2-credentials-policy",
                PolicyDocument=json.dumps(oauth2_permissions),
            )

            print("✅ OAuth2 permissions attached to Gateway role")
            print(f"   - GetResourceOauth2Token: {provider_arn}")
            print(f"   - GetSecretValue: {secret_arn}")  # codeql[py/clear-text-logging-sensitive-data]

        except Exception as e:
            print(f"❌ Failed to update Gateway role permissions: {e}")
            raise

    def setup_m2m_authentication_complete(self, gateway_id: str, runtime_arn: str, gateway_role_arn: str) -> Dict:
        """
        Complete setup workflow for M2M authentication

        Steps:
        1. Create OAuth2 credential provider
        2. Add Runtime as Gateway target with OAuth2
        3. Update Gateway IAM role with OAuth2 permissions

        Args:
            gateway_id: Gateway identifier
            runtime_arn: Runtime ARN
            gateway_role_arn: Gateway IAM role ARN

        Returns:
            Complete M2M authentication configuration
        """
        print("\n" + "=" * 70)
        print("SETTING UP M2M AUTHENTICATION (GATEWAY ↔ RUNTIME)")
        print("=" * 70 + "\n")

        print("Configuration:")
        print(f"  Gateway ID: {gateway_id}")
        print(f"  Runtime ARN: {runtime_arn}")
        print(f"  Gateway Role: {gateway_role_arn}\n")

        # Step 1: Create OAuth2 credential provider
        oauth2_config = self.create_oauth2_credential_provider()
        time.sleep(5)  # Wait for provider to be ready

        # Step 2: Add Runtime as Gateway target with OAuth2
        target_config = self.add_runtime_as_oauth2_target(
            gateway_id=gateway_id,
            runtime_arn=runtime_arn,
            oauth2_provider_arn=oauth2_config["provider_arn"],
        )

        # Step 3: Update Gateway IAM role with OAuth2 permissions
        self.update_gateway_oauth2_permissions(gateway_role_arn=gateway_role_arn)

        complete_config = {
            "oauth2_provider": oauth2_config,
            "gateway_target": target_config,
            "gateway_id": gateway_id,
            "runtime_arn": runtime_arn,
            "gateway_role_arn": gateway_role_arn,
        }

        # Save complete configuration
        put_parameter(
            f"/{self.prefix}/lab-03/m2m-auth-complete-config",
            json.dumps(complete_config, indent=2),
        )

        print("\n" + "=" * 70)
        print("✅ M2M AUTHENTICATION SETUP COMPLETE")
        print("=" * 70 + "\n")

        print("Gateway-to-Runtime M2M Flow:")
        print("  1. Client sends request to Gateway with User JWT")
        print("  2. Gateway validates User JWT")
        print("  3. Gateway uses OAuth2 provider to get M2M token from Cognito")
        print("  4. Gateway calls Runtime with M2M Bearer token")
        print("  5. Runtime validates M2M token and authorizes operation")
        print(f"\nM2M Scopes: {', '.join(target_config['scopes'])}")  # codeql[py/clear-text-logging-sensitive-data]
        print("\nAll configuration saved to SSM Parameter Store")

        return complete_config

    def cleanup_oauth2_resources(self) -> None:
        """Clean up OAuth2 credential provider and related resources"""
        print("\nCleaning up OAuth2 resources...")

        try:
            # Get provider ARN from SSM
            provider_arn = get_parameter(f"/{self.prefix}/lab-03/oauth2-provider-arn")

            # Delete OAuth2 credential provider
            provider_id = provider_arn.split("/")[-1]
            self.agentcore.delete_oauth2_credential_provider(oAuth2CredentialProviderId=provider_id)
            print("✅ Deleted OAuth2 credential provider")

        except Exception as e:
            print(f"⚠️  Could not delete OAuth2 provider: {e}")

        # Delete SSM parameters
        ssm_params = [
            f"/{self.prefix}/lab-03/oauth2-provider-arn",
            f"/{self.prefix}/lab-03/oauth2-secret-arn",
            f"/{self.prefix}/lab-03/oauth2-config",
            f"/{self.prefix}/lab-03/gateway-m2m-target",
            f"/{self.prefix}/lab-03/m2m-auth-complete-config",
        ]

        for param in ssm_params:
            try:
                self.ssm.delete_parameter(Name=param)
            except:  # noqa: E722
                pass

        print("✅ OAuth2 cleanup complete")
