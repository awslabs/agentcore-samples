"""Deterministic tests of the actual Strands loop and AgentCore HTTP adapter. No AWS calls."""

import asyncio
import copy
import json

import httpx
import main
import pytest
from botocore.exceptions import ClientError
from strands.models.model import Model
from strands.types.exceptions import EventLoopException


class ScriptedModel(Model):
    """Speak the model event protocol, leaving tool execution to the real Agent."""

    def __init__(self, mode="tools"):
        self.mode = mode
        self.requests = []
        self.config = {"model_id": "test-model"}

    def get_config(self):
        return self.config

    def update_config(self, **model_config):
        self.config.update(model_config)

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError("This sample does not use structured model output.")
        yield  # Required by the abstract async-generator protocol.

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        turn = len(self.requests)
        self.requests.append(copy.deepcopy(messages))
        await asyncio.sleep(0)  # Let concurrent requests overlap in the isolation test.
        yield {"messageStart": {"role": "assistant"}}
        if self.mode == "loop" or (self.mode == "tools" and turn < 2):
            name = "get_product_info" if turn == 0 or self.mode == "loop" else "get_return_policy"
            arguments = {"query": "PROD-002"} if name == "get_product_info" else {"product_category": "electronics"}
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {"toolUseId": f"lookup-{turn}", "name": name},
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {
                        "toolUse": {"input": json.dumps(arguments)},
                    },
                }
            }
            stop_reason = "tool_use"
        else:
            answer = messages[0]["content"][0]["text"] if self.mode == "echo" else "Smart Watch: 30 days."
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": answer}}}
            stop_reason = "end_turn"
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": stop_reason}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 0},
            }
        }


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        yield client


@pytest.fixture
def models(monkeypatch):
    created = []

    def build(**kwargs):
        assert kwargs["model_id"] == main.MODEL_ID
        assert kwargs["max_tokens"] == 512
        model = ScriptedModel()
        created.append(model)
        return model

    monkeypatch.setattr(main, "BedrockModel", build)
    return created


def test_explicit_region_and_only_read_only_tools(monkeypatch):
    options = []

    def build(**kwargs):
        options.append(kwargs)
        return ScriptedModel()

    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setattr(main, "BedrockModel", build)
    agent = main.create_agent()
    assert options[0]["region_name"] == "us-west-2"
    assert set(agent.tool_names) == {"get_product_info", "get_return_policy"}


@pytest.mark.parametrize("query", ["PROD-002", "prod-002", "  Smart Watch  ", "fitness tracker"])
def test_product_lookup(query):
    assert main.get_product_info(query)["products"][0]["id"] == "PROD-002"


def test_search_returns_category_and_consistent_prices():
    products = main.get_product_info("accessories")["products"]
    assert {p["id"] for p in products} == {"PROD-003", "PROD-004"}
    assert all(p["category"] == "accessories" for p in products)
    assert products[0]["price_usd"] == "39.99"


def test_unknown_and_empty_queries_are_not_full_catalog_dumps():
    assert main.get_product_info(" ") == {"error": "Provide a product ID or search term."}
    assert main.get_product_info("not-a-product") == {"products": []}
    assert "error" in main.get_return_policy("unknown")


