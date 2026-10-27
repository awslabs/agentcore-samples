"""
AG2 Multi-Agent Example — Amazon Bedrock AgentCore Integration
==============================================================

This example demonstrates AG2's multi-agent GroupChat capability — a key
differentiator from the Microsoft AutoGen single-agent hello world example.

Architecture:
  researcher  →  looks up information using a tool
  analyst     →  synthesizes the researcher's findings into insights
  Both agents collaborate via AG2's GroupChat / GroupChatManager orchestration.

Why AG2 over MS AutoGen here:
  - AG2 GroupChat natively supports multi-agent conversation orchestration
  - Native Amazon Bedrock support — no OpenAI API key required
  - MS AutoGen (autogen_agentchat) has a different, incompatible API surface

Import contrast:
  MS AutoGen:  from autogen_agentchat.agents import AssistantAgent      # WRONG for AG2
  AG2:         from autogen.agentchat import AssistantAgent, GroupChat  # correct
"""

import logging
from typing import Annotated

from autogen import LLMConfig
from autogen.agentchat import AssistantAgent, GroupChat, GroupChatManager

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# ---------------------------------------------------------------------------
# Logging — same pattern as other framework integrations in this repo
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ag2_multi_agent")

# ---------------------------------------------------------------------------
# LLM configuration — AG2 native Bedrock support
# AWS credentials resolved automatically (env vars, ~/.aws, or IAM role).
# No OPENAI_API_KEY needed — unlike MS AutoGen's OpenAIChatCompletionClient.
# ---------------------------------------------------------------------------
llm_config = LLMConfig(
    api_type="bedrock",
    model="anthropic.claude-3-sonnet-20240229-v1:0",
    aws_region="us-west-2",
)

# ---------------------------------------------------------------------------
# Tool function — defined at module level (stateless, safe to reuse).
# Registration happens inside main() on a fresh agent each invocation.
# ---------------------------------------------------------------------------
def lookup_info(topic: Annotated[str, "The topic to look up"]) -> str:
    """Return background information for a given topic from a knowledge base."""
    logger.debug("lookup_info called for topic: %s", topic)
    knowledge_base = {
        "ai": (
            "Artificial Intelligence (AI) is transforming industries by automating "
            "complex tasks, enabling natural language interfaces, and unlocking "
            "insights from large datasets. Key areas: ML, NLP, computer vision, "
            "and reinforcement learning."
        ),
        "cloud": (
            "Cloud computing provides on-demand access to computing resources "
            "including servers, storage, databases, and networking. Major providers: "
            "AWS, Azure, GCP. Benefits: scalability, cost efficiency, global reach."
        ),
        "serverless": (
            "Serverless architectures let developers run code without managing "
            "infrastructure. Functions execute on-demand and scale automatically. "
            "Examples: AWS Lambda, Azure Functions. Reduces operational overhead."
        ),
        "bedrock": (
            "Amazon Bedrock is a fully managed service that provides access to "
            "foundation models from leading AI companies via a single API. "
            "Supports model customization, RAG, and agents."
        ),
    }
    result = knowledge_base.get(topic.lower().strip())
    if result:
        return result
    return (
        f"No specific entry found for '{topic}'. "
        f"General context: {topic} is an active area of research and development in technology."
    )


# ---------------------------------------------------------------------------
# Bedrock AgentCore app — identical boilerplate across all framework examples
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()


@app.entrypoint
async def main(payload):
    """
    AgentCore entrypoint — called once per invocation.

    Payload key : "prompt"  (str)
    Default     : "Research the topic of serverless computing"
    Response    : {"result": <str>}

    Flow:
      1. researcher receives the prompt, calls lookup_info tool
      2. researcher posts findings to the GroupChat
      3. analyst reads findings and produces a structured summary
      4. manager orchestrates turn-taking until TERMINATE or max_round

    NOTE: Agents and GroupChat are created fresh per invocation to prevent
    conversation state from leaking between AgentCore calls.
    """
    logger.debug("Starting AG2 multi-agent execution")

    try:
        prompt = payload.get("prompt", "Research the topic of serverless computing")
        logger.debug("Processing prompt: %s", prompt)

        # ---------------------------------------------------------------------------
        # Agent 1: Researcher — created fresh per invocation (no state leakage).
        # ---------------------------------------------------------------------------
        with llm_config:
            researcher = AssistantAgent(
                name="researcher",
                system_message=(
                    "You are a research assistant. Use the lookup_info tool to gather "
                    "information on topics. Present your findings clearly and concisely."
                ),
            )

        # ---------------------------------------------------------------------------
        # Tool registration on the researcher agent — AG2 decorator pattern.
        #
        # @register_for_llm   → injects tool schema into the LLM's context
        # @register_for_execution → allows the executor to call the function
        #
        # Both decorators are required. register_for_execution is the outer decorator.
        # ---------------------------------------------------------------------------
        researcher.register_for_execution()(
            researcher.register_for_llm(description="Look up information about a topic.")(lookup_info)
        )

        # ---------------------------------------------------------------------------
        # Agent 2: Analyst — synthesizes researcher findings into insights.
        # Ends its final response with TERMINATE to signal completion.
        # ---------------------------------------------------------------------------
        with llm_config:
            analyst = AssistantAgent(
                name="analyst",
                system_message=(
                    "You are a senior analyst. Review the researcher's findings and produce "
                    "a concise, actionable summary with key insights and recommendations. "
                    "When your analysis is complete, end your final message with TERMINATE."
                ),
            )

        # ---------------------------------------------------------------------------
        # AG2 GroupChat — multi-agent orchestration.
        #
        # GroupChat coordinates turn-taking between agents.
        # GroupChatManager acts as the conversation driver (also an LLM-powered agent).
        # max_round caps the total number of agent turns to prevent infinite loops.
        # messages=[] is explicitly passed; a fresh list per invocation ensures no
        # history leaks across AgentCore calls.
        # ---------------------------------------------------------------------------
        group_chat = GroupChat(
            agents=[researcher, analyst],
            messages=[],
            max_round=6,
        )

        with llm_config:
            manager = GroupChatManager(
                groupchat=group_chat,
            )

        # a_initiate_chat starts the async multi-agent conversation.
        # The researcher kicks off the chat; the manager routes subsequent turns.
        # Returns a ChatResult with .summary and .chat_history attributes.
        # max_turns is NOT passed here — max_round in GroupChat is the single
        # source of truth for turn limits.
        result = await researcher.a_initiate_chat(
            recipient=manager,
            message=prompt,
        )

        logger.debug("Chat completed. Summary: %s", result.summary)

        # result.summary contains the last message content (default summary_method).
        # result.chat_history is a list of {"role": ..., "content": ...} dicts.
        if result and result.summary:
            return {"result": result.summary}

        # Fallback: extract last message from chat history
        if result and result.chat_history:
            last = result.chat_history[-1]
            content = last.get("content", "No response generated")
            return {"result": content}

        return {"result": "No response generated"}

    except Exception as e:
        logger.error("Error in AG2 multi-agent: %s", e, exc_info=True)
        return {"result": f"Error: {str(e)}"}


app.run()
