"""
Cognito setup helper for AIML301 Workshop
Sets up authentication infrastructure for Labs 3-5

Authentication Flow:
- Lab 3: Gateway with Cognito JWT auth for end users
- Lab 3-5: Gateway-to-Runtime M2M authentication using client credentials
- Labs 4+: Optional user-based access control for advanced use cases

Cognito Resources Created:
1. User Pool: aiml301-UserPool
2. User Auth Client: aiml301-UserAuthClient (public, for end-user authentication)
3. M2M Client: aiml301-M2MClient (confidential, for service-to-service auth)
4. Resource Server: aiml301-agentcore-runtime (with custom scopes)
5. User Pool Domain: aiml301-agentcore-{timestamp}
6. Test User: testuser@aiml301.example.com
"""

import json
import time
import boto3
from typing import Dict, Any, Optional
from lab_helpers.config import AWS_REGION, AWS_PROFILE
from lab_helpers.parameter_store import put_parameter, get_parameter, delete_parameter
from lab_helpers.constants import PARAMETER_PATHS


class CognitoSetup:
    """Manages Cognito user pool setup and configuration"""

    def __init__(self, region: str = AWS_REGION, profile: str = AWS_PROFILE):
        """Initialize Cognito client and session"""
        self.session = boto3.Session(profile_name=profile, region_name=region)
        self.cognito = self.session.client("cognito-idp", region_name=region)
        self.region = region
        self.prefix = "aiml301"
        self.test_user_email = f"testuser@{self.prefix}.example.com"
        self.test_user_password = "<enter password>"  # Meets policy: uppercase, lowercase, numbers, symbols

    def create_user_pool(self) -> str:
        """
        Create Cognito User Pool with security best practices
        Returns: User Pool ID
        """
        user_pool_name = f"{self.prefix}-UserPool"

        print(f"Creating User Pool: {user_pool_name}...")

        try:
            response = self.cognito.create_user_pool(
                PoolName=user_pool_name,
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": 8,
                        "RequireUppercase": True,
                        "RequireLowercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": True,
                        "TemporaryPasswordValidityDays": 7,
                    }
                },
                # Auto-verify email on signup
                AutoVerifiedAttributes=["email"],
                # Email-based username (case insensitive)
                UsernameAttributes=["email"],
                EmailConfiguration={"EmailSendingAccount": "COGNITO_DEFAULT"},
                MfaConfiguration="OFF",  # Disabled for workshop simplicity
                AccountRecoverySetting={"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]},
            )

            user_pool_id = response["UserPool"]["Id"]
            user_pool_arn = response["UserPool"]["Arn"]

            print(f"✅ User Pool created: {user_pool_id}")

            return user_pool_id, user_pool_arn

        except self.cognito.exceptions.UserPoolTaggingException as e:
            print(f"❌ Error creating user pool: {e}")
            raise

    def create_resource_server(self, user_pool_id: str) -> str:
        """
        Create Resource Server with custom scopes for fine-grained authorization

        Scopes:
        - mcp.invoke: Permission to invoke MCP server tools
        - runtime.access: Permission to access AgentCore Runtime

        Returns: Resource Server Identifier
        """
        resource_server_id = f"{self.prefix}-agentcore-runtime"
        resource_server_name = f"{self.prefix} AgentCore Runtime API"

        print(f"Creating Resource Server: {resource_server_id}...")

        try:
            response = self.cognito.create_resource_server(  # noqa: F841
                UserPoolId=user_pool_id,
                Identifier=resource_server_id,
                Name=resource_server_name,
                Scopes=[
                    {
                        "ScopeName": "mcp.invoke",
                        "ScopeDescription": "Permission to invoke MCP server tools",
                    },
                    {
                        "ScopeName": "runtime.access",
                        "ScopeDescription": "Permission to access AgentCore Runtime",
                    },
                ],
            )

            print(f"✅ Resource Server created: {resource_server_id}")
            return resource_server_id

        except Exception as e:
            print(f"❌ Error creating resource server: {e}")
            raise

    def create_user_auth_client(self, user_pool_id: str) -> str:
        """
        Create User Auth Client (PUBLIC)
        For end-user authentication with username/password

        Returns: Client ID
        """
        client_name = f"{self.prefix}-UserAuthClient"

        print(f"Creating User Auth Client: {client_name}...")

        try:
            response = self.cognito.create_user_pool_client(
                UserPoolId=user_pool_id,
                ClientName=client_name,
                GenerateSecret=False,  # Public client - no secret
                RefreshTokenValidity=30,
                AccessTokenValidity=60,
                IdTokenValidity=60,
                TokenValidityUnits={
                    "AccessToken": "minutes",
                    "IdToken": "minutes",
                    "RefreshToken": "days",
                },
                ExplicitAuthFlows=[
                    "ALLOW_USER_PASSWORD_AUTH",
                    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
                    "ALLOW_REFRESH_TOKEN_AUTH",
                    "ALLOW_USER_SRP_AUTH",
                ],
                PreventUserExistenceErrors="ENABLED",
                EnableTokenRevocation=True,
                EnablePropagateAdditionalUserContextData=False,
            )

            client_id = response["UserPoolClient"]["ClientId"]
            print(f"✅ User Auth Client created: {client_id}")

            return client_id

        except Exception as e:
            print(f"❌ Error creating user auth client: {e}")
            raise

    def create_m2m_client(self, user_pool_id: str, resource_server_id: str) -> tuple:
        """
        Create M2M Client (CONFIDENTIAL)
        For service-to-service authentication using client credentials grant

        Returns: (Client ID, Client Secret)
        """
        client_name = f"{self.prefix}-M2MClient"

        print(f"Creating M2M Client: {client_name}...")

        try:
            response = self.cognito.create_user_pool_client(
                UserPoolId=user_pool_id,
                ClientName=client_name,
                GenerateSecret=True,  # Confidential client - requires secret
                RefreshTokenValidity=30,
                AccessTokenValidity=60,
                TokenValidityUnits={"AccessToken": "minutes", "RefreshToken": "days"},
                ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
                AllowedOAuthFlows=["client_credentials"],
                AllowedOAuthFlowsUserPoolClient=True,
                AllowedOAuthScopes=[
                    f"{resource_server_id}/mcp.invoke",
                    f"{resource_server_id}/runtime.access",
                ],
                EnableTokenRevocation=True,
                EnablePropagateAdditionalUserContextData=True,
            )

            client_id = response["UserPoolClient"]["ClientId"]
            client_secret = response["UserPoolClient"]["ClientSecret"]

            print(f"✅ M2M Client created: {client_id}")
            print("   ⚠️  Client secret: ****")
            print("   ⚠️  Store client secret securely (AWS Secrets Manager recommended)")

            return client_id, client_secret

        except Exception as e:
            print(f"❌ Error creating M2M client: {e}")
            raise

    def create_user_pool_domain(self, user_pool_id: str) -> str:
        """
        Create User Pool Domain for OAuth2 token endpoint

        Returns: Domain URL
        """
        # Generate unique domain prefix using timestamp
        timestamp = str(int(time.time()))
        domain_prefix = f"{self.prefix}-agentcore-{timestamp}"

        print(f"Creating User Pool Domain: {domain_prefix}...")

        try:
            response = self.cognito.create_user_pool_domain(  # noqa: F841
                Domain=domain_prefix, UserPoolId=user_pool_id
            )

            # Construct full domain URL
            domain_url = f"https://{domain_prefix}.auth.{self.region}.amazoncognito.com"
            print(f"✅ User Pool Domain created: {domain_url}")

            return domain_url

        except Exception as e:
            print(f"❌ Error creating user pool domain: {e}")
            raise

    def create_groups(self, user_pool_id: str) -> None:
        """
        Create Cognito groups for role-based access control.

        Groups:
        - sre: SRE users who create remediation plans (Precedence 10)
        - approvers: Users who approve and execute plans (Precedence 5)

        Lower precedence number = higher priority
        """
        groups = [
            {
                "GroupName": "sre",
                "Description": "SRE users who create remediation plans",
                "Precedence": 10,
            },
            {
                "GroupName": "approvers",
                "Description": "Approvers who approve and execute remediation plans",
                "Precedence": 5,
            },
        ]

        print("Creating Cognito groups...")

        for group in groups:
            try:
                self.cognito.create_group(
                    UserPoolId=user_pool_id,
                    GroupName=group["GroupName"],
                    Description=group["Description"],
                    Precedence=group["Precedence"],
                )
                print(f"✅ Group created: {group['GroupName']} (Precedence: {group['Precedence']})")
            except self.cognito.exceptions.GroupExistsException:
                print(f"ℹ️  Group already exists: {group['GroupName']}")
            except Exception as e:
                print(f"❌ Error creating group {group['GroupName']}: {e}")
                raise

    def assign_user_to_group(self, user_pool_id: str, username: str, group_name: str) -> None:
        """Assign user to a Cognito group"""
        try:
            self.cognito.admin_add_user_to_group(UserPoolId=user_pool_id, Username=username, GroupName=group_name)
            print(f"✅ User {username} added to group '{group_name}'")  # codeql[py/clear-text-logging-sensitive-data]
        except Exception as e:
            print(f"❌ Error adding user to group: {e}")
            raise

    def create_test_user(self, user_pool_id: str) -> None:
        """Create test user for workshops (SRE role)"""
        print(f"Creating test user: {self.test_user_email}...")

        try:
            self.cognito.admin_create_user(
                UserPoolId=user_pool_id,
                Username=self.test_user_email,
                TemporaryPassword=self.test_user_password,
                UserAttributes=[
                    {"Name": "email", "Value": self.test_user_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",  # Don't send welcome email
            )

            # Set permanent password (same as temporary for simplicity in workshop)
            self.cognito.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=self.test_user_email,
                Password=self.test_user_password,
                Permanent=True,
            )

            print(f"✅ Test user created: {self.test_user_email}")

        except self.cognito.exceptions.UsernameExistsException:
            print(f"ℹ️  Test user already exists: {self.test_user_email}")
        except Exception as e:
            print(f"❌ Error creating test user: {e}")
            raise

    def create_approver_user(self, user_pool_id: str) -> Dict[str, str]:
        """Create approver test user for multi-actor workflow"""
        approver_email = f"approver@{self.prefix}.example.com"
        approver_password = "<enter password>"  # Meets policy requirements

        print(f"Creating approver user: {approver_email}...")

        try:
            self.cognito.admin_create_user(
                UserPoolId=user_pool_id,
                Username=approver_email,
                TemporaryPassword=approver_password,
                UserAttributes=[
                    {"Name": "email", "Value": approver_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",  # Don't send welcome email
            )

            # Set permanent password
            self.cognito.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=approver_email,
                Password=approver_password,
                Permanent=True,
            )

            print(f"✅ Approver user created: {approver_email}")

            return {"email": approver_email, "password": approver_password}

        except self.cognito.exceptions.UsernameExistsException:
            print(f"ℹ️  Approver user already exists: {approver_email}")
            return {"email": approver_email, "password": approver_password}
        except Exception as e:
            print(f"❌ Error creating approver user: {e}")
            raise

    def update_user_auth_client_for_oauth(self, user_pool_id: str, client_id: str, resource_server_id: str) -> None:
        """
        Update User Auth Client to support OAuth flows and custom scopes.
        This enables ID tokens with rich claims (email, groups, etc.)
        """
        print("Updating User Auth Client for OAuth support...")

        try:
            self.cognito.update_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client_id,
                # Keep existing auth flows
                ExplicitAuthFlows=[
                    "ALLOW_USER_PASSWORD_AUTH",
                    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
                    "ALLOW_REFRESH_TOKEN_AUTH",
                    "ALLOW_USER_SRP_AUTH",
                ],
                # Add OAuth flows
                AllowedOAuthFlows=["code", "implicit"],
                AllowedOAuthFlowsUserPoolClient=True,
                # Add custom scopes
                AllowedOAuthScopes=[
                    "openid",
                    "profile",
                    "email",
                    f"{resource_server_id}/mcp.invoke",
                    f"{resource_server_id}/runtime.access",
                ],
                # Add callback URL for local testing
                CallbackURLs=["http://localhost:8080/callback"],
                LogoutURLs=["http://localhost:8080/logout"],
                SupportedIdentityProviders=["COGNITO"],
                # Token validity
                IdTokenValidity=60,
                AccessTokenValidity=60,
                RefreshTokenValidity=30,
                TokenValidityUnits={
                    "AccessToken": "minutes",
                    "IdToken": "minutes",
                    "RefreshToken": "days",
                },
                # Disable for public client (no secret)
                EnablePropagateAdditionalUserContextData=False,
                EnableTokenRevocation=True,
                PreventUserExistenceErrors="ENABLED",
            )

            print("✅ User Auth Client updated with OAuth support")
            print("   • OAuth flows: code, implicit")
            print("   • Scopes: openid, profile, email, custom scopes")
            print("   • ID Token will include email and cognito:groups claims")

        except Exception as e:
            print(f"❌ Error updating User Auth Client: {e}")
            raise

    def setup_cognito(self) -> Dict[str, Any]:
        """
        Execute full Cognito setup and return configuration
        """
        print("\n" + "=" * 70)
        print("COGNITO SETUP FOR AIML301 WORKSHOP")
        print("=" * 70 + "\n")

        # Create user pool
        user_pool_id, user_pool_arn = self.create_user_pool()

        # Create resource server (must be before M2M client)
        resource_server_id = self.create_resource_server(user_pool_id)

        # Create auth clients
        user_auth_client_id = self.create_user_auth_client(user_pool_id)
        m2m_client_id, m2m_client_secret = self.create_m2m_client(user_pool_id, resource_server_id)

        # Create domain
        domain_url = self.create_user_pool_domain(user_pool_id)
        token_endpoint = f"{domain_url}/oauth2/token"

        # Create groups for role-based access control
        self.create_groups(user_pool_id)

        # Create test user (developer role)
        self.create_test_user(user_pool_id)
        self.assign_user_to_group(user_pool_id, self.test_user_email, "sre")

        # Create approver user
        approver_user = self.create_approver_user(user_pool_id)
        self.assign_user_to_group(user_pool_id, approver_user["email"], "approvers")

        # Update User Auth Client for OAuth support (enables ID tokens with rich claims)
        self.update_user_auth_client_for_oauth(user_pool_id, user_auth_client_id, resource_server_id)

        # Build configuration
        cognito_config = {
            "region": self.region,
            "user_pool_id": user_pool_id,
            "user_pool_arn": user_pool_arn,
            "user_pool_name": f"{self.prefix}-UserPool",
            "domain": domain_url,
            "token_endpoint": token_endpoint,
            "user_auth_client": {
                "client_id": user_auth_client_id,
                "client_name": f"{self.prefix}-UserAuthClient",
                "has_secret": False,
                "oauth_flows": ["code", "implicit"],
                "oauth_scopes": [
                    "openid",
                    "profile",
                    "email",
                    f"{resource_server_id}/mcp.invoke",
                    f"{resource_server_id}/runtime.access",
                ],
            },
            "m2m_client": {
                "client_id": m2m_client_id,
                "client_secret": m2m_client_secret,
                "client_name": f"{self.prefix}-M2MClient",
                "has_secret": True,
            },
            "resource_server": {
                "identifier": resource_server_id,
                "name": f"{self.prefix} AgentCore Runtime API",
                "scopes": [
                    f"{resource_server_id}/mcp.invoke",
                    f"{resource_server_id}/runtime.access",
                ],
            },
            "groups": [
                {"name": "sre", "precedence": 10},
                {"name": "approvers", "precedence": 5},
            ],
            "test_user": {
                "username": self.test_user_email,
                "password": self.test_user_password,
                "email": self.test_user_email,
                "group": "sre",
            },
            "approver_user": {
                "username": approver_user["email"],
                "password": approver_user["password"],
                "email": approver_user["email"],
                "group": "approvers",
            },
        }

        return cognito_config

    def save_to_ssm(self, cognito_config: Dict[str, Any]) -> None:
        """Save Cognito configuration to SSM Parameter Store"""
        print("\n" + "=" * 70)
        print("SAVING COGNITO CONFIG TO SSM PARAMETER STORE")
        print("=" * 70 + "\n")

        params = PARAMETER_PATHS["cognito"]

        # Save individual parameters
        put_parameter(params["user_pool_id"], cognito_config["user_pool_id"])
        put_parameter(params["user_pool_name"], cognito_config["user_pool_name"])
        put_parameter(params["user_pool_arn"], cognito_config["user_pool_arn"])
        put_parameter(params["domain"], cognito_config["domain"])
        put_parameter(params["token_endpoint"], cognito_config["token_endpoint"])

        put_parameter(
            params["user_auth_client_id"],
            cognito_config["user_auth_client"]["client_id"],
        )
        put_parameter(
            params["user_auth_client_name"],
            cognito_config["user_auth_client"]["client_name"],
        )

        put_parameter(params["m2m_client_id"], cognito_config["m2m_client"]["client_id"])
        put_parameter(params["m2m_client_secret"], cognito_config["m2m_client"]["client_secret"])
        put_parameter(params["m2m_client_name"], cognito_config["m2m_client"]["client_name"])

        put_parameter(
            params["resource_server_id"],
            cognito_config["resource_server"]["identifier"],
        )
        put_parameter(
            params["resource_server_identifier"],
            cognito_config["resource_server"]["identifier"],
        )

        put_parameter(params["test_user_email"], cognito_config["test_user"]["email"])
        put_parameter(params["test_user_password"], cognito_config["test_user"]["password"])

        # Save approver user credentials
        put_parameter(params["approver_user_email"], cognito_config["approver_user"]["email"])
        put_parameter(
            params["approver_user_password"],
            cognito_config["approver_user"]["password"],
        )

        print("✅ Cognito configuration saved to SSM Parameter Store")

    def save_to_file(self, cognito_config: Dict[str, Any], filename: str = "cognito_config.json") -> None:
        """Save Cognito configuration to local JSON file (for reference)"""
        print(f"\nSaving configuration to {filename}...")

        with open(filename, "w") as f:
            json.dump(cognito_config, f, indent=2)

        print(f"✅ Configuration saved to {filename}")


def setup_cognito_complete() -> Dict[str, Any]:
    """
    Complete Cognito setup workflow
    1. Create all Cognito resources
    2. Save to SSM Parameter Store
    3. Return configuration
    """
    setup = CognitoSetup()

    # Execute setup
    cognito_config = setup.setup_cognito()

    # Save to SSM
    setup.save_to_ssm(cognito_config)

    # Save to file for reference
    setup.save_to_file(cognito_config)

    print("\n" + "=" * 70)
    print("✅ COGNITO SETUP COMPLETE")
    print("=" * 70)
    print("\nKey Configuration:")
    print(f"  User Pool ID: {cognito_config['user_pool_id']}")  # codeql[py/clear-text-logging-sensitive-data]
    print(f"  Domain: {cognito_config['domain']}")  # codeql[py/clear-text-logging-sensitive-data]
    print(f"  Token Endpoint: {cognito_config['token_endpoint']}")  # codeql[py/clear-text-logging-sensitive-data]
    print("\n  User Auth Client:")
    print(
        f"    • Client ID: {cognito_config['user_auth_client']['client_id']}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    print(
        f"    • OAuth Flows: {', '.join(cognito_config['user_auth_client']['oauth_flows'])}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    print("    • OAuth Scopes: openid, profile, email, custom scopes")
    print("\n  M2M Client:")
    print(
        f"    • Client ID: {cognito_config['m2m_client']['client_id']}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    print("    • Client Secret: ****")
    print("\n  Groups Created:")
    print("    • sre (Precedence: 10) - Tools: generate_remediation_plan")
    print("    • approvers (Precedence: 5) - Tools: execute_remediation_step, validate_remediation_environment")
    print("\n  Users Created:")
    print("    • Test User (SRE): **** (password: ****)")
    print("    • Approver User: **** (password: ****)")
    print("\nAll configuration stored in SSM Parameter Store under /aiml301/cognito/*")
    print("Reference copy saved to cognito_config.json\n")

    return cognito_config


def cleanup_cognito(user_pool_id: Optional[str] = None) -> None:
    """
    Clean up Cognito resources

    Args:
        user_pool_id: User Pool ID to delete (if None, fetches from SSM)
    """
    setup = CognitoSetup()

    # Get user pool ID from SSM if not provided
    if user_pool_id is None:
        try:
            user_pool_id = get_parameter(PARAMETER_PATHS["cognito"]["user_pool_id"])
        except Exception as e:
            print(f"❌ Could not retrieve User Pool ID from SSM: {e}")
            return

    print(
        f"Cleaning up Cognito resources for User Pool: {user_pool_id}..."
    )  # codeql[py/clear-text-logging-sensitive-data]
    print("")

    try:
        # Step 1: Get domain from User Pool (if exists)
        print("Step 1: Checking for User Pool Domain...")
        try:
            domain_response = setup.cognito.describe_user_pool(UserPoolId=user_pool_id)
            domain = domain_response.get("UserPool", {}).get("Domain")

            if domain:
                print(f"  Found domain: {domain}")
                print("  Deleting domain...")
                setup.cognito.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
                print(f"  ✅ Domain deleted: {domain}")
            else:
                print("  No domain configured")
        except Exception as e:
            print(f"  ⚠️  Could not check/delete domain: {e}")

        print("")

        # Step 2: Delete User Pool
        print(f"Step 2: Deleting User Pool: {user_pool_id}...")
        setup.cognito.delete_user_pool(UserPoolId=user_pool_id)
        print(f"  ✅ User Pool deleted: {user_pool_id}")

        print("")

        # Step 3: Delete SSM parameters
        print("Step 3: Deleting SSM parameters...")
        params = PARAMETER_PATHS["cognito"]
        deleted_count = 0
        for key, param_path in params.items():
            try:
                delete_parameter(param_path)
                deleted_count += 1
            except:  # noqa: E722
                pass  # Parameter might not exist

        print(f"  ✅ Deleted {deleted_count} SSM parameters")
        print("")
        print("✅ Cognito cleanup complete")

    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        raise
