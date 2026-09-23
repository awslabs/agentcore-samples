#!/usr/bin/env python3
"""
Lab 4: Strands Prevention Agent with AgentCore Browser - AgentCore Runtime Deployment
Uses FastMCP to implement MCP protocol for Gateway-to-Runtime communication

Focuses on:
- MCP protocol implementation with FastMCP
- Prevention-focused infrastructure analysis
- Real-time AWS documentation research using AgentCore Browser
- Proactive recommendations to prevent issues
- Current AWS best practices

Deployed to AgentCore Runtime for serverless execution
"""

import os
import logging

# FastMCP for MCP protocol implementation
from fastmcp import FastMCP

# Strands framework
from strands import Agent
from strands.models import BedrockModel
from strands_tools.browser import AgentCoreBrowser

# Bypass tool consent for AgentCore deployment
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Configure logging with explicit StreamHandler for CloudWatch capture
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s",
    stream=sys.stdout,
    force=True,
)

# Use bedrock_agentcore.app namespace for proper AgentCore logging capture
logger = logging.getLogger("bedrock_agentcore.app")

# Ensure handler exists
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

# Environment variables (set by AgentCore Runtime)
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")

# Log environment diagnostics
logger.info("=" * 80)
logger.info("AGENT INITIALIZATION DIAGNOSTICS")
logger.info("=" * 80)
logger.info(f"Python Version: {sys.version}")
logger.info(f"AWS_REGION: {AWS_REGION}")
logger.info(f"MODEL_ID: {MODEL_ID}")
logger.info(f"DOCKER_CONTAINER: {os.environ.get('DOCKER_CONTAINER', 'NOT SET')}")
logger.info(f"PYTHONUNBUFFERED: {os.environ.get('PYTHONUNBUFFERED', 'NOT SET')}")
logger.info("=" * 80)

# Initialize FastMCP server for AgentCore Runtime
# host="0.0.0.0" - Listens on all interfaces as required by AgentCore
# stateless_http=True - Enables session isolation for enterprise security
mcp = FastMCP("SRE Prevention Agent", host="0.0.0.0", stateless_http=True)  # nosec B104

# Global variables for browser and agent
agentcore_browser = None
prevention_agent = None
BROWSER_AVAILABLE = False


def initialize_browser(region=AWS_REGION):
    """Initialize AgentCore Browser for web research"""
    global agentcore_browser, BROWSER_AVAILABLE

    try:
        logger.debug(f"[DIAGNOSTIC] Attempting to initialize AgentCoreBrowser in region: {region}")
        agentcore_browser = AgentCoreBrowser(region=region)
        BROWSER_AVAILABLE = True
        logger.info("✅ AgentCore Browser initialized")
        logger.debug(f"[DIAGNOSTIC] Browser type: {type(agentcore_browser)}")
        return True
    except Exception as e:
        BROWSER_AVAILABLE = False
        logger.error("❌ AgentCore Browser initialization failed", exc_info=True)
        logger.warning(f"⚠️ AgentCore Browser not available: {e}")
        return False


# Define FastMCP Tools
logger.debug("[DIAGNOSTIC] Registering FastMCP tools...")


