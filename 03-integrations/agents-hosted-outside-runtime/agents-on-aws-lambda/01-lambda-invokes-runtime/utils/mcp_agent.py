"""
Strands MCP agent deployed to AgentCore Runtime.

Uses two MCP servers:
  - AWS Documentation MCP Server (awslabs.aws-documentation-mcp-server)
  - AWS CDK MCP Server (awslabs.cdk-mcp-server)

Invoked via BedrockAgentCoreApp HTTP entrypoint.
"""

from strands import Agent
from strands.models import BedrockModel
from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = """You are a helpful AWS assistant with access to AWS Documentation \
and CDK best practices. Provide concise and accurate information about AWS services \
and infrastructure as code patterns. When asked about pricing or CDK, use your tools \
to search for the most current information."""


def create_aws_docs_client():
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="uvx",
                args=["awslabs.aws-documentation-mcp-server@latest"],
            )
        )
    )


def create_cdk_client():
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="uvx",
                args=["awslabs.cdk-mcp-server@latest"],
            )
        )
    )


@app.entrypoint
def invoke_agent(payload):
    """Process the input payload and return the agent's response."""
    model = BedrockModel(model_id=MODEL_ID)
    aws_docs_client = create_aws_docs_client()
    cdk_client = create_cdk_client()

    with aws_docs_client, cdk_client:
        tools = aws_docs_client.list_tools_sync() + cdk_client.list_tools_sync()
        agent = Agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
        user_input = payload.get("prompt", "")
        print(f"Processing request: {user_input}")
        response = agent(user_input)
        return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
