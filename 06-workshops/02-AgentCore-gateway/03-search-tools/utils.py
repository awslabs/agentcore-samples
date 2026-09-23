import json
import time
from typing import Dict, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

LAMBDA_EXECUTION_ROLE_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
LAMBDA_RUNTIME = "python3.12"
LAMBDA_HANDLER = "lambda_function_code.lambda_handler"
LAMBDA_PACKAGE_TYPE = "Zip"

IAM_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# AgentCore Gateway IAM Role constants
GATEWAY_AGENTCORE_ROLE_NAME = "GatewaySearchAgentCoreRole"
GATEWAY_AGENTCORE_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

GATEWAY_AGENTCORE_POLICY_NAME = "BedrockAgentPolicy"

# Cognito configuration constants
COGNITO_POOL_NAME = "MCPServerPool"
COGNITO_CLIENT_NAME = "MCPServerPoolClient"
COGNITO_PASSWORD_MIN_LENGTH = 8
COGNITO_DEFAULT_USERNAME = "testuser"
COGNITO_DEFAULT_TEMP_PASSWORD = "Temp123!"  # pragma: allowlist secret
COGNITO_DEFAULT_PASSWORD = "MyPassword123!"  # pragma: allowlist secret

COGNITO_AUTH_FLOWS = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

COGNITO_PASSWORD_POLICY = {"PasswordPolicy": {"MinimumLength": COGNITO_PASSWORD_MIN_LENGTH}}


def _format_error_message(error: ClientError) -> str:
    """Format error message from ClientError."""
    return f"{error.response['Error']['Code']}-{error.response['Error']['Message']}"


def _create_or_get_iam_role(iam_client, role_name: str) -> str:
    """Create IAM role or return existing role ARN."""
    try:
        print("Creating IAM role for lambda function")
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(IAM_TRUST_POLICY),
            Description="IAM role to be assumed by lambda function",
        )
        role_arn = response["Role"]["Arn"]

        print("Attaching policy to the IAM role")
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=LAMBDA_EXECUTION_ROLE_POLICY,
        )

        print(f"Role '{role_name}' created successfully: {role_arn}")
        return role_arn

    except ClientError as error:
        if error.response["Error"]["Code"] == "EntityAlreadyExists":
            response = iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]
            print(f"IAM role {role_name} already exists. Using the same ARN {role_arn}")
            return role_arn
        else:
            raise error


def _create_or_get_lambda_function(lambda_client, function_name: str, role_arn: str, code: bytes) -> str:
    """Create Lambda function or return existing function ARN."""
    try:
        print("Creating lambda function")
        response = lambda_client.create_function(
            FunctionName=function_name,
            Role=role_arn,
            Runtime=LAMBDA_RUNTIME,
            Handler=LAMBDA_HANDLER,
            Code={"ZipFile": code},
            Description="Lambda function example for Bedrock AgentCore Gateway",
            PackageType=LAMBDA_PACKAGE_TYPE,
        )
        return response["FunctionArn"]

    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceConflictException":
            response = lambda_client.get_function(FunctionName=function_name)
            lambda_arn = response["Configuration"]["FunctionArn"]
            print(f"AWS Lambda function {function_name} already exists. Using the same ARN {lambda_arn}")
            return lambda_arn
        else:
            raise error


def create_gateway_lambda(lambda_function_code_path: str, lambda_function_name: str) -> Dict[str, Union[str, int]]:
    """Create AWS Lambda function with IAM role for AgentCore Gateway.

    Args:
        lambda_function_code_path: Path to the Lambda function code zip file
        lambda_function_name: Name for the Lambda function

    Returns:
        Dictionary with 'lambda_function_arn' and 'exit_code' keys
    """
    session = boto3.Session()
    region = session.region_name

    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    role_name = f"{lambda_function_name}_lambda_iamrole"

    print("Reading code from zip file")
    with open(lambda_function_code_path, "rb") as f:
        lambda_function_code = f.read()

    try:
        role_arn = _create_or_get_iam_role(iam_client, role_name)
        time.sleep(20)
        try:
            lambda_arn = _create_or_get_lambda_function(
                lambda_client, lambda_function_name, role_arn, lambda_function_code
            )
        except ClientError:
            lambda_arn = _create_or_get_lambda_function(
                lambda_client, lambda_function_name, role_arn, lambda_function_code
            )

        return {"lambda_function_arn": lambda_arn, "exit_code": 0}

    except ClientError as error:
        error_message = _format_error_message(error)
        print(f"Error: {error_message}")
        return {"lambda_function_arn": error_message, "exit_code": 1}
    except Exception as error:
        print(f"Unexpected error: {str(error)}")
        return {"lambda_function_arn": str(error), "exit_code": 1}


