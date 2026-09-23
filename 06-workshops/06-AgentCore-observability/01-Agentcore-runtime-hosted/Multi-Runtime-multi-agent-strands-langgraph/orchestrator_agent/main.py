"""
Orchestrator Agent: Strands-based agent that coordinates sub-agents.
Uses direct AgentCore Runtime invocation with session ID propagation for trace correlation.
"""

from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import baggage
import boto3
import json
import logging
import os
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")


def get_ssm_parameter(name: str) -> str:
    """Get parameter from SSM."""
    return boto3.client("ssm").get_parameter(Name=name)["Parameter"]["Value"]


def get_region() -> str:
    """Get AWS region."""
    return boto3.Session().region_name or os.getenv("AWS_REGION", "us-east-1")


class OrchestratorAgent:
    """Orchestrator that coordinates sub-agents via direct invocation."""

    def __init__(self, session_id: str, user_id: str):
        self.session_id = session_id
        self.user_id = user_id
        self.region = get_region()

        # Create AgentCore client for calling sub-agents
        self.agentcore_client = boto3.client("bedrock-agentcore", region_name=self.region)

        # Load sub-agent ARNs from SSM
        self.travel_arn = get_ssm_parameter("/agents/travel_agent_arn")
        self.weather_arn = get_ssm_parameter("/agents/weather_agent_arn")

        logger.info(f"Initialized orchestrator with session: {session_id}")
        logger.info(f"Travel agent ARN: {self.travel_arn}")
        logger.info(f"Weather agent ARN: {self.weather_arn}")

        # Create agent with tools
        model = BedrockModel(model_id=MODEL_ID)
        self.agent = Agent(
            name="Orchestrator",
            model=model,
            tools=[self._make_travel_tool(), self._make_weather_tool()],
            system_prompt="""You coordinate between specialized agents to help users.
Use ask_travel_agent for destinations, attractions, and travel tips.
Use ask_weather_agent for weather information.
Always call the appropriate agent tools to get real information, then combine the responses into a helpful answer.""",
        )

    def _call_sub_agent(self, agent_arn: str, query: str) -> str:
        """Call a sub-agent via AgentCore Runtime with session propagation."""
        try:
            payload = json.dumps({"prompt": query})

            logger.info(f"Calling sub-agent {agent_arn} with session {self.session_id}")

            # Use the correct API parameters
            response = self.agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=agent_arn,
                qualifier="DEFAULT",
                payload=payload,
                runtimeSessionId=self.session_id,  # For trace correlation
                runtimeUserId=self.user_id,  # Required for SIGV4 auth
            )

            # Read response - use 'response' key (not 'body')
            response_body = response["response"].read().decode("utf-8")
            logger.info(f"Sub-agent response: {response_body[:200]}...")

            # Parse the response
            result = json.loads(response_body)

            # Handle wrapped response format
            if isinstance(result, dict) and "response" in result:
                resp = result["response"]
                if isinstance(resp, list):
                    return " ".join(str(item) for item in resp)
                return str(resp)
            return str(result)

        except Exception as e:
            logger.error(f"Sub-agent call failed: {e}", exc_info=True)
            return f"Error calling agent: {str(e)}"

    def _make_travel_tool(self):
        @tool
        def ask_travel_agent(query: str) -> str:
            """Ask the travel agent for destinations, attractions, and travel tips."""
            return self._call_sub_agent(self.travel_arn, query)

        return ask_travel_agent

    def _make_weather_tool(self):
        @tool
        def ask_weather_agent(query: str) -> str:
            """Ask the weather agent for current weather information."""
            return self._call_sub_agent(self.weather_arn, query)

        return ask_weather_agent

    def invoke(self, query: str) -> str:
        response = self.agent(query)
        return response.message["content"][0]["text"]


@app.entrypoint
def invoke(payload, context):
    """Main entrypoint - receives session context from AgentCore Runtime."""
    prompt = payload.get("prompt", "")

    # Get session ID from AgentCore context or generate new one
    session_id = context.session_id if hasattr(context, "session_id") else str(uuid.uuid4())

    # Set session ID in OpenTelemetry baggage for propagation
    baggage.set_baggage("session.id", session_id)

    # Get user ID from headers or use default
    request_headers = context.request_headers or {}
    user_id = request_headers.get(
        "x-amzn-bedrock-agentcore-runtime-user-id",
        request_headers.get("x-amzn-bedrock-agentcore-runtime-custom-actorid", "orchestrator-user"),
    )

    logger.info(f"Orchestrator received: {prompt}")
    logger.info(f"Session ID: {session_id}, User ID: {user_id}")

    orchestrator = OrchestratorAgent(session_id, user_id)
    return orchestrator.invoke(prompt)


if __name__ == "__main__":
    app.run()
