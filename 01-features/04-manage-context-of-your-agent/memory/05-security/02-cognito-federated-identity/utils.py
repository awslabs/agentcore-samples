import jwt
import boto3
import json
import ssl
import time
from jwt import PyJWKClient


def setup_cognito_user_pool(region, memory_id):
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    try:
        # Create User Pool
        user_pool_response = cognito_client.create_user_pool(
            PoolName="MCPServerPool", Policies={"PasswordPolicy": {"MinimumLength": 8}}
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

        # Create User 1
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username="testuser1",
            TemporaryPassword="Temp123!",  # pragma: allowlist secret
            MessageAction="SUPPRESS",
        )
        # Set Permanent Password for User 1
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username="testuser1",
            Password="MyPassword123!",  # pragma: allowlist secret
            Permanent=True,
        )

        # Create User 2
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username="testuser2",
            TemporaryPassword="Temp123!",  # pragma: allowlist secret
            MessageAction="SUPPRESS",
        )
        # Set Permanent Password for User 2
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username="testuser2",
            Password="MyPassword456!",  # pragma: allowlist secret
            Permanent=True,
        )

        # Authenticate User 1 and get Access Token
        auth_response1 = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": "testuser1", "PASSWORD": "MyPassword123!"},  # pragma: allowlist secret
        )
        bearer_token1 = auth_response1["AuthenticationResult"]["AccessToken"]
        id_token1 = auth_response1["AuthenticationResult"]["IdToken"]

        # Authenticate User 2 and get Access Token
        auth_response2 = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": "testuser2", "PASSWORD": "MyPassword456!"},
        )
        bearer_token2 = auth_response2["AuthenticationResult"]["AccessToken"]
        id_token2 = auth_response2["AuthenticationResult"]["IdToken"]

        # Create Identity Pool federated with User Pool
        identity_pool_info = create_cognito_identity_pool(pool_id, client_id, region, memory_id)

        # Output the required values
        print(f"Pool id: {pool_id}")
        print(f"Discovery URL: https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration")
        print(f"Client ID: {client_id}")
        print(f"Identity Pool ID: {identity_pool_info['identity_pool_id']}")
        print(f"User 1 Bearer Token: {bearer_token1}")
        print(f"User 2 Bearer Token: {bearer_token2}")
        print(f"User 1 Id Token: {id_token1}")
        print(f"User 2 Id Token: {id_token2}")

        # Return values for further processing
        return {
            "pool_id": pool_id,
            "client_id": client_id,
            "identity_pool_id": identity_pool_info["identity_pool_id"],
            "authenticated_role_arn": identity_pool_info["authenticated_role_arn"],
            "bearer_tokens": {"testuser1": bearer_token1, "testuser2": bearer_token2},
            "id_tokens": {"testuser1": id_token1, "testuser2": id_token2},
            "discovery_url": f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def reauthenticate_users(client_id, region, users=None):
    """
    Reauthenticate one or more Cognito users and get their access and ID tokens.

    Parameters:
    - client_id: The Cognito app client ID
    - region: AWS region
    - users: Dictionary of username-password pairs to authenticate. If None, defaults to testuser1 and testuser2.

    Returns:
    - Dictionary with access_tokens and id_tokens for each user
    """
    # Default users if not specified
    if users is None:
        users = {"testuser1": "MyPassword123!", "testuser2": "MyPassword456!"}

    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)

    # Store tokens for each user
    result = {"access_tokens": {}, "id_tokens": {}}

    # Authenticate each user and get their tokens
    for username, password in users.items():
        try:
            auth_response = cognito_client.initiate_auth(
                ClientId=client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": username, "PASSWORD": password},
            )
            result["access_tokens"][username] = auth_response["AuthenticationResult"]["AccessToken"]
            result["id_tokens"][username] = auth_response["AuthenticationResult"]["IdToken"]
            print(f"Successfully authenticated {username}")
        except Exception as e:
            print(f"Error authenticating {username}: {str(e)}")
            result["access_tokens"][username] = None
            result["id_tokens"][username] = None

    return result


def get_user_sub(access_token: str, region: str, user_pool_id: str) -> str:
    """
    Verifies a Cognito access token against JWKS and returns the user's sub (unique ID).

    :param access_token: The JWT access token string
    :param region: AWS region of the Cognito User Pool
    :param user_pool_id: The Cognito User Pool ID
    :return: The user's 'sub' claim if the token is valid
    :raises jwt.InvalidTokenError: If verification fails
    """
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
    ssl_context = ssl.create_default_context()
    jwks_client = PyJWKClient(jwks_url, ssl_context=ssl_context)
    signing_key = jwks_client.get_signing_key_from_jwt(access_token)

    decoded = jwt.decode(
        access_token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        options={"require": ["exp", "iat", "iss", "token_use"]},
    )

    if decoded.get("token_use") != "access":
        raise jwt.InvalidTokenError("Token is not an access token")

    return decoded["sub"]


