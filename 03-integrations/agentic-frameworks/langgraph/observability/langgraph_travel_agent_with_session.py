"""
AgentCore Observability — LangGraph Travel Agent with Session Tracking.

Extends langgraph_travel_agent.py by attaching a session ID to OpenTelemetry baggage
so that all spans from this run are grouped under one session in CloudWatch GenAI
Observability.

Usage:
    opentelemetry-instrument python langgraph_travel_agent_with_session.py --session-id "session-123"
"""

import argparse
import logging
import os
from typing import Annotated

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from opentelemetry import baggage, context
from typing_extensions import TypedDict
from ddgs import DDGS

load_dotenv()

os.environ["LANGSMITH_OTEL_ENABLED"] = "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="LangGraph Travel Agent with Session Tracking")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    parser.add_argument("--user-type", help="User type for analysis (e.g., premium, free)")
    parser.add_argument("--experiment-id", help="Experiment ID for A/B testing")
    return parser.parse_args()


def set_session_context(session_id: str, user_type: str = None, experiment_id: str = None):
    """Attach session ID (and optional attributes) to OTel baggage."""
    ctx = baggage.set_baggage("session.id", session_id)
    if user_type:
        ctx = baggage.set_baggage("user.type", user_type, context=ctx)
    if experiment_id:
        ctx = baggage.set_baggage("experiment.id", experiment_id, context=ctx)
    token = context.attach(ctx)
    logger.info("Session '%s' attached to telemetry context", session_id)
    return token


@tool("web_search")
def web_search(query: str) -> str:
    """Search the web for current information about destinations, attractions, events, and general topics."""
    try:
        ddgs = DDGS()
        results = ddgs.text(query, max_results=5)
        formatted = []
        for i, result in enumerate(results, 1):
            formatted.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )
        return "\n".join(formatted) if formatted else "No results found."
    except Exception as e:
        return f"Search error: {str(e)}"


def build_graph():
    model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    llm = init_chat_model(model_id, model_provider="bedrock_converse", temperature=0.0, max_tokens=512)
    tools = [web_search]
    llm_with_tools = llm.bind_tools(tools)

    class State(TypedDict):
        messages: Annotated[list, add_messages]

    def chatbot(state: State):
        try:
            return {"messages": [llm_with_tools.invoke(state["messages"])]}
        except Exception as e:
            from langchain_core.messages import AIMessage

            return {"messages": [AIMessage(content=f"Error: {str(e)}")]}

    builder = StateGraph(State)
    builder.add_node("chatbot", chatbot)
    builder.add_node("tools", ToolNode(tools=tools))
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")
    builder.add_edge(START, "chatbot")
    return builder.compile()


def main():
    args = parse_args()
    ctx_token = set_session_context(args.session_id, args.user_type, args.experiment_id)

    try:
        graph = build_graph()

        query = (
            "Research and recommend suitable travel destinations for someone looking for cowboy "
            "vibes, rodeos, and museums. Use web search to find current information about venues, "
            "events, and attractions."
        )

        config = {"configurable": {"session_id": args.session_id}}
        output = graph.invoke({"messages": [{"role": "user", "content": query}]}, config=config)
        result = output["messages"][-1].content
        print("\nAgent Response:")
        print("-" * 60)
        print(result)

    finally:
        context.detach(ctx_token)
        logger.info("Session context for '%s' detached", args.session_id)


if __name__ == "__main__":
    main()