def _create_cognito_user_pool(cognito_client, pool_name: str) -> str:
    """Create Cognito User Pool and return pool ID."""
    print(f"Creating Cognito User Pool: {pool_name}")
    response = cognito_client.create_user_pool(PoolName=pool_name, Policies=COGNITO_PASSWORD_POLICY)
    pool_id = response["UserPool"]["Id"]
    print(f"User Pool created with ID: {pool_id}")
    return pool_id


def _create_cognito_app_client(cognito_client, pool_id: str, client_name: str) -> str:
    """Create Cognito App Client and return client ID."""
    print(f"Creating Cognito App Client: {client_name}")
    response = cognito_client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=client_name,
        GenerateSecret=False,
        ExplicitAuthFlows=COGNITO_AUTH_FLOWS,
    )
    client_id = response["UserPoolClient"]["ClientId"]
    print(f"App Client created with ID: {client_id}")
    return client_id


def _create_cognito_user(
    cognito_client,
    pool_id: str,
    username: str,
    temp_password: str,
    permanent_password: str,
) -> None:
    """Create Cognito user with temporary password and set permanent password."""
    print(f"Creating Cognito user: {username}")
    cognito_client.admin_create_user(
        UserPoolId=pool_id,
        Username=username,
        TemporaryPassword=temp_password,
        MessageAction="SUPPRESS",
    )

    print(f"Setting permanent password for user: {username}")
    cognito_client.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=permanent_password,
        Permanent=True,
    )


def _authenticate_user(cognito_client, client_id: str, username: str, password: str) -> str:
    """Authenticate user and return access token."""
    print(f"Authenticating user: {username}")
    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return auth_response["AuthenticationResult"]["AccessToken"]


def get_bearer_token(client_id: str, username: str, password: str, region: Optional[str] = None) -> Optional[str]:
    """Get bearer token from existing Cognito User Pool.

    Args:
        client_id: Cognito App Client ID
        username: Username for authentication
        password: User password
        region: AWS region (if None, uses session default)

    Returns:
        Bearer token string or None if authentication fails
    """
    if not region:
        session = boto3.Session()
        region = session.region_name

    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        print(f"Authenticating user: {username}")
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )

        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
        print("Bearer token obtained successfully")
        return bearer_token

    except ClientError as error:
        if error.response["Error"]["Code"] == "NotAuthorizedException":
            print(f"Authentication failed: Invalid credentials for user {username}")
        elif error.response["Error"]["Code"] == "UserNotFoundException":
            print(f"Authentication failed: User {username} not found")
        elif error.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"Authentication failed: Client ID {client_id} not found")
        else:
            error_message = _format_error_message(error)
            print(f"Cognito Client Error: {error_message}")
        return None
    except Exception as error:
        print(f"Unexpected error getting bearer token: {str(error)}")
        return None


