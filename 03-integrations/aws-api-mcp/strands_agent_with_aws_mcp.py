from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import stdio_client, StdioServerParameters
from strands.tools.mcp import MCPClient

# Initialize BedrockAgentCore app
app = BedrockAgentCoreApp()


stdio_mcp_client = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(
            command="uvx",
            args=["awslabs.aws-api-mcp-server@latest"],
        )
    )
)


@app.entrypoint
def agent_invocation(payload, context):
    """
    The agent uses tools provided by the AWS API MCP Server subprocess

    Args:
        payload: Input with 'prompt' field
        context: AgentCore runtime context
    """

    with stdio_mcp_client:
        # Get the tools from the MCP server
        tools = stdio_mcp_client.list_tools_sync()

        # Create an agent with these tools
        agent = Agent(tools=tools)

        user_message = payload.get("prompt", "")

        if not user_message:
            return {"error": "No prompt provided. Please include a 'prompt' field."}

        # Invoke the agents
        result = agent(user_message)

        print("context:\n-------\n", context)
        print("result:\n*******\n", result)
        return {"result": result.message}


app.run()
