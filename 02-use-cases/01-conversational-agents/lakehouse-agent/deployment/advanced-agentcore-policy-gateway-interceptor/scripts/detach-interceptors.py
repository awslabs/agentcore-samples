#!/usr/bin/env python3
"""
Pre-deploy script: Temporarily detach Interceptors from the Gateway.

Cedar policy creation fails when Interceptors are attached to the Gateway.
This script removes Interceptors while preserving the JWT authorizer configuration.
After CDK deploy, the CDK stack re-attaches the Interceptors automatically.

Usage:
    AWS_PROFILE=<PROFILE> AWS_REGION=us-east-1 python cdk/scripts/detach-interceptors.py
"""

import boto3
import os


def get_ssm_param(ssm: boto3.client, name: str) -> str:
    """Get a parameter from SSM Parameter Store."""
    return ssm.get_parameter(Name=name)["Parameter"]["Value"]


def main() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")
    session = boto3.Session(region_name=region)
    ssm = session.client("ssm")
    client = session.client("bedrock-agentcore-control")

    # Load config from SSM
    gateway_id = get_ssm_param(ssm, "/app/lakehouse-agent/gateway-id")
    gateway_name = get_ssm_param(ssm, "/app/lakehouse-agent/gateway-name")

    # Get current Gateway config
    gw = client.get_gateway(gatewayIdentifier=gateway_id)
    role_arn = gw["roleArn"]
    auth_config = gw["authorizerConfiguration"]

    print(f"Gateway: {gateway_id} ({gateway_name})")
    print(f"Role:    {role_arn}")

    # Update Gateway without Interceptors
    client.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gateway_name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=auth_config,
    )

    print("Interceptors detached successfully.")
    print("You can now run: npx cdk deploy")


if __name__ == "__main__":
    main()
