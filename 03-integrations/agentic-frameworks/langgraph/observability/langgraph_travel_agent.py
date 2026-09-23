"""
AgentCore Observability — LangGraph Travel Agent (non-runtime hosted).

Demonstrates how to instrument a LangGraph agent running outside AgentCore Runtime
so its traces appear in the CloudWatch GenAI Observability dashboard.

The LANGSMITH_OTEL_ENABLED env var bridges LangGraph spans into the standard OTel
pipeline, which ADOT then exports to CloudWatch.

Prerequisites:
    - CloudWatch Transaction Search enabled (see 05-infrastructure-as-code/)
    - OTEL environment variables set (see .env.example)
    - CloudWatch log group created (see setup.py)

Usage:
    opentelemetry-instrument python langgraph_travel_agent.py
"""

import os
import logging
from typing import Annotated

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict
from ddgs import DDGS

load_dotenv()

os.environ["LANGSMITH_OTEL_ENABLED"] = "true"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    graph = build_graph()

    query = (
        "Research and recommend suitable travel destinations for someone looking for cowboy "
        "vibes, rodeos, and museums. Use web search to find current information about venues, "
        "events, and attractions."
    )

    output = graph.invoke({"messages": [{"role": "user", "content": query}]})
    result = output["messages"][-1].content
    print("\nAgent Response:")
    print("-" * 60)
    print(result)


if __name__ == "__main__":
    main()
