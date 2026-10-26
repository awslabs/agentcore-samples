"""Strands agent on Bedrock AgentCore Runtime, querying a Managed KB through Gateway.

Runtime invokes `handler` per request. The handler instantiates an MCP client
that talks to the AgentCore Gateway (SigV4-signed when authorizerType=AWS_IAM),
discovers the KB Retrieve tool, and hands control to a Strands agent that
streams text deltas back through Runtime's SSE channel.
"""

from __future__ import annotations

import logging
import os

from bedrock_agentcore import BedrockAgentCoreApp
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands import Agent
from strands.tools.mcp import MCPClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

GATEWAY_URL = os.environ["GATEWAY_URL"]
REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are a knowledge-base assistant. Answer ONLY using information returned "
    "by the knowledge base tool. For every user question, call the tool first. "
    "If the tool returns no relevant results, reply: \"I don't have that in the "
    'knowledge base." Do not answer from prior model knowledge. Always cite the '
    "source URI(s) from the tool's results when you do answer.",
)

app = BedrockAgentCoreApp()


def _build_mcp_client() -> MCPClient:
    return MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=GATEWAY_URL,
            aws_service="bedrock-agentcore",
            aws_region=REGION,
        )
    )


@app.entrypoint
async def handler(payload):
    prompt = (payload or {}).get("prompt")
    if not prompt:
        yield {"error": "request must contain a 'prompt' field"}
        return

    with _build_mcp_client() as mcp_client:
        tools = list(mcp_client.list_tools_sync())
        logger.info("discovered %d MCP tool(s) from gateway", len(tools))

        agent = Agent(
            model=MODEL_ID,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
        )

        async for event in agent.stream_async(prompt):
            if "data" in event:
                yield event["data"]


if __name__ == "__main__":
    app.run()
