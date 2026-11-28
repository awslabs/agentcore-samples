"""Real LangGraph routing and calculator; model decisions are controlled."""

import os
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError
from starlette.exceptions import HTTPException

with patch.dict(os.environ, AWS_REGION="us-west-2", AWS_DEFAULT_REGION="us-west-2", AWS_EC2_METADATA_DISABLED="true"):
    import agent


def test_bounds_and_invalid_arguments():
    assert agent.calculator.invoke({"operation": "multiply", "left": 25, "right": 17}) == "425.0"
    assert agent.calculator.invoke({"operation": "sqrt", "left": 144}) == "12.0"
    for args in (
        {"operation": "divide", "left": 5, "right": 0},
        {"operation": "sqrt", "left": -1},
        {"operation": "add", "left": float("inf"), "right": 0},
    ):
        assert agent.calculator.invoke(args).startswith("Error:")
    with pytest.raises(ValidationError):
        agent.calculator.invoke({"operation": "other", "left": 1})


def test_graph_executes_tool_then_returns_final_message(monkeypatch):
    calls = []

    class Model:
        def bind_tools(self, tools):
            assert {t.name for t in tools} == {"calculator", "weather"}
            return self

        def invoke(self, messages):
            calls.append(messages)
            if not isinstance(messages[-1], ToolMessage):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "calculator", "args": {"operation": "add", "left": 40, "right": 2}, "id": "fixture"}
                    ],
                )
            assert messages[-1].content == "42.0"
            return AIMessage(content=[{"type": "text", "text": "42"}])

    monkeypatch.setattr(agent, "agent", agent.create_agent(Model()))
    assert agent.langgraph_bedrock({"prompt": "What is 40 + 2?"}) == "42"
    assert len(calls) == 2
    with pytest.raises(HTTPException) as err:
        agent.langgraph_bedrock({"prompt": " "})
    assert err.value.status_code == 400 and len(calls) == 2
