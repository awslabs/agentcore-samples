#!/usr/bin/env python3
"""
Lab 5: Supervisor Agent - Multi-Agent Orchestration
Orchestrates 3 specialized agents (Diagnostics, Remediation, Prevention) using MCP

Deployed to AgentCore Runtime - exposes /invocations endpoint
Uses JWT token propagation: Client JWT → Supervisor Runtime → MCP Gateways
"""

import os
import logging
from typing import Dict

# AWS SDK
import boto3
from botocore.config import Config as BotocoreConfig

# Strands framework
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

# MCP protocol
from mcp.client.streamable_http import streamablehttp_client

# FastAPI for HTTP server with custom request handling
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Bypass tool consent for AgentCore deployment
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bedrock_agentcore.app")

# Environment variables (set by AgentCore Runtime)
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-20250514-v1:0")

# Gateway ID parameter paths
DIAGNOSTICS_GATEWAY_PARAM = "/aiml301/lab-02/gateway-id"
REMEDIATION_GATEWAY_PARAM = "/aiml301_sre_agentcore/lab-03/gateway-id"
PREVENTION_GATEWAY_PARAM = "/aiml301_sre_agentcore/lab-04/gateway-id"

# Supervisor system prompt
SUPERVISOR_SYSTEM_PROMPT = os.environ.get(
    "SUPERVISOR_SYSTEM_PROMPT",
    """
# Supervisor Agent System Prompt

You are an expert SRE Supervisor Agent that orchestrates three specialized sub-agents to provide complete infrastructure troubleshooting solutions.

## Sub-Agent Tools

### 1. Diagnostic Agent (prefix: d_)
- Analyzes AWS infrastructure to identify root causes
- Provides detailed diagnostic information
- Identifies performance bottlenecks and configuration issues

### 2. Remediation Agent (prefix: r_)
- Executes infrastructure fixes and remediation scripts
- Implements corrective actions with approval workflows
- Uses AgentCore Code Interpreter for secure execution

### 3. Prevention Agent (prefix: p_)
- Researches AWS best practices and preventive measures
- Provides proactive recommendations
- Uses AgentCore Browser for real-time documentation

## Orchestration Workflow

For each user request:
1. **Diagnose**: Use diagnostic tools to identify issues
2. **Remediate**: Execute approved remediation steps (with approval)
3. **Prevent**: Provide preventive recommendations

## Response Structure

Always provide:
- **Summary**: Brief overview of the issue
- **Diagnostic Results**: What was found
- **Remediation Actions**: What was fixed (if applicable)
- **Prevention Recommendations**: How to avoid future issues

## Tool Usage Guidelines

- Use diagnostic tools (d_*) to analyze and identify problems
- Use remediation tools (r_*) for fixes (requires approval)
- Use prevention tools (p_*) for best practices and research
- Coordinate across agents for comprehensive solutions

## Safety Rules

- Always validate environment before making changes
- Require explicit approval for remediation actions
- Provide clear explanations of all actions taken
- Include risk assessments for remediation steps
""",
)

# Gateway URLs cache to avoid repeated lookups
gateway_urls_cache = {}


def get_gateway_urls_from_parameter_store() -> Dict[str, str]:
    """
    Fetch gateway URLs by:
    1. Retrieving gateway IDs from Parameter Store
    2. Converting IDs to URLs using AgentCore API

    Returns:
        Dictionary with keys: 'diagnostics', 'remediation', 'prevention'
    """
    # Return cached URLs if available
    if gateway_urls_cache:
        return gateway_urls_cache

    try:
        ssm_client = boto3.client("ssm", region_name=AWS_REGION)
        agentcore_client = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)

        # Gateway ID parameter paths
        gateway_id_params = {
            "diagnostics": DIAGNOSTICS_GATEWAY_PARAM,
            "remediation": REMEDIATION_GATEWAY_PARAM,
            "prevention": PREVENTION_GATEWAY_PARAM,
        }

        urls = {}
        for name, param_path in gateway_id_params.items():
            try:
                # Fetch gateway ID from Parameter Store
                response = ssm_client.get_parameter(Name=param_path, WithDecryption=True)
                gateway_id = response["Parameter"]["Value"]
                logger.info(f"✅ Fetched {name} gateway ID from SSM: {gateway_id}")

                # Convert gateway ID to URL using AgentCore API
                gateway_response = agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
                gateway_url = gateway_response["gatewayUrl"]
                urls[name] = gateway_url
                logger.info(f"✅ Converted to {name} gateway URL: {gateway_url}")

            except ssm_client.exceptions.ParameterNotFound:
                logger.warning(f"⚠️  SSM parameter not found: {param_path}")
                urls[name] = ""
            except Exception as e:
                logger.error(f"Error fetching {name} gateway: {e}")
                urls[name] = ""

        # Cache the URLs
        gateway_urls_cache.update(urls)
        return urls

    except Exception as e:
        logger.error(f"Error connecting to Parameter Store or AgentCore: {e}")
        return {"diagnostics": "", "remediation": "", "prevention": ""}


