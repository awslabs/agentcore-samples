from typing import Annotated

from langchain.chat_models import init_chat_model
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition


import logging

langchain_logger = logging.getLogger("langchain")
langchain_logger.setLevel(logging.DEBUG)
import os

print("Starting up...")
os.environ["LANGSMITH_OTEL_ENABLED"] = "true"

llm = init_chat_model(
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    model_provider="bedrock_converse",
)

## Define search tool
from langchain_community.tools import DuckDuckGoSearchRun

search = DuckDuckGoSearchRun()
tools = [search]
llm_with_tools = llm.bind_tools(tools)

print("Defining state...")


## Define state
class State(TypedDict):
    messages: Annotated[list, add_messages]


graph_builder = StateGraph(State)


def chatbot(state: State):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}


print("Configuring graph...")
graph_builder.add_node("chatbot", chatbot)

tool_node = ToolNode(tools=tools)

graph_builder.add_node("tools", tool_node)

graph_builder.add_conditional_edges(
    "chatbot",
    tools_condition,
)
# Any time a tool is called, we return to the chatbot to decide the next step
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")
graph = graph_builder.compile()
graph_configured = True

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
def agent_invocation(payload, context):
    print("received payload")
    print(payload)

    tmp_msg = {
        "messages": [
            {
                "role": "user",
                "content": payload.get(
                    "prompt",
                    "No prompt found in input, please guide customer as to what tools can be used",
                ),
            }
        ]
    }
    tmp_output = graph.invoke(tmp_msg)
    print(tmp_output)

    return {"result": tmp_output["messages"][-1].content}


if __name__ == "__main__":
    app.run()
