"""
Lab 02: AgentCore Gateway Service Role Setup

Creates the IAM service role required for Gateway to invoke Lambda targets.
Separate from Lambda execution role - Gateway needs its own role.
"""

import json
import boto3
from lab_helpers.constants import PARAMETER_PATHS
from lab_helpers.parameter_store import put_parameter


def create_gateway_service_role(region_name="us-west-2", account_id=None):
    """
    Create IAM service role for AgentCore Gateway.

    Gateway needs permissions to:
    1. Invoke Lambda functions
    2. Access CloudWatch logs
    3. Call other services as needed

    Args:
        region_name: AWS region
        account_id: AWS account ID (fetched if not provided)

    Returns:
        Dictionary with role ARN and other details
    """
    iam_client = boto3.client("iam", region_name=region_name)
    sts_client = boto3.client("sts", region_name=region_name)
    ssm_client = boto3.client("ssm", region_name=region_name)  # noqa: F841

    # Get account ID if not provided
    if not account_id:
        account_id = sts_client.get_caller_identity()["Account"]

    role_name = "aiml301-gateway-service-role"

    # Trust relationship: Allow bedrock-agentcore service to assume this role
    # Restricted to specific account and gateway ARN pattern for security
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region_name}:{account_id}:gateway/*"},
                },
            }
        ],
    }

    # Permissions: Gateway needs to invoke Lambda, access CloudWatch, and manage AgentCore resources
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeLambdaFunctions",
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": "*",
            },
            {
                "Sid": "BedrockAgentCorePermissions",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:*"],
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchLogsPermissions",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                "Resource": "*",
            },
        ],
    }

    try:
        # Check if role already exists
        try:
            role = iam_client.get_role(RoleName=role_name)
            print(f"✓ Gateway service role already exists: {role['Role']['Arn']}")
            role_arn = role["Role"]["Arn"]
        except iam_client.exceptions.NoSuchEntityException:
            print(f"Creating gateway service role: {role_name}")

            # Create the role
            response = iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="Service role for AgentCore Gateway to invoke Lambda targets",
            )

            role_arn = response["Role"]["Arn"]
            print(f"✓ Gateway service role created: {role_arn}")

            # Attach inline policy for Lambda invocation
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="gateway-invoke-lambda",
                PolicyDocument=json.dumps(permissions_policy),
            )
            print("✓ Permissions policy attached")

        # Save to Parameter Store for later use (using constants for consistency)
        gateway_role_arn_param = PARAMETER_PATHS["lab_02"]["gateway_role_arn"]
        put_parameter(
            gateway_role_arn_param,
            role_arn,
            description="Gateway service role ARN for Lab 02",
            region_name=region_name,
        )
        print(f"✓ Role ARN saved to Parameter Store: {gateway_role_arn_param}")

        return {
            "role_arn": role_arn,
            "role_name": role_name,
            "account_id": account_id,
            "region": region_name,
        }

    except Exception as e:
        print(f"❌ Error creating gateway service role: {e}")
        raise


if __name__ == "__main__":
    from lab_helpers.config import AWS_REGION

    print("=" * 70)
    print("Setting up AgentCore Gateway Service Role")
    print("=" * 70)
    print()

    result = create_gateway_service_role(region_name=AWS_REGION)

    print()
    print("=" * 70)
    print("✅ Gateway Service Role Setup Complete")
    print("=" * 70)
    print(f"Role ARN: {result['role_arn']}")
    print()