def create_gateway_iam_role(
    lambda_arns: List[str],
    role_name: str = GATEWAY_AGENTCORE_ROLE_NAME,
    policy_name: str = GATEWAY_AGENTCORE_POLICY_NAME,
) -> Optional[str]:
    """Create IAM role for AgentCore Gateway with Lambda invoke permissions.

    Args:
        lambda_arns: List of Lambda function ARNs to grant invoke permissions
        role_name: Name for the IAM role
        policy_name: Name for the inline policy

    Returns:
        Role ARN string or None if creation fails
    """
    session = boto3.Session()
    region = session.region_name

    iam_client = boto3.client("iam", region_name=region)

    try:
        # Create the IAM role
        print(f"Creating IAM role: {role_name}")
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(GATEWAY_AGENTCORE_TRUST_POLICY),
            Description="IAM role for AgentCore Gateway to invoke Lambda functions",
        )
        role_arn = response["Role"]["Arn"]

        # Create the inline policy document
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "InvokeFunction",
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": lambda_arns,
                }
            ],
        }

        # Attach the inline policy
        print(f"Attaching policy: {policy_name}")
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
        )

        print(f"Gateway IAM role created successfully: {role_arn}")
        return role_arn

    except ClientError as error:
        if error.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"IAM role {role_name} already exists. Retrieving existing role...")
            response = iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]

            # Update the policy if role exists
            try:
                policy_document = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "InvokeFunction",
                            "Effect": "Allow",
                            "Action": "lambda:InvokeFunction",
                            "Resource": lambda_arns,
                        }
                    ],
                }

                iam_client.put_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name,
                    PolicyDocument=json.dumps(policy_document),
                )
                print(f"Updated policy for existing role: {role_arn}")

            except ClientError as policy_error:
                print(f"Warning: Could not update policy: {_format_error_message(policy_error)}")

            return role_arn
        else:
            error_message = _format_error_message(error)
            print(f"Error creating IAM role: {error_message}")
            return None
    except Exception as error:
        print(f"Unexpected error creating IAM role: {str(error)}")
        return None


def _extract_function_name_from_arn(lambda_arn: str) -> str:
    """Extract function name from Lambda ARN.

    Args:
        lambda_arn: Lambda function ARN

    Returns:
        Function name extracted from ARN

    Example:
        arn:aws:lambda:us-east-1:123456789012:function:my-function -> my-function
    """
    # ARN format: arn:aws:lambda:region:account:function:function-name
    if lambda_arn.startswith("arn:aws:lambda:"):
        return lambda_arn.split(":")[-1]
    else:
        # If it's already a function name, return as is
        return lambda_arn


def delete_gateway_lambda(lambda_function_arn: str) -> bool:
    """Delete Lambda function and associated IAM role.

    Args:
        lambda_function_arn: ARN or name of the Lambda function to delete

    Returns:
        True if deletion successful, False otherwise
    """
    session = boto3.Session()
    region = session.region_name

    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    # Extract function name from ARN
    lambda_function_name = _extract_function_name_from_arn(lambda_function_arn)
    role_name = f"{lambda_function_name}_lambda_iamrole"

    try:
        # Delete Lambda function (can use ARN or name)
        print(f"Deleting Lambda function: {lambda_function_name}")
        lambda_client.delete_function(FunctionName=lambda_function_arn)
        print(f"Lambda function {lambda_function_name} deleted successfully")

        # Delete IAM role and detach policies
        try:
            print(f"Detaching policies from IAM role: {role_name}")
            iam_client.detach_role_policy(
                RoleName=role_name,
                PolicyArn=LAMBDA_EXECUTION_ROLE_POLICY,
            )

            print(f"Deleting IAM role: {role_name}")
            iam_client.delete_role(RoleName=role_name)
            print(f"IAM role {role_name} deleted successfully")

        except ClientError as role_error:
            if role_error.response["Error"]["Code"] == "NoSuchEntity":
                print(f"IAM role {role_name} not found, skipping")
            else:
                print(f"Warning: Could not delete IAM role: {_format_error_message(role_error)}")

        return True

    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"Lambda function {lambda_function_name} not found")
            return False
        else:
            error_message = _format_error_message(error)
            print(f"Error deleting Lambda function: {error_message}")
            return False
    except Exception as error:
        print(f"Unexpected error deleting Lambda function: {str(error)}")
        return False