def create_agentcore_role(agent_name, region):
    iam_client = boto3.client("iam")
    agentcore_role_name = f"agentcore-{agent_name}-role"
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowAgentToAssumeRole",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": f"arn:aws:iam::{account_id}:role/cognito_authenticated_*",
            },
            {
                "Sid": "BedrockPermissions",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                "Resource": [f"arn:aws:ecr:{region}:{account_id}:repository/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            },
            {
                "Sid": "ECRTokenAccess",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Effect": "Allow",
                "Resource": "*",
                "Action": "cloudwatch:PutMetricData",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                "Sid": "GetAgentAccessToken",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/{agent_name}-*",
                ],
            },
            {
                "Sid": "CognitoIdentityPoolAccess",
                "Effect": "Allow",
                "Action": [
                    "cognito-identity:GetId",
                    "cognito-identity:GetCredentialsForIdentity",
                ],
                "Resource": "*",
            },
            {
                "Sid": "CognitoUserPoolAccess",
                "Effect": "Allow",
                "Action": ["cognito-idp:GetUser"],
                "Resource": [f"arn:aws:cognito-idp:{region}:{account_id}:userpool/*"],
            },
        ],
    }
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": f"{account_id}"},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    assume_role_policy_document_json = json.dumps(assume_role_policy_document)
    role_policy_document = json.dumps(role_policy)
    # Create IAM Role for the AgentCore Runtime
    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

        # Pause to make sure role is created
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(RoleName=agentcore_role_name, MaxItems=100)
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=agentcore_role_name, PolicyName=policy_name)
        print(f"deleting {agentcore_role_name}")
        iam_client.delete_role(RoleName=agentcore_role_name)
        print(f"recreating {agentcore_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    # Attach the AgentCore policy
    print(f"attaching role policy {agentcore_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_role_name,
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role


def create_cognito_identity_pool(user_pool_id, client_id, region, memory_id="*"):
    """
    Create a Cognito Identity Pool federated with a User Pool

    Args:
        user_pool_id: Cognito User Pool ID
        client_id: Cognito User Pool Client ID
        region: AWS region
        memory_id: Optional - Specific memory ID to restrict access to (default is "*" for all memories)
    """
    identity_client = boto3.client("cognito-identity", region_name=region)

    # Get AWS account ID
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    # Create the Identity Pool with User Pool as auth provider
    response = identity_client.create_identity_pool(
        IdentityPoolName="MemoryAgentIdentityPool",
        AllowUnauthenticatedIdentities=False,
        CognitoIdentityProviders=[
            {
                "ProviderName": f"cognito-idp.{region}.amazonaws.com/{user_pool_id}",
                "ClientId": client_id,
                "ServerSideTokenCheck": True,
            }
        ],
    )

    identity_pool_id = response["IdentityPoolId"]

    # Create a shorter, unique role name using just the last part of the identity pool ID
    # This ensures we stay under the 64 character limit
    short_id = identity_pool_id.split(":")[-1][-12:].replace("-", "")  # Last 12 chars without dashes
    authenticated_role_name = f"cognito_auth_{short_id}"

    # Create roles for authenticated users
    iam_client = boto3.client("iam")

    authenticated_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:GetMemoryRecord",
                    "bedrock-agentcore:ListActors",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:DeleteMemoryRecord",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/{memory_id}"],
                "Condition": {"StringEquals": {"bedrock-agentcore:actorId": "${cognito-identity.amazonaws.com:sub}"}},
            },
            {
                "Effect": "Allow",
                "Action": ["mobileanalytics:PutEvents", "cognito-sync:*"],
                "Resource": "*",
            },
        ],
    }

    trust_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": "cognito-identity.amazonaws.com"},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {"cognito-identity.amazonaws.com:aud": identity_pool_id},
                    "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"},
                },
            }
        ],
    }

    # Create authenticated role
    try:
        auth_role = iam_client.create_role(
            RoleName=authenticated_role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy_document),
            Description=f"Role for Identity Pool {identity_pool_id}",
        )

        # Create shorter policy name too
        auth_policy_name = f"AuthPolicy_{short_id}"
        auth_policy = iam_client.create_policy(
            PolicyName=auth_policy_name,
            PolicyDocument=json.dumps(authenticated_policy_document),
        )

        iam_client.attach_role_policy(RoleName=authenticated_role_name, PolicyArn=auth_policy["Policy"]["Arn"])
    except iam_client.exceptions.EntityAlreadyExistsException:
        # Role already exists, get its ARN
        response = iam_client.get_role(RoleName=authenticated_role_name)
        auth_role = response

    # Set identity pool roles
    identity_client.set_identity_pool_roles(
        IdentityPoolId=identity_pool_id,
        Roles={"authenticated": auth_role["Role"]["Arn"]},
    )

    return {
        "identity_pool_id": identity_pool_id,
        "authenticated_role_arn": auth_role["Role"]["Arn"],
    }


def get_aws_credentials_for_identity(identity_pool_id, id_token, region, user_pool_id):
    """
    Get temporary AWS credentials for a Cognito identity using a User Pool ID token

    Args:
        identity_pool_id: Cognito Identity Pool ID
        id_token: ID token from Cognito User Pool authentication
        region: AWS region
        user_pool_id: Cognito User Pool ID

    Returns:
        Dictionary with AWS credentials
    """
    identity_client = boto3.client("cognito-identity", region_name=region)

    # Get ID from identity pool
    get_id_response = identity_client.get_id(
        IdentityPoolId=identity_pool_id,
        Logins={f"cognito-idp.{region}.amazonaws.com/{user_pool_id}": id_token},
    )
    identity_id = get_id_response["IdentityId"]

    # Get credentials for the identity
    get_credentials_response = identity_client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={f"cognito-idp.{region}.amazonaws.com/{user_pool_id}": id_token},
    )

    # Return the temporary credentials
    credentials = get_credentials_response["Credentials"]
    return {
        "access_key_id": credentials["AccessKeyId"],
        "secret_key": credentials["SecretKey"],
        "session_token": credentials["SessionToken"],
        "expiration": credentials["Expiration"],
        "identity_id": identity_id,
    }
