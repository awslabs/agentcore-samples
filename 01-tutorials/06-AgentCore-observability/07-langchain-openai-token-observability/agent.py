#!/usr/bin/env python3

import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry import baggage, context
LangchainInstrumentor().instrument()

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from langchain_openai import AzureChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
import requests

# Azure OpenAI Configuration (set via environment variables)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "https://iqbsacccccc-eastus2.cognitiveservices.azure.com/")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "Dhhhhhhhcccvvvvv")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini-2")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

# Define weather tool
@tool
def get_weather(location: str) -> str:
    """Get weather forecast for a US location. Use city name or coordinates."""
    try:
        # Try to get coordinates for the location
        if "," in location and all(part.replace(".", "").replace("-", "").isdigit() for part in location.split(",")):
            lat, lon = location.split(",")
            url = f"https://api.weather.gov/points/{lat.strip()},{lon.strip()}"
        else:
            # For city names, use Miami as default example
            url = "https://api.weather.gov/points/25.7617,-80.1918"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        forecast_url = data["properties"]["forecast"]
        forecast_response = requests.get(forecast_url, timeout=10)
        forecast_response.raise_for_status()
        forecast_data = forecast_response.json()
        
        periods = forecast_data["properties"]["periods"][:3]
        result = []
        for period in periods:
            result.append(f"{period['name']}: {period['detailedForecast']}")
        
        return "\n\n".join(result)
    except Exception as e:
        return f"Error fetching weather: {str(e)}"

# Create Azure OpenAI model
llm = AzureChatOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    azure_deployment=AZURE_OPENAI_DEPLOYMENT,
    api_version=AZURE_OPENAI_API_VERSION,
    temperature=0.7,
)

SYSTEM_PROMPT = "You are a helpful weather assistant for US locations. Use the get_weather tool to fetch forecasts and present them clearly."

tools = [get_weather]
agent_executor = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)

# Create AgentCore app
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    user_message = payload.get("prompt", "Hello!")
    session_id = payload.get("session_id", "default-session")
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    try:
        result = agent_executor.invoke({"messages": [{"role": "user", "content": user_message}]})
        output = result["messages"][-1].content
        return {"result": {"role": "assistant", "content": [{"text": output}]}}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"result": {"role": "assistant", "content": [{"text": f"Error: {str(e)}"}]}}
    finally:
        context.detach(token)

if __name__ == "__main__":
    app.run()
