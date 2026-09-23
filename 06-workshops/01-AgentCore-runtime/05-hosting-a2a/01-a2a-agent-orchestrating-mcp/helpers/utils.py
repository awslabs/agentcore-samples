"""
Utility functions for Amazon Bedrock AgentCore A2A tutorial.

This module provides helper functions for managing AWS resources including:
- SSM parameters
- Secrets Manager
- Cognito User Pools
- IAM roles and policies
- AgentCore runtimes
- CloudWatch logs
- ECR repositories
"""

import base64
import hashlib
import hmac
import json
import os
from typing import Dict, Optional

import boto3
from boto3.session import Session

sts_client = boto3.client("sts")

# Get AWS account details
REGION = boto3.session.Session().region_name

USERNAME = "testuser"
SECRET_NAME = "aws_docs_assistant"
SSM_DOCS_AGENT_ROLE_ARN = "/app/aws_docs_assistant/agentcore/runtime_execution_role_arn"
POLICY_NAME = f"AWSDocsAssistantBedrockAgentCorePolicy-{REGION}"
LOG_GROUP_BASE_NAME = "/aws/bedrock-agentcore/runtimes/"

SSM_DOCS_AGENT_ARN = "/app/aws_docs_assistant/agentcore/agent_arn"
SSM_BLOGS_AGENT_ARN = "/app/aws_blogs_assistant/agentcore/agent_arn"

AWS_DOCS_ROLE_NAME = f"AWSDocsAssistantBedrockAgentCoreRole-{REGION}"
AWS_BLOG_ROLE_NAME = f"AWSBlogsAssistantBedrockAgentCoreRole-{REGION}"
ORCHESTRATOR_ROLE_NAME = f"AWSOrchestratorAssistantAgentCoreRole-{REGION}"


# General functions
def get_aws_account_id() -> str:
    """Get the AWS account ID for the current session."""
    sts = boto3.client("sts")
    return sts.get_caller_identity()["Account"]


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    """Get a parameter value from AWS Systems Manager Parameter Store."""
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)
    return response["Parameter"]["Value"]


def put_ssm_parameter(name: str, value: str, parameter_type: str = "String", with_encryption: bool = False) -> None:
    """Put a parameter value into AWS Systems Manager Parameter Store."""
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
    """Delete a parameter from AWS Systems Manager Parameter Store."""
    ssm = boto3.client("ssm")
    try:
        ssm.delete_parameter(Name=name)
    except ssm.exceptions.ParameterNotFound:
        pass


def save_secret(secret_value: str) -> bool:
    """Save a secret in AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)

    try:
        secrets_client.create_secret(
            Name=SECRET_NAME,
            SecretString=secret_value,
            Description=("Secret containing the Cognito Configuration for the AWS Docs Agent"),
        )
        print("✅ Created secret")
    except secrets_client.exceptions.ResourceExistsException:
        secrets_client.update_secret(SecretId=SECRET_NAME, SecretString=secret_value)
        print("✅ Updated existing secret")
    except secrets_client.exceptions.ClientError as e:
        print(f"❌ Error saving secret: {str(e)}")
        return False
    return True


def get_cognito_secret() -> Optional[str]:
    """Get a secret value from AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)
    try:
        response = secrets_client.get_secret_value(SecretId=SECRET_NAME)
        return response["SecretString"]
    except secrets_client.exceptions.ClientError as e:
        print(f"❌ Error getting secret: {str(e)}")
        return None


def delete_cognito_secret() -> bool:
    """Delete a secret from AWS Secrets Manager."""
    boto_session = Session()
    region = boto_session.region_name
    secrets_client = boto3.client("secretsmanager", region_name=region)
    try:
        secrets_client.delete_secret(SecretId=SECRET_NAME, ForceDeleteWithoutRecovery=True)
        print("✅ Secret Deleted")
        return True
    except secrets_client.exceptions.ClientError as e:
        print(f"❌ Error deleting secret: {str(e)}")
        return False


# Cognito Resources
def reauthenticate_user(client_id: str, client_secret: str) -> str:
    """Reauthenticate user and return bearer token."""
    boto_session = Session()
    region = boto_session.region_name
    # Initialize Cognito client
    cognito_client = boto3.client("cognito-idp", region_name=region)
    # Authenticate User and get Access Token

    message = bytes(USERNAME + client_id, "utf-8")
    key = bytes(client_secret, "utf-8")
    secret_hash = base64.b64encode(hmac.new(key, message, digestmod=hashlib.sha256).digest()).decode()

    auth_response = cognito_client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": USERNAME,
            "PASSWORD": "MyPassword123!",  # pragma: allowlist secret
            "SECRET_HASH": secret_hash,
        },
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    return bearer_token


