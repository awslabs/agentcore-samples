import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict

import boto3
import yaml
from boto3.session import Session

sts_client = boto3.client("sts")

# Get AWS account details
REGION = boto3.session.Session().region_name

username = "testuser"
sm_name = "customer_support_agent"


role_name = f"CustomerSupportAssistantBedrockAgentCoreRole-{REGION}"
policy_name = f"CustomerSupportAssistantBedrockAgentCorePolicy-{REGION}"


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    ssm = boto3.client("ssm")

    response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)

    return response["Parameter"]["Value"]


def put_ssm_parameter(name: str, value: str, parameter_type: str = "String", with_encryption: bool = False) -> None:
    ssm = boto3.client("ssm")

    put_params = {
        "Name": name,
        "Value": value,
        "Type": parameter_type,
        "Overwrite": True,
    }

    if with_encryption:
        put_params["Type"] = "SecureString"

    ssm.put_parameter(**put_params)


def delete_ssm_parameter(name: str) -> None:
    ssm = boto3.client("ssm")
    try:
        ssm.delete_parameter(Name=name)
    except ssm.exceptions.ParameterNotFound:
        pass


def load_api_spec(file_path: str) -> list:
    with open(file_path, "r") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected a list in the JSON file")
    return data


def get_aws_region() -> str:
    session = Session()
    return session.region_name


def get_aws_account_id() -> str:
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]


def get_cognito_client_secret() -> str:
    client = boto3.client("cognito-idp")
    response = client.describe_user_pool_client(
        UserPoolId=get_ssm_parameter("/app/customersupport/agentcore/pool_id"),
        ClientId=get_ssm_parameter("/app/customersupport/agentcore/client_id"),
    )
    return response["UserPoolClient"]["ClientSecret"]


def read_config(file_path: str) -> Dict[str, Any]:
    """
    Read configuration from a file path. Supports JSON, YAML, and YML formats.

    Args:
        file_path (str): Path to the configuration file

    Returns:
        Dict[str, Any]: Configuration data as a dictionary

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file format is not supported or invalid
        yaml.YAMLError: If YAML parsing fails
        json.JSONDecodeError: If JSON parsing fails
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    # Get file extension to determine format
    _, ext = os.path.splitext(file_path.lower())

    try:
        with open(file_path, "r", encoding="utf-8") as file:
            if ext == ".json":
                return json.load(file)
            elif ext in [".yaml", ".yml"]:
                return yaml.safe_load(file)
            else:
                # Try to auto-detect format by attempting JSON first, then YAML
                content = file.read()
                file.seek(0)

                # Try JSON first
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    # Try YAML
                    try:
                        return yaml.safe_load(content)
                    except yaml.YAMLError:
                        raise ValueError(
                            f"Unsupported configuration file format: {ext}. Supported formats: .json, .yaml, .yml"
                        )

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in configuration file {file_path}: {e}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in configuration file {file_path}: {e}")
    except Exception as e:
        raise ValueError(f"Error reading configuration file {file_path}: {e}")


def save_customer_support_secret(secret_value):
    """Save a secret in AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)

    try:
        secrets_client.create_secret(
            Name=sm_name,
            SecretString=secret_value,
            Description="Secret containing the Cognito Configuration for the Customer Support Agent",
        )
        print("✅ Created secret")
    except secrets_client.exceptions.ResourceExistsException:
        secrets_client.update_secret(SecretId=sm_name, SecretString=secret_value)
        print("✅ Updated existing secret")
    except Exception as e:
        print(f"❌ Error saving secret: {str(e)}")
        return False
    return True


def get_customer_support_secret():
    """Get a secret value from AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)
    try:
        response = secrets_client.get_secret_value(SecretId=sm_name)
        return response["SecretString"]
    except Exception as e:
        print(f"Error getting secret: {str(e)}")
        return None


def delete_customer_support_secret():
    """Delete a secret from AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)
    try:
        secrets_client.delete_secret(SecretId=sm_name, ForceDeleteWithoutRecovery=True)
        print("✅ Deleted secret!")
        return True
    except Exception as e:
        print(f"❌ Error deleting secret: {str(e)}")
        return False


