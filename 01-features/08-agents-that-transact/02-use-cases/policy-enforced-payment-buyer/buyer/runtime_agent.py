"""AgentCore Runtime buyer with a deterministic Policy-before-Payments tool."""

from __future__ import annotations

import os
import uuid
from typing import Any, Mapping

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.payments import PaymentManager
from strands import Agent, tool
from strands.models import BedrockModel

from buyer.core import PaymentRequirement, PolicyEnforcedBuyer
from buyer.gateway import GatewayPolicyAuthorizer, GatewayPolicyContext
from buyer.runtime_context import (
    RuntimePaymentContext,
    runtime_context,
    validate_seller_url,
)


app = BedrockAgentCoreApp()
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = """You purchase a single paid resource through the
purchase_paid_resource tool. Do not call arbitrary HTTP tools, construct payment
headers, or describe a payment as settled. Report the returned content and the
payment execution state."""


class AgentCorePaymentExecutor:
    """Create payment headers for the requirement Policy just authorized."""

    execution_label = "agentcore-payments"

    def __init__(self, context: RuntimePaymentContext, region: str) -> None:
        self._context = context
        self._manager = PaymentManager(
            payment_manager_arn=context.manager_arn,
            region_name=region,
            agent_name="policy-enforced-payment-buyer",
        )

    def payment_headers(self, requirement: PaymentRequirement) -> Mapping[str, str]:
        return self._manager.generate_payment_header(
            user_id=self._context.user_id,
            payment_instrument_id=self._context.instrument_id,
            payment_session_id=self._context.session_id,
            payment_required_request=requirement.payment_required_request,
            network_preferences=[requirement.network],
            client_token=str(uuid.uuid4()),
        )


def _purchase_tool(context: RuntimePaymentContext):
    region = os.environ.get("AWS_REGION", "").strip()
    if not region:
        raise RuntimeError("AWS_REGION is required for the Runtime payment buyer")

    buyer = PolicyEnforcedBuyer(
        policy_authorizer=GatewayPolicyAuthorizer(
            GatewayPolicyContext(
                gateway_url=context.gateway_url,
                target_name=context.target_name,
                policy_session_id=context.policy_session_id,
                region=region,
            )
        ),
        payment_executor=AgentCorePaymentExecutor(context, region),
    )

    @tool
    def purchase_paid_resource(resource_url: str) -> dict[str, Any]:
        """Purchase one x402-protected resource after Policy authorizes it.

        Args:
            resource_url: Full URL of the seller resource to purchase.

        Returns:
            Content only after Policy authorization and the payment retry.
        """

        validate_seller_url(resource_url, context.seller_base_url)
        result = buyer.purchase(resource_url)
        return {
            "statusCode": result.status_code,
            "content": result.body,
            "paymentExecution": result.payment_execution,
            "settlement": result.settlement,
            "policyInput": result.requirement.policy_input(),
        }

    return purchase_paid_resource


@app.entrypoint
def handle_request(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Run a payment-capable buyer with an app-owned session and Policy Gateway."""

    del context
    payment_context = runtime_context(payload)
    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, streaming=True),
        tools=[_purchase_tool(payment_context)],
        system_prompt=SYSTEM_PROMPT,
    )
    result = agent(str(payload.get("prompt") or ""))
    content = result.message.get("content", [])
    text = "\n".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("text")
    )
    return {"response": text or str(result)}


if __name__ == "__main__":
    app.run()
