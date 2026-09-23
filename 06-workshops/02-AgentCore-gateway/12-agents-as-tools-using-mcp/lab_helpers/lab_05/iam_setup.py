"""
Lab 05: Supervisor Runtime IAM Setup

Creates IAM role for supervisor agent runtime with permissions to:
- Invoke Bedrock models for orchestration
- Call 3 sub-agent gateways with JWT token propagation
- Retrieve gateway URLs from Parameter Store
- Write logs to CloudWatch
"""

import json
import boto3
import logging
from typing import Dict
from botocore.exceptions import ClientError

from lab_helpers.config import AWS_REGION

logger = logging.getLogger(__name__)


def create_supervisor_runtime_iam_role(role_name: str, region: str = AWS_REGION, account_id: str = None) -> Dict:
    """
    Create IAM role for Supervisor Runtime with multi-gateway orchestration permissions.

    The supervisor runtime needs permissions to:
    1. Connect to 3 different agent gateways (Diagnostics, Remediation, Prevention)
    2. Orchestrate requests across multiple agents
    3. Invoke Bedrock models for LLM-based orchestration logic
    4. Retrieve gateway URLs from Parameter Store
    5. Write logs to CloudWatch

    Authentication uses JWT token propagation:
    - User provides JWT token in Authorization header
    - Supervisor Runtime extracts JWT and propagates to gateway connections
    - No M2M credentials or token retrieval needed

    Args:
        role_name: Name for the IAM role
        region: AWS region (default: from config)
        account_id: AWS account ID (auto-detected if not provided)

    Returns:
        Dict with role_name, role_arn, and policy details
    """
    iam = boto3.client("iam", region_name=region)
    sts = boto3.client("sts", region_name=region)

    # Get account ID
    if not account_id:
        account_id = sts.get_caller_identity()["Account"]

    logger.info(f"Creating supervisor runtime IAM role: {role_name}")
    logger.info("Authentication: JWT token propagation (User JWT → Supervisor → Gateways)")

    # Trust policy: Allow bedrock-agentcore service to assume role
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
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

    # Create role
    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for Lab 05 Supervisor Agent Runtime - Multi-agent orchestration",
            Tags=[
                {"Key": "Workshop", "Value": "AIML301"},
                {"Key": "Lab", "Value": "Lab-05"},
                {"Key": "Component", "Value": "SupervisorRuntime"},
            ],
        )
        role_arn = response["Role"]["Arn"]
        logger.info(f"✅ Role created: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            logger.warning(f"⚠️ Role {role_name} already exists, using existing role")
            response = iam.get_role(RoleName=role_name)
            role_arn = response["Role"]["Arn"]
        else:
            logger.error(f"❌ Failed to create role: {e}")
            raise

    # Inline policy for supervisor-specific permissions
    policy_name = f"{role_name}-policy"
    supervisor_policy = {
        "Version": "2012-10-17",
        "Statement": [
            # 1. Bedrock Model Invocation (for orchestration logic)
            {
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",  # Cross-region model IDs (e.g., us.anthropic.claude-*)
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/*",
                    f"arn:aws:bedrock:us-east-1:{account_id}:inference-profile/*",
                    f"arn:aws:bedrock:us-east-2:{account_id}:inference-profile/*",
                    f"arn:aws:bedrock:us-west-2:{account_id}:inference-profile/*",
                ],
            },
            # 2. CloudWatch Logs (Runtime logging)
            {
                "Sid": "CloudWatchLogs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/*"],
            },
            # 2b. X-Ray Tracing (Runtime observability and tracing)
            {
                "Sid": "XRayTracing",
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
            # 3. Gateway Access (call sub-agent gateways)
            {
                "Sid": "GatewayAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeGateway",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:ListGateways",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"],
            },
            # 6. Parameter Store (Configuration and gateway URL retrieval)
            {
                "Sid": "ParameterStoreRead",
                "Effect": "Allow",
                "Action": [
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:GetParametersByPath",
                ],
                "Resource": [f"arn:aws:ssm:{region}:{account_id}:parameter/*"],
            },
            # 7. KMS (Decrypt secrets and parameters)
            {
                "Sid": "KMSDecrypt",
                "Effect": "Allow",
                "Action": ["kms:Decrypt", "kms:DescribeKey"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "kms:ViaService": [
                            f"secretsmanager.{region}.amazonaws.com",
                            f"ssm.{region}.amazonaws.com",
                        ]
                    }
                },
            },
            # 8. ECR Access (Pull container images)
            {
                "Sid": "ECRAccess",
                "Effect": "Allow",
                "Action": [
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                "Resource": "*",
            },
        ],
    }

    # Attach inline policy
    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(supervisor_policy),
        )
        logger.info(f"✅ Inline policy attached: {policy_name}")
    except ClientError as e:
        logger.error(f"❌ Failed to attach policy: {e}")
        raise

    # Return role information
    return {
        "role_name": role_name,
        "role_arn": role_arn,
        "policy_name": policy_name,
        "region": region,
        "account_id": account_id,
        "permissions": {
            "bedrock_models": "InvokeModel and streaming",
            "gateways": "Call 3 sub-agent gateways with JWT propagation",
            "cloudwatch_logs": "Runtime logging",
            "parameter_store": "Gateway URL retrieval (/aiml301/lab-0X/gateway-id)",
            "kms": "Decrypt parameters",
            "ecr": "Pull container images",
        },
    }


def delete_supervisor_runtime_iam_role(role_name: str, region: str = AWS_REGION) -> bool:
    """
    Delete supervisor runtime IAM role and associated policies.

    Args:
        role_name: Name of the IAM role to delete
        region: AWS region (default: from config)

    Returns:
        True if deletion successful, False otherwise
    """
    iam = boto3.client("iam", region_name=region)

    logger.info(f"Deleting supervisor runtime IAM role: {role_name}")

    try:
        # List and delete inline policies
        response = iam.list_role_policies(RoleName=role_name)
        for policy_name in response.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            logger.info(f"✅ Deleted inline policy: {policy_name}")

        # List and detach managed policies
        response = iam.list_attached_role_policies(RoleName=role_name)
        for policy in response.get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            logger.info(f"✅ Detached managed policy: {policy['PolicyName']}")

        # Delete role
        iam.delete_role(RoleName=role_name)
        logger.info(f"✅ Deleted role: {role_name}")

        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            logger.warning(f"⚠️ Role {role_name} does not exist")
            return True
        else:
            logger.error(f"❌ Failed to delete role: {e}")
            return False
