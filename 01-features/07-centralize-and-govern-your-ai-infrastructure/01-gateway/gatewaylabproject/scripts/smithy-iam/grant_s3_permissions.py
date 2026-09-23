"""Grant S3 permissions to the gateway IAM role for the Smithy S3 tutorial.

The gateway role created by the AgentCore CLI does not include S3 permissions
by default. This script adds an inline policy allowing S3 read operations.

Requires GATEWAY_ID environment variable (or reads from .env).

Usage:
    uv run python scripts/smithy-iam/grant_s3_permissions.py
"""

import json
import os
import sys

import boto3


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def main():
    load_env()

    gateway_id = os.environ.get("GATEWAY_ID")
    if not gateway_id:
        print("ERROR: GATEWAY_ID not set. Export it or add to the script .env")
        sys.exit(1)

    region = boto3.Session().region_name
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    iam = boto3.client("iam")

    gw = control.get_gateway(gatewayIdentifier=gateway_id)
    role_arn = gw["roleArn"]
    role_name = role_arn.split("/")[-1]

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:ListAllMyBuckets",
                    "s3:ListBucket",
                    "s3:GetObject",
                    "s3:HeadBucket",
                    "s3:HeadObject",
                ],
                "Resource": "*",
            }
        ],
    }

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="SmithyS3Policy",
        PolicyDocument=json.dumps(policy),
    )
    print(f"Granted S3 permissions to {role_name}")


if __name__ == "__main__":
    main()
