"""
Data Analyst Conversational Assistant - Main Application

This application provides an intelligent conversational data analyst assistant.
It leverages Anthropic Claude models on Amazon Bedrock for natural language processing,
Amazon Bedrock AgentCore Gateway for centralized tool access, AgentCore Policy Engine for
guardrail enforcement, and AgentCore Memory for conversation context management.

Key Features:
- Natural language to SQL query conversion via Gateway-hosted MCP tools
- Guardrails-in-Policy via Cedar (prompt attack, content filtering, PII suppression)
- Short-term memory (conversation persistence) and long-term semantic memory (facts across sessions)
- Real-time streaming responses
- OpenTelemetry tracing via ADOT (agent invocations, tool calls, memory operations)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# OpenTelemetry — ADOT auto-instruments Bedrock calls; we add custom spans for
# tool execution, memory operations, and request-level context.
from opentelemetry import baggage, context, trace
from opentelemetry.trace import StatusCode

tracer = trace.get_tracer("strands.telemetry.tracer", "1.0.0")

# Bedrock Agent Core imports
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from mcp.client.streamable_http import streamablehttp_client
from query_results import QueryResults, memory_facts
from request_security import GatewaySigV4, authenticated_actor, request_ids

# Custom module imports
from src.utils import load_file_content
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient


@tool
def current_time(timezone: str = "UTC") -> str:
    """Return the current time in the requested IANA timezone."""
    return datetime.now(ZoneInfo(timezone)).isoformat()


# Retrieve AgentCore Memory ID
memory_id = os.environ.get("MEMORY_ID")

# Retrieve Bedrock Model ID
bedrock_model_id_env = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

# Gateway URL for centralized MCP tools (guardrails enforced at Gateway via Policy Engine)
gateway_url = os.environ.get("GATEWAY_URL", "")

# A level survives Uvicorn startup configuration; .disabled does not.
logging.getLogger("strands.event_loop.streaming").setLevel(logging.ERROR)
# Initialize the Bedrock Agent Core app
app = BedrockAgentCoreApp()


def load_system_prompt():
    """
    Load the system prompt configuration for the data analyst conversational assistant.

    Returns:
        str: The system prompt configuration for the assistant
    """
    print("\n" + "=" * 50)
    print("📝 LOADING SYSTEM PROMPT")
    print("=" * 50)

    fallback_prompt = """You are a specialized Data Analyst Conversational Assistant with expertise in
                analyzing data trends, performance metrics, and insights. You can execute SQL queries,
                interpret data, and provide actionable business intelligence."""

    try:
        prompt = load_file_content(str(Path(__file__).with_name("instructions.txt")), default_content=fallback_prompt)
        if prompt == fallback_prompt:
            print("⚠️  Using fallback prompt (instructions.txt not found)")
        else:
            print("✅ Successfully loaded system prompt from instructions.txt")
            print(f"📊 Prompt length: {len(prompt)} characters")
        print("=" * 50 + "\n")
        return prompt
    except Exception:
        print("❌ Could not load system prompt; using the fallback")
        print("=" * 50 + "\n")
        return fallback_prompt


# Load the system prompt
DATA_ANALYST_SYSTEM_PROMPT = load_system_prompt()


def get_mcp_client():
    """Create an MCP client connected to the AgentCore Gateway."""
    if not gateway_url:
        raise RuntimeError("GATEWAY_URL environment variable not set")
    return MCPClient(lambda: streamablehttp_client(gateway_url, auth=GatewaySigV4(os.environ["AWS_REGION"])))


@app.entrypoint
async def agent_invocation(payload):
    """Main entry point for data analysis requests with streaming responses.

    Expected payload structure:
    {
        "prompt": "Your data analysis question",
        "prompt_uuid": "required-uuid",
        "id_token": "short-lived-cognito-id-token",
        "user_timezone": "US/Pacific",
        "session_id": "conversation-session-id",
        "user_id": "optional-consistency-assertion-must-match-token-sub"
    }

    Returns:
        AsyncGenerator: Yields streaming response chunks with analysis results
    """
    token = None
    try:
        # IAM authenticates the transport; verify the Cognito ID token separately
        # so an authenticated user cannot select somebody else's actor/namespace.
        user_id = authenticated_actor(payload)
        operation = payload.get("operation", "chat")
        if operation == "memory_facts":
            yield json.dumps({"facts": memory_facts(user_id)}) + "\n"
            return
        prompt_uuid, session_id = request_ids(payload, user_id)
        results = QueryResults(user_id, prompt_uuid)
        if operation == "query_results":
            yield json.dumps({"results": results.read()}) + "\n"
            return
        if operation != "chat":
            raise ValueError("Unsupported operation")
        user_message = payload.get("prompt", "")
        if not isinstance(user_message, str) or not 1 <= len(user_message) <= 16000:
            raise ValueError("Prompt must be 1-16000 characters")
        user_timezone = payload.get("user_timezone", "UTC")
        user_name = "User"

        # Set OpenTelemetry baggage for trace correlation across spans
        ctx = baggage.set_baggage("session.id", payload["session_id"])
        ctx = baggage.set_baggage("user.id", user_id, context=ctx)
        ctx = baggage.set_baggage("prompt.uuid", prompt_uuid, context=ctx)
        token = context.attach(ctx)

        print("Processing authenticated analysis request")

        with tracer.start_as_current_span(
            "agent_invocation",
            attributes={
                "gen_ai.system": "aws.bedrock",
                "gen_ai.request.model": bedrock_model_id_env,
                "session.id": payload["session_id"],
                "user.id": user_id,
                "prompt.uuid": prompt_uuid,
            },
        ) as invocation_span:
            bedrock_model = BedrockModel(model_id=bedrock_model_id_env)

            # Configure AgentCore Memory with STM + LTM retrieval
            with tracer.start_as_current_span(
                "memory_configure",
                attributes={
                    "memory.id": memory_id or "",
                    "memory.session_id": session_id,
                    "memory.actor_id": user_id,
                },
            ):
                agentcore_memory_config = AgentCoreMemoryConfig(
                    memory_id=memory_id,
                    session_id=session_id,
                    actor_id=user_id,
                    retrieval_config={
                        "/facts/{actorId}": RetrievalConfig(
                            top_k=5,
                            relevance_score=0.3,
                        ),
                    },
                )

            # Configure system prompt with user context
            system_prompt = DATA_ANALYST_SYSTEM_PROMPT.replace("{timezone}", user_timezone).replace(
                "{user_name}", user_name
            )

            # Initialize session manager (explicit close instead of context manager for async generator)
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=agentcore_memory_config,
                region_name=os.environ.get("AWS_REGION", "us-east-1"),
            )

            try:
                # Connect to AgentCore Gateway for centralized MCP tools
                mcp_client = get_mcp_client()
                with mcp_client:
                    gateway_tools = mcp_client.list_tools_sync()
                    print(f"Gateway tools loaded: {len(gateway_tools)}")

                    agent = Agent(
                        model=bedrock_model,
                        system_prompt=system_prompt,
                        session_manager=session_manager,
                        tools=[current_time] + gateway_tools,
                        hooks=[results],
                        callback_handler=None,
                    )

                    # Stream the response
                    tool_active = False

                    async for item in agent.stream_async(user_message):
                        if "event" in item:
                            event = item["event"]

                            if "contentBlockStart" in event and "toolUse" in event["contentBlockStart"].get(
                                "start", {}
                            ):
                                tool_active = True
                                yield json.dumps({"event": event}) + "\n"

                            elif "contentBlockStop" in event and tool_active:
                                tool_active = False
                                yield json.dumps({"event": event}) + "\n"

                        elif "start_event_loop" in item:
                            yield json.dumps(item) + "\n"
                        elif "current_tool_use" in item and tool_active:
                            yield json.dumps(item["current_tool_use"]) + "\n"
                        elif "data" in item:
                            yield json.dumps({"data": item["data"]}) + "\n"

                    invocation_span.set_status(StatusCode.OK)
            finally:
                with tracer.start_as_current_span("memory_flush"):
                    try:
                        session_manager.close()
                    except Exception:
                        print("Memory flush failed; the response is preserved")

    except Exception:
        # Do not return tokens, SQL, database details, or internal tracebacks.
        yield json.dumps({"error": "Request rejected or processing failed"}) + "\n"
    finally:
        if token is not None:
            context.detach(token)


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("🚀 STARTING DATA ANALYST CONVERSATIONAL ASSISTANT")
    print("=" * 80)
    print("📡 Server starting on port 8080...")
    print("🌐 Health check endpoint: /ping")
    print("🎯 Analysis endpoint: /invocations")
    print("📋 Ready to analyze data trends and insights!")
    print("=" * 80)
    app.run()