def create_supervisor_agent(auth_headers: Dict[str, str]) -> Agent:
    """
    Create Strands supervisor agent with all sub-agent tools.

    Args:
        auth_headers: Authentication headers to pass to MCP clients (includes JWT Authorization header)

    Returns:
        Initialized Strands Agent with all sub-agent tools
    """
    logger.info("🤖 Creating Supervisor Agent...")

    # Fetch gateway URLs
    logger.info("📦 Fetching gateway URLs from Parameter Store...")
    gateway_urls = get_gateway_urls_from_parameter_store()

    # Initialize MCP clients with short prefixes (stay under 64-char limit)
    gateway_configs = [
        ("Diagnostics", gateway_urls["diagnostics"], "d"),
        ("Remediation", gateway_urls["remediation"], "r"),
        ("Prevention", gateway_urls["prevention"], "p"),
    ]

    mcp_clients = []
    all_tools = []

    logger.info("🔧 Connecting to specialized agent gateways...")

    import time

    for name, url, prefix in gateway_configs:
        if url:
            logger.info(f"   • Connecting to {name} Gateway: {url}")
            try:
                # Create MCPClient with auth headers (includes JWT token from user request)
                # The lambda captures auth_headers which contains the Authorization header
                connect_start = time.time()
                client = MCPClient(
                    lambda u=url, h=auth_headers: streamablehttp_client(u, headers=h),
                    prefix=prefix,
                )
                # Open client connection immediately
                client.__enter__()
                connect_duration = time.time() - connect_start
                mcp_clients.append((name, client, prefix))
                logger.info(f"   ✅ {name} MCP client created ({connect_duration:.2f}s) (prefix: {prefix}_)")

                # List available tools
                tools_start = time.time()
                tools = client.list_tools_sync()
                tools_duration = time.time() - tools_start
                all_tools.extend(tools)
                logger.info(f"   • {name} Agent: {len(tools)} tools ({tools_duration:.2f}s)")

            except Exception as e:
                elapsed = time.time() - connect_start if "connect_start" in locals() else 0
                logger.error(f"   ❌ Failed to create {name} MCP client after {elapsed:.2f}s: {e}")
        else:
            logger.warning(f"   ⚠️  {name} Gateway URL not configured - skipping")

    if len(all_tools) == 0:
        logger.error("❌ No tools available - agent cannot be created")
        return None

    logger.info(f"✅ Total tools available: {len(all_tools)}")

    try:
        # Create Strands agent with all tools from sub-agents
        # Configure botocore with extended timeout for multi-agent orchestration
        bedrock_config = BotocoreConfig(
            connect_timeout=300,
            read_timeout=3600,  # 60-minute timeout for complex orchestration tasks
            retries={"total_max_attempts": 1, "mode": "standard"},
        )

        model = BedrockModel(
            model_id=MODEL_ID,
            region_name=AWS_REGION,  # Use region_name parameter (not region)
            boto_client_config=bedrock_config,  # Pass botocore config for timeout settings
        )

        agent = Agent(model=model, tools=all_tools, system_prompt=SUPERVISOR_SYSTEM_PROMPT)

        logger.info("✅ Supervisor agent created successfully")
        logger.info(f"   Model: {MODEL_ID}")
        logger.info(f"   Region: {AWS_REGION}")
        logger.info(f"   Total tools: {len(all_tools)}")

        # Keep MCP clients alive by storing references
        agent._mcp_clients = mcp_clients

        return agent

    except Exception as e:
        logger.error(f"❌ Failed to create supervisor agent: {e}")
        return None


