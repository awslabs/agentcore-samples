from strands import Agent
from strands.models import BedrockModel
from mcp import StdioServerParameters, stdio_client
from strands.tools.mcp import MCPClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Initialize the BedrockAgentCoreApp
app = BedrockAgentCoreApp()


# Connect to AWS Documentation MCP server
def create_aws_docs_client():
    return MCPClient(
        lambda: stdio_client(StdioServerParameters(command="uvx", args=["awslabs.aws-documentation-mcp-server@latest"]))
    )


# Connect to AWS CDK MCP server
def create_cdk_client():
    return MCPClient(lambda: stdio_client(StdioServerParameters(command="uvx", args=["awslabs.cdk-mcp-server@latest"])))


# Function to create agent with tools from both MCP servers
def create_agent():
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    model = BedrockModel(model_id=model_id)

    aws_docs_client = create_aws_docs_client()
    cdk_client = create_cdk_client()

    with aws_docs_client, cdk_client:
        # Get tools from both MCP servers
        tools = aws_docs_client.list_tools_sync() + cdk_client.list_tools_sync()

        # Create agent with these tools
        agent = Agent(
            model=model,
            tools=tools,
            system_prompt="""You are a helpful AWS assistant with access to AWS Documentation 
            and CDK best practices. Provide concise and accurate information about AWS services 
            and infrastructure as code patterns. When asked about pricing or CDK, use your tools 
            to search for the most current information.""",
        )

    return agent, aws_docs_client, cdk_client


@app.entrypoint
def invoke_agent(payload):
    """Process the input payload and return the agent's response"""
    agent, aws_docs_client, cdk_client = create_agent()

    with aws_docs_client, cdk_client:
        user_input = payload.get("prompt")
        print(f"Processing request: {user_input}")
        response = agent(user_input)
        return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
