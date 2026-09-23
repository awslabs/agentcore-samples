#!/usr/bin/env python3
"""
Cognito Setup for Health Lakehouse Data
Creates User Pool, App Client, Resource Server, and test users with OAuth scopes
Writes configuration to SSM Parameter Store

Usage:
    python setup_cognito.py
"""

import boto3
import json
import re
from pathlib import Path
from typing import Dict, Optional


class CognitoSetup:
    def __init__(self):
        """Initialize Cognito setup with region from boto3 session."""
        # Get region from boto3 session
        session = boto3.Session()
        self.region = session.region_name

        self.cognito = boto3.client("cognito-idp", region_name=self.region)
        self.ssm = boto3.client("ssm", region_name=self.region)
        self.sts = boto3.client("sts", region_name=self.region)
        self.lambda_client = boto3.client("lambda", region_name=self.region)
        self.env_file = Path(__file__).parent.parent / ".env"

        print(f"Initialized Cognito setup for region: {self.region}")

    def find_existing_user_pool(self, pool_name: str) -> Optional[str]:
        """Find existing user pool by name."""
        try:
            paginator = self.cognito.get_paginator("list_user_pools")
            for page in paginator.paginate(MaxResults=60):
                for pool in page.get("UserPools", []):
                    if pool["Name"] == pool_name:
                        print(f"ℹ️  Found existing User Pool: {pool['Id']}")
                        return pool["Id"]
        except Exception as e:
            print(f"⚠️  Error searching for user pool: {e}")
        return None

    def get_user_pool_client(self, user_pool_id: str, client_name: str) -> Optional[Dict]:
        """Get existing app client by name."""
        try:
            paginator = self.cognito.get_paginator("list_user_pool_clients")
            for page in paginator.paginate(UserPoolId=user_pool_id, MaxResults=60):
                for client in page.get("UserPoolClients", []):
                    if client["ClientName"] == client_name:
                        # Get full client details including secret
                        full_client = self.cognito.describe_user_pool_client(
                            UserPoolId=user_pool_id, ClientId=client["ClientId"]
                        )
                        print(f"ℹ️  Found existing App Client: {client['ClientId']}")
                        return full_client["UserPoolClient"]
        except Exception as e:
            print(f"⚠️  Error searching for app client: {e}")
        return None

    def get_user_pool_domain(self, user_pool_id: str) -> Optional[str]:
        """Get existing domain for user pool."""
        try:
            response = self.cognito.describe_user_pool(UserPoolId=user_pool_id)
            domain = response["UserPool"].get("Domain")
            if domain:
                domain_url = f"https://{domain}.auth.{self.region}.amazoncognito.com"
                print(f"ℹ️  Found existing domain: {domain_url}")
                return domain_url
        except Exception as e:
            print(f"⚠️  Error getting domain: {e}")
        return None

    def store_parameters_in_ssm(self, config: Dict):
        """
        Store Cognito configuration in SSM Parameter Store.

        Args:
            config: Dictionary with user_pool_id, client_id, domain, m2m_client_id, m2m_client_secret, and optionally client_secret
        """
        print("\n💾 Storing configuration in SSM Parameter Store...")

        # Get account ID for constructing ARN
        account_id = self.sts.get_caller_identity()["Account"]
        user_pool_arn = f"arn:aws:cognito-idp:{self.region}:{account_id}:userpool/{config['user_pool_id']}"

        parameters = [
            {
                "name": "/app/lakehouse-agent/cognito-user-pool-id",
                "value": config["user_pool_id"],
                "description": "Cognito User Pool ID for authentication",
            },
            {
                "name": "/app/lakehouse-agent/cognito-user-pool-arn",
                "value": user_pool_arn,
                "description": "Cognito User Pool ARN",
            },
            {
                "name": "/app/lakehouse-agent/cognito-app-client-id",
                "value": config["client_id"],
                "description": "Cognito App Client ID (supports user auth and M2M)",
            },
            {
                "name": "/app/lakehouse-agent/cognito-domain",
                "value": config["domain"],
                "description": "Cognito domain URL for OAuth",
            },
            {
                "name": "/app/lakehouse-agent/cognito-resource-server-id",
                "value": "lakehouse-api",
                "description": "Cognito Resource Server identifier",
            },
            {
                "name": "/app/lakehouse-agent/cognito-region",
                "value": self.region,
                "description": "AWS region for Cognito",
            },
            {
                "name": "/app/lakehouse-agent/cognito-m2m-client-id",
                "value": config["m2m_client_id"],
                "description": "Cognito M2M-only App Client ID (client_credentials only)",
            },
        ]

        # Store client secret as SecureString if available
        if "client_secret" in config and config["client_secret"]:
            try:
                self.ssm.put_parameter(
                    Name="/app/lakehouse-agent/cognito-app-client-secret",
                    Value=config["client_secret"],
                    Description="Cognito App Client Secret (SecureString)",
                    Type="SecureString",
                    Overwrite=True,
                )
                print("✅ Stored parameter (SecureString): /app/lakehouse-agent/cognito-app-client-secret")
            except Exception as e:
                print(f"❌ Error storing client secret: {e}")
                raise

        # Store M2M client secret as SecureString
        if "m2m_client_secret" in config and config["m2m_client_secret"]:
            try:
                self.ssm.put_parameter(
                    Name="/app/lakehouse-agent/cognito-m2m-client-secret",
                    Value=config["m2m_client_secret"],
                    Description="Cognito M2M App Client Secret (SecureString)",
                    Type="SecureString",
                    Overwrite=True,
                )
                print("✅ Stored parameter (SecureString): /app/lakehouse-agent/cognito-m2m-client-secret")
            except Exception as e:
                print(f"❌ Error storing M2M client secret: {e}")
                raise

        # Store other parameters as String type
        for param in parameters:
            try:
                self.ssm.put_parameter(
                    Name=param["name"],
                    Value=param["value"],
                    Description=param["description"],
                    Type="String",
                    Overwrite=True,
                )
                print(
                    f"✅ Stored parameter: {param['name']} = {param['value']}"
                )  # codeql[py/clear-text-logging-sensitive-data]
            except Exception as e:
                print(
                    f"❌ Error storing parameter {param['name']}: {e}"
                )  # codeql[py/clear-text-logging-sensitive-data]
                raise

    def write_to_env(self, config: Dict):
        """
        Write configuration to .env file.

        Note: This function is deprecated and will be removed in a future version.
        Configuration should be managed through SSM Parameter Store.
        This is kept temporarily for backward compatibility during migration.
        """
        print("⚠️  Warning: .env file updates are deprecated. Please migrate to SSM Parameter Store.")
        print("   Run: python ../ssm_migrate.py --migrate")

        try:
            # Read existing .env file
            env_content = {}
            if self.env_file.exists():
                with open(self.env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            env_content[key.strip()] = value.strip()

            # Update with new values
            env_content["COGNITO_USER_POOL_ID"] = config["user_pool_id"]
            env_content["COGNITO_APP_CLIENT_ID"] = config["client_id"]
            if "client_secret" in config:
                env_content["COGNITO_APP_CLIENT_SECRET"] = config["client_secret"]
            env_content["COGNITO_DOMAIN"] = config["domain"]
            env_content["COGNITO_RESOURCE_SERVER_ID"] = "lakehouse-api"

            # Construct User Pool ARN
            user_pool_arn = f"arn:aws:cognito-idp:{self.region}:{self.sts.get_caller_identity()['Account']}:userpool/{config['user_pool_id']}"
            env_content["COGNITO_USER_POOL_ARN"] = user_pool_arn

            # Write back to .env file
            with open(self.env_file, "w") as f:
                for key, value in sorted(env_content.items()):
                    f.write(f"{key}={value}\n")  # codeql[py/clear-text-storage-of-sensitive-data]

            print(f"\n✅ Configuration written to {self.env_file} (for backward compatibility)")

        except Exception as e:
            print(f"❌ Error writing to .env file: {e}")
            raise

    def setup(self, pool_name: str = "lakehouse-pool") -> Dict:
        # Check for existing User Pool
        user_pool_id = self.find_existing_user_pool(pool_name)

        if not user_pool_id:
            # Create User Pool with username-password authentication enabled
            # NOTE: NOT using UsernameAttributes to allow email to be the actual username
            pool_response = self.cognito.create_user_pool(
                PoolName=pool_name,
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": 8,
                        "RequireUppercase": True,
                        "RequireLowercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": True,
                    }
                },
                AutoVerifiedAttributes=["email"],
                # NOT setting UsernameAttributes - this allows email to be used as username directly
                Schema=[{"Name": "email", "Required": True, "Mutable": True}],
                AdminCreateUserConfig={
                    "AllowAdminCreateUserOnly": False  # Allow users to sign up
                },
            )
            user_pool_id = pool_response["UserPool"]["Id"]
            print(f"✅ User Pool created: {user_pool_id}")
            print("   Note: Email will be used as username (not as alias)")
        else:
            print(f"✅ Using existing User Pool: {user_pool_id}")
            print("   ⚠️  Warning: If this pool was created with UsernameAttributes=['email'],")
            print("      users will have UUID usernames. Delete the pool and recreate, or")
            print("      run cleanup_test_users.py to delete old users.")

        # Create Resource Server with scopes (if not exists)
        # Note: Scope names cannot contain '/' - using '.' instead
        try:
            resource_server = self.cognito.create_resource_server(
                UserPoolId=user_pool_id,
                Identifier="lakehouse-api",
                Name="Lakehouse Data API",
                Scopes=[
                    {"ScopeName": "claims.query", "ScopeDescription": "Query claims"},
                    {"ScopeName": "claims.submit", "ScopeDescription": "Submit claims"},
                    {"ScopeName": "claims.update", "ScopeDescription": "Update claims"},
                    {
                        "ScopeName": "claims.approve",
                        "ScopeDescription": "Approve/deny claims",
                    },
                ],
            )
            print("✅ Resource Server created with scopes")
        except self.cognito.exceptions.ResourceNotFoundException:
            print("ℹ️  Resource Server already exists")
        except Exception as e:
            if "already exists" in str(e).lower():
                print("ℹ️  Resource Server already exists")
            else:
                raise

        # Check for existing App Client
        existing_client = self.get_user_pool_client(user_pool_id, "lakehouse-client")

        if existing_client:
            client_id = existing_client["ClientId"]
            client_secret = existing_client.get("ClientSecret")
            print(f"ℹ️  App Client exists: {client_id}")
            print("   Updating to support both client_credentials and user authentication...")

            # Update existing client to support both flows
            self.cognito.update_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client_id,
                ClientName="lakehouse-client",
                ExplicitAuthFlows=[
                    "ALLOW_USER_SRP_AUTH",  # Secure Remote Password (SRP) auth
                    "ALLOW_ADMIN_USER_PASSWORD_AUTH",  # Admin user password auth (for testing)
                    "ALLOW_REFRESH_TOKEN_AUTH",  # Refresh token auth
                ],
                AllowedOAuthFlows=["client_credentials"],  # Machine-to-machine authentication
                AllowedOAuthScopes=[
                    "lakehouse-api/claims.query",
                    "lakehouse-api/claims.submit",
                    "lakehouse-api/claims.update",
                    "lakehouse-api/claims.approve",
                ],
                AllowedOAuthFlowsUserPoolClient=True,
                PreventUserExistenceErrors="ENABLED",  # Security best practice
            )
            print("✅ App Client updated to support user authentication and client_credentials")
        else:
            # Create App Client supporting both user auth and client credentials
            client_response = self.cognito.create_user_pool_client(
                UserPoolId=user_pool_id,
                ClientName="lakehouse-client",
                GenerateSecret=True,
                ExplicitAuthFlows=[
                    "ALLOW_USER_SRP_AUTH",  # Secure Remote Password (SRP) auth
                    "ALLOW_ADMIN_USER_PASSWORD_AUTH",  # Admin user password auth (for testing)
                    "ALLOW_REFRESH_TOKEN_AUTH",  # Refresh token auth
                ],
                AllowedOAuthFlows=["client_credentials"],  # Machine-to-machine authentication
                AllowedOAuthScopes=[
                    "lakehouse-api/claims.query",
                    "lakehouse-api/claims.submit",
                    "lakehouse-api/claims.update",
                    "lakehouse-api/claims.approve",
                ],
                AllowedOAuthFlowsUserPoolClient=True,
                PreventUserExistenceErrors="ENABLED",  # Security best practice
            )
            client_id = client_response["UserPoolClient"]["ClientId"]
            client_secret = client_response["UserPoolClient"].get("ClientSecret")
            print(f"✅ App Client created: {client_id}")

        # Check for existing domain or create new one
        domain_url = self.get_user_pool_domain(user_pool_id)

        if not domain_url:
            # Create domain
            # Domain names can only contain lowercase letters, numbers, and hyphens
            # Extract only alphanumeric characters from pool ID and convert to lowercase
            pool_id_clean = re.sub(r"[^a-zA-Z0-9]", "", user_pool_id).lower()[:8]
            domain_name = f"lakehouse-{pool_id_clean}"

            try:
                self.cognito.create_user_pool_domain(Domain=domain_name, UserPoolId=user_pool_id)
                domain_url = f"https://{domain_name}.auth.{self.region}.amazoncognito.com"
                print(f"✅ Domain created: {domain_url}")
            except Exception as e:
                if "already exists" in str(e).lower() or "domain" in str(e).lower():
                    domain_url = f"https://{domain_name}.auth.{self.region}.amazoncognito.com"
                    print(f"ℹ️  Domain already exists: {domain_url}")
                else:
                    raise
        else:
            print(f"✅ Using existing domain: {domain_url}")

        # Create Cognito groups (if not exist)
        groups = [
            {"name": "policyholders", "description": "Policy holders group"},
            {"name": "adjusters", "description": "Claims adjusters group"},
            {"name": "administrators", "description": "Administrators group"},
        ]

        for group in groups:
            try:
                self.cognito.create_group(
                    GroupName=group["name"],
                    UserPoolId=user_pool_id,
                    Description=group["description"],
                )
                print(f"✅ Group created: {group['name']}")
            except self.cognito.exceptions.GroupExistsException:
                print(f"ℹ️  Group already exists: {group['name']}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print(f"ℹ️  Group already exists: {group['name']}")
                else:
                    print(f"⚠️  Error creating group {group['name']}: {e}")

        # Create test users with email as username (skip if already exist)
        test_users = [
            {
                "email": "policyholder001@example.com",
                "name": "Policyholder 001",
                "group": "policyholders",
            },
            {
                "email": "policyholder002@example.com",
                "name": "Policyholder 002",
                "group": "policyholders",
            },
            {
                "email": "adjuster001@example.com",
                "name": "Adjuster 001",
                "group": "adjusters",
            },
            {
                "email": "adjuster002@example.com",
                "name": "Adjuster 002",
                "group": "adjusters",
            },
            {"email": "admin@example.com", "name": "Admin", "group": "administrators"},
        ]

        for user in test_users:
            email = user["email"]
            try:
                # Create user with email as username
                self.cognito.admin_create_user(
                    UserPoolId=user_pool_id,
                    Username=email,  # Username is the email address
                    UserAttributes=[
                        {"Name": "email", "Value": email},
                        {"Name": "email_verified", "Value": "true"},
                    ],
                    TemporaryPassword="TempPass123!",
                    MessageAction="SUPPRESS",  # Don't send welcome email
                )
                print(f"✅ Test user created: {email} (username: {email})")

                # Add user to group
                try:
                    self.cognito.admin_add_user_to_group(
                        UserPoolId=user_pool_id, Username=email, GroupName=user["group"]
                    )
                    print(f"   ✅ Added to group: {user['group']}")
                except Exception as e:
                    print(f"   ⚠️  Error adding to group: {e}")

            except self.cognito.exceptions.UsernameExistsException:
                print(f"ℹ️  Test user already exists: {email}")
                # Try to add to group even if user exists
                try:
                    self.cognito.admin_add_user_to_group(
                        UserPoolId=user_pool_id, Username=email, GroupName=user["group"]
                    )
                    print(f"   ✅ Added to group: {user['group']}")
                except Exception as e:
                    if "already a member" in str(e).lower() or "member of group" in str(e).lower():
                        print(f"   ℹ️  Already in group: {user['group']}")
                    else:
                        print(f"   ⚠️  Error adding to group: {e}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    print(f"ℹ️  Test user already exists: {email}")
                else:
                    print(f"⚠️  Error creating user {email}: {e}")

        result = {
            "user_pool_id": user_pool_id,
            "client_id": client_id,
            "domain": domain_url,
        }

        if "client_secret" in locals() and client_secret:
            result["client_secret"] = client_secret

        # Create M2M-only app client
        m2m_client = self.create_m2m_client(user_pool_id)
        result["m2m_client_id"] = m2m_client["client_id"]
        result["m2m_client_secret"] = m2m_client["client_secret"]

        # Store configuration in SSM Parameter Store
        self.store_parameters_in_ssm(result)

        # Write to .env file (deprecated, for backward compatibility)
        self.write_to_env(result)

        # Automatically try to configure post-auth trigger if Lambda exists
        self.add_post_auth_trigger(user_pool_id=user_pool_id)

        return result

    def create_m2m_client(self, user_pool_id: str) -> Dict:
        """
        Create M2M-only app client with client_credentials OAuth flow.

        Args:
            user_pool_id: Cognito User Pool ID

        Returns:
            Dictionary with client_id and client_secret
        """
        print("\n🤖 Creating M2M-only app client...")

        # Check for existing M2M client
        existing_m2m_client = self.get_user_pool_client(user_pool_id, "lakehouse-m2m-client")

        # M2M client configuration (client_credentials flow only)
        client_config = {
            "UserPoolId": user_pool_id,
            "ClientName": "lakehouse-m2m-client",
            "GenerateSecret": True,
            "ExplicitAuthFlows": [],  # No user auth flows for M2M
            "AllowedOAuthFlows": ["client_credentials"],  # Only client_credentials
            "AllowedOAuthScopes": [
                "lakehouse-api/claims.query",
                "lakehouse-api/claims.submit",
                "lakehouse-api/claims.update",
                "lakehouse-api/claims.approve",
            ],
            "AllowedOAuthFlowsUserPoolClient": True,
            "SupportedIdentityProviders": [],  # No identity providers for M2M
            "CallbackURLs": ["https://localhost"],  # Dummy URL for M2M
            "PreventUserExistenceErrors": "ENABLED",
        }

        if existing_m2m_client:
            client_id = existing_m2m_client["ClientId"]
            print(f"ℹ️  M2M App Client exists: {client_id}")
            print("   Updating configuration...")

            # Update with M2M configuration
            # Remove GenerateSecret as it's not valid for update_user_pool_client
            update_config = {k: v for k, v in client_config.items() if k != "GenerateSecret"}
            update_config["ClientId"] = client_id
            self.cognito.update_user_pool_client(**update_config)

            # Get updated client to retrieve secret
            updated_client = self.cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
            client_secret = updated_client["UserPoolClient"].get("ClientSecret")
            print("✅ M2M App Client updated with client_credentials flow")
        else:
            # Create with M2M configuration
            client_response = self.cognito.create_user_pool_client(**client_config)
            client_id = client_response["UserPoolClient"]["ClientId"]
            client_secret = client_response["UserPoolClient"].get("ClientSecret")
            print(f"✅ M2M App Client created: {client_id}")
            print("   Configuration: client_credentials flow only")

        return {"client_id": client_id, "client_secret": client_secret}

    def add_post_auth_trigger(self, user_pool_id: str = None, lambda_name: str = "lakehouse-cognito-post-auth"):
        """
        Add Post-Authentication Lambda trigger to Cognito User Pool.

        Args:
            user_pool_id: Cognito User Pool ID (if None, reads from SSM)
            lambda_name: Name of the Lambda function to use as trigger

        Returns:
            True if successful, False otherwise
        """
        print("\n🔗 Configuring Post-Authentication Lambda trigger...")

        # Get User Pool ID from SSM if not provided
        if not user_pool_id:
            try:
                response = self.ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")
                user_pool_id = response["Parameter"]["Value"]
                print(f"ℹ️  Retrieved User Pool ID from SSM: {user_pool_id}")
            except Exception as e:
                print(f"❌ Error retrieving User Pool ID from SSM: {e}")
                print("   Please provide user_pool_id or run setup_cognito.py first")
                return False

        # Check if Lambda function exists
        try:
            self.lambda_client.get_function(FunctionName=lambda_name)
            print(f"✅ Lambda function exists: {lambda_name}")
        except self.lambda_client.exceptions.ResourceNotFoundException:
            print(f"⚠️  Lambda function '{lambda_name}' not found")
            print("   Skipping post-auth trigger configuration")
            print("   To enable login audit logging, run:")
            print("   1. bash deploy_post_auth_lambda.sh")
            print("   2. python setup_cognito.py --add-post-auth-trigger")
            return False
        except Exception as e:
            print(f"⚠️  Error checking Lambda function: {e}")
            print("   Skipping post-auth trigger configuration")
            return False

        # Get Lambda ARN
        account_id = self.sts.get_caller_identity()["Account"]
        lambda_arn = f"arn:aws:lambda:{self.region}:{account_id}:function:{lambda_name}"

        try:
            # Update User Pool with Lambda trigger
            self.cognito.update_user_pool(UserPoolId=user_pool_id, LambdaConfig={"PostAuthentication": lambda_arn})
            print("✅ Post-Authentication trigger configured")
            print(f"   Lambda: {lambda_name}")
            print(f"   ARN: {lambda_arn}")
            print("   Login events will be logged to DynamoDB table: lakehouse_user_login_audit")
            return True

        except Exception as e:
            print(f"❌ Error configuring trigger: {e}")
            print("\n💡 Make sure:")
            print(f"   1. Lambda function '{lambda_name}' exists")
            print("   2. Lambda has permission for Cognito to invoke it")
            print("   3. Run: bash deploy_post_auth_lambda.sh")
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Setup Cognito User Pool for Lakehouse Agent")
    parser.add_argument(
        "--add-post-auth-trigger",
        action="store_true",
        help="Only add Post-Authentication Lambda trigger to existing User Pool (skip full setup)",
    )
    args = parser.parse_args()

    setup = CognitoSetup()

    if args.add_post_auth_trigger:
        # Only configure the post-auth trigger
        success = setup.add_post_auth_trigger()
        if success:
            print("\n✅ Post-Authentication trigger configured successfully!")
            print("\n📊 Login events will now be logged to DynamoDB table: lakehouse_user_login_audit")
        else:
            print("\n❌ Failed to configure Post-Authentication trigger")
            print("   Run: bash deploy_post_auth_lambda.sh first")
        exit(0 if success else 1)

    # Normal setup flow (includes automatic post-auth trigger configuration)
    result = setup.setup()

    print(
        f"\n📝 Configuration:\n{json.dumps({k: v for k, v in result.items() if 'secret' not in k}, indent=2)}"
    )  # codeql[py/clear-text-logging-sensitive-data]
    if "client_secret" in result:
        print(f"\n🔐 User App Client Secret: {result['client_secret']}")  # codeql[py/clear-text-logging-sensitive-data]
        print("   (Also stored securely in SSM Parameter Store)")
    if "m2m_client_secret" in result:
        print(
            f"\n🤖 M2M App Client Secret: {result['m2m_client_secret']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print("   (Also stored securely in SSM Parameter Store)")

    print("\n💾 SSM Parameters Stored:")
    print("   • /app/lakehouse-agent/cognito-user-pool-id")
    print("   • /app/lakehouse-agent/cognito-user-pool-arn")
    print("   • /app/lakehouse-agent/cognito-app-client-id (user auth + M2M)")
    print("   • /app/lakehouse-agent/cognito-app-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/cognito-m2m-client-id (M2M only)")
    print("   • /app/lakehouse-agent/cognito-m2m-client-secret (SecureString)")
    print("   • /app/lakehouse-agent/cognito-domain")
    print("   • /app/lakehouse-agent/cognito-resource-server-id")
    print("   • /app/lakehouse-agent/cognito-region")

    print("\n👥 Test Users Created:")
    print("   • policyholder001@example.com → policyholders group")
    print("   • policyholder002@example.com → policyholders group")
    print("   • adjuster001@example.com → adjusters group")
    print("   • adjuster002@example.com → adjusters group")
    print("   • admin@example.com → administrators group")
    print("   Default password: TempPass123!")
    print("   Note: Users will be prompted to change password on first login")

    print("\n👥 Cognito Groups:")
    print("   • policyholders - Policy holders group")
    print("   • adjusters - Claims adjusters group")
    print("   • administrators - Administrators group")

    print("\n🔑 App Clients:")
    print(f"   1. lakehouse-client (ID: {result['client_id']})")
    print("      - Supports: User authentication (SRP, Admin Password) + M2M")
    print("      - Use for: Streamlit UI, user-facing applications")
    print(f"   2. lakehouse-m2m-client (ID: {result['m2m_client_id']})")  # codeql[py/clear-text-logging-sensitive-data]
    print("      - Supports: M2M only (client_credentials)")
    print("      - Use for: Gateway-to-Runtime, service-to-service, test scripts")

    print("\n⚠️  If you see UUID usernames instead of emails:")
    print("   1. Run: python cleanup_test_users.py")
    print("   2. Delete the User Pool from AWS Console")
    print("   3. Run this script again to recreate with correct settings")