def setup_cognito_user_pool() -> Optional[Dict[str, str]]:
    """Set up Cognito user pool and return configuration."""
    boto_session = Session()
    region = boto_session.region_name
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
            GenerateSecret=True,
            ExplicitAuthFlows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
                "ALLOW_USER_SRP_AUTH",
            ],
        )

        client_config = app_client_response["UserPoolClient"]
        client_id = client_config["ClientId"]
        client_secret = client_config["ClientSecret"]

        # Create and configure user
        cognito_client.admin_create_user(
            UserPoolId=pool_id,
            Username=USERNAME,
            TemporaryPassword="Temp123!",  # pragma: allowlist secret
            MessageAction="SUPPRESS",
        )

        cognito_client.admin_set_user_password(
            UserPoolId=pool_id,
            Username=USERNAME,
            Password="MyPassword123!",  # pragma: allowlist secret
            Permanent=True,
        )

        # Generate secret hash and authenticate
        message = bytes(USERNAME + client_id, "utf-8")
        key_bytes = bytes(client_secret, "utf-8")
        secret_hash = base64.b64encode(hmac.new(key_bytes, message, digestmod=hashlib.sha256).digest()).decode()

        auth_response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": USERNAME,
                "PASSWORD": "MyPassword123!",  # pragma: allowlist secret
                "SECRET_HASH": secret_hash,
            },
        )
        bearer_token = auth_response["AuthenticationResult"]["AccessToken"]

        # Create configuration object
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"

        cognito_config = {
            "pool_id": pool_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "secret_hash": secret_hash,
            "bearer_token": bearer_token,
            "discovery_url": discovery_url,
        }

        # Output and save configuration
        print(f"Pool id: {pool_id}")
        print(f"Discovery URL: {discovery_url}")
        print(f"Client ID: {client_id}")
        print(f"Bearer Token: {bearer_token}")

        save_secret(json.dumps(cognito_config))
        return cognito_config

    except cognito_client.exceptions.ClientError as e:
        print(f"Error: {e}")
        return None


def cleanup_cognito_resources(pool_id: str) -> bool:
    """Delete Cognito resources including users, app clients, and user pool."""
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

            except cognito_client.exceptions.ClientError as e:
                print(f"Error during cleanup: {str(e)}")
                return False
        else:
            print("No matching user pool found")
            return True

    except cognito_client.exceptions.ClientError as e:
        print(f"Error initializing cleanup: {str(e)}")
        return False


# AgentCore Resources
def create_agentcore_runtime_execution_role(role_name: str) -> Optional[str]:
    """Create IAM role for AgentCore runtime execution."""
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
                    "ArnLike": {"aws:SourceArn": (f"arn:aws:bedrock-agentcore:{region}:{account_id}:*")},
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
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
                    f"workload-identity-directory/default/workload-identity/*",
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
                "Sid": "GetSecrets",
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [f"arn:aws:secretsmanager:{region}:{account_id}:secret:{SECRET_NAME}*"],
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
            Description=("IAM role for Amazon Bedrock AgentCore with required permissions"),
        )

        print(f"✅ Created IAM role: {role_name}")
        print(f"Role ARN: {role_response['Role']['Arn']}")

        # Check if policy already exists
        policy_arn = f"arn:aws:iam::{account_id}:policy/{POLICY_NAME}"

        try:
            iam.get_policy(PolicyArn=policy_arn)
            print(f"ℹ️ Policy {POLICY_NAME} already exists")
        except iam.exceptions.NoSuchEntityException:
            # Create policy
            policy_response = iam.create_policy(
                PolicyName=POLICY_NAME,
                PolicyDocument=json.dumps(policy_document),
                Description="Policy for Amazon Bedrock AgentCore permissions",
            )
            print(f"✅ Created policy: {POLICY_NAME}")
            policy_arn = policy_response["Policy"]["Arn"]

        # Attach policy to role
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print("✅ Attached policy to role")
        except iam.exceptions.ClientError as e:
            if "already attached" in str(e).lower():
                print("ℹ️ Policy already attached to role")
            else:
                raise

        print(f"Policy ARN: {policy_arn}")

        put_ssm_parameter(
            SSM_DOCS_AGENT_ROLE_ARN,
            role_response["Role"]["Arn"],
        )
        return role_response["Role"]["Arn"]

    except iam.exceptions.ClientError as e:
        print(f"❌ Error creating IAM role: {str(e)}")
        return None


def delete_agentcore_runtime_execution_role(role_name: str) -> None:
    """Delete AgentCore runtime execution role and associated policy."""
    iam = boto3.client("iam")

    try:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
        policy_arn = f"arn:aws:iam::{account_id}:policy/{POLICY_NAME}"

        # Detach policy from role
        try:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            print("✅ Detached policy from role")
        except iam.exceptions.ClientError:
            pass

        # Delete role
        try:
            iam.delete_role(RoleName=role_name)
            print(f"✅ Deleted role: {role_name}")
        except iam.exceptions.ClientError:
            pass

        # Delete policy
        try:
            iam.delete_policy(PolicyArn=policy_arn)
            print(f"✅ Deleted policy: {POLICY_NAME}")
        except iam.exceptions.ClientError:
            pass

        delete_ssm_parameter(SSM_DOCS_AGENT_ROLE_ARN)

    except iam.exceptions.ClientError as e:
        print(f"❌ Error during cleanup: {str(e)}")


