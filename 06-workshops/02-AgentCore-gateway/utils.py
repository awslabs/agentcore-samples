import boto3
import json
import time
from boto3.session import Session
import botocore
from botocore.exceptions import ClientError
import requests


def setup_cognito_user_pool():
    boto_session = Session()
    region = boto_session.region_name

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

        # Create User
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username="testuser",
            TemporaryPassword="Temp123!",  # pragma: allowlist secret  -- tutorial-only test credential
            MessageAction="SUPPRESS",
        )

        # Set Permanent Password
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username="testuser",
            Password="MyPassword123!",  # pragma: allowlist secret  -- tutorial-only test credential
            Permanent=True,
        )

        # Authenticate User and get Access Token
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": "testuser", "PASSWORD": "MyPassword123!"},  # pragma: allowlist secret
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


def get_or_create_user_pool(cognito, USER_POOL_NAME):
    response = cognito.list_user_pools(MaxResults=60)
    for pool in response["UserPools"]:
        if pool["Name"] == USER_POOL_NAME:
            user_pool_id = pool["Id"]
            response = cognito.describe_user_pool(UserPoolId=user_pool_id)

            # Get the domain from user pool description
            user_pool = response.get("UserPool", {})
            domain = user_pool.get("Domain")

            if domain:
                region = user_pool_id.split("_")[0] if "_" in user_pool_id else REGION  # noqa: F821
                domain_url = f"https://{domain}.auth.{region}.amazoncognito.com"
                print(f"Found domain for user pool {user_pool_id}: {domain} ({domain_url})")
            else:
                print(f"No domains found for user pool {user_pool_id}")
            return pool["Id"]
    print("Creating new user pool")
    created = cognito.create_user_pool(PoolName=USER_POOL_NAME)
    user_pool_id = created["UserPool"]["Id"]
    user_pool_id_without_underscore_lc = user_pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(Domain=user_pool_id_without_underscore_lc, UserPoolId=user_pool_id)
    print("Domain created as well")
    return created["UserPool"]["Id"]


def get_or_create_resource_server(cognito, user_pool_id, RESOURCE_SERVER_ID, RESOURCE_SERVER_NAME, SCOPES):
    try:
        existing = cognito.describe_resource_server(  # noqa: F841
            UserPoolId=user_pool_id, Identifier=RESOURCE_SERVER_ID
        )
        return RESOURCE_SERVER_ID
    except cognito.exceptions.ResourceNotFoundException:
        print("creating new resource server")
        cognito.create_resource_server(
            UserPoolId=user_pool_id,
            Identifier=RESOURCE_SERVER_ID,
            Name=RESOURCE_SERVER_NAME,
            Scopes=SCOPES,
        )
        return RESOURCE_SERVER_ID


def get_or_create_m2m_client(cognito, user_pool_id, CLIENT_NAME, RESOURCE_SERVER_ID, SCOPES=None):
    response = cognito.list_user_pool_clients(UserPoolId=user_pool_id, MaxResults=60)
    for client in response["UserPoolClients"]:
        if client["ClientName"] == CLIENT_NAME:
            describe = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client["ClientId"])
            return client["ClientId"], describe["UserPoolClient"]["ClientSecret"]
    print("creating new m2m client")

    # Default scopes if not provided (for backward compatibility)
    if SCOPES is None:
        SCOPES = [
            f"{RESOURCE_SERVER_ID}/gateway:read",
            f"{RESOURCE_SERVER_ID}/gateway:write",
        ]

    created = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=SCOPES,
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH"],
    )
    return (
        created["UserPoolClient"]["ClientId"],
        created["UserPoolClient"]["ClientSecret"],
    )


def get_token(
    user_pool_id: str,
    client_id: str,
    client_secret: str,
    scope_string: str,
    REGION: str,
) -> dict:
    try:
        user_pool_id_without_underscore = user_pool_id.replace("_", "")
        url = f"https://{user_pool_id_without_underscore}.auth.{REGION}.amazoncognito.com/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope_string,
        }
        print(client_id)
        response = requests.post(url, headers=headers, data=data, timeout=3600)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        return {"error": str(err)}


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
                "Effect": "Allow",
                "Resource": "*",
                "Action": "s3:GetObject",
            },
            {"Effect": "Allow", "Resource": "*", "Action": "lambda:InvokeFunction"},
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:*", "iam:PassRole"],
                "Resource": "*",
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
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
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


