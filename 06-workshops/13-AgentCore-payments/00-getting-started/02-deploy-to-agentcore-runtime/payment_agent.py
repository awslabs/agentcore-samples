"""
Payment-enabled Strands Agent for AgentCore Runtime.

This agent uses the AgentCorePaymentsPlugin to automatically handle
x402 payments. When deployed to AgentCore Runtime, it runs under the
ProcessPaymentRole execution role — it can only process payments within
the budget set by the application backend.

The app backend passes ALL payment context via the invocation payload:
  - payment_manager_arn
  - payment_session_id (fresh session with payment limits)
  - payment_instrument_id
  - user_id

The agent does not read payment config from environment variables.
This keeps the agent stateless and enforces that the app backend
controls what the agent can access.

Deployment:
    agentcore create --name PaymentAgent --defaults
    agentcore deploy
"""

import json
import os

from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel
from strands_tools import http_request

# Load .env for local testing — in Runtime, values come from the payload
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

app = BedrockAgentCoreApp()

# Only non-payment config comes from env — model and region
REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = """You are a helpful research assistant with the ability to access paid APIs.
Use the http_request tool to access URLs. When you encounter paid content behind x402 paywalls,
the payment is handled automatically within your session budget.
Always report what you found and how much it cost."""


@app.entrypoint
def handle_request(payload, context=None):
    """Handle an invocation from the app backend.

    Args:
        payload: JSON dict from the invoker. Must include:
            - prompt: The user's request
            - payment_manager_arn: ARN of the Payment Manager
            - user_id: User identifier for payment isolation
            - payment_session_id: Session with budget (created by app backend)
            - payment_instrument_id: Wallet to pay from (created by app backend)
        context: AgentCore Runtime context (provides session_id, etc.)

    The app backend creates the session and passes all payment context.
    The agent runs under ProcessPaymentRole and can only spend within
    the session budget. It cannot create sessions or instruments.
    """
    # agentcore invoke wraps the JSON arg as {"prompt": "<json-string>"}
    raw_prompt = payload.get("prompt", "")
    if isinstance(raw_prompt, str) and raw_prompt.strip().startswith("{"):
        try:
            inner = json.loads(raw_prompt)
            if "payment_manager_arn" in inner:
                payload = inner
        except json.JSONDecodeError:
            pass

    prompt = payload.get("prompt", "Hello")
    payment_manager_arn = payload.get("payment_manager_arn")
    user_id = payload.get("user_id")
    session_id = payload.get("payment_session_id")
    instrument_id = payload.get("payment_instrument_id")

    # Validate — all payment fields must come from the app backend
    missing = []
    if not payment_manager_arn:
        missing.append("payment_manager_arn")
    if not user_id:
        missing.append("user_id")
    if not session_id:
        missing.append("payment_session_id")
    if not instrument_id:
        missing.append("payment_instrument_id")
    if missing:
        return {"error": f"Missing required fields in payload: {', '.join(missing)}"}

    # Create plugin per-request with the context from the app backend
    payment_plugin = AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=payment_manager_arn,
            user_id=user_id,
            payment_instrument_id=instrument_id,
            payment_session_id=session_id,
            region=REGION,
            network_preferences_config=["eip155:84532", "base-sepolia"],
        )
    )

    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, streaming=True),
        tools=[http_request],
        plugins=[payment_plugin],
        system_prompt=SYSTEM_PROMPT,
    )

    result = agent(prompt)
    return {"response": result.message.get("content", [{}])[0].get("text", str(result))}


if __name__ == "__main__":
    app.run()
