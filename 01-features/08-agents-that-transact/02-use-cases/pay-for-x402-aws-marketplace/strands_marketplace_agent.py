"""
Research agent that finds AWS Marketplace sellers and pays them per request, billed to
your AWS account.

    User ─► Strands agent
              │
              ├─ discover (AgentCore Gateway, aws-marketplace connector)
              │    search_marketplace ─► get_listing ─► get_seller_endpoints
              │
              └─ buy (AgentCore payments, AWS Billing)
                   fetch_paid_api(url)
                     ├─► GET seller ───────────────────────► 402 + quote (PAYMENT-REQUIRED)
                     ├─► ProcessPayment(AWS_BILLING_X402) ─► budget check, payment token
                     └─► GET seller + PAYMENT-SIGNATURE ───► 200 + content

No wallet, no stablecoin, no seller API key. Two IAM roles keep budgets and spending apart:
the script creates the payment session as MarketplaceBuyerSessionRole, then runs the agent
as MarketplaceBuyerAgentRole, which can pay within that session but can't create a new one.
AgentCore payments enforces the session's spend cap on the server, and the agent can only
pay endpoints that Marketplace returned in this run.

If GATEWAY_URL isn't set, the agent skips discovery and only pays SELLER_URL.

Usage:
    python strands_marketplace_agent.py
    python strands_marketplace_agent.py "Find an x402 search API on Marketplace and buy one result"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from dotenv import load_dotenv
from marketplace_discovery import GatewayMcpClient, GatewayToolError, MarketplaceDiscovery
from marketplace_payments import AgentCorePaymentsClient, MarketplacePayer, PaymentsApiError, assume_role_session
from strands import Agent, tool
from strands.models import BedrockModel

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)
_missing = [
    k for k in ("AWS_REGION", "PAYMENT_MANAGER_ARN", "SESSION_ROLE_ARN", "AGENT_ROLE_ARN") if not os.environ.get(k)
]
if _missing:
    sys.exit(f"Missing {', '.join(_missing)}. Run setup_aws_billing.py first; it writes them to .env.")
REGION = os.environ["AWS_REGION"]
MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
GATEWAY_URL = os.environ.get("GATEWAY_URL")
SELLER_URL = os.environ.get("SELLER_URL")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
SESSION_ROLE_ARN = os.environ["SESSION_ROLE_ARN"]
AGENT_ROLE_ARN = os.environ["AGENT_ROLE_ARN"]

SESSION_BUDGET_USD = os.environ.get("SESSION_BUDGET_USD", "0.05")  # server-side cap for this run
MAX_PRICE_PER_CALL_USD = os.environ.get("MAX_PRICE_PER_CALL_USD", "0.02")  # client-side cap per request


@dataclass
class BuyerRun:
    agent: Agent
    payer: MarketplacePayer
    discovery: MarketplaceDiscovery | None
    session_id: str

    def tool_calls(self) -> list[str]:
        return [b["toolUse"]["name"] for m in self.agent.messages for b in m.get("content", []) if "toolUse" in b]


def create_budget(budget_usd: str, user_id: str) -> str:
    """Create a payment session with a spend limit, as the session role.
    In production this runs in your backend, not next to the agent."""
    admin = AgentCorePaymentsClient(
        REGION, assume_role_session(SESSION_ROLE_ARN, "set-budget", REGION), user_id=user_id
    )
    return admin.create_payment_session(MANAGER_ARN, budget_usd, expiry_minutes=60)["paymentSessionId"]


def build_buyer_agent(
    session_id: str, user_id: str = "demo-user", max_price_usd: str = MAX_PRICE_PER_CALL_USD
) -> BuyerRun:
    """Build an agent that spends from `session_id`. Everything it does runs as the agent role."""
    if not GATEWAY_URL and not SELLER_URL:
        sys.exit("Set GATEWAY_URL (run setup_gateway.py) or SELLER_URL in .env")
    agent_aws = assume_role_session(AGENT_ROLE_ARN, "marketplace-agent", REGION)

    discovery = None
    if GATEWAY_URL:
        gw_region = os.environ.get("GATEWAY_REGION", REGION)
        gw_session = assume_role_session(AGENT_ROLE_ARN, "marketplace-agent", gw_region)
        discovery = MarketplaceDiscovery(GatewayMcpClient(GATEWAY_URL, gw_region, gw_session))

    # With discovery, the payer shares the set of hosts Marketplace returned, so it starts
    # empty and only grows when get_seller_endpoints runs. Without it, only SELLER_URL is payable.
    allowed = discovery.discovered_hosts if discovery else {urlparse(SELLER_URL).hostname}
    payer = MarketplacePayer(
        AgentCorePaymentsClient(REGION, agent_aws, user_id=user_id),
        MANAGER_ARN,
        session_id,
        max_price_usd=Decimal(max_price_usd),
        allowed_hosts=allowed,
    )

    def _error(kind: str, e: Exception) -> str:
        return json.dumps({"error": kind, "reason": str(e)})

    @tool
    def search_marketplace(query: str) -> str:
        """Search AWS Marketplace for API products. Returns listing IDs, names and descriptions.
        Free. Use this first, then get_listing on the best match.

        Args:
            query: What you're looking for, for example "x402 web search".
        """
        try:
            return json.dumps(discovery.search(query))
        except GatewayToolError as e:
            return _error("search_failed", e)

    @tool
    def get_listing(listing_id: str) -> str:
        """Get details for a Marketplace listing, including its product IDs. Free.

        Args:
            listing_id: A listing ID from search_marketplace, like "prodview-...".
        """
        try:
            return json.dumps(discovery.get_listing(listing_id))
        except GatewayToolError as e:
            return _error("listing_failed", e)

    @tool
    def get_seller_endpoints(product_id: str) -> str:
        """Get the paid API endpoints for a Marketplace product that accept AWS Billing (x402),
        with estimated cost. Free. Only endpoints returned here can be paid.

        Args:
            product_id: A product ID from get_listing, like "prod-...".
        """
        try:
            return json.dumps([e.__dict__ for e in discovery.x402_endpoints(product_id)])
        except GatewayToolError as e:
            return _error("endpoints_failed", e)

    @tool
    def fetch_paid_api(url: str) -> str:
        """Call a seller's API. If it requires payment (HTTP 402), pay with AWS Billing within
        the session budget and return the paid response. Each call costs money.

        Args:
            url: Full HTTPS URL of the seller endpoint, exactly as returned by get_seller_endpoints
                or as given in your instructions.
        """
        try:
            resp = payer.fetch(url)
        except PermissionError as e:
            return _error("payment_refused", e)
        except PaymentsApiError as e:
            return json.dumps({"error": "payment_failed", "status": e.status, "reason": e.message})
        out = {"status": resp.status_code, "body": resp.body[:4000]}
        if resp.receipt:
            out["paid"] = {
                "amount_usd": resp.receipt.amount_usd,
                "product_id": resp.receipt.product_id,
                "process_payment_id": resp.receipt.process_payment_id,
            }
        return json.dumps(out)

    @tool
    def get_remaining_budget() -> str:
        """Return how much of this session's spend limit is left, in USD."""
        return json.dumps(payer.remaining_budget())

    if discovery:
        tools = [search_marketplace, get_listing, get_seller_endpoints, fetch_paid_api, get_remaining_budget]
        how_to_find = (
            "Find sellers with search_marketplace, then get_listing, then get_seller_endpoints. "
            "Only call URLs that get_seller_endpoints returned, and check their estimated cost "
            "before you buy. Discovery is free."
        )
    else:
        tools = [fetch_paid_api, get_remaining_budget]
        how_to_find = f"The seller endpoint is exactly {SELLER_URL} . Call that URL as given; do not add paths."

    system_prompt = f"""You are a research assistant that can buy data from AWS Marketplace sellers.
{how_to_find}
fetch_paid_api costs money and is billed to the user's AWS account.
Call a paid endpoint at most once per question.
If a payment is refused or fails, report the reason. Do not look for free or alternative URLs.
Always tell the user what you bought, from which seller, and what it cost."""

    agent = Agent(
        model=BedrockModel(model_id=MODEL_ID, boto_session=agent_aws),
        tools=tools,
        system_prompt=system_prompt,
        callback_handler=None,
    )
    return BuyerRun(agent, payer, discovery, session_id)


