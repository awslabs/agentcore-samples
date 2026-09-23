"""
Amazon Bedrock AgentCore Gateway Creation Script

This script creates and configures an Amazon Bedrock AgentCore Gateway for the
Device Management System. The gateway serves as the secure entry point for
MCP (Model Context Protocol) requests, handling authentication via Amazon Cognito
and routing requests to the appropriate AWS Lambda function targets.

The script performs the following operations:
1. Load configuration from environment variables
2. Configure Amazon Cognito JWT authentication
3. Create the Amazon Bedrock AgentCore Gateway
4. Update environment variables with gateway information

Environment Variables Required:
    AWS_REGION: AWS region for gateway deployment
    ENDPOINT_URL: Amazon Bedrock AgentCore control endpoint
    COGNITO_USERPOOL_ID: Amazon Cognito User Pool ID for authentication
    COGNITO_CLIENT_ID: Amazon Cognito App Client ID
    ROLE_ARN: IAM role ARN with bedrock-agentcore permissions
    GATEWAY_NAME: Name for the gateway (optional)
    GATEWAY_DESCRIPTION: Description for the gateway (optional)

Environment Variables Updated:
    GATEWAY_ID: Generated gateway identifier
    GATEWAY_ARN: Generated gateway ARN
    GATEWAY_IDENTIFIER: Alias for GATEWAY_ID

Example Usage:
    python create_gateway.py

Output:
    Gateway created successfully!
    Gateway ID: gateway-12345
    Gateway ARN: arn:aws:bedrock-agentcore:region:account:gateway/gateway-12345
"""

import boto3
import os
import sys
from dotenv import load_dotenv, set_key
from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient

# Initialize the Gateway client
gateway_client = GatewayClient(region_name="us-west-2")

# Load environment variables from .env file
load_dotenv()

# Get environment variables
AWS_REGION = os.getenv("AWS_REGION")
ENDPOINT_URL = os.getenv("ENDPOINT_URL")
COGNITO_USERPOOL_ID = os.getenv("COGNITO_USERPOOL_ID")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "Device-Management-Gateway")
ROLE_ARN = os.getenv("ROLE_ARN")
GATEWAY_DESCRIPTION = os.getenv("GATEWAY_DESCRIPTION", "Device Management Gateway")

print("Endpoint URL: {}".format(ENDPOINT_URL))
print("AWS Region: {}".format(AWS_REGION))

# Initialize the Bedrock Agent Core Control client
bedrock_agent_core_client = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)

# Configure the authentication
auth_config = {
    "customJWTAuthorizer": {
        "allowedClients": [COGNITO_CLIENT_ID],
        "discoveryUrl": f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USERPOOL_ID}/.well-known/openid-configuration",
    }
}

# Create the gateway
try:
    create_response = bedrock_agent_core_client.create_gateway(
        name=GATEWAY_NAME,
        roleArn=ROLE_ARN,  # The IAM Role must have permissions to create/list/get/delete Gateway
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration=auth_config,
        description=GATEWAY_DESCRIPTION,
    )

    # Print the gateway ID and other information
    gateway_id = create_response.get("gatewayId")
    gateway_arn = create_response.get("gatewayArn")
    print("Gateway created successfully!")
    print(f"Gateway ID: {gateway_id}")
    print(f"Gateway ARN: {gateway_arn}")
    print(f"Creation Time: {create_response.get('creationTime')}")

    # Update the .env file with the gateway information
    env_file_path = ".env"
    try:
        if gateway_id:
            set_key(env_file_path, "GATEWAY_ID", gateway_id)
            print(f"Updated .env file with GATEWAY_ID: {gateway_id}")

        if gateway_arn:
            set_key(env_file_path, "GATEWAY_ARN", gateway_arn)
            print(f"Updated .env file with GATEWAY_ARN: {gateway_arn}")

        # Also keep the legacy GATEWAY_IDENTIFIER for backward compatibility
        if gateway_id:
            set_key(env_file_path, "GATEWAY_IDENTIFIER", gateway_id)
            print(f"Updated .env file with GATEWAY_IDENTIFIER: {gateway_id}")

    except Exception as e:
        print(f"Warning: Failed to update .env file: {e}")

except Exception as e:
    print(f"Error creating gateway: {e}")
    sys.exit(1)
