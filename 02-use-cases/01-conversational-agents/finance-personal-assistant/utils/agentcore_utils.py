import boto3
from boto3.session import Session


def setup_cognito_user_pool():
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    try:
        # Create User Pool
        user_pool_response = cognito_client.create_user_pool(
            PoolName="agentpool", Policies={"PasswordPolicy": {"MinimumLength": 8}}
        )
        pool_id = user_pool_response["UserPool"]["Id"]
        # Create App Client
        app_client_response = cognito_client.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName="MCPServerPoolClient",
            GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        )
        client_id = app_client_response["UserPoolClient"]["ClientId"]
        # Create User
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username="testuser",
            TemporaryPassword="Temp123!",
            MessageAction="SUPPRESS",
        )
        # Set Permanent Password
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username="testuser",
            Password="MyPassword123!",
            Permanent=True,
        )
        # Authenticate User and get Access Token
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": "testuser", "PASSWORD": "MyPassword123!"},
        )
        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
        # Output the required values
        print(f"Pool id: {pool_id}")
        print(f"Discovery URL: https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration")
        print(f"Client ID: {client_id}")
        print(f"Bearer Token: {bearer_token}")

        # Return values if needed for further processing
        return {
            "pool_id": pool_id,
            "client_id": client_id,
            "bearer_token": bearer_token,
            "discovery_url": f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def reauthenticate_user(client_id):
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    # Authenticate User and get Access Token
    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": "testuser", "PASSWORD": "MyPassword123!"},
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    return bearer_token


def delete_cognito_user_pool(pool_id=None):
    """
    Delete a Cognito User Pool by ID, or find and delete agentpool if no ID provided.

    Args:
        pool_id: The pool ID to delete (optional)

    Returns:
        bool: True if deletion was successful, False otherwise
    """
    boto_session = Session()
    region = boto_session.region_name
    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        # If no pool_id provided, find agentpool
        if not pool_id:
            response = cognito_client.list_user_pools(MaxResults=60)
            for pool in response.get("UserPools", []):
                if pool.get("Name") == "agentpool":
                    pool_id = pool.get("Id")
                    break

            if not pool_id:
                print("agentpool not found")
                return False

        # Delete the user pool
        print(f"Deleting Cognito User Pool: {pool_id}")
        cognito_client.delete_user_pool(UserPoolId=pool_id)
        print(f"Successfully deleted User Pool: {pool_id}")
        return True

    except Exception as e:
        print(f"Error deleting User Pool: {e}")
        return False
