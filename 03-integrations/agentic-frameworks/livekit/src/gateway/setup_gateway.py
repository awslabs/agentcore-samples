#!/usr/bin/env python3
"""Setup AgentCore Gateway with Lambda target for interview evaluation"""

import json
import sys
import time

import boto3

region = "us-east-1"
gateway_name = "interview-evaluation-gateway"
lambda_arn = "arn:aws:lambda:us-east-1:123456789012:function:EvaluateInterview"

client = boto3.client("bedrock-agentcore-control", region_name=region)
cognito_client = boto3.client("cognito-idp", region_name=region)
iam_client = boto3.client("iam", region_name=region)

print("🚀 Setting up AgentCore Gateway...")

# Step 1: Create Cognito User Pool for OAuth
print("Step 1: Creating OAuth authorization server...")
try:
    user_pool = cognito_client.create_user_pool(
        PoolName=f"{gateway_name}-pool", AutoVerifiedAttributes=["email"]
    )
    user_pool_id = user_pool["UserPool"]["Id"]

    app_client = cognito_client.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=f"{gateway_name}-client",
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = app_client["UserPoolClient"]["ClientId"]

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
    print(f"✓ OAuth server created: {user_pool_id}")
except Exception as e:
    print(f"Note: {e}")
    user_pool_id = "us-east-1_PLACEHOLDER"
    client_id = "placeholder-client-id"
    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

# Step 2: Create IAM role for Gateway
print("Step 2: Creating IAM role...")
role_name = "AgentCoreGatewayRole"
try:
    role = iam_client.get_role(RoleName=role_name)
    role_arn = role["Role"]["Arn"]
    print(f"✓ Using existing role: {role_arn}")
except Exception:
    role = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
        ),
    )
    role_arn = role["Role"]["Arn"]
    print(f"✓ Role created: {role_arn}")

time.sleep(10)

# Step 3: Create Gateway
print("Step 3: Creating Gateway...")
try:
    gateway = client.create_gateway(
        name=gateway_name,
        description="Gateway for interview evaluation tools",
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {"discoveryUrl": discovery_url, "allowedAudience": [client_id]}
        },
    )
    print(f"Gateway response: {json.dumps(gateway, indent=2, default=str)}")
    gateway_id = gateway["gateway"]["gatewayId"]
    gateway_arn = gateway["gateway"]["gatewayArn"]
    print(f"✓ Gateway created: {gateway_id}")
except Exception as e:
    print(f"Error creating gateway: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

time.sleep(5)

# Step 4: Create Gateway Target for Lambda
print("Step 4: Adding Lambda target...")
target = client.create_gateway_target(
    gatewayId=gateway_id,
    name="evaluate_interview",
    description="Evaluates interview transcripts and generates feedback PDF",
    target={"lambda": {"functionArn": lambda_arn}},
    toolSpec={
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The session ID of the interview to evaluate",
                    }
                },
                "required": ["session_id"],
            }
        }
    },
)

print(f"✓ Target created: {target['gatewayTarget']['name']}")

# Save configuration
config = {
    "gateway_id": gateway_id,
    "gateway_arn": gateway_arn,
    "region": region,
    "user_pool_id": user_pool_id,
    "client_id": client_id,
}

with open("gateway_config.json", "w") as f:
    json.dump(config, f, indent=2)

print("\n" + "=" * 60)
print("✅ Gateway setup complete!")
print(f"Gateway ID: {gateway_id}")
print(f"Gateway ARN: {gateway_arn}")
print("Tool Name: evaluate_interview")
print("\nConfiguration saved to: gateway_config.json")
print("=" * 60)
