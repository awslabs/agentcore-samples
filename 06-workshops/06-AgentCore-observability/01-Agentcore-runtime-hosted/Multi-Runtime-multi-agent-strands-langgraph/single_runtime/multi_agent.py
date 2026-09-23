"""
Single Runtime Multi-Agent: All agents (Orchestrator, Travel, Weather) in one runtime.
Uses Strands framework with StrandsTelemetry for unified observability.
"""

from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from ddgs import DDGS
import os


app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")


# --- Travel Agent Tools ---
@tool
def web_search(query: str) -> str:
    """Search the web for travel information."""
    results = DDGS().text(query, max_results=3)
    return "\n".join([f"- {r['title']}: {r['body']}" for r in results])


# --- Weather Agent Tools ---
@tool
def get_weather(location: str) -> str:
    """Get current weather for a location (dummy implementation)."""
    # Dummy weather data for demo
    weather_data = {
        "new york": "72°F, Partly Cloudy",
        "london": "59°F, Rainy",
        "tokyo": "68°F, Clear",
        "paris": "65°F, Overcast",
    }
    return weather_data.get(location.lower(), f"Weather data for {location}: 70°F, Clear skies")


# --- Sub-Agents ---
model = BedrockModel(model_id=MODEL_ID)

travel_agent = Agent(
    name="Travel Agent",
    model=model,
    tools=[web_search],
    system_prompt="You are a travel expert. Use web_search to find travel information.",
)

weather_agent = Agent(
    name="Weather Agent",
    model=model,
    tools=[get_weather],
    system_prompt="You are a weather assistant. Use get_weather to provide weather information.",
)


# --- Orchestrator Tools ---
@tool
def ask_travel_agent(query: str) -> str:
    """Ask the travel agent for travel-related information."""
    response = travel_agent(query)
    return response.message["content"][0]["text"]


@tool
def ask_weather_agent(query: str) -> str:
    """Ask the weather agent for weather information."""
    response = weather_agent(query)
    return response.message["content"][0]["text"]


# --- Orchestrator Agent ---
orchestrator = Agent(
    name="Orchestrator",
    model=model,
    tools=[ask_travel_agent, ask_weather_agent],
    system_prompt="""You coordinate between specialized agents.
Use ask_travel_agent for destinations, attractions, and travel tips.
Use ask_weather_agent for weather information.
Combine responses into a helpful answer.""",
)


@app.entrypoint
def invoke(payload, context):
    """Main entrypoint for the runtime."""
    prompt = payload.get("prompt", "")
    response = orchestrator(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
