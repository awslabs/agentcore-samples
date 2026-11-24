#!/usr/bin/env python3
"""
Create IAM role for Gateway Interceptor Lambda
"""

import json
import os
import sys

import boto3

# Make the repo's utils/ importable (iam_roles lives there) when this script
# runs from its own deployment subdir.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from utils.iam_roles import RoleOwnershipError, ensure_role


def create_lambda_role():
    """Create IAM role for Lambda execution.

    Re-running on the role this sample created re-applies its trust policy and
    policies. A same-name role this sample did not create is refused, with no change.
    """
    session = boto3.Session()
    region = session.region_name

    iam = boto3.client("iam", region_name=region)
    sts = boto3.client("sts", region_name=region)
    ssm = boto3.client("ssm", region_name=region)

    account_id = sts.get_caller_identity()["Account"]
    role_name = "InsuranceClaimsGatewayInterceptorRole"

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
        print(f"Creating IAM role: {role_name}")
        role_arn, created = ensure_role(
            iam,
            ssm,
            role_name,
            trust_policy,
            description="Lambda execution role for Gateway Interceptor",
            tags=[
                {"Key": "Application", "Value": "lakehouse-agent"},
                {"Key": "Purpose", "Value": "claims-interceptor-role"},
            ],
        )
        if created:
            print(f"✅ Created IAM role: {role_arn}")
        else:
            print(f"ℹ️  Role {role_name} already exists and was created by this sample; re-applying its policies")
            print(f"✅ Using existing role: {role_arn}")

        # Attach basic Lambda execution policy
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print("✅ Attached AWSLambdaBasicExecutionRole policy")

        # Add inline policy for STS AssumeRole on tenant roles
        sts_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AssumeRoleTenantRoles",
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Resource": [
                        f"arn:aws:iam::{account_id}:role/lakehouse-users-role",
                        f"arn:aws:iam::{account_id}:role/lakehouse-adjusters-role",
                        f"arn:aws:iam::{account_id}:role/lakehouse-policyholders-role",
                        f"arn:aws:iam::{account_id}:role/lakehouse-administrators-role",
                    ],
                }
            ],
        }

        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AssumeRoleTenantPolicy",
            PolicyDocument=json.dumps(sts_policy),
        )
        print("✅ Attached inline policy for STS AssumeRole on tenant roles")

        # Add inline policy for KMS decrypt (for Lambda environment variables)
        kms_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "KMSDecryptForLambda",
                    "Effect": "Allow",
                    "Action": ["kms:Decrypt", "kms:DescribeKey"],
                    "Resource": f"arn:aws:kms:{region}:{account_id}:key/*",
                }
            ],
        }

        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="KMSDecryptPolicy",
            PolicyDocument=json.dumps(kms_policy),
        )
        print("✅ Attached inline policy for KMS decrypt")

        # Store role ARN in SSM Parameter Store
        print("💾 Storing role ARN in SSM Parameter Store...")
        ssm.put_parameter(
            Name="/app/lakehouse-agent/interceptor-lambda-role-arn",
            Value=role_arn,
            Description="IAM role ARN for Gateway Interceptor Lambda",
            Type="String",
            Overwrite=True,
        )
        print("✅ Stored parameter: /app/lakehouse-agent/interceptor-lambda-role-arn")

        return role_arn

    except RoleOwnershipError as e:
        print(str(e))
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error creating role: {e}")
        sys.exit(1)


if __name__ == "__main__":
    role_arn = create_lambda_role()
    print("\n✅ Lambda Role ARN stored in SSM Parameter Store")
    print(f"   /app/lakehouse-agent/interceptor-lambda-role-arn = {role_arn}")