def create_agentcore_gateway_role(gateway_name):
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "VisualEditor0",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                ],
                "Resource": "*",
            }
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
    # Create IAM Role for the Lambda function
    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

        # Pause to make sure role is created
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(RoleName=agentcore_gateway_role_name, MaxItems=100)
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=agentcore_gateway_role_name, PolicyName=policy_name)
        print(f"deleting {agentcore_gateway_role_name}")
        iam_client.delete_role(RoleName=agentcore_gateway_role_name)
        print(f"recreating {agentcore_gateway_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    # Attach the AWSLambdaBasicExecutionRole policy
    print(f"attaching role policy {agentcore_gateway_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_gateway_role_name,
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role


def create_agentcore_gateway_role_with_region(gateway_name, region):
    """
    Create an IAM role for AgentCore Gateway with explicit region specification.

    Args:
        gateway_name: Name of the gateway
        region: AWS region where the gateway will be deployed

    Returns:
        IAM role response
    """
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "VisualEditor0",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                ],
                "Resource": "*",
            }
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

    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(RoleName=agentcore_gateway_role_name, MaxItems=100)
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=agentcore_gateway_role_name, PolicyName=policy_name)
        print(f"deleting {agentcore_gateway_role_name}")
        iam_client.delete_role(RoleName=agentcore_gateway_role_name)
        print(f"recreating {agentcore_gateway_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    print(f"attaching role policy {agentcore_gateway_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_gateway_role_name,
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role


def create_agentcore_gateway_role_s3_smithy(gateway_name):
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    boto_session = Session()
    region = boto_session.region_name
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "VisualEditor0",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:*",
                    "bedrock:*",
                    "agent-credential-provider:*",
                    "iam:PassRole",
                    "secretsmanager:GetSecretValue",
                    "lambda:InvokeFunction",
                    "s3:*",
                ],
                "Resource": "*",
            }
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
    # Create IAM Role for the Lambda function
    try:
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

        # Pause to make sure role is created
        time.sleep(10)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print("Role already exists -- deleting and creating it again")
        policies = iam_client.list_role_policies(RoleName=agentcore_gateway_role_name, MaxItems=100)
        print("policies:", policies)
        for policy_name in policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=agentcore_gateway_role_name, PolicyName=policy_name)
        print(f"deleting {agentcore_gateway_role_name}")
        iam_client.delete_role(RoleName=agentcore_gateway_role_name)
        print(f"recreating {agentcore_gateway_role_name}")
        agentcore_iam_role = iam_client.create_role(
            RoleName=agentcore_gateway_role_name,
            AssumeRolePolicyDocument=assume_role_policy_document_json,
        )

    # Attach the AWSLambdaBasicExecutionRole policy
    print(f"attaching role policy {agentcore_gateway_role_name}")
    try:
        iam_client.put_role_policy(
            PolicyDocument=role_policy_document,
            PolicyName="AgentCorePolicy",
            RoleName=agentcore_gateway_role_name,
        )
    except Exception as e:
        print(e)

    return agentcore_iam_role


def create_gateway_lambda(lambda_function_code_path) -> dict[str, int]:
    boto_session = Session()
    region = boto_session.region_name

    return_resp = {"lambda_function_arn": "Pending", "exit_code": 1}

    # Initialize Cognito client
    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    role_name = "gateway_lambda_iamrole"
    role_arn = ""
    lambda_function_name = "gateway_lambda"

    print("Reading code from zip file")
    with open(lambda_function_code_path, "rb") as f:
        lambda_function_code = f.read()

    try:
        print("Creating IAM role for lambda function")

        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            Description="IAM role to be assumed by lambda function",
        )

        role_arn = response["Role"]["Arn"]

        print("Attaching policy to the IAM role")

        response = iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )

        print(f"Role '{role_name}' created successfully: {role_arn}")
        time.sleep(100)
    except botocore.exceptions.ClientError as error:
        if error.response["Error"]["Code"] == "EntityAlreadyExists":
            response = iam_client.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]
            print(f"IAM role {role_name} already exists. Using the same ARN {role_arn}")
        else:
            error_message = error.response["Error"]["Code"] + "-" + error.response["Error"]["Message"]
            print(f"Error creating role: {error_message}")
            return_resp["lambda_function_arn"] = error_message

    if role_arn != "":
        print("Creating lambda function")
        # Create lambda function
        try:
            lambda_response = lambda_client.create_function(
                FunctionName=lambda_function_name,
                Role=role_arn,
                Runtime="python3.12",
                Handler="lambda_function_code.lambda_handler",
                Code={"ZipFile": lambda_function_code},
                Description="Lambda function example for Bedrock AgentCore Gateway",
                PackageType="Zip",
            )

            return_resp["lambda_function_arn"] = lambda_response["FunctionArn"]
            return_resp["exit_code"] = 0
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] == "ResourceConflictException":
                response = lambda_client.get_function(FunctionName=lambda_function_name)
                lambda_arn = response["Configuration"]["FunctionArn"]
                print(f"AWS Lambda function {lambda_function_name} already exists. Using the same ARN {lambda_arn}")
                return_resp["lambda_function_arn"] = lambda_arn
            else:
                error_message = error.response["Error"]["Code"] + "-" + error.response["Error"]["Message"]
                print(f"Error creating lambda function: {error_message}")
                return_resp["lambda_function_arn"] = error_message

    return return_resp


