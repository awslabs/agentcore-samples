"""
AG2 (formerly AutoGen) Hello World — Amazon Bedrock AgentCore Integration
=========================================================================

AG2 is the community continuation of Microsoft AutoGen, maintained at ag2ai/ag2.
Key advantage over MS AutoGen: AG2 has NATIVE Amazon Bedrock support built in —
no OpenAI API key required. AWS credentials are picked up automatically from the
environment, ~/.aws/credentials, or an IAM role.

Comparison with MS AutoGen (autogen_agentchat):
  MS AutoGen:  from autogen_agentchat.agents import AssistantAgent
               from autogen_ext.models.openai import OpenAIChatCompletionClient
               model_client = OpenAIChatCompletionClient(model="gpt-4o")  # requires OPENAI_API_KEY

  AG2:         from autogen.agentchat import AssistantAgent
               from autogen import LLMConfig
               llm_config = LLMConfig(api_type="bedrock", model="...", aws_region="...")

Both packages expose similar agent abstractions, but AG2 natively integrates with
Amazon Bedrock models using AWS credentials — no third-party API keys needed.
"""

import logging
from typing import Annotated

from autogen import LLMConfig
from autogen.agentchat import AssistantAgent

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# ---------------------------------------------------------------------------
# Logging — same pattern as other framework integrations in this repo
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ag2_agent")

# ---------------------------------------------------------------------------
# LLM configuration — AG2 native Bedrock support
#
# Unlike MS AutoGen (which requires OpenAIChatCompletionClient + OPENAI_API_KEY),
# AG2 connects directly to Amazon Bedrock via api_type="bedrock".
# AWS credentials are resolved automatically (env vars, ~/.aws, or IAM role).
# ---------------------------------------------------------------------------
llm_config = LLMConfig(
    api_type="bedrock",
    model="anthropic.claude-3-sonnet-20240229-v1:0",
    aws_region="us-west-2",
)

# ---------------------------------------------------------------------------
# AG2 AssistantAgent
#
# MS AutoGen equivalent:
#   agent = AssistantAgent(name="...", model_client=model_client, tools=[...])
#
# AG2 equivalent — LLMConfig is passed directly; tools are registered separately
# via decorators (see below) rather than as a constructor argument.
# ---------------------------------------------------------------------------
with llm_config:
    agent = AssistantAgent(
        name="weather_agent",
        system_message=(
            "You are a helpful assistant. "
            "Use the get_weather tool to answer weather questions."
        ),
    )

# ---------------------------------------------------------------------------
# Tool registration — AG2 decorator pattern
#
# Two decorators are always required:
#   @agent.register_for_llm(description="...")
#       -> Generates the JSON schema and tells the LLM the tool exists.
#   @agent.register_for_execution()
#       -> Allows the agent executor to actually call the function at runtime.
#
# Both must be applied to the same function. Because register_for_llm wraps
# the function first, register_for_execution is the outer decorator.
#
# MS AutoGen equivalent:
#   async def get_weather(city: str) -> str: ...
#   agent = AssistantAgent(..., tools=[get_weather])   # passed at construction
#
# AG2: do NOT pass tools to the constructor or to a_run() when using decorators.
# ---------------------------------------------------------------------------
@agent.register_for_execution()
@agent.register_for_llm(description="Get the current weather for a given city.")
def get_weather(city: Annotated[str, "The name of the city to get weather for"]) -> str:
    """Return a mock weather report for the requested city."""
    logger.debug("get_weather called for city: %s", city)
    return f"The weather in {city} is 73 degrees and Sunny."


# ---------------------------------------------------------------------------
# Bedrock AgentCore app — identical boilerplate across all framework examples
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()


@app.entrypoint
async def main(payload):
    """
    AgentCore entrypoint — called once per invocation.

    Payload key : "prompt"  (str)
    Default     : "What is the weather in New York?"
    Response    : {"result": <str>}

    MS AutoGen used:  result = await Console(agent.run_stream(task=prompt))
    AG2 uses      :   response = await agent.a_run(...); await response.process()
    """
    logger.debug("Starting AG2 agent execution")

    try:
        prompt = payload.get("prompt", "What is the weather in New York?")
        logger.debug("Processing prompt: %s", prompt)

        # a_run() returns an AsyncRunResponseProtocol object.
        # Tools registered via decorators above are automatically available —
        # do NOT pass them again here.
        # summary_method="last_msg" (default) makes response.summary the plain-text
        # content of the final AI message — convenient for JSON serialization.
        response = await agent.a_run(
            message=prompt,
            max_turns=5,
            user_input=False,
        )

        # process() drives the async execution loop; must be awaited before
        # accessing response.summary or response.messages.
        await response.process()

        logger.debug("Agent summary: %s", response.summary)

        # response.summary is a plain string (the last AI message content)
        # when summary_method="last_msg" (the default).
        if response.summary:
            return {"result": response.summary}

        return {"result": "No response generated"}

    except Exception as e:
        logger.error("Error in AG2 agent: %s", e, exc_info=True)
        return {"result": f"Error: {str(e)}"}


app.run()
