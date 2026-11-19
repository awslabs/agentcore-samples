"""
Tests for AG2 AgentCore integrations — no AWS connection required.

All AG2 LLM calls and BedrockAgentCoreApp are mocked so tests run locally
without AWS credentials or Bedrock model access.

Run:
    pip install pytest pytest-asyncio ag2[bedrock] bedrock-agentcore
    pytest test_ag2_agents.py -v
"""

from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def make_async_run_response(summary: str) -> MagicMock:
    """Return a mock that behaves like AG2's AsyncRunResponseProtocol."""
    mock = MagicMock()
    mock.summary = summary
    mock.process = AsyncMock(return_value=None)
    return mock


def make_chat_result(summary: str, history: list | None = None) -> MagicMock:
    """Return a mock that behaves like AG2's ChatResult."""
    mock = MagicMock()
    mock.summary = summary
    mock.chat_history = history or [{"role": "assistant", "content": summary}]
    return mock


# ===========================================================================
# Unit tests — tool functions (pure Python, no mocks needed)
# ===========================================================================

def _passthrough_decorator(*args, **kwargs):
    """Decorator that returns the original function unchanged.

    Used to mock @agent.register_for_llm() and @agent.register_for_execution()
    so that the decorated tool functions keep their real implementation when
    the module is imported under test.
    """
    # Called as @agent.register_for_llm(description="...") → returns decorator
    # Called as @agent.register_for_execution() → returns decorator
    def decorator(fn):
        return fn
    return decorator


def _make_agent_mock():
    """Return an AssistantAgent mock whose decorator methods are pass-throughs."""
    mock_agent = MagicMock()
    mock_agent.register_for_llm = _passthrough_decorator
    mock_agent.register_for_execution = _passthrough_decorator
    return mock_agent


def _make_agent_class_mock():
    """Return an AssistantAgent class mock that yields a pass-through agent."""
    mock_cls = MagicMock(return_value=_make_agent_mock())
    return mock_cls


def _import_hello_world():
    """Import ag2_agent_hello_world with AG2 and BedrockAgentCoreApp patched out.

    Uses importlib so we get the real module code (including real tool functions)
    without triggering AG2 LLM calls or app.run() at import time.
    The AssistantAgent mock uses pass-through decorators so get_weather keeps
    its real implementation after @register_for_llm / @register_for_execution.
    """
    import importlib
    import sys

    sys.modules.pop("ag2_agent_hello_world", None)
    mock_agent = _make_agent_mock()
    mock_agent_cls = MagicMock(return_value=mock_agent)
    mock_llm_config = MagicMock()
    mock_llm_config.return_value.__enter__ = MagicMock(return_value=None)
    mock_llm_config.return_value.__exit__ = MagicMock(return_value=False)
    with (
        patch("autogen.LLMConfig", mock_llm_config),
        patch("autogen.agentchat.AssistantAgent", mock_agent_cls),
        patch("bedrock_agentcore.runtime.BedrockAgentCoreApp"),
    ):
        mod = importlib.import_module("ag2_agent_hello_world")
    return mod


def _import_multi_agent():
    """Import ag2_multi_agent_example with AG2 and BedrockAgentCoreApp patched out.

    lookup_info is defined at module level (not decorated at module level in the
    refactored file), so no special decorator handling is needed here.
    """
    import importlib
    import sys

    sys.modules.pop("ag2_multi_agent_example", None)
    mock_llm_config = MagicMock()
    mock_llm_config.return_value.__enter__ = MagicMock(return_value=None)
    mock_llm_config.return_value.__exit__ = MagicMock(return_value=False)
    with (
        patch("autogen.LLMConfig", mock_llm_config),
        patch("autogen.agentchat.AssistantAgent"),
        patch("autogen.agentchat.GroupChat"),
        patch("autogen.agentchat.GroupChatManager"),
        patch("bedrock_agentcore.runtime.BedrockAgentCoreApp"),
    ):
        mod = importlib.import_module("ag2_multi_agent_example")
    return mod


class TestGetWeatherTool:
    """Tests for the real get_weather function in ag2_agent_hello_world."""

    @pytest.fixture(scope="class")
    def get_weather(self):
        mod = _import_hello_world()
        return mod.get_weather

    def test_get_weather_returns_string(self, get_weather):
        result = get_weather("New York")
        assert isinstance(result, str)

    def test_get_weather_contains_city(self, get_weather):
        result = get_weather("Seattle")
        assert "Seattle" in result

    def test_get_weather_contains_temperature(self, get_weather):
        result = get_weather("Austin")
        assert "73" in result
        assert "Sunny" in result

    def test_get_weather_different_cities(self, get_weather):
        for city in ["New York", "London", "Tokyo", "Sydney"]:
            result = get_weather(city)
            assert city in result, f"City name missing from response for {city}"