def delete_gateway(gateway_client, gatewayId):
    print("Deleting all targets for gateway", gatewayId)
    list_response = gateway_client.list_gateway_targets(gatewayIdentifier=gatewayId, maxResults=100)
    for item in list_response["items"]:
        targetId = item["targetId"]
        print("Deleting target ", targetId)
        gateway_client.delete_gateway_target(gatewayIdentifier=gatewayId, targetId=targetId)
        time.sleep(5)
    print("Deleting gateway ", gatewayId)
    gateway_client.delete_gateway(gatewayIdentifier=gatewayId)


def delete_all_gateways(gateway_client):
    try:
        list_response = gateway_client.list_gateways(maxResults=100)
        for item in list_response["items"]:
            gatewayId = item["gatewayId"]
            delete_gateway(gatewayId)
    except Exception as e:
        print(e)


def get_current_role_arn():
    sts_client = boto3.client("sts")
    role_arn = sts_client.get_caller_identity()["Arn"]
    return {role_arn}


def create_gateway_invoke_tool_role(role_name, gateway_id, current_arn):
    # Normalize current_arn
    if isinstance(current_arn, (list, set, tuple)):
        current_arn = list(current_arn)[0]
    current_arn = str(current_arn)

    # AWS clients
    boto_session = Session()
    region = boto_session.region_name
    iam_client = boto3.client("iam", region_name=region)
    sts_client = boto3.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]

    # --- Trust policy (AssumeRolePolicyDocument) ---
    assume_role_policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRoleByAgentCore",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": ["sts:AssumeRole"],
            },
            {
                "Sid": "AllowCallerToAssume",
                "Effect": "Allow",
                "Principal": {"AWS": [current_arn]},
                "Action": ["sts:AssumeRole"],
            },
        ],
    }
    assume_role_policy_json = json.dumps(assume_role_policy_document)

    # ---  Inline role policy (Bedrock gateway invoke) ---
    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}",
            }
        ],
    }
    role_policy_json = json.dumps(role_policy)

    # --- Create or update IAM role ---
    try:
        agentcoregw_iam_role = iam_client.create_role(
            RoleName=role_name, AssumeRolePolicyDocument=assume_role_policy_json
        )
        print(f"Created new role: {role_name}")
        time.sleep(3)
    except iam_client.exceptions.EntityAlreadyExistsException:
        print(f"Role '{role_name}' already exists — updating trust and inline policy.")
        iam_client.update_assume_role_policy(RoleName=role_name, PolicyDocument=assume_role_policy_json)
        for policy_name in iam_client.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        agentcoregw_iam_role = iam_client.get_role(RoleName=role_name)

    # Attach inline role policy (gateway invoke)
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="AgentCorePolicy",
        PolicyDocument=role_policy_json,
    )

    role_arn = agentcoregw_iam_role["Role"]["Arn"]

    # ---  Ensure current_arn can assume role (with retry) ---
    arn_parts = current_arn.split(":")
    resource_type, resource_name = arn_parts[5].split("/", 1)

    assume_policy = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": role_arn}],
    }

    # Attach assume-role policy if user/role
    try:
        if resource_type == "user":
            iam_client.put_user_policy(
                UserName=resource_name,
                PolicyName=f"AllowAssume_{role_name}",
                PolicyDocument=json.dumps(assume_policy),
            )
        elif resource_type == "role":
            iam_client.put_role_policy(
                RoleName=resource_name,
                PolicyName=f"AllowAssume_{role_name}",
                PolicyDocument=json.dumps(assume_policy),
            )
    except ClientError as e:
        print(f"Unable to attach assume-role policy: {e}")
        print("Make sure the caller has iam:PutUserPolicy or iam:PutRolePolicy permission.")

    # Retry loop for eventual consistency
    max_retries = 5
    for i in range(max_retries):
        try:
            sts_client.assume_role(RoleArn=role_arn, RoleSessionName="testSession")
            print(f"Caller {current_arn} can now assume role {role_name}")
            break
        except ClientError as e:
            if "AccessDenied" in str(e):
                print(f"Attempt {i + 1}/{max_retries}: AccessDenied, retrying in 3s...")
                time.sleep(3)
            else:
                raise
    else:
        raise RuntimeError(f"Failed to assume role {role_name} after {max_retries} retries")

    print(f" Role '{role_name}' is ready and {current_arn} can invoke the Bedrock Agent Gateway.")
    return agentcoregw_iam_role


