#!/usr/bin/env python3
"""
Simplified DIY Agent following EXACT AWS documentation MCP patterns
Based on: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-clients.html
"""

import functools
import sys
import os
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(project_root)

# AWS documented imports
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands_tools import think

# Shared utilities
from agent_shared.config_manager import AgentCoreConfigManager
from agent_shared.auth import setup_oauth, get_m2m_token, is_oauth_available
from agent_shared.memory import (
    setup_memory,
    get_conversation_context,
    save_conversation,
    is_memory_available,
)
from agent_shared.responses import (
    format_diy_response,
    extract_text_from_event,
    format_error_response,
)

import time
from agent_shared import mylogger

logger = mylogger.get_logger()

# ============================================================================
# EXACT AWS DOCUMENTATION PATTERNS
# ============================================================================


def _create_streamable_http_transport(url, headers=None):
    """
    EXACT function from AWS documentation
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-clients.html
    """
    return streamablehttp_client(url, headers=headers)


# def execute_agent(bedrock_model, prompt):
#     """
#     EXACT pattern from AWS documentation for Strands MCP Client
#     https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-using-mcp-clients.html
#     """
#     # Get configuration
#     config_manager = AgentCoreConfigManager()
#     gateway_url = config_manager.get_gateway_url()

#     if not gateway_url or not is_oauth_available():
#         # Fallback to local tools
#         logger.info("🏠 No MCP available - using local tools")
#         local_tools = [get_current_time, echo_message, think]
#         agent = Agent(model=bedrock_model, tools=local_tools)
#         return agent(prompt)

#     try:
#         access_token = get_m2m_token()
#         if not access_token:
#             raise Exception("No access token")

#         # Create headers for authentication
#         headers = {"Authorization": f"Bearer {access_token}"}

#         # EXACT AWS pattern: Create MCP client with functools.partial
#         mcp_client = MCPClient(functools.partial(
#             _create_streamable_http_transport,
#             url=gateway_url,
#             headers=headers
#         ))

#         # EXACT AWS pattern: Use context manager
#         with mcp_client:
#             tools = mcp_client.list_tools_sync()

#             # Add local tools
#             all_tools = [get_current_time, echo_message, think]
#             if tools:
#                 all_tools.extend(tools)
#                 logger.info(f"🛠️ Using {len(tools)} MCP tools + local tools")

#             logger.info("$$$$$$$$$$$$$$$$$$$$")
#             logger.info(tools)
#             logger.info("$$$$$$$$$$$$$$$$$$$$")
#             agent = Agent(model=bedrock_model, tools=all_tools)
#             return agent(prompt)

#     except Exception as e:
#         logger.error(f"❌ MCP execution failed: {e}")
#         # Fallback to local tools
#         local_tools = [get_current_time, echo_message, think]
#         agent = Agent(model=bedrock_model, tools=local_tools)
#         return agent(prompt)


