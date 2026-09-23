#!/usr/bin/env python3

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from .agent_state import AgentState
from .constants import SREConstants

# Import logging config
from .logging_config import configure_logging
from .multi_agent_langgraph import create_multi_agent_system

# Configure logging based on DEBUG environment variable
# This ensures debug mode works even when not run via __main__
if not logging.getLogger().handlers:
    # Check if DEBUG is already set in environment
    debug_from_env = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    configure_logging(debug_from_env)


# Custom filter to exclude /ping endpoint logs
class PingEndpointFilter(logging.Filter):
    def filter(self, record):
        # Filter out GET /ping requests from access logs
        if hasattr(record, "getMessage"):
            message = record.getMessage()
            if '"GET /ping HTTP/' in message:
                return False
        return True


# Configure uvicorn access logger to filter out ping requests
uvicorn_logger = logging.getLogger("uvicorn.access")
uvicorn_logger.addFilter(PingEndpointFilter())

logger = logging.getLogger(__name__)

# Simple FastAPI app
app = FastAPI(title="SRE Agent Runtime", version="1.0.0")


# Simple request/response models
class InvocationRequest(BaseModel):
    input: Dict[str, Any]


class InvocationResponse(BaseModel):
    output: Dict[str, Any]


# Global variables for agent state
agent_graph = None
tools: list[BaseTool] = []


async def initialize_agent():
    """Initialize the SRE agent system using the same method as CLI."""
    global agent_graph, tools

    if agent_graph is not None:
        return  # Already initialized

    try:
        logger.info("Initializing SRE Agent system...")

        # Get provider from environment variable with bedrock as default
        provider = os.getenv("LLM_PROVIDER", "bedrock").lower()

        # Validate provider
        if provider not in ["anthropic", "bedrock"]:
            logger.warning(f"Invalid provider '{provider}', defaulting to 'bedrock'")
            provider = "bedrock"

        logger.info(f"Environment LLM_PROVIDER: {os.getenv('LLM_PROVIDER', 'NOT_SET')}")
        logger.info(f"Using LLM provider: {provider}")
        logger.info(f"Calling create_multi_agent_system with provider: {provider}")

        # Create multi-agent system using the same function as CLI
        agent_graph, tools = await create_multi_agent_system(provider)

        logger.info(
            f"SRE Agent system initialized successfully with {len(tools)} tools"
        )

    except Exception as e:
        from .llm_utils import LLMAccessError, LLMAuthenticationError, LLMProviderError

        if isinstance(e, (LLMAuthenticationError, LLMAccessError, LLMProviderError)):
            logger.error(f"LLM Provider Error: {e}")
            print(f"\n❌ {type(e).__name__}:")
            print(str(e))
            print("\n💡 Set LLM_PROVIDER environment variable to switch providers:")
            other_provider = "anthropic" if provider == "bedrock" else "bedrock"
            print(f"   export LLM_PROVIDER={other_provider}")
        else:
            logger.error(f"Failed to initialize SRE Agent system: {e}")
        raise


