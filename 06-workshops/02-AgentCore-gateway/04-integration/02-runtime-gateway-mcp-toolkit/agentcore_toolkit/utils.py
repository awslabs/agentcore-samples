import boto3
import json
import time
import os
from boto3.session import Session
import botocore
from botocore.exceptions import ClientError
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def wait_for_iam_role_propagation(iam_client, role_name, max_retries=30, delay=2):
    """Wait for IAM role to be available after creation."""
    for i in range(max_retries):
        try:
            iam_client.get_role(RoleName=role_name)
            return True
        except iam_client.exceptions.NoSuchEntityException:
            if i < max_retries - 1:
                time.sleep(delay)
            else:
                return False
    return False


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

        # Get credentials from environment variables
        username = os.getenv("COGNITO_USERNAME", "testuser")
        temp_password = os.getenv("COGNITO_TEMP_PASSWORD", "Temp123!")
        password = os.getenv("COGNITO_PASSWORD", "MyPassword123!")

        # Create User
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            TemporaryPassword=temp_password,
            MessageAction="SUPPRESS",
        )

        # Set Permanent Password
        cognito_client.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=True)

        # Authenticate User and get Access Token
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]

        # Output the required values
        print(f"Pool id: {pool_id}")
        print(f"Discovery URL: https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration")
        print("Client ID: <redacted>")
        print("Bearer Token: <redacted>")

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


def get_or_create_oauth2_credential_provider(region, identity_provider_name, runtime_cognito):
    cognito_provider_arn = ""
    identity_client = boto3.client("bedrock-agentcore-control", region_name=region)
    # Create OAuth2 credential provider
    try:
        cognito_provider = identity_client.create_oauth2_credential_provider(
            name=identity_provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "discoveryUrl": runtime_cognito["discovery_url"],
                    },
                    "clientId": runtime_cognito["client_id"],
                    "clientSecret": runtime_cognito["client_secret"],
                }
            },
        )
        cognito_provider_arn = cognito_provider["credentialProviderArn"]
    except Exception as e:
        if "already exists" in str(e):
            print(f"Identity provider {identity_provider_name} already exists")
            cognito_provider = identity_client.get_oauth2_credential_provider(name=identity_provider_name)
            cognito_provider_arn = cognito_provider["credentialProviderArn"]
        else:
            raise f"Got an Exception while creating Agentcore gateway:{str(e)}"
    return cognito_provider_arn


def get_or_create_agentcore_gateway(region, iam_role, auth_config, gw_config):
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
    gateway_id = ""
    gateway_url = ""
    try:
        print(f"Creating Gateway {gw_config['name']} with IAM Role {iam_role['Role']['Arn']}")
        response = gateway_client.create_gateway(
            name=gw_config["name"],
            roleArn=iam_role["Role"]["Arn"],
            protocolType="MCP",
            protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"], "searchType": "SEMANTIC"}},
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            description=gw_config["description"],
        )
        gateway_id = response["gatewayId"]
        gateway_url = response["gatewayUrl"]
    except Exception as e:
        if "already exists" in str(e):
            print("Gateway already exists, retrieving existing gateway...")
            response = gateway_client.list_gateways()
            for gateway in response["items"]:
                if gateway["name"] == gw_config["name"]:
                    gateway_id = gateway["gatewayId"]
                    response = gateway_client.get_gateway(gatewayIdentifier=gateway_id)
                    gateway_url = response["gatewayUrl"]
                    break
        else:
            raise f"Got an Exception while creating Agentcore gateway:{str(e)}"

    print(f"Gateway ID: {gateway_id}; Gateway URL: {gateway_url}")
    return {"gateway_id": gateway_id, "gateway_url": gateway_url}