def test_policy_normalization_and_catalog_cannot_be_mutated_through_results():
    assert main.get_return_policy(" Electronics ")["window"] == "30 days"
    main.get_return_policy("electronics")["window"] = "999 days"
    main.get_product_info("PROD-002")["products"][0]["name"] = "Changed"
    assert main.RETURN_POLICIES["electronics"]["window"] == "30 days"
    assert main.PRODUCTS["PROD-002"]["name"] == "Smart Watch"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "text",
        {},
        {"prompt": None},
        {"prompt": 42},
        {"prompt": True},
        {"prompt": " "},
        {"prompt": "x" * 2001},
        {"prompt": "hello", "system_prompt": "override"},
    ],
)
async def test_invalid_requests_never_construct_a_model(client, models, payload):
    response = await client.post(
        "/invocations", content=json.dumps(payload), headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert "error" in response.json()
    assert not models


async def test_malformed_json_and_health(client, models):
    assert (await client.get("/ping")).status_code == 200
    assert (
        await client.post("/invocations", content="{", headers={"content-type": "application/json"})
    ).status_code == 400
    assert not models


@pytest.mark.parametrize("origin", ["https://untrusted.invalid", "null", "http://localhost:8080"])
async def test_browser_origin_is_rejected_before_inference(client, models, origin):
    response = await client.post("/invocations", json={"prompt": "hello"}, headers={"origin": origin})
    assert response.status_code == 403
    assert not models


async def test_form_style_json_is_rejected_before_inference(client, models):
    response = await client.post("/invocations", content='{"prompt":"hello"}', headers={"content-type": "text/plain"})
    assert response.status_code == 415
    assert not models


async def test_empty_model_answer_is_an_error(client, monkeypatch):
    class EmptyAgent:
        async def invoke_async(self, prompt):
            return " "

    monkeypatch.setattr(main, "create_agent", EmptyAgent)
    response = await client.post("/invocations", json={"prompt": "hello"})
    assert response.status_code == 502


@pytest.mark.parametrize(
    "raw, status, answer",
    [
        ("<thinking>private analysis</thinking>Smart Watch: $249.99", 200, "Smart Watch: $249.99"),
        ("<THINKING>line one\nline two</THINKING>30 days", 200, "30 days"),
        ("<thinking>unfinished analysis", 502, None),
        ("<thinking>analysis only</thinking>", 502, None),
    ],
)
async def test_reasoning_markup_is_not_returned(client, monkeypatch, raw, status, answer):
    class AnswerAgent:
        async def invoke_async(self, prompt):
            return raw

    monkeypatch.setattr(main, "create_agent", AnswerAgent)
    response = await client.post("/invocations", json={"prompt": "hello"})
    assert response.status_code == status
    if answer is not None:
        assert response.json() == {"response": answer}
    assert "thinking" not in response.text.lower()
    assert "private analysis" not in response.text


async def test_real_agent_executes_both_tools(client, models, caplog):
    response = await client.post("/invocations", json={"prompt": "Can I return PROD-002?"})
    assert response.status_code == 200
    assert response.json() == {"response": "Smart Watch: 30 days."}
    assert len(models) == 1
    assert len(models[0].requests) == 3
    history = json.dumps(models[0].requests[-1])
    assert "Smart Watch" in history
    assert "30 days" in history
    assert "tool_completed=get_product_info" in caplog.text
    assert "tool_completed=get_return_policy" in caplog.text
    assert "Can I return PROD-002?" not in caplog.text
    assert "Smart Watch" not in caplog.text


async def test_sequential_and_concurrent_requests_do_not_share_history(client, monkeypatch):
    models = []

    def build(**kwargs):
        model = ScriptedModel(mode="echo")
        models.append(model)
        return model

    monkeypatch.setattr(main, "BedrockModel", build)
    prompts = ["marker-one", "marker-two", "marker-three"]
    first = await client.post("/invocations", json={"prompt": prompts[0]})
    later = await asyncio.gather(*(client.post("/invocations", json={"prompt": p}) for p in prompts[1:]))
    assert [r.json()["response"] for r in [first, *later]] == prompts
    assert len(models) == 3
    for model, prompt in zip(models, prompts, strict=True):
        history = json.dumps(model.requests)
        assert prompt in history
        assert all(other not in history for other in prompts if other != prompt)


async def test_runaway_tool_loop_is_bounded(client, monkeypatch):
    model = ScriptedModel(mode="loop")
    monkeypatch.setattr(main, "BedrockModel", lambda **kwargs: model)
    response = await client.post("/invocations", json={"prompt": "Keep looking things up"})
    assert response.status_code == 200
    assert response.json() == {"response": "Too many reasoning steps. Try a more specific question."}
    assert len(model.requests) == main.MAX_MODEL_STEPS


async def test_timeout_cancels_the_task(client, monkeypatch):
    cancelled = asyncio.Event()

    class SlowAgent:
        async def invoke_async(self, prompt):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

    monkeypatch.setattr(main, "create_agent", SlowAgent)
    monkeypatch.setattr(main, "REQUEST_TIMEOUT_SECONDS", 0.01)
    response = await client.post("/invocations", json={"prompt": "hello"})
    assert response.status_code == 504
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "provider_error, status",
    [
        (
            ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "private-detail"}},
                "ConverseStream",
            ),
            502,
        ),
        (EventLoopException(RuntimeError("private-detail")), 502),
        (RuntimeError("private-detail"), 500),
    ],
)
async def test_error_details_are_not_exposed(client, monkeypatch, caplog, provider_error, status):
    class FailingAgent:
        async def invoke_async(self, prompt):
            raise provider_error

    monkeypatch.setattr(main, "create_agent", FailingAgent)
    response = await client.post("/invocations", json={"prompt": "private-prompt"})
    assert response.status_code == status
    assert "private-detail" not in response.text + caplog.text
    assert "private-prompt" not in caplog.text