def delete_gateway_iam_role(
    role_name: str = GATEWAY_AGENTCORE_ROLE_NAME,
    policy_name: str = GATEWAY_AGENTCORE_POLICY_NAME,
) -> bool:
    """Delete IAM role for AgentCore Gateway.

    Args:
        role_name: Name of the IAM role to delete
        policy_name: Name of the inline policy to delete

    Returns:
        True if deletion successful, False otherwise
    """
    session = boto3.Session()
    region = session.region_name

    iam_client = boto3.client("iam", region_name=region)

    try:
        # Delete inline policy first
        print(f"Deleting inline policy: {policy_name}")
        iam_client.delete_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
        )
        print(f"Inline policy {policy_name} deleted successfully")

        # Delete IAM role
        print(f"Deleting IAM role: {role_name}")
        iam_client.delete_role(RoleName=role_name)
        print(f"IAM role {role_name} deleted successfully")

        return True

    except ClientError as error:
        if error.response["Error"]["Code"] == "NoSuchEntity":
            print(f"IAM role {role_name} or policy {policy_name} not found")
            return False
        else:
            error_message = _format_error_message(error)
            print(f"Error deleting IAM role: {error_message}")
            return False
    except Exception as error:
        print(f"Unexpected error deleting IAM role: {str(error)}")
        return False


def delete_cognito_user_pool(
    pool_name: str = COGNITO_POOL_NAME,
    username: str = COGNITO_DEFAULT_USERNAME,
) -> bool:
    """Delete Cognito User Pool and associated resources.

    Args:
        pool_name: Name of the Cognito User Pool to delete
        username: Username to delete from the pool

    Returns:
        True if deletion successful, False otherwise
    """
    session = boto3.Session()
    region = session.region_name

    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        # Find the User Pool by name
        print(f"Finding User Pool: {pool_name}")
        response = cognito_client.list_user_pools(MaxResults=50)

        pool_id = None
        for pool in response["UserPools"]:
            if pool["Name"] == pool_name:
                pool_id = pool["Id"]
                break

        if not pool_id:
            print(f"User Pool {pool_name} not found")
            return False

        # Delete user first
        try:
            print(f"Deleting user: {username}")
            cognito_client.admin_delete_user(
                UserPoolId=pool_id,
                Username=username,
            )
            print(f"User {username} deleted successfully")
        except ClientError as user_error:
            if user_error.response["Error"]["Code"] == "UserNotFoundException":
                print(f"User {username} not found, skipping")
            else:
                print(f"Warning: Could not delete user: {_format_error_message(user_error)}")

        # Delete User Pool (this will also delete app clients)
        print(f"Deleting User Pool: {pool_name}")
        cognito_client.delete_user_pool(UserPoolId=pool_id)
        print(f"User Pool {pool_name} deleted successfully")

        return True

    except ClientError as error:
        error_message = _format_error_message(error)
        print(f"Error deleting Cognito User Pool: {error_message}")
        return False
    except Exception as error:
        print(f"Unexpected error deleting Cognito User Pool: {str(error)}")
        return False


def setup_cognito_user_pool(
    pool_name: str = COGNITO_POOL_NAME,
    client_name: str = COGNITO_CLIENT_NAME,
    username: str = COGNITO_DEFAULT_USERNAME,
    temp_password: str = COGNITO_DEFAULT_TEMP_PASSWORD,
    permanent_password: str = COGNITO_DEFAULT_PASSWORD,
) -> Optional[Dict[str, str]]:
    """Set up Cognito User Pool with app client and test user.

    Args:
        pool_name: Name for the Cognito User Pool
        client_name: Name for the App Client
        username: Username for the test user
        temp_password: Temporary password for the test user
        permanent_password: Permanent password for the test user

    Returns:
        Dictionary with client_id and discovery_url or None if setup fails
    """
    session = boto3.Session()
    region = session.region_name

    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        pool_id = _create_cognito_user_pool(cognito_client, pool_name)
        client_id = _create_cognito_app_client(cognito_client, pool_id, client_name)

        _create_cognito_user(cognito_client, pool_id, username, temp_password, permanent_password)

        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

        # Output the required values
        print(f"Pool ID: {pool_id}")
        print(f"Discovery URL: {discovery_url}")
        print(f"Client ID: {client_id}")

        return {
            "client_id": client_id,
            "discovery_url": discovery_url,
        }

    except ClientError as error:
        error_message = _format_error_message(error)
        print(f"Cognito Client Error: {error_message}")
        return None
    except Exception as error:
        print(f"Unexpected error setting up Cognito: {str(error)}")
        return None