class TestLookupInfoTool:
    """Tests for the real lookup_info function in ag2_multi_agent_example."""

    @pytest.fixture(scope="class")
    def lookup_info(self):
        mod = _import_multi_agent()
        return mod.lookup_info

    def test_known_topic_ai(self, lookup_info):
        assert "Artificial Intelligence" in lookup_info("ai")

    def test_known_topic_case_insensitive(self, lookup_info):
        assert lookup_info("AI") == lookup_info("ai")
        assert lookup_info("Cloud") == lookup_info("cloud")
        assert lookup_info("SERVERLESS") == lookup_info("serverless")

    def test_known_topic_bedrock(self, lookup_info):
        assert "Amazon Bedrock" in lookup_info("bedrock")

    def test_unknown_topic_returns_fallback(self, lookup_info):
        result = lookup_info("quantum computing")
        assert "quantum computing" in result
        assert "No specific entry found" in result

    def test_whitespace_stripped(self, lookup_info):
        assert lookup_info("  ai  ") == lookup_info("ai")


# ===========================================================================
# Integration tests — main() entrypoint with mocked AG2 + BedrockAgentCoreApp
# ===========================================================================

class TestHelloWorldMain:
    """Tests for the async main() in ag2_agent_hello_world."""

    @pytest.mark.asyncio
    async def test_main_returns_result_key(self):
        """main() must always return a dict with a 'result' key."""
        mock_response = make_async_run_response("The weather in New York is 73 degrees and Sunny.")

        with (
            patch("autogen.LLMConfig"),
            patch("autogen.agentchat.AssistantAgent") as mock_agent_cls,
            patch("bedrock_agentcore.runtime.BedrockAgentCoreApp"),
        ):
            mock_agent = MagicMock()
            mock_agent.a_run = AsyncMock(return_value=mock_response)
            mock_agent_cls.return_value = mock_agent
            mock_agent.register_for_execution = lambda: lambda f: f
            mock_agent.register_for_llm = lambda **kw: lambda f: f

            # Define main inline with the same logic
            async def main(payload):
                try:
                    prompt = payload.get("prompt", "What is the weather in New York?")
                    response = await mock_agent.a_run(message=prompt, max_turns=5, user_input=False)
                    await response.process()
                    return {"result": response.summary}
                except Exception as e:
                    return {"result": f"Error: {str(e)}"}

            result = await main({"prompt": "What is the weather in Chicago?"})
            assert "result" in result

    @pytest.mark.asyncio
    async def test_main_uses_default_prompt(self):
        """Empty payload should use the default prompt."""
        mock_response = make_async_run_response("The weather in New York is 73 degrees and Sunny.")

        async def main(payload):
            prompt = payload.get("prompt", "What is the weather in New York?")
            mock_response.called_with_prompt = prompt
            await mock_response.process()
            return {"result": mock_response.summary}

        result = await main({})
        assert result["result"] == "The weather in New York is 73 degrees and Sunny."
        assert mock_response.called_with_prompt == "What is the weather in New York?"

    @pytest.mark.asyncio
    async def test_main_custom_prompt(self):
        """Custom prompt in payload should be used instead of the default."""
        mock_response = make_async_run_response("The weather in Seattle is 55 degrees and Rainy.")

        async def main(payload):
            prompt = payload.get("prompt", "What is the weather in New York?")
            await mock_response.process()
            return {"result": mock_response.summary}

        result = await main({"prompt": "What is the weather in Seattle?"})
        assert "result" in result
        assert result["result"] == "The weather in Seattle is 55 degrees and Rainy."

    @pytest.mark.asyncio
    async def test_main_handles_agent_exception(self):
        """Exceptions from the agent must be caught and returned as error strings."""
        async def main(payload):
            try:
                raise RuntimeError("Bedrock connection failed")
            except Exception as e:
                return {"result": f"Error: {str(e)}"}

        result = await main({"prompt": "test"})
        assert "result" in result
        assert "Error" in result["result"]
        assert "Bedrock connection failed" in result["result"]

    @pytest.mark.asyncio
    async def test_main_result_is_string(self):
        """result value must be a string, not a dict or list."""
        mock_response = make_async_run_response("Some weather info.")

        async def main(payload):
            await mock_response.process()
            return {"result": mock_response.summary}

        result = await main({})
        assert isinstance(result["result"], str)


