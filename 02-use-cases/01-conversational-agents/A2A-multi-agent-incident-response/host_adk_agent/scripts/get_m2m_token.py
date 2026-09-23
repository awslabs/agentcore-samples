#!/usr/bin/env python3
"""
Get OAuth2 access token using username/password from Cognito.
Reads configuration from .a2a.config and authenticates with AWS Cognito.
"""

import sys
import boto3
import yaml
from pathlib import Path
from typing import Dict, Any, Tuple
from getpass import getpass


class CognitoAuthenticator:
    """Handle Cognito authentication operations"""

    def __init__(self, region: str):
        self.region = region
        self.cognito = boto3.client("cognito-idp", region_name=region)
        self.cfn = boto3.client("cloudformation", region_name=region)

    def get_config_from_stack(self, stack_name: str) -> Dict[str, str]:
        """Extract Cognito config from CloudFormation stack outputs"""
        response = self.cfn.describe_stacks(StackName=stack_name)
        outputs = {o["OutputKey"]: o["OutputValue"] for o in response["Stacks"][0]["Outputs"]}

        # Parse user pool ID from DiscoveryUrl
        # Format: https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration
        discovery_url = outputs["DiscoveryUrl"]
        user_pool_id = discovery_url.split("/")[3]  # Extract user_pool_id (index 3)

        return {
            "user_pool_id": user_pool_id,
            "client_id": outputs.get("WebUserPoolClientId"),
        }

    def authenticate(self, user_pool_id: str, client_id: str, username: str, password: str) -> str:
        """Authenticate user and return access token"""
        response = self.cognito.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )

        # Handle new password challenge
        if response.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
            print("\n⚠️  Password change required")
            new_password = getpass("Enter new password: ")
            response = self.cognito.admin_respond_to_auth_challenge(
                UserPoolId=user_pool_id,
                ClientId=client_id,
                ChallengeName="NEW_PASSWORD_REQUIRED",
                ChallengeResponses={"USERNAME": username, "NEW_PASSWORD": new_password},
                Session=response["Session"],
            )

        if "AuthenticationResult" not in response:
            raise ValueError("Authentication failed: Unexpected response")

        return response["AuthenticationResult"]["AccessToken"]


def load_config() -> Dict[str, Any]:
    """Load deployment configuration"""
    config_path = Path(__file__).parent.parent.parent / ".a2a.config"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Configuration not found at {config_path}\nRun 'uv run deploy.py' first to create the configuration."
        )

    with open(config_path) as f:
        return yaml.safe_load(f)


def get_credentials(config: Dict[str, Any]) -> Tuple[str, str]:
    """Get username and password from config or user input"""
    username = config.get("cognito", {}).get("admin_email")
    password = config.get("cognito", {}).get("admin_password")

    if not username:
        username = input("Username (email): ")

    if not password:
        password = getpass("Password: ")

    return username, password


def get_m2m_token():
    try:
        # Load configuration
        config = load_config()
        region = config["aws"]["region"]
        stack_name = config["stacks"]["cognito"]

        # Initialize authenticator and get Cognito config
        auth = CognitoAuthenticator(region)
        cognito_config = auth.get_config_from_stack(stack_name)

        # Get credentials and authenticate
        username, password = get_credentials(config)
        access_token = auth.authenticate(
            cognito_config["user_pool_id"],
            cognito_config["client_id"],
            username,
            password,
        )

        return access_token

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if "NotAuthorizedException" in str(type(e)):
            print(
                "\n❌ Authentication failed: Invalid username or password",
                file=sys.stderr,
            )
        else:
            print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Main execution flow"""

    access_token = get_m2m_token()

    # Display access token
    print(f"Bearer Token: {access_token}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user", file=sys.stderr)
        sys.exit(0)