@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup."""
    await initialize_agent()


@app.post("/invocations", response_model=InvocationResponse)
async def invoke_agent(request: InvocationRequest):
    """Main agent invocation endpoint."""
    global agent_graph, tools

    logger.info("Received invocation request")

    try:
        # Ensure agent is initialized
        await initialize_agent()

        # Extract user prompt
        user_prompt = request.input.get("prompt", "")
        if not user_prompt:
            raise HTTPException(
                status_code=400,
                detail="No prompt found in input. Please provide a 'prompt' key in the input.",
            )

        logger.info(f"Processing query: {user_prompt}")

        # Extract session_id and user_id from request
        session_id = request.input.get("session_id", "")
        user_id = request.input.get("user_id", "default_user")

        logger.info(f"Session ID: {session_id}, User ID: {user_id}")

        # Create initial state exactly like the CLI does
        initial_state: AgentState = {
            "messages": [HumanMessage(content=user_prompt)],
            "next": "supervisor",
            "agent_results": {},
            "current_query": user_prompt,
            "metadata": {},
            "requires_collaboration": False,
            "agents_invoked": [],
            "final_response": None,
            "auto_approve_plan": True,  # Always auto-approve plans in runtime mode
            "session_id": session_id,  # Required for memory retrieval
            "user_id": user_id,  # Required for user personalization
        }

        # Process through the agent graph exactly like the CLI
        final_response = ""

        logger.info("Starting agent graph execution")

        async for event in agent_graph.astream(initial_state):
            for node_name, node_output in event.items():
                logger.info(f"Processing node: {node_name}")

                # Log key events from each node
                if node_name == "supervisor":
                    next_agent = node_output.get("next", "")
                    metadata = node_output.get("metadata", {})
                    logger.info(f"Supervisor routing to: {next_agent}")
                    if metadata.get("routing_reasoning"):
                        logger.info(
                            f"Routing reasoning: {metadata['routing_reasoning']}"
                        )

                elif node_name in [
                    "kubernetes_agent",
                    "logs_agent",
                    "metrics_agent",
                    "runbooks_agent",
                ]:
                    node_output.get("agent_results", {})
                    logger.info(f"{node_name} completed with results")

                # Capture final response from aggregate node
                elif node_name == "aggregate":
                    final_response = node_output.get("final_response", "")
                    logger.info("Aggregate node completed, final response captured")

        if not final_response:
            logger.warning("No final response received from agent graph")
            final_response = (
                "I encountered an issue processing your request. Please try again."
            )
        else:
            logger.info(f"Final response length: {len(final_response)} characters")

        # Simple response format
        response_data = {
            "message": final_response,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": SREConstants.app.agent_model_name,
        }

        logger.info("Successfully processed agent request")
        logger.info("Returning invocation response")
        return InvocationResponse(output=response_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent processing failed: {e}")
        logger.exception("Full exception details:")
        raise HTTPException(
            status_code=500, detail=f"Agent processing failed: {str(e)}"
        )


@app.get("/ping")
async def ping():
    """Health check endpoint."""
    return {"status": "healthy"}


async def invoke_sre_agent_async(prompt: str, provider: str = "anthropic") -> str:
    """
    Programmatic interface to invoke SRE agent.

    Args:
        prompt: The user prompt/query
        provider: LLM provider ("anthropic" or "bedrock")

    Returns:
        The agent's response as a string
    """
    try:
        # Create the multi-agent system
        graph, tools = await create_multi_agent_system(provider=provider)

        # Create initial state
        initial_state: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "next": "supervisor",
            "agent_results": {},
            "current_query": prompt,
            "metadata": {},
            "requires_collaboration": False,
            "agents_invoked": [],
            "final_response": None,
        }

        # Execute and get final response
        final_response = ""
        async for event in graph.astream(initial_state):
            for node_name, node_output in event.items():
                if node_name == "aggregate":
                    final_response = node_output.get("final_response", "")

        return final_response or "I encountered an issue processing your request."

    except Exception as e:
        logger.error(f"Agent invocation failed: {e}")
        raise


def invoke_sre_agent(prompt: str, provider: str = "anthropic") -> str:
    """
    Synchronous wrapper for invoke_sre_agent_async.

    Args:
        prompt: The user prompt/query
        provider: LLM provider ("anthropic" or "bedrock")

    Returns:
        The agent's response as a string
    """
    return asyncio.run(invoke_sre_agent_async(prompt, provider))


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="SRE Agent Runtime")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "bedrock"],
        default=os.getenv("LLM_PROVIDER", "bedrock"),
        help="LLM provider to use (default: bedrock)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging and trace output",
    )

    args = parser.parse_args()

    # Configure logging based on debug flag
    from .logging_config import configure_logging

    debug_enabled = configure_logging(args.debug)

    # Set environment variables
    os.environ["LLM_PROVIDER"] = args.provider
    os.environ["DEBUG"] = "true" if debug_enabled else "false"

    logger.info(f"Starting SRE Agent Runtime with provider: {args.provider}")
    if debug_enabled:
        logger.info("Debug logging enabled")
    uvicorn.run(app, host=args.host, port=args.port)
