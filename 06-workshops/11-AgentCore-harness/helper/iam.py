"""IAM helpers for AgentCore Harness — creates the execution role and permissions."""

import json
import boto3
from typing import Optional

ROLE_NAME = "HarnessExecutionRole"
POLICY_NAME = "HarnessExecutionPolicy"

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
            "Action": "sts:AssumeRole",
        }
    ],
}

PERMISSIONS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BedrockInvokeModel",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": "*",
        },
        {
            "Sid": "ECRPull",
            "Effect": "Allow",
            "Action": [
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetAuthorizationToken",
            ],
            "Resource": "*",
        },
        {
            "Sid": "EcrPublicPull",
            "Effect": "Allow",
            "Action": ["ecr-public:GetAuthorizationToken"],
            "Resource": "*",
        },
        {
            "Sid": "StsForEcrPublicPull",
            "Effect": "Allow",
            "Action": ["sts:GetServiceBearerToken"],
            "Resource": "*",
        },
        {
            "Sid": "XRay",
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": "*",
        },
        {
            "Sid": "AgentCore",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:*Memory*",
                "bedrock-agentcore:*Browser*",
                "bedrock-agentcore:*Gateway*",
                "bedrock-agentcore:*CodeInterpreter*",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetEvent",
            ],
            "Resource": "*",
        },
        {
            "Sid": "GetAgentCoreApiKeys",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetResourceApiKey"],
            "Resource": "*",
        },
    ],
}


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def create_harness_role(role_name: str = ROLE_NAME) -> Optional[str]:
    """Create the IAM execution role required by AgentCore Harness. Returns the role ARN.

    Idempotent — if the role already exists, returns its ARN.
    """
    iam = boto3.client("iam")

    try:
        existing = iam.get_role(RoleName=role_name)
        arn = existing["Role"]["Arn"]
        print(f"Role {role_name} already exists: {arn}")
        return arn
    except iam.exceptions.NoSuchEntityException:
        pass

    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
        Description="Execution role for Amazon Bedrock AgentCore Harness",
    )
    arn = resp["Role"]["Arn"]
    print(f"Created role: {arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(PERMISSIONS_POLICY),
    )
    print(f"Attached policy: {POLICY_NAME}")

    return arn


def delete_harness_role(role_name: str = ROLE_NAME) -> None:
    """Delete the Harness execution role and its inline policy."""
    iam = boto3.client("iam")
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName=POLICY_NAME)
        print(f"Deleted inline policy: {POLICY_NAME}")
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        iam.delete_role(RoleName=role_name)
        print(f"Deleted role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"Role {role_name} not found")