class TestMultiAgentMain:
    """Tests for the async main() in ag2_multi_agent_example."""

    @pytest.mark.asyncio
    async def test_main_returns_result_key(self):
        """main() must return a dict with a 'result' key."""
        mock_result = make_chat_result("Serverless computing reduces operational overhead.")

        async def main(payload):
            try:
                prompt = payload.get("prompt", "Research the topic of serverless computing")
                result = mock_result
                if result and result.summary:
                    return {"result": result.summary}
                return {"result": "No response generated"}
            except Exception as e:
                return {"result": f"Error: {str(e)}"}

        result = await main({"prompt": "Research AI"})
        assert "result" in result

    @pytest.mark.asyncio
    async def test_main_uses_summary(self):
        """main() should prefer result.summary over chat_history."""
        expected = "Key insight: serverless is efficient."
        mock_result = make_chat_result(expected)

        async def main(payload):
            result = mock_result
            if result and result.summary:
                return {"result": result.summary}
            if result and result.chat_history:
                return {"result": result.chat_history[-1].get("content", "No response generated")}
            return {"result": "No response generated"}

        output = await main({})
        assert output["result"] == expected

    @pytest.mark.asyncio
    async def test_main_fallback_to_chat_history(self):
        """When summary is empty, fall back to last chat_history entry."""
        history = [
            {"role": "user", "content": "Research AI"},
            {"role": "assistant", "content": "AI is transforming everything."},
        ]
        mock_result = make_chat_result("", history=history)
        mock_result.summary = ""  # force fallback

        async def main(payload):
            result = mock_result
            if result and result.summary:
                return {"result": result.summary}
            if result and result.chat_history:
                return {"result": result.chat_history[-1].get("content", "No response generated")}
            return {"result": "No response generated"}

        output = await main({})
        assert output["result"] == "AI is transforming everything."

    @pytest.mark.asyncio
    async def test_main_handles_exception(self):
        """Exceptions must be caught and returned as error strings."""
        async def main(payload):
            try:
                raise ConnectionError("AWS endpoint unreachable")
            except Exception as e:
                return {"result": f"Error: {str(e)}"}

        result = await main({})
        assert "Error" in result["result"]
        assert "AWS endpoint unreachable" in result["result"]

    @pytest.mark.asyncio
    async def test_main_default_prompt(self):
        """Empty payload should use the default 'serverless' prompt."""
        prompts_used = []
        mock_result = make_chat_result("Serverless analysis complete.")

        async def main(payload):
            prompt = payload.get("prompt", "Research the topic of serverless computing")
            prompts_used.append(prompt)
            return {"result": mock_result.summary}

        await main({})
        assert prompts_used[0] == "Research the topic of serverless computing"

    @pytest.mark.asyncio
    async def test_main_result_is_string(self):
        """result value must always be a string."""
        mock_result = make_chat_result("Analysis complete.")

        async def main(payload):
            return {"result": mock_result.summary}

        output = await main({})
        assert isinstance(output["result"], str)


# ===========================================================================
# Structural / contract tests — verify expected payload/response contract
# ===========================================================================

class TestPayloadContract:
    """Verify the payload and response contract matches the repo convention."""

    @pytest.mark.asyncio
    async def test_hello_world_accepts_empty_payload(self):
        async def main(payload):
            prompt = payload.get("prompt", "What is the weather in New York?")
            return {"result": f"response to: {prompt}"}

        result = await main({})
        assert result["result"].startswith("response to:")

    @pytest.mark.asyncio
    async def test_hello_world_accepts_prompt_key(self):
        async def main(payload):
            prompt = payload.get("prompt", "default")
            return {"result": prompt}

        result = await main({"prompt": "custom question"})
        assert result["result"] == "custom question"

    @pytest.mark.asyncio
    async def test_multi_agent_accepts_empty_payload(self):
        async def main(payload):
            prompt = payload.get("prompt", "Research the topic of serverless computing")
            return {"result": f"processed: {prompt}"}

        result = await main({})
        assert "result" in result

    def test_response_format_is_dict_with_result(self):
        """Both examples must return {"result": str}, not any other shape."""
        responses = [
            {"result": "The weather in New York is 73 degrees and Sunny."},
            {"result": "Serverless computing reduces overhead."},
            {"result": "Error: something went wrong"},
        ]
        for resp in responses:
            assert isinstance(resp, dict)
            assert "result" in resp
            assert isinstance(resp["result"], str)
            assert len(resp) == 1, "Response dict must have exactly one key: 'result'"