def get_or_create_cognito_pool(refresh_token=False):
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    try:
        # check for existing cognito pool
        cognito_config_str = get_customer_support_secret()
        cognito_config = json.loads(cognito_config_str)
        if refresh_token:
            cognito_config["bearer_token"] = reauthenticate_user(
                cognito_config["client_id"], cognito_config["client_secret"]
            )
        return cognito_config
    except Exception:
        print("No existing cognito config found. Creating a new one..")

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
            GenerateSecret=True,
            ExplicitAuthFlows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
                "ALLOW_USER_SRP_AUTH",
            ],
        )
        print(app_client_response["UserPoolClient"])
        client_id = app_client_response["UserPoolClient"]["ClientId"]
        client_secret = app_client_response["UserPoolClient"]["ClientSecret"]

        # Create User
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            TemporaryPassword="Temp123!",  # pragma: allowlist secret
            MessageAction="SUPPRESS",
        )

        # Set Permanent Password
        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username=username,
            Password="MyPassword123!",  # pragma: allowlist secret
            Permanent=True,
        )

        message = bytes(username + client_id, "utf-8")
        key = bytes(client_secret, "utf-8")
        secret_hash = base64.b64encode(hmac.new(key, message, digestmod=hashlib.sha256).digest()).decode()

        # Authenticate User and get Access Token
        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": "MyPassword123!",  # pragma: allowlist secret
                "SECRET_HASH": secret_hash,
            },
        )
        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
        # Output the required values
        print(f"Pool id: {pool_id}")
        print(f"Discovery URL: {discovery_url}")
        print(f"Client ID: {client_id}")
        print(f"Bearer Token: {bearer_token}")
        # Return values if needed for further processing
        cognito_config = {
            "pool_id": pool_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "secret_hash": secret_hash,
            "bearer_token": bearer_token,
            "discovery_url": discovery_url,
        }
        put_ssm_parameter("/app/customersupport/agentcore/client_id", client_id)
        put_ssm_parameter("/app/customersupport/agentcore/pool_id", pool_id)
        put_ssm_parameter("/app/customersupport/agentcore/cognito_discovery_url", discovery_url)
        put_ssm_parameter("/app/customersupport/agentcore/client_secret", client_secret)

        save_customer_support_secret(json.dumps(cognito_config))

        return cognito_config
    except Exception as e:
        print(f"Error: {e}")
        return None


def cleanup_cognito_resources(pool_id):
    """
    Delete Cognito resources including users, app clients, and user pool
    """
    try:
        # Initialize Cognito client using the same session configuration
        boto_session = Session()
        region = boto_session.region_name
        cognito_client = boto3.client("cognito-idp", region_name=region)

        if pool_id:
            try:
                # List and delete all app clients
                clients_response = cognito_client.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)

                for client in clients_response["UserPoolClients"]:
                    print(f"Deleting app client: {client['ClientName']}")
                    cognito_client.delete_user_pool_client(UserPoolId=pool_id, ClientId=client["ClientId"])

                # List and delete all users
                users_response = cognito_client.list_users(UserPoolId=pool_id, AttributesToGet=["email"])

                for user in users_response.get("Users", []):
                    print(f"Deleting user: {user['Username']}")
                    cognito_client.admin_delete_user(UserPoolId=pool_id, Username=user["Username"])

                # Delete the user pool
                print(f"Deleting user pool: {pool_id}")
                cognito_client.delete_user_pool(UserPoolId=pool_id)

                print("Successfully cleaned up all Cognito resources")
                return True

            except cognito_client.exceptions.ResourceNotFoundException:
                print(f"User pool {pool_id} not found. It may have already been deleted.")
                return True

            except Exception as e:
                print(f"Error during cleanup: {str(e)}")
                return False
        else:
            print("No matching user pool found")
            return True

    except Exception as e:
        print(f"Error initializing cleanup: {str(e)}")
        return False


def reauthenticate_user(client_id, client_secret):
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    # Authenticate User and get Access Token

    message = bytes(username + client_id, "utf-8")
    key = bytes(client_secret, "utf-8")
    secret_hash = base64.b64encode(hmac.new(key, message, digestmod=hashlib.sha256).digest()).decode()

    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": "MyPassword123!",  # pragma: allowlist secret
            "SECRET_HASH": secret_hash,
        },
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    return bearer_token