def get_client_secrets(cognito_client, user_pool_id, client_configs):
    print("Retrieving client secrets from Cognito...")
    client_secrets = {}

    for client_config in client_configs:
        try:
            response = cognito_client.describe_user_pool_client(
                UserPoolId=user_pool_id, ClientId=client_config["client_id"]
            )
            client_secrets[client_config["client_id"]] = response["UserPoolClient"]["ClientSecret"]
            print(f"  ✓ Retrieved secret for {client_config['name']}")
        except Exception as e:
            print(f"  ✗ Failed to get secret for {client_config['name']}: {e}")

    print(f"\n✓ Retrieved {len(client_secrets)} client secrets")
    return client_secrets


def create_dynamodb_table(table_name, key_schema, attribute_definitions, region="us-east-1"):
    """
    Create DynamoDB table with specified schema.
    """
    dynamodb_client = boto3.client("dynamodb", region_name=region)

    try:
        response = dynamodb_client.create_table(  # noqa: F841
            TableName=table_name,
            KeySchema=key_schema,
            AttributeDefinitions=attribute_definitions,
            BillingMode="PAY_PER_REQUEST",
        )

        print(f"✓ Table created: {table_name}")

        # Wait for table to be active
        waiter = dynamodb_client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)
        print("  Table is active")

        return table_name

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"⚠ Table already exists: {table_name}")
            return table_name
        else:
            raise


def batch_write_dynamodb(table_name, items, region="us-east-1"):
    """
    Batch write items to DynamoDB table.
    """
    from datetime import datetime

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    # Batch write
    with table.batch_writer() as batch:
        for item in items:
            # Add timestamps if not present
            if "CreatedAt" not in item:
                item["CreatedAt"] = datetime.utcnow().isoformat()
            if "UpdatedAt" not in item:
                item["UpdatedAt"] = datetime.utcnow().isoformat()
            batch.put_item(Item=item)

    print(f"✓ Wrote {len(items)} items to {table_name}")
    return len(items)


def create_lambda_role_with_policies(role_name, policy_statements, description="Lambda execution role"):
    iam_client = boto3.client("iam")

    # Trust policy for Lambda
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=description,
        )
        role_arn = role_response["Role"]["Arn"]
        print(f"✓ IAM role created: {role_name}")

    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"⚠ Role already exists: {role_name}")
            role_response = iam_client.get_role(RoleName=role_name)
            role_arn = role_response["Role"]["Arn"]
        else:
            raise

    # Attach basic Lambda execution policy
    iam_client.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    # Attach custom policies if provided
    if policy_statements:
        custom_policy = {"Version": "2012-10-17", "Statement": policy_statements}

        try:
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="CustomPolicy",
                PolicyDocument=json.dumps(custom_policy),
            )
            print("  ✓ Custom policy attached")
        except Exception as e:
            print(f"  ⚠ Policy error: {e}")

    # Wait for role to propagate
    time.sleep(10)

    return role_arn


def deploy_lambda_function(
    function_name,
    role_arn,
    lambda_code_path,
    environment_vars=None,
    description="Lambda function",
    timeout=30,
    memory_size=256,
    region="us-east-1",
):
    """
    Deploy Lambda function from Python code file.
    """
    import zipfile
    import io
    from pathlib import Path

    lambda_client = boto3.client("lambda", region_name=region)

    # Read Lambda code
    lambda_code_path = Path(lambda_code_path)
    if not lambda_code_path.exists():
        raise FileNotFoundError(f"Lambda code not found: {lambda_code_path}")

    with open(lambda_code_path, "r") as f:
        lambda_code = f.read()

    # Create deployment package
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("lambda_function.py", lambda_code)

    zip_buffer.seek(0)
    deployment_package = zip_buffer.read()

    # Build function config
    function_config = {
        "FunctionName": function_name,
        "Runtime": "python3.9",
        "Role": role_arn,
        "Handler": "lambda_function.lambda_handler",
        "Code": {"ZipFile": deployment_package},
        "Description": description,
        "Timeout": timeout,
        "MemorySize": memory_size,
    }

    # Add environment variables if provided
    if environment_vars:
        function_config["Environment"] = {"Variables": environment_vars}

    try:
        response = lambda_client.create_function(**function_config)
        lambda_arn = response["FunctionArn"]
        print(f"✓ Lambda created: {function_name}")

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print(f"⚠ Lambda already exists: {function_name}")
            response = lambda_client.get_function(FunctionName=function_name)
            lambda_arn = response["Configuration"]["FunctionArn"]
        else:
            raise

    return lambda_arn


