"""
AgentCore Observability — LlamaIndex Agent with Session Tracking.

Extends llama_index_agent.py by attaching a session ID to OpenTelemetry baggage
so that all spans from this run are grouped under one session in CloudWatch GenAI
Observability.

Usage:
    opentelemetry-instrument python llama_index_agent_with_session.py --session-id "session-123"
"""

import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.observability.otel import LlamaIndexOpenTelemetry
from opentelemetry import baggage, context

load_dotenv()

# Initialize OpenTelemetry instrumentation for LlamaIndex
instrumentor = LlamaIndexOpenTelemetry(debug=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("llamaindex").setLevel(logging.INFO)


def parse_args():
    parser = argparse.ArgumentParser(description="LlamaIndex Agent with Session Tracking")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    return parser.parse_args()


def set_session_context(session_id: str):
    """Attach session ID to OTel baggage so all spans share the session context."""
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    logger.info("Session '%s' attached to telemetry context", session_id)
    return token


def multiply(a: int, b: int) -> int:
    """Multiply two integers and return the result."""
    return a * b


def add(a: int, b: int) -> int:
    """Add two integers and return the result."""
    return a + b


def get_model():
    model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    return BedrockConverse(model=model_id, region_name=region)


async def run_agent(query: str):
    model = get_model()
    agent = FunctionAgent(tools=[add, multiply], llm=model)
    instrumentor.start_registering()
    result = await agent.run(query)
    print("\nAgent Response:")
    print("-" * 60)
    print(str(result))
    return result


def main():
    args = parse_args()
    ctx_token = set_session_context(args.session_id)
    try:
        query = "What is (121 + 2) * 5?"
        asyncio.run(run_agent(query))
    finally:
        try:
            context.detach(ctx_token)
            logger.info("Session context for '%s' detached", args.session_id)
        except ValueError as e:
            logger.warning("Context detach error: %s", e)


if __name__ == "__main__":
    main()
