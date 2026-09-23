"""
AgentCore Observability — LlamaIndex Agent (non-runtime hosted).

Demonstrates how to instrument a LlamaIndex FunctionAgent running outside AgentCore
Runtime so its traces appear in the CloudWatch GenAI Observability dashboard.

LlamaIndexOpenTelemetry bridges LlamaIndex spans into the standard OTel pipeline,
which ADOT then exports to CloudWatch.

Prerequisites:
    - CloudWatch Transaction Search enabled (see 05-infrastructure-as-code/)
    - OTEL environment variables set (see .env.example)
    - CloudWatch log group created (see setup.py)

Usage:
    opentelemetry-instrument python llama_index_agent.py
"""

import asyncio
import logging
import os

from dotenv import load_dotenv
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.observability.otel import LlamaIndexOpenTelemetry

load_dotenv()

# Initialize OpenTelemetry instrumentation for LlamaIndex
instrumentor = LlamaIndexOpenTelemetry(debug=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("llamaindex").setLevel(logging.INFO)


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


async def main():
    model = get_model()
    agent = FunctionAgent(tools=[add, multiply], llm=model)

    # Start registering spans
    instrumentor.start_registering()

    query = "What is (121 + 2) * 5?"
    result = await agent.run(query)
    print("\nAgent Response:")
    print("-" * 60)
    print(str(result))


if __name__ == "__main__":
    asyncio.run(main())
