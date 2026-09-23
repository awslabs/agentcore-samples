#!/usr/bin/env python3
"""
Health Lakehouse Data Agent using Strands and AgentCore Gateway
Connects to Gateway tools for querying and managing lakehouse data with OAuth-based access control
"""

import os
import logging
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore import BedrockAgentCoreApp
from typing import Dict, Any, Optional
import boto3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bypass tool consent for AgentCore deployment
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Initialize AgentCore App
app = BedrockAgentCoreApp()

# System prompt for lakehouse data agent
CLAIMS_SYSTEM_PROMPT = """
You are a helpful lakehouse data assistant that provides tools to help users query and update data in the lakehouse.

**Technical Context**:

You have access to tools that query the data lakehouse and surrounding data stores like DynamoDB.
Users can access tools and data based on their groups.

**Special instruction for admin group users**
For admin group users, they might encounter tool access issue. Retry with text-to-sql tool provided in case a specific tool fails.

**Communication Guidelines**:

Be professional, empathetic, and clear
Explain insurance terms in simple language
When helping with claims, gather all necessary information before submission
If you are working for administrators group, try to use text_to_sql tool in case the user do not have access to specific tools. 

**DO NOT MAKE UP ANSWERS. YOUR RESPONSES SHOULD BE BASED ON SOLID FACTS ONLY. DO NOT ANSWER WHEN YOU DO NOT KNOW**
"""

# Default model ID
MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def get_config() -> Dict[str, Optional[str]]:
    """
    Load configuration from environment variables and SSM Parameter Store.

    Priority:
    1. Environment variables (set by AgentCore Runtime)
    2. SSM Parameter Store
    3. Defaults

    Returns:
        Dictionary with configuration values
    """
    config = {}

    # Get region from boto3 session with proper fallback
    try:
        session = boto3.Session()
        config["region"] = (
            os.environ.get("AWS_REGION") or session.region_name or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        )
        if not session.region_name:
            logger.warning("⚠️  No region in AWS config, using fallback")
        logger.info(f"✅ Region: {config['region']}")
    except Exception as e:
        logger.warning(f"⚠️  Could not detect region: {e}")
        config["region"] = "us-east-1"

    # Try to get Gateway ARN from environment variable first
    config["gateway_arn"] = os.environ.get("GATEWAY_ARN")

    # If not in environment, try SSM Parameter Store
    if not config["gateway_arn"]:
        try:
            ssm = boto3.client("ssm", region_name=config["region"])
            response = ssm.get_parameter(Name="/app/lakehouse-agent/gateway-arn")
            config["gateway_arn"] = response["Parameter"]["Value"]
            logger.info(f"✅ Gateway ARN from SSM: {config['gateway_arn']}")
        except Exception as e:
            logger.warning(f"⚠️  Gateway ARN not found in SSM: {e}")
            config["gateway_arn"] = None
    else:
        logger.info(f"✅ Gateway ARN from environment: {config['gateway_arn']}")

    return config


def get_gateway_url(gateway_arn: str, region: str) -> str:
    """Convert Gateway ARN to URL using AgentCore API."""
    try:
        # Extract gateway ID from ARN
        # Format: arn:aws:bedrock-agentcore:region:account:gateway/gateway-id
        gateway_id = gateway_arn.split("/")[-1]

        # Get gateway details
        agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_url = response["gatewayUrl"]

        logger.info(f"✅ Gateway URL: {gateway_url}")
        return gateway_url
    except Exception as e:
        logger.error(f"❌ Error getting gateway URL: {e}")
        return ""


@app.entrypoint
def handle_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle requests to the lakehouse agent.

    Args:
        payload: Request with prompt and bearer token

    Returns:
        Agent response
    """
    user_prompt = payload.get("prompt", "Hello")
    bearer_token = payload.get("bearer_token", "")

    logger.info(f"📥 Received request: {user_prompt[:100]}...")
    logger.info(f"🔑 Bearer token present: {bool(bearer_token)}")

    # Load configuration
    config = get_config()
    gateway_arn = config["gateway_arn"]
    region = config["region"]

    # Get tools from Gateway if configured
    tools = []
    logger.info(f"🔗 Connecting to Gateway: {gateway_arn}")
    gateway_url = get_gateway_url(gateway_arn, region)

    # Create auth headers with bearer token
    auth_headers = {"Authorization": f"Bearer {bearer_token}"}

    # Create MCP client with authentication
    mcp_client = MCPClient(
        lambda: streamablehttp_client(gateway_url, headers=auth_headers),
        prefix="claims",
    )

    # Open connection and get tools
    mcp_client.__enter__()
    tools = mcp_client.list_tools_sync()
    logger.info(f"✅ Loaded {len(tools)} tools from Gateway")

    # Create Bedrock model
    model = BedrockModel(model_id=MODEL_ID, region_name=region)

    # Create agent with Gateway tools (if available)
    agent = Agent(model=model, tools=tools, system_prompt=CLAIMS_SYSTEM_PROMPT)

    # Process request
    logger.info("⏳ Processing request...")
    response = agent(user_prompt)
    logger.info("✅ Request processed")

    # Extract response content
    response_text = ""
    if hasattr(response, "message") and "content" in response.message:
        for content in response.message["content"]:
            if isinstance(content, dict) and "text" in content:
                response_text += content["text"]
    else:
        response_text = str(response)

    return {
        "content": response_text,
        "tool_calls": len(response.tool_calls) if hasattr(response, "tool_calls") else 0,
    }


if __name__ == "__main__":
    app.run()