@mcp.tool()
def research_agent(research_topic_query: str):
    """Research AWS best practices and prevention strategies using AgentCore Browser

    Analyzes infrastructure for proactive improvements by accessing real-time AWS documentation. Provides prevention recommendations, implementation roadmaps, and monitoring best practices.

    Args:
        research_topic_query: Topic to research (e.g., "DynamoDB performance optimization", "EC2 cost reduction strategies", "S3 security hardening")

    Returns:
        Analysis with prevention opportunities, AWS best practices, and implementation guidance
    """

    global prevention_agent, agentcore_browser, BROWSER_AVAILABLE

    try:
        logger.debug("[DIAGNOSTIC] setup_prevention_agent() called")
        logger.info("=" * 80)
        logger.info("📥 INCOMING REQUEST")
        logger.info(f"research_topic_query: {research_topic_query}")
        logger.info("=" * 80)

        logger.debug("[DIAGNOSTIC] setup_prevention_agent() called")

        if not BROWSER_AVAILABLE:
            logger.debug("[DIAGNOSTIC] Browser not available, initializing...")
            initialize_browser(AWS_REGION)

        if not BROWSER_AVAILABLE:
            logger.debug("[DIAGNOSTIC] Browser initialization failed, returning None")
            return None

        # Reuse the global browser instance (already initialized)
        logger.debug("[DIAGNOSTIC] Using existing AgentCoreBrowser instance...")
        if not agentcore_browser:
            logger.error("[DIAGNOSTIC] Browser flag is True but instance is None!")
            return None

        # Setup Bedrock model
        logger.debug(f"[DIAGNOSTIC] Setting up BedrockModel with model_id: {MODEL_ID}")
        model = BedrockModel(
            model_id=MODEL_ID,
            streaming=True,
        )

        # Create agent with browser tool (reuse existing browser instance)
        logger.debug("[DIAGNOSTIC] Creating Strands Agent with browser tool...")
        system_prompt = """ I need you to analyze our CRM infrastructure for prevention opportunities using the available tool to access AWS documentation. 

    
    Please use the browser tool to access these specific AWS documentation pages and provide analysis:
    
    1. First, use the browser tool to visit: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/best-practices.html
    2. Then visit: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-best-practices.html  
    3. Finally visit: https://docs.aws.amazon.com/wellarchitected/latest/framework/
    
    Based on what you find in the AWS documentation, provide analysis focusing on:
    
    1. **Proactive Infrastructure Management**: Best practices we should implement
    4. **Monitoring and Alerting**: Best practices for proactive monitoring
    
    Provide your analysis with:
    - Executive summary of prevention opportunities
    - Implementation roadmap with AWS best practices
    - Success metrics for measuring prevention effectiveness
    
    """
        prevention_agent = Agent(system_prompt=system_prompt, model=model, tools=[agentcore_browser.browser])

        logger.info("✅ Prevention agent with browser tool initialized")
        logger.debug(f"[DIAGNOSTIC] Agent type: {type(prevention_agent)}")
        # logger.debug(f"System prompt length: {len(system_prompt)}")
        # logger.debug(f"Tools: {[tool.__name__ if hasattr(tool, '__name__') else str(tool) for tool in prevention_agent.tools]}")

    except Exception as e:
        logger.error("❌ Failed to setup prevention agent", exc_info=True)
        logger.error(f"Exception: {e}")
        return f"Error: Failed to initialize agent - {str(e)}"

    return_text = ""
    response = prevention_agent(research_topic_query)
    # 3. LOG RAW RESPONSE OBJECT
    logger.info("=" * 80)
    logger.info("📤 RAW AGENT RESPONSE")
    logger.info(f"Response type: {type(response)}")
    logger.info(f"Response attributes: {dir(response)}")
    logger.debug(f"Full response object: {response}")
    logger.debug(f"Response.message: {response.message}")
    logger.info("=" * 80)
    response_content = response.message.get("content", [])
    if response_content:
        for content in response_content:
            if isinstance(content, dict) and "text" in content:
                return_text = content["text"]

    return return_text


# Note: Browser initialization is LAZY - happens on first tool call
# This prevents blocking during module import and FastMCP server startup

logger.info("=" * 80)
logger.info("🚀 Module loaded - Browser will initialize on first tool call (lazy)")
logger.info("=" * 80)


# Run the FastMCP server
if __name__ == "__main__":
    # AgentCore Runtime requires stateless streamable-HTTP transport (NOT stdio)
    # Per AWS docs: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html
    # - Transport: streamable-http (stateless, HTTP-based)
    # - Port: 8000 (MCP protocol requirement)
    # - Host: 0.0.0.0 (listen on all interfaces)

    logger.info("=" * 80)
    logger.info("🚀 PHASE 2: FastMCP Server Startup")
    logger.info("=" * 80)
    logger.info("Starting FastMCP server with streamable-http transport on port 8000")
    logger.debug(f"[DIAGNOSTIC] FastMCP instance: {mcp}")
    logger.debug(
        f"[DIAGNOSTIC] FastMCP tools: {mcp.list_tools() if hasattr(mcp, 'list_tools') else 'method not available'}"
    )
    logger.info("=" * 80)

    try:
        logger.info("🔌 Calling mcp.run(transport='streamable-http')...")
        mcp.run(transport="streamable-http")
    except Exception as e:
        logger.error("❌ FastMCP server failed to start", exc_info=True)
        logger.error(f"Exception: {e}")
        raise