def grant_gateway_invoke_permission(function_name, region="us-east-1"):
    """
    Grant Gateway permission to invoke the Lambda interceptor.

    Args:
        function_name: Name of the Lambda function
        region: AWS region
    """
    lambda_client = boto3.client("lambda", region_name=region)
    sts_client = boto3.client("sts")
    account_id = sts_client.get_caller_identity()["Account"]

    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId="AllowGatewayInvoke",
            Action="lambda:InvokeFunction",
            Principal="bedrock-agentcore.amazonaws.com",
            SourceArn=f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
        )
        print("✓ Gateway invoke permission added to Lambda")
        print("  Principal: bedrock-agentcore.amazonaws.com")

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print("⚠ Permission already exists (this is fine)")
        else:
            print(f"⚠ Error adding permission: {e}")
            raise


def create_lambda_role(role_name, description="Lambda execution role"):
    """
    Create basic IAM role for Lambda with execution permissions.

    Args:
        role_name (str): Name of the IAM role
        description (str): Role description

    Returns:
        str: Role ARN
    """
    iam_client = boto3.client("iam")

    # Trust policy for Lambda
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=description,
        )
        role_arn = role_response["Role"]["Arn"]
        print(f"✓ IAM role created: {role_name}")

    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"⚠ Role already exists: {role_name}")
            role_response = iam_client.get_role(RoleName=role_name)
            role_arn = role_response["Role"]["Arn"]
        else:
            raise

    # Attach basic Lambda execution policy
    iam_client.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    # Wait for role to propagate
    time.sleep(10)

    return role_arn


def delete_gateway_targets(gateway_client, gateway_id, target_ids):
    """
    Delete multiple gateway targets.
    """
    print(f"Deleting {len(target_ids)} gateway targets...")
    for target_id in target_ids:
        try:
            gateway_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            print(f"  ✓ Deleted target: {target_id}")
        except Exception as e:
            print(f"  ✗ Failed to delete target {target_id}: {e}")
        time.sleep(2)


def delete_lambda_functions(function_names, region="us-east-1"):
    """
    Delete multiple Lambda functions.
    """
    lambda_client = boto3.client("lambda", region_name=region)
    print(f"Deleting {len(function_names)} Lambda functions...")

    for function_name in function_names:
        try:
            lambda_client.delete_function(FunctionName=function_name)
            print(f"  ✓ Deleted Lambda: {function_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                print(f"  ✗ Failed to delete {function_name}: {e}")
        time.sleep(1)


def delete_iam_role(role_name):
    """
    Delete IAM role and its attached policies.

    """
    iam_client = boto3.client("iam")

    try:
        # Detach managed policies
        attached_policies = iam_client.list_attached_role_policies(RoleName=role_name)
        for policy in attached_policies["AttachedPolicies"]:
            iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

        # Delete inline policies
        inline_policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in inline_policies["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # Delete role
        iam_client.delete_role(RoleName=role_name)
        print(f"✓ Deleted IAM role: {role_name}")

    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            print(f"✗ Failed to delete role {role_name}: {e}")


def delete_cognito_user_pool(user_pool_id, region="us-east-1"):
    """
    Delete Cognito user pool.

    """
    cognito_client = boto3.client("cognito-idp", region_name=region)

    try:
        cognito_client.delete_user_pool(UserPoolId=user_pool_id)
        print(f"✓ Deleted Cognito user pool: {user_pool_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            print(f"✗ Failed to delete user pool: {e}")


def delete_dynamodb_table(table_name, region="us-east-1"):
    """
    Delete DynamoDB table.

    """
    dynamodb_client = boto3.client("dynamodb", region_name=region)

    try:
        dynamodb_client.delete_table(TableName=table_name)
        print(f"✓ Deleted DynamoDB table: {table_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            print(f"✗ Failed to delete table: {e}")