DEFAULT_PROMPT = (
    "Find an x402 search API on AWS Marketplace, buy one result, summarize it, and tell me how much budget is left."
)
DEFAULT_PROMPT_NO_GATEWAY = (
    "Buy one search result from the Marketplace seller, summarize it, then tell me how much budget is left."
)


def main() -> None:
    user_id = os.environ.get("USER_ID", "demo-user")
    session_id = create_budget(SESSION_BUDGET_USD, user_id)
    print(f"Payment session {session_id} (budget ${SESSION_BUDGET_USD})")
    run = build_buyer_agent(session_id, user_id)

    prompt = sys.argv[1] if len(sys.argv) > 1 else (DEFAULT_PROMPT if run.discovery else DEFAULT_PROMPT_NO_GATEWAY)
    print(f"\nUser: {prompt}\n")
    result = run.agent(prompt)
    print(f"Tool calls: {' -> '.join(run.tool_calls())}\n")
    print(f"Agent: {result}\n")
    total = sum(Decimal(r.amount_usd) for r in run.payer.receipts)
    print(f"Payments this run: {len(run.payer.receipts)}, total ${total}")
    for r in run.payer.receipts:
        print(f"  ${r.amount_usd}  {r.product_id}  {r.process_payment_id}")


if __name__ == "__main__":
    main()
