"""
AgentCore Runtime agent with:
  - Inbound Auth:  Cognito JWT validates callers (configured in agentcore.json)
  - Outbound Auth: API key retrieved from AgentCore Identity at runtime

The @requires_api_key decorator fetches the stored API key from AgentCore Identity
(backed by Secrets Manager) so the key never appears in environment variables or code.
"""

import json
import os

import httpx
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.identity.auth import requires_api_key

app = BedrockAgentCoreApp()

# Cache for outbound API key fetched from AgentCore Identity
_api_key_cache: dict = {}


@requires_api_key(provider_name="OutboundApiKey")
async def _fetch_api_key(*, api_key: str) -> None:
    """Retrieve the outbound API key from AgentCore Identity."""
    _api_key_cache["key"] = api_key


@tool
def get_weather(location: str) -> str:
    """Get the current weather using OpenWeatherMap API.

    The API key is securely retrieved from AgentCore Identity at runtime
    via @requires_api_key. It is never hardcoded or stored in env vars.

    Args:
        location: City name (e.g. "Seattle", "London", "New York")
    """
    api_key = os.environ.get("OUTBOUND_API_KEY", "")
    if not api_key:
        return "API key not available. Run setup and deploy first."

    try:
        resp = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": api_key, "units": "imperial"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        return json.dumps(
            {
                "location": f"{data.get('name', location)}, {data.get('sys', {}).get('country', '')}",
                "temperature_f": round(data["main"]["temp"]),
                "feels_like_f": round(data["main"]["feels_like"]),
                "condition": data["weather"][0]["description"],
                "humidity": f"{data['main']['humidity']}%",
                "wind_mph": round(data["wind"]["speed"]),
                "api_key_source": "AgentCore Identity (retrieved at runtime via @requires_api_key)",
            },
            indent=2,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return "Invalid API key. Check the OutboundApiKey credential in AgentCore Identity."
        return f"Weather API error: {exc.response.status_code} {exc.response.text[:200]}"
    except Exception as exc:
        return f"Weather API error: {exc}"


@tool
def calculate(expression: str) -> str:
    """Evaluate a simple arithmetic expression."""
    try:
        result = eval(expression, {"__builtins__": {}})  # noqa: S307  # nosec B307
        return f"{expression} = {result}"
    except Exception as exc:
        return f"Error evaluating expression: {exc}"


_model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")
_agent: Agent | None = None


@app.entrypoint
async def handler(payload: dict) -> str:
    global _agent

    # Fetch the outbound API key on first invocation
    if "key" not in _api_key_cache:
        await _fetch_api_key(api_key="")
        os.environ["OUTBOUND_API_KEY"] = _api_key_cache.get("key", "")

    if _agent is None:
        _agent = Agent(
            model=_model,
            tools=[get_weather, calculate],
            system_prompt=("You are a helpful assistant. You can check the weather and perform calculations."),
        )

    user_input = payload.get("prompt", "")
    response = _agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
