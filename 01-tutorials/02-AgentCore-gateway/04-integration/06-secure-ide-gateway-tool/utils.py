import boto3
import json
import time
from boto3.session import Session

USER_NAME = "testuser"
PASSWORD = "MyPassword123!"
TEMP_ADMIN_PASSWORD = "Temp123!"


def setup_cognito_user_pool(pool_name="MCPServerPool"):
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    try:
        # Create User Pool
        user_pool_response = cognito_client.create_user_pool(
            PoolName=pool_name, Policies={"PasswordPolicy": {"MinimumLength": 8}}
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
            Username=USER_NAME,
            TemporaryPassword=TEMP_ADMIN_PASSWORD,
            MessageAction="SUPPRESS",
        )
        # Set Permanent Password
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id, Username=USER_NAME, Password=PASSWORD, Permanent=True
        )
        # Authenticate User and get Access Token
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": USER_NAME, "PASSWORD": PASSWORD},
        )
        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
        refresh_token = auth_response["AuthenticationResult"]["RefreshToken"]
        # Output the required values
        print(f"Pool id: {pool_id}")
        print(
            f"Discovery URL: https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
        )
        print(f"Client ID: {client_id}")
        print(f"Bearer Token: {bearer_token}")
        print(f"Refresh Token: {refresh_token}")

        # Return values if needed for further processing
        return {
            "pool_id": pool_id,
            "client_id": client_id,
            "bearer_token": bearer_token,
            "refresh_token": refresh_token,
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
        AuthParameters={"USERNAME": USER_NAME, "PASSWORD": PASSWORD},
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    return bearer_token


def create_agentcore_role(agent_name):
    iam_client = boto3.client("iam")
    agentcore_role_name = f"agentcore-{agent_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
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
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/*"
                ],
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
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
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
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"
                    },
                },
            }
        ],
    }

    assume_role_policy_document_json = json.dumps(assume_role_policy_document)
    role_policy_document = json.dumps(role_policy)
    # Create IAM Role for the Lambda function
    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

        # Pause to make sure role is created
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(
            RoleName=agentcore_role_name, MaxItems=100
        )
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(
                RoleName=agentcore_role_name, PolicyName=policy_name
            )
        print(f"deleting {agentcore_role_name}")
        iam_client.delete_role(RoleName=agentcore_role_name)
        print(f"recreating {agentcore_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    # Attach the AWSLambdaBasicExecutionRole policy
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


# Additional utility functions for Cognito and Gateway management


def get_or_create_user_pool(cognito_client, pool_name):
    """
    Get existing user pool by name or create a new one.
    Returns the user pool ID.
    """
    # Check if pool already exists
    paginator = cognito_client.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == pool_name:
                return pool["Id"]

    # Create new pool
    response = cognito_client.create_user_pool(
        PoolName=pool_name, Policies={"PasswordPolicy": {"MinimumLength": 8}}
    )
    return response["UserPool"]["Id"]


def get_or_create_resource_server(
    cognito_client, user_pool_id, identifier, name, scopes
):
    """
    Get existing resource server or create a new one.
    """
    try:
        cognito_client.describe_resource_server(
            UserPoolId=user_pool_id, Identifier=identifier
        )
        return  # Already exists
    except cognito_client.exceptions.ResourceNotFoundException:
        pass

    cognito_client.create_resource_server(
        UserPoolId=user_pool_id, Identifier=identifier, Name=name, Scopes=scopes
    )


def get_or_create_m2m_client(
    cognito_client, user_pool_id, client_name, resource_server_id, scope_names
):
    """
    Get existing M2M client or create a new one.
    Returns (client_id, client_secret).
    """
    # Check if client exists
    paginator = cognito_client.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=user_pool_id, MaxResults=60):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == client_name:
                # Get client details including secret
                details = cognito_client.describe_user_pool_client(
                    UserPoolId=user_pool_id, ClientId=client["ClientId"]
                )
                return details["UserPoolClient"]["ClientId"], details[
                    "UserPoolClient"
                ].get("ClientSecret")

    # Create new client
    response = cognito_client.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=scope_names,
        AllowedOAuthFlowsUserPoolClient=True,
    )
    return response["UserPoolClient"]["ClientId"], response["UserPoolClient"][
        "ClientSecret"
    ]


def get_token(user_pool_id, client_id, client_secret, scope_string, REGION):
    """
    Get OAuth token from Cognito using client credentials flow.
    """
    import base64
    import requests

    # For client credentials, we need to use the domain
    # First, get or create a domain
    cognito = boto3.client("cognito-idp", region_name=REGION)

    try:
        domain_response = cognito.describe_user_pool(UserPoolId=user_pool_id)
        domain = domain_response["UserPool"].get("Domain")

        if not domain:
            # Create a domain
            import random
            import string

            domain = f"agentcore-{''.join(random.choices(string.ascii_lowercase + string.digits, k=8))}"
            cognito.create_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
            time.sleep(2)
    except Exception as e:
        print(f"Domain setup error: {e}")
        raise

    # Token endpoint
    token_endpoint = f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token"

    # Prepare credentials
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {credentials}",
    }

    data = {"grant_type": "client_credentials", "scope": scope_string}

    response = requests.post(token_endpoint, headers=headers, data=data)
    response.raise_for_status()
    return response.json()


def delete_gateway(gateway_client, gatewayId):
    """
    Delete a gateway and all its targets.
    """
    # First, list and delete all targets
    try:
        targets = gateway_client.list_gateway_targets(gatewayIdentifier=gatewayId)
        for target in targets.get("items", []):
            print(f"  Deleting target: {target['targetId']}")
            gateway_client.delete_gateway_target(
                gatewayIdentifier=gatewayId, targetIdentifier=target["targetId"]
            )
            time.sleep(2)
    except Exception as e:
        print(f"  Warning listing/deleting targets: {e}")

    # Delete the gateway
    gateway_client.delete_gateway(gatewayIdentifier=gatewayId)
    print(f"  Gateway {gatewayId} deleted")


def delete_cognito_user_pool(user_pool_id, region):
    """
    Delete a Cognito user pool and its domain.
    """
    cognito = boto3.client("cognito-idp", region_name=region)

    # Delete domain first if exists
    try:
        pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)
        domain = pool_info["UserPool"].get("Domain")
        if domain:
            cognito.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
            time.sleep(2)
    except Exception as e:
        print(f"  Warning deleting domain: {e}")

    # Delete the user pool
    cognito.delete_user_pool(UserPoolId=user_pool_id)
    print(f"  User pool {user_pool_id} deleted")


def delete_iam_role(role_name):
    """
    Delete an IAM role and its attached policies.
    """
    iam = boto3.client("iam")

    # Detach managed policies
    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    except Exception as e:
        print(f"  Warning detaching policies: {e}")

    # Delete inline policies
    try:
        inline = iam.list_role_policies(RoleName=role_name)
        for policy_name in inline["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
    except Exception as e:
        print(f"  Warning deleting inline policies: {e}")

    # Delete the role
    iam.delete_role(RoleName=role_name)
    print(f"  IAM role {role_name} deleted")
