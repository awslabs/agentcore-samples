"""
Multi-Agent Payment Orchestrator for AgentCore Runtime.

Three agents in one runtime:
- Research Agent: deep data gathering (Coinbase wallet, Session A)
- Discovery Agent: find cheap tools (Privy wallet, Session B)
- Orchestrator: routes tasks, monitors budgets, NO payment plugin

The app backend passes two session IDs and two instrument IDs via the
invocation payload. Each specialist gets its own plugin with its own
budget. The orchestrator cannot spend — structural enforcement.

Deployment:
    agentcore create --name PaymentOrchestrator --defaults
    agentcore deploy
"""

import os
import json

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from strands_tools import http_request
import boto3

app = BedrockAgentCoreApp()

PAYMENT_MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")


@app.entrypoint
def handle_request(payload, context=None):
    """Handle an invocation from the app backend.

    Args:
        payload: JSON dict with:
            - prompt: The user's request
            - user_id: User identifier
            - research_session_id: Session A (research agent budget)
            - research_instrument_id: Coinbase instrument
            - discovery_session_id: Session B (discovery agent budget)
            - discovery_instrument_id: Privy instrument
    """
    prompt = payload.get("prompt", "Hello")
    user_id = payload.get("user_id", "default-user")

    research_session_id = payload.get("research_session_id")
    research_instrument_id = payload.get("research_instrument_id")
    discovery_session_id = payload.get("discovery_session_id")
    discovery_instrument_id = payload.get("discovery_instrument_id")

    if not all(
        [
            research_session_id,
            research_instrument_id,
            discovery_session_id,
            discovery_instrument_id,
        ]
    ):
        return {"error": "Missing session or instrument IDs in payload"}

    # --- Specialist plugins (each with own session + instrument) ---

    research_plugin = AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=PAYMENT_MANAGER_ARN,
            user_id=user_id,
            payment_instrument_id=research_instrument_id,
            payment_session_id=research_session_id,
            region=REGION,
        )
    )

    discovery_plugin = AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=PAYMENT_MANAGER_ARN,
            user_id=user_id,
            payment_instrument_id=discovery_instrument_id,
            payment_session_id=discovery_session_id,
            region=REGION,
        )
    )

    # --- Budget check tool (orchestrator only) ---

    dp_client = boto3.client("bedrock-agentcore", region_name=REGION)

    @tool
    def check_budgets() -> str:
        """Check remaining budget for each specialist agent.

        Returns:
            JSON with per-agent spend and remaining budget.
        """
        results = {}
        for label, sid in [
            ("research_agent", research_session_id),
            ("discovery_agent", discovery_session_id),
        ]:
            info = dp_client.get_payment_session(
                paymentManagerArn=PAYMENT_MANAGER_ARN,
                paymentSessionId=sid,
                userId=user_id,
            )
            sess = info
            results[label] = {
                "session_id": sid,
                "available": sess.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
                "budget": sess.get("limits", {}).get("maxSpendAmount", "N/A"),
            }
        return json.dumps(results, indent=2)

    # --- Specialist agents ---

    model = BedrockModel(model_id=MODEL_ID, streaming=True)

    research_agent = Agent(
        model=model,
        tools=[http_request],
        plugins=[research_plugin],
        system_prompt=(
            "You are a research specialist. Use http_request to access paid endpoints "
            "on the Coinbase Bazaar (Base Sepolia testnet). "
            "IMPORTANT: Only use GET requests. Never use POST, PUT, or DELETE. "
            "When you discover endpoints, look for the URL in the 'resource' field. "
            "Payment is handled automatically via x402. "
            "Report what data you found and what it cost."
        ),
    )

    discovery_agent = Agent(
        model=model,
        tools=[http_request],
        plugins=[discovery_plugin],
        system_prompt=(
            "You are a data discovery specialist. Use http_request to access paid "
            "endpoints on the Coinbase Bazaar (Base Sepolia testnet). "
            "IMPORTANT: Only use GET requests. Never use POST, PUT, or DELETE. "
            "Payment is handled automatically via x402. "
            "Report what you found and the cost."
        ),
    )

    # --- Orchestrator (NO plugin — cannot spend) ---

    orchestrator = Agent(
        model=model,
        tools=[
            research_agent.as_tool(
                name="research_agent",
                description="Research specialist with Coinbase wallet and its own payment budget.",
            ),
            discovery_agent.as_tool(
                name="discovery_agent",
                description="Discovery specialist with Privy wallet and its own payment budget. Use as fallback.",
            ),
            check_budgets,
        ],
        system_prompt=(
            "You are an orchestrator that coordinates specialist agents.\n"
            "- research_agent: paid data lookups (own budget, Coinbase wallet)\n"
            "- discovery_agent: paid data lookups (own budget, Privy wallet)\n"
            "- check_budgets: monitor spend across both agents\n\n"
            "You cannot make payments yourself. Only the specialists can spend.\n"
            "If one agent's budget is exhausted, route remaining work to the other.\n"
            "After tasks complete, check budgets and report total spend."
        ),
    )

    result = orchestrator(prompt)
    return {"response": result.message.get("content", [{}])[0].get("text", str(result))}


if __name__ == "__main__":
    app.run()
