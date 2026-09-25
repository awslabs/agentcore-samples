"""Run the real Agents SDK delegation and approval loop with scripted model output."""

import asyncio
import json

import pytest
from agents import Model, ModelResponse, RunConfig, Runner, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from pay_for_research import build_agent_team


def tool_call(name, call_id, arguments):
    return ResponseFunctionToolCall(
        id=call_id, call_id=call_id, type="function_call", name=name, arguments=json.dumps(arguments)
    )


def message(text):
    return ResponseOutputMessage(
        id="message-" + text,
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class ScriptedModel(Model):
    def __init__(self):
        self.outputs = [
            tool_call("research_public_evidence", "public-call", {"input": "Research AMZN"}),
            message("Public-evidence-gap"),
            tool_call("research_premium_evidence", "premium-call", {"input": "Close the evidence gap"}),
            tool_call("fetch_approved_premium_source", "fetch-call", {}),
            tool_call("payment_session_status", "status-call", {}),
            message("Premium-report"),
            message("Completed-brief"),
        ]

    async def get_response(self, *args, **kwargs):
        return ModelResponse(output=[self.outputs.pop(0)], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError("This test does not stream")


class RecordingPaymentClient:
    def __init__(self):
        self.urls = []

    def fetch(self, url):
        self.urls.append(url)
        return '{"ok": true, "payment_made": true}'

    def session_status(self):
        return '{"available_spend": "0.20"}'


@pytest.mark.parametrize("approved", [True, False])
def test_nested_payment_approval_blocks_spending_until_the_outer_run_resumes(approved):
    async def exercise():
        model = ScriptedModel()
        payment = RecordingPaymentClient()
        team = build_agent_team(
            payment,
            approved_paid_url="https://merchant.example/data",
            require_payment_approval=True,
            model=model,
            include_web_search=False,
        )
        run_config = RunConfig(tracing_disabled=True)
        result = await Runner.run(team.lead, "Research AMZN", run_config=run_config)

        assert result.interruptions
        assert payment.urls == []
        state = result.to_state()
        for interruption in result.interruptions:
            if approved:
                state.approve(interruption)
            else:
                state.reject(interruption)
        result = await Runner.run(team.lead, state, run_config=run_config)

        assert not result.interruptions
        assert result.final_output == "Completed-brief"
        assert payment.urls == (["https://merchant.example/data"] if approved else [])
        assert not model.outputs

    asyncio.run(exercise())
