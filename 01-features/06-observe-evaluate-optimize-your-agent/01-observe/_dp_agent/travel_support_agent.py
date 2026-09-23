import os
import logging
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = BedrockAgentCoreApp()


@tool
def lookup_booking(booking_ref: str) -> str:
    """Look up a travel booking by reference number."""
    logger.info("Looking up booking: %s", booking_ref)
    print("agent.email: support@travelco.com")  # Intentional PII in logs for demo
    print("agent.phone: 1-800-555-0199")
    return f"Booking {booking_ref}: Flight NYC-LHR, 2026-06-15, Seat 22A. Status: Confirmed."


@tool
def get_weather(destination: str) -> str:
    """Get weather for a travel destination."""
    return f"{destination}: Sunny, 18°C. Great travel conditions."


def get_model():
    return BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
        temperature=0.0,
        max_tokens=512,
        guardrail_id=os.getenv("BEDROCK_GUARDRAIL_ID"),
        guardrail_version=os.getenv("BEDROCK_GUARDRAIL_VERSION"),
        guardrail_trace="enabled",
    )


agent = Agent(
    model=get_model(),
    system_prompt="You are a travel support agent. Use lookup_booking to retrieve booking details and get_weather to check destination conditions.",
    tools=[lookup_booking, get_weather],
    trace_attributes={"tags": ["Strands", "DataProtection"]},
)


@app.entrypoint
def travel_support(payload):
    user_input = payload.get("prompt", "")
    print(f"Processing: {user_input[:100]}")
    print("agent.id: EMP-A9X42B")  # Will be masked by CW Logs policy
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
