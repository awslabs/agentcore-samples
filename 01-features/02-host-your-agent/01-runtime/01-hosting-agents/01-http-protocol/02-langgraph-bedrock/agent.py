"""A stateless Bedrock tool-routing loop on AgentCore Runtime."""

import math
import operator
from typing import Literal

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from starlette.exceptions import HTTPException

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
app = BedrockAgentCoreApp()


@tool
def calculator(
    operation: Literal["add", "subtract", "multiply", "divide", "sqrt"], left: float, right: float | None = None
) -> str:
    """Apply one arithmetic operation to finite numbers. For sqrt, supply only left."""
    operations = {"add": operator.add, "subtract": operator.sub, "multiply": operator.mul, "divide": operator.truediv}
    if not math.isfinite(left) or (right is not None and not math.isfinite(right)):
        return "Error: use finite numbers."
    try:
        if operation == "sqrt":
            if right is not None:
                return "Error: sqrt takes one number."
            result = math.sqrt(left)
        elif right is None:
            return "Error: supply two numbers."
        else:
            result = operations[operation](left, right)
    except (ValueError, ZeroDivisionError):
        return "Error: the operation is undefined for those inputs."
    return str(result) if math.isfinite(result) else "Error: result is not finite."


@tool
def weather() -> str:
    """Return fixture weather for the tool-routing demo, not a live forecast."""
    return "sunny"


def create_agent(model):
    tools = [calculator, weather]
    bound = model.bind_tools(tools)
    system = SystemMessage(
        content="Use the calculator for math (one operation per tool call). Weather is a fixed demo response, not a forecast."
    )

    def chatbot(state: MessagesState):
        return {"messages": [bound.invoke([system, *state["messages"]])]}

    builder = StateGraph(MessagesState)
    builder.add_node("chatbot", chatbot)
    builder.add_node("tools", ToolNode(tools))
    builder.set_entry_point("chatbot")
    builder.add_conditional_edges("chatbot", tools_condition)
    builder.add_edge("tools", "chatbot")
    return builder.compile()


agent = create_agent(ChatBedrockConverse(model=MODEL_ID, temperature=0.1))


@app.entrypoint
def langgraph_bedrock(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise HTTPException(400, "Send a nonempty prompt of at most 4000 characters.")
    try:
        response = agent.invoke({"messages": [HumanMessage(content=prompt)]}, config={"recursion_limit": 12})
        return response["messages"][-1].text
    except Exception:  # noqa: BLE001 - Model/tool exceptions may include request data.
        raise HTTPException(502, "The agent could not complete the request.") from None


if __name__ == "__main__":
    app.run()
