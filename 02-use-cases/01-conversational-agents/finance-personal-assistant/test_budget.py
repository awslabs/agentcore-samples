import asyncio
import json
import os
import unittest
from unittest.mock import patch

from budget_agent import make_coordinator, make_model, split_budget
from strands.models.model import Model


class Scripted(Model):
    def __init__(self, name, argument, final, expected):
        self.name, self.argument, self.final, self.expected = name, argument, final, expected
        self.calls = 0

    def get_config(self):
        return {"model_id": "fixture"}

    def update_config(self, **kwargs):
        pass

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.calls == 1:
            assert any(t["name"] == self.name for t in tool_specs)
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "fixture-1", "name": self.name}},
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": json.dumps(self.argument)}},
                }
            }
            reason = "tool_use"
        else:
            assert self.expected in str(messages)
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": self.final}}}
            reason = "end_turn"
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": reason}}
        yield {
            "metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 0}}
        }


class Budget(unittest.TestCase):
    def test_nested_tools(self):
        specialist = Scripted(
            "split_budget", {"monthly_cents": 10001}, "The fictional budget leaves 2001 cents for savings.", "2001"
        )
        coordinator = Scripted(
            "ask_budget_specialist", {"question": "Allocate 10001 cents"}, "Summary: 2001 cents.", specialist.final
        )
        models = iter([specialist, coordinator])
        agent = make_coordinator(lambda: next(models))
        self.assertEqual(str(asyncio.run(agent.invoke_async("Example budget"))).strip(), coordinator.final)
        self.assertEqual((coordinator.calls, specialist.calls), (2, 2))

    def test_all_cents_accounted_for(self):
        for n in (1, 3, 100, 10001, 100_000_000):
            self.assertEqual(sum(split_budget(n).values()), n)
        for n in (True, 1.2, 0, -1, 100_000_001):
            with self.assertRaises(ValueError):
                split_budget(n)

    def test_guardrail_is_required(self):
        with (
            patch.dict(
                os.environ,
                {"GUARDRAIL_VERSION": "DRAFT", "GUARDRAIL_ID": "fixture", "BEDROCK_MODEL_ID": "fixture"},
                clear=True,
            ),
            self.assertRaises(ValueError),
        ):
            make_model()
        with patch.dict(
            os.environ, {"GUARDRAIL_VERSION": "1", "GUARDRAIL_ID": "fixture", "BEDROCK_MODEL_ID": "fixture"}, clear=True
        ):
            config = make_model().get_config()
            self.assertEqual((config["guardrail_id"], config["guardrail_version"]), ("fixture", "1"))