def get_or_create_agentcore_gateway_target(region, target_creation_params):
    # Create gateway target
    gw_target_info = {}
    try:
        gateway_client = boto3.client("bedrock-agentcore-control", region_name=region)
        print(f"Target Creation Params: {target_creation_params}")  # codeql[py/clear-text-logging-sensitive-data]
        gw_target_info = gateway_client.create_gateway_target(
            name=target_creation_params["name"],
            gatewayIdentifier=target_creation_params["gateway_id"],
            targetConfiguration={"mcp": {"mcpServer": {"endpoint": target_creation_params["agent_url"]}}},
            credentialProviderConfigurations=[
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": target_creation_params["cognito_provider_arn"],
                            "scopes": [target_creation_params["scope_string"]],
                        }
                    },
                }
            ],
        )
        print(
            f"Gateway target {target_creation_params['name']} created successfully ✓"
        )  # codeql[py/clear-text-logging-sensitive-data]
    except Exception as e:
        if "already exists" in str(e):
            print(
                f"Gateway target {target_creation_params['name']} already exists, retrieving details..."  # codeql[py/clear-text-logging-sensitive-data]
            )
            response = gateway_client.list_gateway_targets(gatewayIdentifier=target_creation_params["gateway_id"])
            for target in response["items"]:
                if target["name"] == target_creation_params["name"]:
                    gw_target_info = target
                    break
        else:
            raise f"Got an Exception while creating Agentcore gateway target:{str(e)}"
    return gw_target_info


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
        response = requests.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as err:
        return {"error": str(err)}


def create_agentcore_role(agent_name, region=None):
    iam_client = boto3.client("iam")
    agentcore_role_name = f"agentcore-{agent_name}-role"
    if region is None:
        region = Session().region_name
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

        # Wait for role to be available
        if not wait_for_iam_role_propagation(iam_client, agentcore_role_name):
            print(f"Warning: Role {agentcore_role_name} may not be fully propagated")
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


def create_agentcore_gateway_role(gateway_name, region=None):
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    if region is None:
        region = Session().region_name
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

        # Wait for role to be available
        if not wait_for_iam_role_propagation(iam_client, agentcore_gateway_role_name):
            print(f"Warning: Role {agentcore_gateway_role_name} may not be fully propagated")
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


def create_agentcore_gateway_role_s3_smithy(gateway_name, region=None):
    iam_client = boto3.client("iam")
    agentcore_gateway_role_name = f"agentcore-{gateway_name}-role"
    if region is None:
        region = Session().region_name
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

        # Wait for role to be available
        if not wait_for_iam_role_propagation(iam_client, agentcore_gateway_role_name):
            print(f"Warning: Role {agentcore_gateway_role_name} may not be fully propagated")
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
        # Wait for role to be available
        if not wait_for_iam_role_propagation(iam_client, role_name):
            print(f"Warning: Role {role_name} may not be fully propagated")
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


def delete_gateway_target(gateway_client, gatewayId):
    print("Deleting all targets for gateway", gatewayId)
    list_response = gateway_client.list_gateway_targets(gatewayIdentifier=gatewayId, maxResults=100)
    for item in list_response["items"]:
        targetId = item["targetId"]
        print("Deleting target ", targetId)
        gateway_client.delete_gateway_target(gatewayIdentifier=gatewayId, targetId=targetId)
        # Brief pause for gateway target deletion
        time.sleep(1)


def delete_gateway(gateway_client, gateway_name):
    gateway_id = None
    try:
        list_response = gateway_client.list_gateways(maxResults=100)
        for item in list_response["items"]:
            print(item)
            if item["name"] == gateway_name:
                gateway_id = item["gatewayId"]
                delete_gateway_target(gateway_client, gateway_id)
                break
        print("Deleting gateway ", gateway_id)
        gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
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
        # Wait for role to be available
        if not wait_for_iam_role_propagation(iam_client, role_name):
            print(f"Warning: Role {role_name} may not be fully propagated")
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
                print(f"Attempt {i + 1}/{max_retries}: AccessDenied, retrying in 1s...")
                time.sleep(1)
            else:
                raise
    else:
        raise RuntimeError(f"Failed to assume role {role_name} after {max_retries} retries")

    print(f" Role '{role_name}' is ready and {current_arn} can invoke the Bedrock Agent Gateway.")
    return agentcoregw_iam_role