async def execute_agent_streaming(bedrock_model, prompt):
    """
    Streaming version of AWS documented pattern
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
    # Fallback to local tools if gateway or oauth is not working
    if not gateway_url or not is_oauth_available():
        logger.info("🏠 No MCP available - using local streaming")
        local_tools = [get_current_time, echo_message, think]
        # agent = Agent(model=bedrock_model, tools=local_tools, system_prompt=system_prompt)
        agent = Agent(model=bedrock_model, tools=local_tools)
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
            all_tools = [get_current_time, echo_message]
            if tools:
                all_tools.extend(tools)
                logger.info(f"🛠️ Streaming with {len(tools)} MCP tools + local tools")

            logger.info("$$$$$$$$$$$$$$$$$$$$")
            logger.info(f"All tools count: {len(all_tools)}")
            logger.info("$$$$$$$$$$$$$$$$$$$$")

            agent = Agent(model=bedrock_model, tools=all_tools, system_prompt=system_prompt)
            async for event in agent.stream_async(prompt):
                # logger.info("=" * 50)
                # logger.info(f"Raw event: {event}")
                # logger.info(f"Event type: {type(event)} at {time.time()}")
                # Extract delta text if it's a contentBlockDelta event
                if isinstance(event, dict) and "event" in event:
                    inner_event = event["event"]
                    if "contentBlockDelta" in inner_event:
                        delta = inner_event["contentBlockDelta"].get("delta", {})
                        if "text" in delta:
                            logger.info(delta["text"])
                # logger.info("*" * 50)
                yield event

    except Exception as e:
        logger.error(f"❌ MCP streaming failed: {e}")
        # Fallback to local streaming
        logger.info("🏠 Falling back to local streaming")
        local_tools = [get_current_time, echo_message, think]
        agent = Agent(model=bedrock_model, tools=local_tools)
        async for event in agent.stream_async(prompt):
            logger.info("@@@@@@@@@@@@@@@@@@@@")
            logger.info(tools)
            logger.info("@@@@@@@@@@@@@@@@@@@@")
            yield event


# ============================================================================
# LOCAL TOOLS
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
# CONFIGURATION
# ============================================================================

config_manager = AgentCoreConfigManager()
model_settings = config_manager.get_model_settings()

logger.info(f"🚀 Simple DIY Agent with model: {model_settings['model_id']}")

# ============================================================================
# STREAMING RESPONSE
# ============================================================================


async def stream_response(
    user_message: str, session_id: str = None, actor_id: str = "user"
) -> AsyncGenerator[str, None]:
    """Stream agent response using AWS documented patterns"""
    response_parts = []

    try:
        logger.info(f"🔄 Processing: {user_message[:50]}...")

        # Get conversation context if available
        context = ""
        if is_memory_available() and session_id:
            context = get_conversation_context(session_id, actor_id)

        # Prepare message with context
        final_message = user_message
        if context:
            final_message = f"{context}\n\nCurrent user message: {user_message}"

        # Create model with longer timeout for streaming
        model = BedrockModel(**model_settings, streaming=True, timeout=900)

        # Use AWS documented streaming pattern
        time.time()

        async for event in execute_agent_streaming(model, final_message):
            # Format and yield response
            formatted = format_diy_response(event)
            yield formatted
            time.time()

            # Collect text for memory
            text = extract_text_from_event(event)
            if text:
                response_parts.append(text)

            # Brief pause to prevent overwhelming the client
            # await asyncio.sleep(0.01)

        # Save to memory if available
        if is_memory_available() and session_id and response_parts:
            full_response = "".join(response_parts)
            save_conversation(session_id, user_message, full_response, actor_id)
            logger.info("💾 Conversation saved")

    except Exception as e:
        logger.error(f"❌ Streaming error: {e}")
        error_response = format_error_response(str(e), "diy")
        yield error_response


# ============================================================================
# INITIALIZATION
# ============================================================================


def initialize():
    """Initialize OAuth and Memory"""
    logger.info("🚀 Initializing Simple DIY Agent...")

    if setup_oauth():
        logger.info("✅ OAuth initialized")
    else:
        logger.warning("⚠️ OAuth not available")

    if setup_memory():
        logger.info("✅ Memory initialized")
    else:
        logger.warning("⚠️ Memory not available")

    logger.info("✅ Simple DIY Agent ready")


# Initialize on startup
try:
    initialize()
except Exception as e:
    logger.error(f"❌ Initialization failed: {e}")

# ============================================================================
# FASTAPI APP
# ============================================================================

app = FastAPI(title="Simple DIY Agent (AWS Pattern)", version="1.0.0")


class InvocationRequest(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    actor_id: str = "user"


@app.post("/invocations")
async def invoke_agent(request: InvocationRequest):
    """AgentCore Runtime endpoint using exact AWS MCP patterns"""
    logger.info("📥 Received invocation request")

    try:
        return StreamingResponse(
            stream_response(request.prompt, request.session_id, request.actor_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",  # ,
                # "X-Accel-Buffering": "no",  # Disable nginx buffering
                # "Transfer-Encoding": "chunked"
            },
        )

    except Exception as e:
        logger.error(f"💥 Request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Agent processing failed: {str(e)}")


@app.get("/ping")
async def ping():
    """Health check endpoint"""
    return {"status": "healthy", "agent_type": "diy_simple", "pattern": "aws_exact"}


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    logger.info("🚀 Starting Simple DIY Agent with AWS patterns...")
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