def create_agentcore_runtime_execution_role():
    iam = boto3.client("iam")
    boto_session = Session()
    region = boto_session.region_name
    account_id = get_aws_account_id()

    # Trust relationship policy
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    # IAM policy document
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
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
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/customer_support_agent-*",
                ],
            },
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ApplyGuardrail",
                    "bedrock:Retrieve",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{region}:{account_id}:*",
                ],
            },
            {
                "Sid": "AllowAgentToUseMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetMemoryRecord",
                    "bedrock-agentcore:GetMemory",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"],
            },
            {
                "Sid": "GetMemoryId",
                "Effect": "Allow",
                "Action": ["ssm:GetParameter"],
                "Resource": [f"arn:aws:ssm:{region}:{account_id}:parameter/*"],
            },
            {
                "Sid": "GatewayAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:InvokeGateway",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"],
            },
        ],
    }

    try:
        # Check if role already exists
        try:
            existing_role = iam.get_role(RoleName=role_name)
            print(f"ℹ️ Role {role_name} already exists")
            print(f"Role ARN: {existing_role['Role']['Arn']}")
            return existing_role["Role"]["Arn"]
        except iam.exceptions.NoSuchEntityException:
            pass

        # Create IAM role
        role_response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for Amazon Bedrock AgentCore with required permissions",
        )

        print(f"✅ Created IAM role: {role_name}")
        print(f"Role ARN: {role_response['Role']['Arn']}")

        # Check if policy already exists
        policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

        try:
            iam.get_policy(PolicyArn=policy_arn)
            print(f"ℹ️ Policy {policy_name} already exists")
        except iam.exceptions.NoSuchEntityException:
            # Create policy
            policy_response = iam.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document),
                Description="Policy for Amazon Bedrock AgentCore permissions",
            )
            print(f"✅ Created policy: {policy_name}")
            policy_arn = policy_response["Policy"]["Arn"]

        # Attach policy to role
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print("✅ Attached policy to role")
        except Exception as e:
            if "already attached" in str(e).lower():
                print("ℹ️ Policy already attached to role")
            else:
                raise

        print(f"Policy ARN: {policy_arn}")

        put_ssm_parameter(
            "/app/customersupport/agentcore/runtime_execution_role_arn",
            role_response["Role"]["Arn"],
        )
        return role_response["Role"]["Arn"]

    except Exception as e:
        print(f"❌ Error creating IAM role: {str(e)}")
        return None


def delete_agentcore_runtime_execution_role():
    iam = boto3.client("iam")

    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

        # Detach policy from role
        try:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print("✅ Detached policy from role")
        except Exception:
            pass

        # Delete role
        try:
            iam.delete_role(RoleName=role_name)
            print(f"✅ Deleted role: {role_name}")
        except Exception:
            pass

        # Delete policy
        try:
            iam.delete_policy(PolicyArn=policy_arn)
            print(f"✅ Deleted policy: {policy_name}")
        except Exception:
            pass

        delete_ssm_parameter("/app/customersupport/agentcore/runtime_execution_role_arn")

    except Exception as e:
        print(f"❌ Error during cleanup: {str(e)}")


def agentcore_memory_cleanup(memory_id: str = None):
    """List all memories and their associated strategies"""
    control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    if memory_id:
        response = control_client.delete_memory(memoryId=memory_id)
        print(f"✅ Successfully deleted memory: {memory_id}")
    else:
        next_token = None
        while True:
            # Build request parameters
            params = {}
            if next_token:
                params["nextToken"] = next_token

            # List memories
            try:
                response = control_client.list_memories(**params)

                # Process each memory
                for memory in response.get("memories", []):
                    memory_id = memory.get("id")
                    print(f"\nMemory ID: {memory_id}")
                    print(f"Status: {memory.get('status')}")
                    response = control_client.delete_memory(memoryId=memory_id)
                    response = control_client.list_memories(**params)
                    print(f"✅ Successfully deleted memory: {memory_id}")

                response = control_client.list_memories(**params)
                # Process each memory status
                for memory in response.get("memories", []):
                    memory_id = memory.get("id")
                    print(f"\nMemory ID: {memory_id}")
                    print(f"Status: {memory.get('status')}")

            except Exception as e:
                print(f"⚠️  Error getting memory details: {e}")
            # Check for more results
            next_token = response.get("nextToken")
            if not next_token:
                break


def gateway_target_cleanup(gateway_id: str = None):
    gateway_client = boto3.client(
        "bedrock-agentcore-control",
        region_name=REGION,
    )

    if not gateway_id:
        response = gateway_client.list_gateways()
        gateway_id = response["items"][0]["gatewayId"]
    print(f"🗑️  Deleting all targets for gateway: {gateway_id}")

    # List and delete all targets
    list_response = gateway_client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)

    targets_deleted = False
    for item in list_response["items"]:
        target_id = item["targetId"]
        print(f"   Deleting target: {target_id}")
        gateway_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        print(f"   ✅ Target {target_id} deleted")
        targets_deleted = True

    # Wait for target deletions to propagate
    if targets_deleted:
        print("⏳ Waiting for target deletions to propagate...")
        time.sleep(5)

    # Delete the gateway
    print(f"🗑️  Deleting gateway: {gateway_id}")
    gateway_client.delete_gateway(gatewayIdentifier=gateway_id)
    print(f"✅ Gateway {gateway_id} deleted successfully")