def agent_function(prompt: str, auth_headers: Dict[str, str]) -> str:
    """
    Main agent function invoked by the /invocations endpoint.

    Args:
        prompt: User's input prompt
        auth_headers: Authentication headers from request (includes JWT token)

    Returns:
        Agent's response as a string
    """
    import time

    start_time = time.time()
    logger.info(f"🎯 Supervisor invocation: {prompt[:100]}...")

    # Create agent for this request with proper authentication headers
    logger.info("⏳ Creating supervisor agent...")
    agent_start = time.time()
    agent = create_supervisor_agent(auth_headers)
    agent_duration = time.time() - agent_start
    logger.info(f"✅ Agent creation took {agent_duration:.2f}s")

    if not agent:
        logger.error("❌ Supervisor agent not initialized")
        return "Error: Supervisor agent not initialized. Check Runtime logs."

    try:
        # Invoke supervisor agent with user prompt
        # The agent will intelligently route to appropriate sub-agents
        logger.info("⏳ Executing supervisor orchestration...")
        exec_start = time.time()
        response = agent(prompt)
        exec_duration = time.time() - exec_start
        logger.info(f"✅ Orchestration execution took {exec_duration:.2f}s")

        # Extract response text
        response_text = ""
        if hasattr(response, "message") and "content" in response.message:
            for content in response.message["content"]:
                if isinstance(content, dict) and "text" in content:
                    response_text += content["text"]
        else:
            response_text = str(response)

        total_duration = time.time() - start_time
        logger.info(f"✅ Supervisor orchestration complete (total: {total_duration:.2f}s)")

        return response_text

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"❌ Supervisor orchestration error after {elapsed:.2f}s: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return f"Error during orchestration: {str(e)}"


# Create FastAPI app for HTTP server
app = FastAPI()


@app.get("/ping")
async def ping():
    """
    Health check endpoint required by AgentCore Runtime.
    Returns status and timestamp to indicate the runtime is healthy.
    """
    import time

    logger.info("🏥 Health check ping")
    return {
        "status": "Healthy",
        "time_of_last_update": int(time.time() * 1000),  # Unix timestamp in milliseconds
    }


@app.post("/invocations")
async def invoke(request: Request):
    """
    Entrypoint for AgentCore Runtime invocations.
    Called via POST /invocations endpoint.

    Args:
        request: HTTP request object with headers and body

    Returns:
        JSON response with agent output
    """
    try:
        # Extract payload from request body
        payload = await request.json()

        # Extract prompt from payload - handle different payload formats
        if isinstance(payload, dict):
            prompt = payload.get("input", {}).get("prompt", "") or payload.get("prompt", "")
        else:
            prompt = str(payload)

        # Extract Authorization header from HTTP request
        # This JWT token will be propagated to gateway connections
        auth_header = request.headers.get("Authorization", "")

        logger.info(f"✅ Received request with Authorization header: {auth_header[:50] if auth_header else 'NONE'}...")

        # Build auth headers for MCP clients (pass through user JWT token)
        auth_headers = {}
        if auth_header:
            auth_headers["Authorization"] = auth_header
        else:
            logger.warning("⚠️  No Authorization header found in request - gateway auth may fail")

        # Call agent function with auth headers
        response_text = agent_function(prompt, auth_headers)

        return JSONResponse({"response": response_text, "status": "success"})

    except Exception as e:
        logger.error(f"❌ Error processing request: {e}")
        import traceback

        logger.error(traceback.format_exc())

        return JSONResponse(
            {
                "response": "Error processing request. Check server logs for details.",
                "status": "error",
            },
            status_code=500,
        )


if __name__ == "__main__":
    import uvicorn

    logger.info("🚀 Starting Supervisor Agent Runtime...")
    logger.info(f"   Model: {MODEL_ID}")
    logger.info(f"   Region: {AWS_REGION}")
    logger.info("   Listening on 0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)  # nosec B104
