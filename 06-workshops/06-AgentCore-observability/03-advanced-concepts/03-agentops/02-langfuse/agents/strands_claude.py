from bedrock_agentcore.runtime import BedrockAgentCoreApp
import os
from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient
from langfuse import get_client


streamable_http_mcp_client = MCPClient(lambda: streamablehttp_client("https://langfuse.com/api/mcp"))


# Function to initialize Bedrock model
def get_bedrock_model():
    model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

    bedrock_model = BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=4096)
    return bedrock_model


# Initialize the Bedrock model
bedrock_model = get_bedrock_model()

# Define the agent's system prompt (exact from AWS sample)
system_prompt = os.getenv("SYSTEM_PROMPT", "You are an experienced agent supporting developers.")
env = os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "DEV")

app = BedrockAgentCoreApp()


@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload
    """

    user_input = payload.get("prompt")
    trace_id = payload.get("trace_id")
    parent_obs_id = payload.get("parent_obs_id")
    print("User input:", user_input)

    # Initialize Strands telemetry and setup OTLP exporter
    strands_telemetry = StrandsTelemetry()
    strands_telemetry.setup_otlp_exporter()

    # Create an agent with MCP tools
    with streamable_http_mcp_client:
        mcp_tools = streamable_http_mcp_client.list_tools_sync()

        # Create the agent
        agent = Agent(model=bedrock_model, system_prompt=system_prompt, tools=mcp_tools)
        # Reopen span for OTEL distributed tracing in DEV and TST environments to consolidate traces from AgentCore and Langfuse experiments
        if env == "DEV" or env == "TST":
            with get_client().start_as_current_observation(
                name="strands-agent",
                trace_context={
                    "trace_id": trace_id,
                    "parent_observation_id": parent_obs_id,
                },
            ):
                response = agent(user_input)
        else:
            response = agent(user_input)

    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
