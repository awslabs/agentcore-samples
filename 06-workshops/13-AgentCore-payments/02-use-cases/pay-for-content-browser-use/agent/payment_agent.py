"""
Pay for Content (Browser Use) — Strands agent for AgentCore Runtime.

The agent uses AgentCoreBrowser to navigate a paywalled page, reads the
x402 requirement from the DOM, calls process_x402_payment to generate a
proof, fills it into the paywall UI, and returns the unlocked content.

When deployed to AgentCore Runtime, the container runs under
ProcessPaymentRole. The agent's PaymentManager uses the container's
ambient credentials — there is no sts:AssumeRole inside the agent.

The app backend (notebook) creates the payment session under
ManagementRole and passes all payment context via the invocation payload:

    payment_manager_arn      — Payment Manager ARN
    payment_session_id       — fresh session with budget
    payment_instrument_id    — wallet to pay from
    user_id                  — payment isolation key
    paywall_url              — page to retrieve

Browser x402 pattern: the requirement is read from a <script> element in
the DOM, not from an HTTP 402 response. The plugin's auto-intercept hook
does not fire — process_x402_payment constructs the synthetic 402 shape
PaymentManager expects.
"""

import json
import os
import uuid

from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from bedrock_agentcore.payments.manager import PaymentManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands_tools.browser import AgentCoreBrowser

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
# Identifier reported in the AgentCore Payments observability dashboard's
# "Agents using Payments" counter and on each payment span's
# `payment_agent_name` attribute. Set via the
# X-Amzn-Bedrock-AgentCore-Payments-Agent-Name HTTP header on every
# data-plane call when PaymentManager is constructed with agent_name=.
AGENT_NAME = os.environ.get("AGENT_NAME", "PayForContentBrowserAgent")

SYSTEM_PROMPT = """\
You are a content retrieval agent with access to Amazon Bedrock AgentCore payments.
You can autonomously browse paywalled websites and pay for premium content using the
x402 micropayment protocol — without any human involvement in the payment step.

When asked to retrieve content from a URL, follow these steps in order:

1. Use the browser tool to navigate to the URL.
2. Find the <script id="x402-requirement"> element and read its JSON content.
3. Call process_x402_payment with the full JSON text of that element.
4. Use the browser tool to interact with the paywall UI:
   - Discover payment form elements dynamically using button text, input types,
     and aria-labels — do not rely on hardcoded IDs from any particular site.
   - On the reference sample content provider the IDs are: pay-btn, proof-input,
     verify-btn, content — but real x402 sites will differ.
5. Wait for the content to become visible, then extract and return it.
6. Report the content retrieved and the amount paid in USDC.

Always be transparent about what you paid and what content you retrieved.
"""


@app.entrypoint
def handle_request(payload, context=None):
    """Handle a paywall retrieval request from the app backend.

    Args:
        payload: dict with:
            prompt                 — natural-language task (may include the URL)
            paywall_url            — target paywalled page
            payment_manager_arn    — Payment Manager ARN
            user_id                — payment isolation key
            payment_session_id     — fresh session with budget
            payment_instrument_id  — wallet to pay from
        context: AgentCore Runtime context (provides session_id, etc.)
    """
    # `agentcore invoke` wraps a JSON arg as {"prompt": "<json-string>"}; unwrap it.
    raw_prompt = payload.get("prompt", "")
    if isinstance(raw_prompt, str) and raw_prompt.strip().startswith("{"):
        try:
            inner = json.loads(raw_prompt)
            if "payment_manager_arn" in inner:
                payload = inner
        except json.JSONDecodeError:
            pass

    payment_manager_arn = payload.get("payment_manager_arn")
    user_id = payload.get("user_id")
    session_id = payload.get("payment_session_id")
    instrument_id = payload.get("payment_instrument_id")
    paywall_url = payload.get("paywall_url")
    prompt = payload.get("prompt") or (
        f"Please retrieve the premium article from {paywall_url}. "
        f"Pay for it using x402 and give me a summary of what it contains."
    )

    missing = [
        name
        for name, value in [
            ("payment_manager_arn", payment_manager_arn),
            ("user_id", user_id),
            ("payment_session_id", session_id),
            ("payment_instrument_id", instrument_id),
            ("paywall_url", paywall_url),
        ]
        if not value
    ]
    if missing:
        return {"error": f"Missing required fields in payload: {', '.join(missing)}"}

    # PaymentManager uses the container's ambient credentials (ProcessPaymentRole
    # when deployed; whatever role is active when running locally for dev).
    # agent_name populates the X-Amzn-Bedrock-AgentCore-Payments-Agent-Name
    # header on every data-plane call so AgentCore Payments observability
    # can attribute spans/metrics back to this agent.
    payment_manager = PaymentManager(
        payment_manager_arn=payment_manager_arn,
        region_name=REGION,
        agent_name=AGENT_NAME,
    )

    @tool
    def process_x402_payment(requirement_json: str) -> dict:
        """Process an x402 v2 payment requirement and return a signed proof.

        Args:
            requirement_json: JSON string of the x402 requirement read from
                              the <script id="x402-requirement"> DOM element.

        Returns:
            dict with proof_b64, amount, and status.
        """
        requirement = json.loads(requirement_json)

        first_accept = requirement["accepts"][0]
        amount_units = int(first_accept.get("maxAmountRequired") or first_accept.get("amount", 0))
        # Token's smallest unit (e.g. 1_000_000 for USDC's 6 decimals) — the
        # value is reported back to the caller for display, not used for
        # routing or settlement.
        amount = amount_units / 1_000_000

        # generate_payment_header expects an HTTP-402-shaped envelope.
        # In the browser pattern the requirement comes from a DOM script tag
        # rather than an HTTP 402 response, so we wrap it to match the SDK's
        # input contract.
        payment_required_request = {
            "statusCode": 402,
            "headers": {},
            "body": requirement,
        }

        header_dict = payment_manager.generate_payment_header(
            user_id=user_id,
            payment_instrument_id=instrument_id,
            payment_session_id=session_id,
            payment_required_request=payment_required_request,
            client_token=str(uuid.uuid4()),
        )
        proof_b64 = list(header_dict.values())[0]

        return {
            "proof_b64": proof_b64,
            "amount": amount,
            "status": "PROOF_GENERATED",
        }

    payments_plugin = AgentCorePaymentsPlugin(
        config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=payment_manager_arn,
            user_id=user_id,
            payment_instrument_id=instrument_id,
            payment_session_id=session_id,
            region=REGION,
            agent_name=AGENT_NAME,
        )
    )

    agent_core_browser = AgentCoreBrowser(region=REGION)

    agent = Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[agent_core_browser.browser, process_x402_payment],
        plugins=[payments_plugin],
        model=MODEL_ID,
    )

    result = agent(prompt)
    text = result.message.get("content", [{}])[0].get("text", str(result))
    return {"response": text}


if __name__ == "__main__":
    app.run()