def runtime_resource_cleanup(agent_runtime_id: str) -> None:
    """Clean up AgentCore runtime resources."""
    try:
        # Initialize AWS clients
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

        # Delete the AgentCore Runtime
        response = agentcore_control_client.delete_agent_runtime(agentRuntimeId=agent_runtime_id)
        print(f"  ✅ Agent runtime {agent_runtime_id} deleted: {response['status']}")
    except Exception as e:
        print(f"  ⚠️  Error during runtime cleanup: {e}")


def ecr_repo_cleanup() -> None:
    """Clean up ECR repositories."""
    try:
        ecr_client = boto3.client("ecr", region_name=REGION)
        # Delete the ECR repository
        print("  🗑️  Deleting ECR repository...")
        repositories = ecr_client.describe_repositories()

        repo_patterns = [
            "bedrock-agentcore-aws_docs_assistant",
            "bedrock-agentcore-aws_blog_assistant",
            "bedrock-agentcore-aws_orchestrator_assistant",
        ]

        for repo in repositories["repositories"]:
            repo_name = repo["repositoryName"]
            if any(pattern in repo_name for pattern in repo_patterns):
                ecr_client.delete_repository(repositoryName=repo_name, force=True)
                print(f"  ✅ ECR repository deleted: {repo_name}")
    except Exception as e:
        print(f"  ⚠️  Error during ECR cleanup: {e}")


def get_memory_name(agent_name: str) -> Optional[str]:
    """Get memory name for a given agent."""
    try:
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        resp = agentcore_control_client.list_memories()
        for mem in resp["memories"]:
            if agent_name in mem["id"]:
                return mem["id"]
        return None
    except Exception as e:
        print(f"  ⚠️  Error getting memories: {e}")
        return None


def short_memory_cleanup(agent_name: str) -> None:
    """Clean up short-term memory for an agent."""
    try:
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        memory_id = get_memory_name(agent_name)
        if memory_id:
            agentcore_control_client.delete_memory(memoryId=memory_id)
            print(f" ✅ Memory {memory_id} deleted.")
    except Exception as e:
        print(f"  ⚠️  Error deleting memories: {e}")


# Observability Cleanup
def delete_observability_resources(agent_name: str) -> None:
    """Delete observability resources for an agent."""
    # Configuration
    log_stream_name = "default"

    logs_client = boto3.client("logs", region_name=REGION)

    complete_log_group = LOG_GROUP_BASE_NAME + agent_name + "-DEFAULT"

    # Delete log stream first (must be done before deleting log group)
    try:
        print(f"  🗑️  Deleting log stream '{log_stream_name}'...")
        logs_client.delete_log_stream(logGroupName=complete_log_group, logStreamName=log_stream_name)
        print(f"  ✅ Log stream '{log_stream_name}' deleted successfully")
    except logs_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  ℹ️  Log stream '{log_stream_name}' doesn't exist")
        else:
            print(f"  ⚠️  Error deleting log stream: {e}")

    # Delete log group
    try:
        print(f"  🗑️  Deleting log group '{complete_log_group}'...")
        logs_client.delete_log_group(logGroupName=complete_log_group)
        print(f"  ✅ Log group '{complete_log_group}' deleted successfully")
    except logs_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"  ℹ️  Log group '{complete_log_group}' doesn't exist")
        else:
            print(f"  ⚠️  Error deleting log group: {e}")


# Local Files Cleanup


def local_file_cleanup() -> None:
    """Clean up local files created during the tutorial."""
    # List of files to clean up
    files_to_delete = [
        "Dockerfile",
        ".dockerignore",
        ".bedrock_agentcore.yaml",
        "agents/strands_aws_docs.py",
        "agents/orchestrator.py",
        "agents/requirements.txt",
        "agents/strands_aws_blogs_news.py",
    ]

    deleted_files = []
    missing_files = []

    for file in files_to_delete:
        if os.path.exists(file):
            try:
                os.unlink(file)
                deleted_files.append(file)
                print(f"  ✅ Deleted {file}")
            except OSError as e:
                print(f"  ⚠️  Error deleting {file}: {e}")
        else:
            missing_files.append(file)

    if deleted_files:
        print(f"\n📁 Successfully deleted {len(deleted_files)} files")
    if missing_files:
        print(f"ℹ️  {len(missing_files)} files were already missing: {', '.join(missing_files)}")
