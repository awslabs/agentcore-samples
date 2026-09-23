"""AWS Documentation agent on AgentCore Runtime (HTTP protocol).

A Strands agent that answers AWS questions using the AWS Documentation MCP
server (awslabs.aws-documentation-mcp-server), launched in-process over stdio
via uvx. Served through the AgentCore Runtime HTTP contract
(BedrockAgentCoreApp + @app.entrypoint).
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = """You are an AWS Documentation Assistant powered by the AWS Documentation MCP server. Your role is to help users find accurate, up-to-date information from AWS documentation.

Key capabilities:
- Search and retrieve information from AWS service documentation
- Provide clear, accurate answers about AWS services, features, and best practices
- Help users understand AWS concepts, APIs, and configuration options
- Guide users to relevant AWS documentation sections

Guidelines:
- Always prioritize official AWS documentation as your source of truth
- Provide specific, actionable information when possible
- Include relevant links or references to AWS documentation when helpful
- If you are unsure about something, clearly state your limitations
- Focus on being helpful, accurate, and concise in your responses

You have access to AWS documentation search tools to help answer user questions effectively."""

# Built lazily on first invocation. Building at import time would launch the
# uvx MCP server (a cold-start download) during runtime init and exceed the
# 30s initialization budget.
_agent = None
_mcp_client = None


def _get_agent() -> Agent:
    global _agent, _mcp_client
    if _agent is None:
        import sys

        from mcp import StdioServerParameters, stdio_client
        from strands.tools.mcp import MCPClient

        # Launch the AWS Documentation MCP server from the installed package
        # (it is a direct dependency), not via uvx, which is not on PATH in the
        # managed runtime. python -m runs the package's server entrypoint.
        _mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "awslabs.aws_documentation_mcp_server.server"],
                )
            )
        )
        _mcp_client.start()
        _agent = Agent(
            model=BedrockModel(model_id=MODEL_ID),
            system_prompt=SYSTEM_PROMPT,
            tools=_mcp_client.list_tools_sync(),
        )
    return _agent


@app.entrypoint
def agent_invocation(payload, context):
    """Handle an AgentCore Runtime invocation."""
    user_message = payload.get("prompt")
    if not user_message:
        raise ValueError("prompt not provided")
    result = _get_agent()(user_message)
    return {"result": result.message}


if __name__ == "__main__":
    app.run()
