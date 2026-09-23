#!/usr/bin/env python3
"""
SDK Agent for AgentCore Runtime with BedrockAgentCoreApp
Uses shared business logic with DIY agent for consistency
"""

# ============================================================================
# IMPORTS
# ============================================================================

from bedrock_agentcore.runtime import BedrockAgentCoreApp
import functools
import json
import logging
import sys
import os

# Add paths for both container and local development environments
current_dir = os.path.dirname(os.path.abspath(__file__))

# Detect container vs local environment
if current_dir.startswith("/app"):
    # Container environment - AgentCore CLI packages everything in /app
    sys.path.append("/app")  # For agent_shared
else:
    # Local development environment
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
    sys.path.append(project_root)  # For shared.config_manager
    sys.path.append(os.path.dirname(current_dir))  # For agent_shared

# Strands imports
from strands import Agent, tool
from strands.models import BedrockModel

# Use AWS documented Strands MCP client pattern
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

# Import loop control tools from strands_tools
from strands_tools import think, stop, handoff_to_user

# Shared configuration manager (agent-local copy for CLI packaging)
from agent_shared.config_manager import AgentCoreConfigManager

# Agent-specific shared utilities
from agent_shared.auth import setup_oauth, get_m2m_token, is_oauth_available
from agent_shared.memory import (
    setup_memory,
    get_conversation_context,
    save_conversation,
    is_memory_available,
)
from agent_shared.responses import (
    format_sdk_response,
    extract_text_from_event,
    format_error_response,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# EXACT AWS DOCUMENTATION PATTERNS
# ============================================================================


def _create_streamable_http_transport(url, headers=None):
    """
    EXACT function from AWS documentation
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-clients.html
    """
    return streamablehttp_client(url, headers=headers)


# ============================================================================
# CONFIGURATION
# ============================================================================

# Initialize configuration manager
config_manager = AgentCoreConfigManager()

# Load model settings
model_settings = config_manager.get_model_settings()
gateway_url = config_manager.get_gateway_url()

logger.info(f"🚀 SDK Agent (CLI deployable) starting with model: {model_settings['model_id']}")
if gateway_url:
    logger.info(f"🌐 Gateway configured: {gateway_url}")
else:
    logger.info("🏠 No gateway configured - using local tools only")

# ============================================================================
# TOOLS
# ============================================================================


@tool(name="get_current_time", description="Get the current date and time")
def get_current_time() -> str:
    """Get current timestamp"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")


@tool(name="echo_message", description="Echo back a message for testing")
def echo_message(message: str) -> str:
    """Echo back the provided message"""
    return f"Echo: {message}"


# ============================================================================
# STREAMING WITH MCP CONTEXT MANAGEMENT
# ============================================================================


async def execute_agent_streaming_sdk(bedrock_model, prompt):
    """
    Streaming version of AWS documented pattern for SDK agent
    """
    # Get configuration
    config_manager = AgentCoreConfigManager()
    gateway_url = config_manager.get_gateway_url()

    # Define system prompt for the agent
    system_prompt = """You are an AWS Operations Assistant with read-only access to AWS resources through specialized tools.

🚨 MANDATORY BEHAVIOR: IMMEDIATE PROGRESS UPDATES WITH EMOJIS 🚨

YOU MUST FOLLOW THIS EXACT PATTERN FOR EVERY REQUEST:

1. Start with: "I'll help you [task]. Here's my plan:" followed by numbered steps
2. Use emojis consistently: 🔍 before each check, ✅ after each result
3. After EVERY tool call, immediately provide the result with ✅
4. Use echo_message tool if needed to ensure progress updates are sent
5. Never execute multiple tools without progress updates between them

Available AWS Services: EC2, S3, Lambda, CloudFormation, IAM, RDS, CloudWatch, Cost Explorer, ECS, EKS, SNS, SQS, DynamoDB, Route53, API Gateway, SES, Bedrock, SageMaker.

Remember: Progress updates with emojis are MANDATORY, not optional! Follow the exact pattern shown above.
"""

    # Fallback to local tools if gateway or oauth is not working
    if not gateway_url or not is_oauth_available():
        logger.info("🏠 No MCP available - using local streaming")
        local_tools = [get_current_time, echo_message, think, stop, handoff_to_user]
        agent = Agent(model=bedrock_model, tools=local_tools, system_prompt=system_prompt)
        async for event in agent.stream_async(prompt):
            yield event
        return

    try:
        access_token = get_m2m_token()
        if not access_token:
            raise Exception("No access token")

        # Create headers for authentication
        headers = {"Authorization": f"Bearer {access_token}"}

        # EXACT AWS pattern: Create MCP client with functools.partial
        mcp_client = MCPClient(functools.partial(_create_streamable_http_transport, url=gateway_url, headers=headers))

        # EXACT AWS pattern: Use context manager
        with mcp_client:
            tools = mcp_client.list_tools_sync()

            # Add local tools
            all_tools = [get_current_time, echo_message, think, stop, handoff_to_user]
            if tools:
                all_tools.extend(tools)
                logger.info(f"🛠️ SDK Streaming with {len(tools)} MCP tools + local tools")

            agent = Agent(model=bedrock_model, tools=all_tools, system_prompt=system_prompt)
            async for event in agent.stream_async(prompt):
                yield event

    except Exception as e:
        logger.error(f"❌ MCP streaming failed: {e}")
        # Fallback to local streaming
        logger.info("🏠 Falling back to local streaming")
        local_tools = [get_current_time, echo_message, think, stop, handoff_to_user]
        agent = Agent(model=bedrock_model, tools=local_tools, system_prompt=system_prompt)
        async for event in agent.stream_async(prompt):
            yield event


# ============================================================================
# AGENT SETUP
# ============================================================================


def create_strands_agent(use_mcp=True):
    """Create Strands agent with local tools and optionally MCP tools using AWS compliant pattern"""
    # Create BedrockModel
    model = BedrockModel(**model_settings)

    # Define system prompt for the agent
    system_prompt = """You are an AWS Operations Assistant with read-only access to AWS resources through specialized tools.

🚨 MANDATORY BEHAVIOR: IMMEDIATE PROGRESS UPDATES WITH EMOJIS 🚨

YOU MUST FOLLOW THIS EXACT PATTERN FOR EVERY REQUEST:

1. Start with: "I'll help you [task]. Here's my plan:" followed by numbered steps
2. Use emojis consistently: 🔍 before each check, ✅ after each result
3. After EVERY tool call, immediately provide the result with ✅
4. Use echo_message tool if needed to ensure progress updates are sent
5. Never execute multiple tools without progress updates between them

REQUIRED RESPONSE PATTERN:
```
I'll help you get an AWS account overview. Here's my plan:
1. Check EC2 instances
2. List S3 buckets
3. Review Lambda functions
4. Check IAM resources
5. Look at databases

🔍 Checking EC2 instances now...
[Execute EC2 tool]
✅ Found 2 EC2 instances: 1 running (t3.large), 1 stopped (t3a.2xlarge)

🔍 Now checking S3 buckets...
[Execute S3 tool]  
✅ Found 47 S3 buckets - mix of service and personal storage

🔍 Next, reviewing Lambda functions...
[Execute Lambda tool]
✅ Found 5 Lambda functions including MCP tools and API handlers

[Continue this exact pattern for ALL tasks]

📊 **Complete Overview:**
[Final detailed summary]
```

CRITICAL RULES - NO EXCEPTIONS:
- Use 🔍 before EVERY tool execution
- Use ✅ immediately after EVERY tool result
- Provide specific results after each tool call
- Never batch multiple tool calls without intermediate updates
- Use echo_message tool to send progress updates if needed
- Break complex operations into smaller atomic tasks

ATOMIC TASK BREAKDOWN STRATEGY:
Your role is to break down complex AWS queries into very small, atomic tasks and execute them step-by-step with immediate progress updates.

EXECUTION WORKFLOW:
1. **Think First**: Use the think tool to break down complex requests into atomic steps
2. **Announce Plan**: Tell the user your step-by-step plan with numbered steps
3. **Execute with Updates**: For each step:
   - Say "🔍 [What you're about to check]..."
   - Execute the tool
   - Immediately say "✅ [What you found]"
4. **Final Summary**: Provide comprehensive summary with 📊

TOOL USAGE STRATEGY:
1. **think**: ALWAYS use first to break down requests into atomic steps
2. **echo_message**: Use for progress announcements if streaming isn't working
3. **AWS tools**: Execute one atomic operation at a time
4. **get_current_time**: Use when time-based queries are needed
5. **stop**: Use if you exceed 15 tool calls with a summary
6. **handoff_to_user**: Use if you need guidance

PROGRESS INDICATORS (MANDATORY):
- 🤔 Thinking/Planning
- 🔍 About to check/query (REQUIRED before each tool)
- ✅ Task completed (REQUIRED after each tool)
- 📊 Final summary
- ⚠️ Issues found
- 💡 Recommendations

EXAMPLE ATOMIC TASKS:

❌ WRONG - No progress updates:
"Let me check your AWS resources... [long pause] ...here's your overview"

✅ CORRECT - With progress updates:
"I'll check your AWS resources. Here's my plan:
1. EC2 instances
2. S3 buckets
3. Lambda functions

🔍 Checking EC2 instances now...
✅ Found 2 instances: 1 running, 1 stopped

🔍 Now checking S3 buckets...
✅ Found 47 buckets across various services

🔍 Next, reviewing Lambda functions...
✅ Found 5 functions including MCP tools

📊 **Complete Overview:** [detailed summary]"

CRITICAL SUCCESS FACTORS:
- Every tool execution MUST be preceded by 🔍 announcement
- Every tool result MUST be followed by ✅ summary
- Use specific numbers and details in progress updates
- Maintain consistent emoji usage throughout
- Provide immediate feedback, never batch operations silently

Available AWS Services: EC2, S3, Lambda, CloudFormation, IAM, RDS, CloudWatch, Cost Explorer, ECS, EKS, SNS, SQS, DynamoDB, Route53, API Gateway, SES, Bedrock, SageMaker.

Remember: Progress updates with emojis are MANDATORY, not optional! Follow the exact pattern shown above.
"""

    # Start with local tools including loop control tools
    tools = [get_current_time, echo_message, think, stop, handoff_to_user]

    # Add MCP tools if available and requested - but don't try to use them in agent creation
    # The MCP client context manager issue means we should fall back to local tools for now
    if use_mcp and gateway_url and is_oauth_available():
        logger.info("🏠 MCP tools requested but using local tools only due to context manager constraints")
        logger.info("🛠️ SDK Agent will use local tools for reliable operation")
    else:
        if not gateway_url:
            logger.info("🏠 No gateway configured - using local tools only")
        elif not is_oauth_available():
            logger.info("🔑 OAuth not available - using local tools only")
        else:
            logger.info(f"🛠️ MCP disabled - using {len(tools)} local tools only")

    logger.info(f"🛠️ SDK Agent created with {len(tools)} local tools")
    return Agent(model=model, tools=tools, system_prompt=system_prompt)


# ============================================================================
# AGENTCORE APP
# ============================================================================

app = BedrockAgentCoreApp()

# ============================================================================
# STREAMING
# ============================================================================


def extract_prompt_from_payload(payload):
    """Extract prompt from payload supporting both direct and wrapped formats"""
    try:
        # Direct format: {"prompt": "message", "session_id": "optional", "actor_id": "user"}
        if isinstance(payload, dict) and "prompt" in payload:
            return (
                payload.get("prompt", "No prompt provided"),
                payload.get("session_id"),
                payload.get("actor_id", "user"),
            )

        # Wrapped format: {"payload": "{\"prompt\": \"message\"}"}
        if isinstance(payload, dict) and "payload" in payload:
            try:
                inner_payload = json.loads(payload["payload"])
                return (
                    inner_payload.get("prompt", "No prompt provided"),
                    inner_payload.get("session_id"),
                    inner_payload.get("actor_id", "user"),
                )
            except json.JSONDecodeError:
                logger.warning("⚠️ Invalid JSON in wrapped payload")
                return "Invalid payload format", None, "user"

        # Fallback
        logger.warning(f"⚠️ Unexpected payload format: {type(payload)}")
        return (
            "No prompt found in input, please provide a JSON payload with prompt key",
            None,
            "user",
        )

    except Exception as e:
        logger.error(f"❌ Failed to extract prompt: {e}")
        return f"Error processing payload: {str(e)}", None, "user"


# ============================================================================
# SDK APP
# ============================================================================

# Using automatic ping handler from BedrockAgentCoreApp


@app.entrypoint
async def invoke(payload):
    """Process user input and return a response with memory support"""
    logger.info("📥 Received SDK invocation request")

    response_parts = []

    try:
        # Extract prompt and metadata from payload
        user_message, session_id, actor_id = extract_prompt_from_payload(payload)

        logger.info(f"🚀 SDK Agent invocation: {user_message[:50]}...")
        logger.info(f"📋 Session: {session_id}, Actor: {actor_id}")

        # Get conversation context if memory is available
        context = ""
        if is_memory_available() and session_id:
            context = get_conversation_context(session_id, actor_id)
            if context:
                logger.info(f"💾 Retrieved context length: {len(context)} chars")

        # Prepare final message with context
        final_message = user_message
        if context:
            final_message = f"{context}\n\nCurrent user message: {user_message}"

        # Create model with streaming enabled
        model = BedrockModel(**model_settings, streaming=True, timeout=900)

        # Use the streaming function with proper MCP context management
        async for event in execute_agent_streaming_sdk(model, final_message):
            # Format event for SDK (keeps format_sdk_response)
            formatted = format_sdk_response(event)
            yield formatted

            # Extract text for memory storage
            text = extract_text_from_event(event)
            if text:
                response_parts.append(text)

        # Save conversation to memory after streaming
        if is_memory_available() and session_id and response_parts:
            full_response = "".join(response_parts)
            save_conversation(session_id, user_message, full_response, actor_id)
            logger.info("💾 Conversation saved successfully")

    except Exception as e:
        logger.error(f"❌ SDK streaming error: {e}")
        error_response = format_error_response(str(e), "sdk")
        yield error_response


# ============================================================================
# STARTUP INITIALIZATION
# ============================================================================


def initialize_services():
    """Initialize services on startup"""
    logger.info("🚀 Starting SDK Agent...")

    # Initialize OAuth
    if setup_oauth():
        logger.info("✅ OAuth initialized")
    else:
        logger.warning("⚠️ OAuth not available")

    # Initialize Memory
    if setup_memory():
        logger.info("✅ Memory initialized")
    else:
        logger.warning("⚠️ Memory not available")

    logger.info("✅ SDK Agent ready (using streaming pattern)")


def cleanup_resources():
    """Clean up resources on shutdown"""
    logger.info("🛑 Shutting down SDK Agent...")
    logger.info("✅ SDK Agent shutdown complete")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logger.info("🚀 Starting SDK Agent...")

    # Initialize services before starting the app
    initialize_services()

    try:
        app.run()
    finally:
        # Clean up resources on shutdown
        cleanup_resources()