def runtime_resource_cleanup(runtime_arn: str = None):
    try:
        # Initialize AWS clients
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        ecr_client = boto3.client("ecr", region_name=REGION)
        if runtime_arn:
            runtime_id = runtime_arn.split(":")[-1].split("/")[-1]
            response = agentcore_control_client.delete_agent_runtime(agentRuntimeId=runtime_id)
            print(f"  ✅ Agent runtime deleted: {response['status']}")
        else:
            # Delete the AgentCore Runtime
            # print("  🗑️  Deleting AgentCore Runtime...")
            runtimes = agentcore_control_client.list_agent_runtimes()
            for runtime in runtimes["agentRuntimes"]:
                response = agentcore_control_client.delete_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
                print(f"  ✅ Agent runtime deleted: {response['status']}")

        # Delete the ECR repository
        print("  🗑️  Deleting ECR repository...")
        repositories = ecr_client.describe_repositories()
        for repo in repositories["repositories"]:
            if "bedrock-agentcore-customer_support_agent" in repo["repositoryName"]:
                ecr_client.delete_repository(repositoryName=repo["repositoryName"], force=True)
                print(f"  ✅ ECR repository deleted: {repo['repositoryName']}")

    except Exception as e:
        print(f"  ⚠️  Error during runtime cleanup: {e}")


def delete_observability_resources():
    # Configuration
    log_group_name = "agents/customer-support-assistant-logs"
    log_stream_name = "default"

    logs_client = boto3.client("logs", region_name=REGION)

    # Delete log stream first (must be done before deleting log group)
    try:
        print(f"  🗑️  Deleting log stream '{log_stream_name}'...")
        logs_client.delete_log_stream(logGroupName=log_group_name, logStreamName=log_stream_name)
        print(f"  ✅ Log stream '{log_stream_name}' deleted successfully")
    except Exception as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  ℹ️  Log stream '{log_stream_name}' doesn't exist")
        else:
            print(f"  ⚠️  Error deleting log stream: {e}")

    # Delete log group
    try:
        print(f"  🗑️  Deleting log group '{log_group_name}'...")
        logs_client.delete_log_group(logGroupName=log_group_name)
        print(f"  ✅ Log group '{log_group_name}' deleted successfully")
    except Exception as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  ℹ️  Log group '{log_group_name}' doesn't exist")
        else:
            print(f"  ⚠️  Error deleting log group: {e}")


def local_file_cleanup():
    # List of files to clean up
    files_to_delete = [
        "Dockerfile",
        ".dockerignore",
        ".bedrock_agentcore.yaml",
        "customer_support_agent.py",
        "agent_runtime.py",
    ]

    deleted_files = []
    missing_files = []

    for file in files_to_delete:
        if os.path.exists(file):
            try:
                os.unlink(file)
                deleted_files.append(file)
                print(f"  ✅ Deleted {file}")
            except Exception as e:
                print(f"  ⚠️  Error deleting {file}: {e}")
        else:
            missing_files.append(file)

    if deleted_files:
        print(f"\n📁 Successfully deleted {len(deleted_files)} files")
    if missing_files:
        print(f"ℹ️  {len(missing_files)} files were already missing: {', '.join(missing_files)}")


def policy_engine_cleanup(policy_engine_id: str = None):
    policy_client = boto3.client(
        "bedrock-agentcore-control",
        region_name=REGION,
    )

    if not policy_engine_id:
        response = policy_client.list_policy_engines()
        policy_engine_id = response["policyEngines"][0]["policyEngineId"]

    print(f"🗑️  Deleting all policies for policy engine: {policy_engine_id}")

    # List and delete all policies
    list_response = policy_client.list_policies(policyEngineId=policy_engine_id, maxResults=100)

    policies_deleted = False
    for item in list_response["policies"]:
        policy_id = item["policyId"]
        print(f"   Deleting policy: {policy_id}")
        policy_client.delete_policy(policyEngineId=policy_engine_id, policyId=policy_id)
        print(f"   ✅ Policy {policy_id} deleted")
        policies_deleted = True

    # Wait for policy deletions to propagate before deleting the engine
    if policies_deleted:
        print("⏳ Waiting for policy deletions to propagate...")
        time.sleep(5)  # 5 seconds is usually sufficient

    # Delete the policy engine
    print(f"🗑️  Deleting policy engine: {policy_engine_id}")
    policy_client.delete_policy_engine(policyEngineId=policy_engine_id)
    print(f"✅ Policy engine {policy_engine_id} deleted successfully")
