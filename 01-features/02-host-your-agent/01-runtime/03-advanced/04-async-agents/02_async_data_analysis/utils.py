# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
import json
import time
import logging
import yaml
import uuid
from datetime import datetime
from boto3.session import Session
from typing import Optional


def generate_unique_agent_name(base_name: str = "async_data_analysis_agent") -> str:
    """Generate a unique agent name that complies with AWS constraints.

    AWS Pattern: [a-zA-Z][a-zA-Z0-9_]{0,47}
    - Must start with letter
    - Only letters, numbers, underscores
    - Max 48 characters total
    """
    # Use shorter timestamp and UUID to fit within 48 char limit
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # Use underscore instead of hyphen
    short_uuid = str(uuid.uuid4()).replace("-", "")[:8]  # Remove hyphens from UUID

    # Create base name that fits AWS constraints
    if base_name.startswith("async_data_analysis_agent"):
        # Shorten base name to fit within limits
        base = "async_data_agent"
    else:
        base = base_name[:15]  # Limit base name length

    unique_name = f"{base}_{timestamp}_{short_uuid}"

    # Ensure it fits within 48 character limit
    if len(unique_name) > 48:
        # Truncate if necessary
        available_chars = 48 - len(f"_{timestamp}_{short_uuid}")
        base = base[:available_chars]
        unique_name = f"{base}_{timestamp}_{short_uuid}"

    return unique_name


def update_agent_name_in_config(config_path: str = ".bedrock_agentcore.yaml", new_name: str = None):
    """Update agent name in configuration to use unique name."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        if not new_name:
            new_name = generate_unique_agent_name()

        # Update default agent name
        config["default_agent"] = new_name

        # Update agent configuration
        if "agents" in config:
            old_agents = dict(config["agents"])
            config["agents"] = {}

            for old_name, agent_config in old_agents.items():
                # Use the new unique name
                config["agents"][new_name] = agent_config
                config["agents"][new_name]["name"] = new_name

                # Reset agent IDs for fresh deployment
                if "bedrock_agentcore" in agent_config:
                    agent_config["bedrock_agentcore"]["agent_id"] = None
                    agent_config["bedrock_agentcore"]["agent_arn"] = None
                    agent_config["bedrock_agentcore"]["agent_session_id"] = None

                break  # Only update the first agent

        # Write back the updated configuration
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logging.info(f"✅ Updated agent name to: {new_name}")
        return new_name

    except Exception as e:
        logging.error(f"❌ Failed to update agent name: {e}")
        return None


def reset_agent_configuration(config_path: str = ".bedrock_agentcore.yaml"):
    """Dynamically reset agent configuration to allow fresh deployment."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        # Reset agent-specific fields
        if "agents" in config:
            for agent_name, agent_config in config["agents"].items():
                if "bedrock_agentcore" in agent_config:
                    agent_config["bedrock_agentcore"]["agent_id"] = None
                    agent_config["bedrock_agentcore"]["agent_arn"] = None
                    agent_config["bedrock_agentcore"]["agent_session_id"] = None

        # Write back the updated configuration
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logging.info(f"✅ Agent configuration reset in {config_path}")
        return True

    except Exception as e:
        logging.error(f"❌ Failed to reset agent configuration: {e}")
        return False


def get_agent_status(config_path: str = ".bedrock_agentcore.yaml"):
    """Check current agent deployment status."""
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        agents_status = {}
        if "agents" in config:
            for agent_name, agent_config in config["agents"].items():
                bedrock_config = agent_config.get("bedrock_agentcore", {})
                agent_id = bedrock_config.get("agent_id")
                agent_arn = bedrock_config.get("agent_arn")

                agents_status[agent_name] = {
                    "agent_id": agent_id,
                    "agent_arn": agent_arn,
                    "deployed": agent_id is not None and agent_arn is not None,
                }

        return agents_status

    except Exception as e:
        logging.error(f"❌ Failed to get agent status: {e}")
        return {}


def ensure_fresh_deployment(config_path: str = ".bedrock_agentcore.yaml"):
    """Ensure configuration is ready for fresh deployment."""
    status = get_agent_status(config_path)

    for agent_name, info in status.items():
        if info["deployed"]:
            logging.info(f"🔄 Agent '{agent_name}' has existing deployment, resetting for fresh deployment")
            reset_agent_configuration(config_path)
            break
    else:
        logging.info("✅ Configuration ready for fresh deployment")

    return True


class SecureCodeInterpreter:
    """Secure CodeInterpreter with network isolation and restricted S3 access."""

    def __init__(self, region: str, allowed_s3_buckets: Optional[list] = None):
        self.region = region
        self.allowed_s3_buckets = allowed_s3_buckets or []
        self.control_client = boto3.client("bedrock-agentcore-control", region_name=region)
        self.code_interpreter_id = None
        self.execution_role_arn = None

    def create_restricted_execution_role(self, role_name: str) -> str:
        """Create IAM role with minimal S3 permissions for specific buckets only."""
        iam_client = boto3.client("iam")

        # Create trust policy for CodeInterpreter
        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        # Create minimal S3 policy for allowed buckets only
        s3_resources = []
        if self.allowed_s3_buckets:
            for bucket in self.allowed_s3_buckets:
                s3_resources.extend([f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"])

        execution_policy = {
            "Version": "2012-10-17",
            "Statement": [
                (
                    {
                        "Effect": "Allow",
                        "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                        "Resource": s3_resources,
                    }
                    if s3_resources
                    else {"Effect": "Deny", "Action": "*", "Resource": "*"}
                )
            ],
        }

        try:
            # Create role
            role_response = iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Restricted execution role for secure CodeInterpreter",
            )

            # Attach policy
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="RestrictedS3Access",
                PolicyDocument=json.dumps(execution_policy),
            )

            role_arn = role_response["Role"]["Arn"]
            logging.info(f"Created restricted execution role: {role_arn}")
            return role_arn

        except iam_client.exceptions.EntityAlreadyExistsException:
            # Role exists, get ARN
            role_response = iam_client.get_role(RoleName=role_name)
            return role_response["Role"]["Arn"]

    def create_secure_code_interpreter(self, name: str) -> str:
        """Create CodeInterpreter with sandbox mode (no internet access)."""

        # Create restricted execution role
        role_name = f"secure-code-interpreter-{name}-role"
        self.execution_role_arn = self.create_restricted_execution_role(role_name)

        # Wait for role to be available
        time.sleep(10)

        try:
            response = self.control_client.create_code_interpreter(
                name=name,
                description="Secure CodeInterpreter with network isolation",
                executionRoleArn=self.execution_role_arn,
                networkConfiguration={
                    "networkMode": "SANDBOX"  # No internet access, only S3 and DNS
                },
            )

            self.code_interpreter_id = response["codeInterpreterId"]
            logging.info(f"Created secure CodeInterpreter: {self.code_interpreter_id}")
            logging.info("Network mode: SANDBOX (no internet access)")
            logging.info(f"S3 access limited to buckets: {self.allowed_s3_buckets}")

            return self.code_interpreter_id

        except Exception as e:
            logging.error(f"Failed to create secure CodeInterpreter: {e}")
            raise

    def get_code_interpreter_client(self):
        """Get CodeInterpreter client configured for secure execution."""
        if not self.code_interpreter_id:
            raise ValueError("CodeInterpreter not created. Call create_secure_code_interpreter first.")

        from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

        # Use custom CodeInterpreter with restricted configuration
        return CodeInterpreter(region=self.region, code_interpreter_id=self.code_interpreter_id)


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
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/us.anthropic.claude-sonnet-4-20250514-v1:0",
                    f"arn:aws:bedrock:{region}::foundation-model/us.anthropic.claude-haiku-4-5-20251001-v1:0",
                ],
            },
            {
                "Sid": "ECRImageAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                "Resource": [
                    f"arn:aws:ecr:{region}:{account_id}:repository/bedrock-agentcore/*",
                    f"arn:aws:ecr:{region}:{account_id}:repository/bedrock-agentcore-*",
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
                "Resource": [f"arn:aws:xray:{region}:{account_id}:*"],
            },
            {
                "Effect": "Allow",
                "Resource": f"arn:aws:cloudwatch:{region}:{account_id}:metric/bedrock-agentcore/*",
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
                "Sid": "CodeInterpreterManagement",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateCodeInterpreter",
                    "bedrock-agentcore:DeleteCodeInterpreter",
                    "bedrock-agentcore:GetCodeInterpreter",
                    "bedrock-agentcore:ListCodeInterpreters",
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:InvokeCodeInterpreter",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:code-interpreter/*"],
            },
            {
                "Sid": "IAMRoleManagement",
                "Effect": "Allow",
                "Action": [
                    "iam:CreateRole",
                    "iam:GetRole",
                    "iam:PutRolePolicy",
                    "iam:DeleteRole",
                    "iam:DeleteRolePolicy",
                    "iam:ListRolePolicies",
                ],
                "Resource": [f"arn:aws:iam::{account_id}:role/secure-code-interpreter-*"],
            },
            {
                "Sid": "STSGetCallerIdentity",
                "Effect": "Allow",
                "Action": ["sts:GetCallerIdentity"],
                "Resource": "*",
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
