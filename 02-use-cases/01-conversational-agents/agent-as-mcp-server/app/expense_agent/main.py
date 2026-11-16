"""
Agent-as-MCP-server: a full Strands reasoning agent hidden behind one MCP tool.

From the MCP client's perspective (Claude custom connector, Kiro, Claude Code,
Amazon Quick, ...) this server exposes a single tool: `process_expense_report`.
Inside that tool handler a Strands agent runs a multi-step workflow on Amazon
Bedrock: it parses the report, converts currency, looks up company policy,
decides approve/reject, and records the decision. The client only ever sees the
final plain-language answer.

MCP appears on both sides of the agent:
  * inbound  -- this process is an MCP server hosted on AgentCore Runtime
  * outbound -- the agent is an MCP client of an AgentCore Gateway, where the
                business tools (a Lambda) are registered as MCP targets

Auth for the outbound hop uses AgentCore Identity: the Cognito M2M client
credentials live in the Identity token vault and are fetched at call time via
the @requires_access_token decorator. No secrets in code or env vars.

Runs locally with `python main.py` (tools fall back to in-process mocks when no
Gateway is configured) and deploys unchanged to AgentCore Runtime in MCP
protocol mode (stateless streamable-http on 0.0.0.0:8000/mcp).
"""

import logging
import os

from bedrock_agentcore.identity.auth import requires_access_token
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("expense_agent")

# ---------------------------------------------------------------------------
# Configuration -- all injected by the AgentCore CLI / CDK at deploy time.
# The CLI sets AGENTCORE_GATEWAY_<GATEWAYNAME>_URL for every gateway in the project.
# ---------------------------------------------------------------------------

GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_URL", os.getenv("AGENTCORE_GATEWAY_EXPENSETOOLSGATEWAY_URL", ""))
GATEWAY_CREDENTIAL_PROVIDER = os.getenv("AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", "expense-gateway-m2m")
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke").replace(",", " ").split()
MODEL_ID = os.getenv("AGENT_MODEL_ID", "global.anthropic.claude-sonnet-4-6")

mcp = FastMCP("expense_agent", host="0.0.0.0", stateless_http=True)

# ---------------------------------------------------------------------------
# In-process tool. Kept local on purpose to show the hybrid option: an agent can
# mix its own tools with tools discovered from a Gateway.
# ---------------------------------------------------------------------------

EXCHANGE_RATES_TO_USD = {"USD": 1.0, "EUR": 1.09, "GBP": 1.27, "TRY": 0.024, "JPY": 0.0067, "INR": 0.012}


@tool
def convert_to_usd(amount: float, currency: str) -> dict:
    """Convert an amount in a given currency to USD.

    Args:
        amount: The amount in the original currency.
        currency: ISO currency code, e.g. 'EUR', 'GBP', 'TRY', 'JPY', 'INR', 'USD'.
    """
    rate = EXCHANGE_RATES_TO_USD.get(currency.upper().strip())
    if rate is None:
        return {"error": f"No rate for '{currency}'. Known: {sorted(EXCHANGE_RATES_TO_USD)}"}
    return {"amount_usd": round(amount * rate, 2), "rate_used": rate}


# ---------------------------------------------------------------------------
# Local fallback tools -- only used when no Gateway is configured (local dev).
# In the deployed sample these live in a Lambda behind AgentCore Gateway.
# ---------------------------------------------------------------------------

_LOCAL_POLICY = {
    "meals": {"limit_usd": 75, "receipt_required_above_usd": 25},
    "travel": {"limit_usd": 500, "receipt_required_above_usd": 0},
    "office": {"limit_usd": 200, "receipt_required_above_usd": 50},
    "software": {"limit_usd": 100, "receipt_required_above_usd": 0},
}


@tool
def lookup_expense_policy(category: str) -> dict:
    """Look up the company expense policy for a category (local fallback).

    Args:
        category: One of 'meals', 'travel', 'office', 'software'.
    """
    return _LOCAL_POLICY.get(category.lower().strip(), {"error": f"Unknown category '{category}'"})


@tool
def record_decision(employee: str, category: str, amount_usd: float, decision: str, reason: str) -> dict:
    """Record the final approve/reject decision (local fallback).

    Args:
        employee: Employee name.
        category: Expense category.
        amount_usd: Amount in USD.
        decision: 'APPROVED' or 'REJECTED'.
        reason: One-sentence justification.
    """
    record_id = f"EXP-{abs(hash((employee, category, amount_usd, decision))) % 100000:05d}"
    log.info("[expense-system:local] %s %s %s %s", record_id, decision, employee, reason)
    return {"record_id": record_id, "status": "recorded"}


# ---------------------------------------------------------------------------
# Gateway tools via AgentCore Identity (M2M OAuth, secrets in the token vault).
# ---------------------------------------------------------------------------


@requires_access_token(
    provider_name=GATEWAY_CREDENTIAL_PROVIDER,
    auth_flow="M2M",
    scopes=GATEWAY_OAUTH_SCOPES,
)
def _build_gateway_client(*, access_token: str) -> MCPClient:
    """Create an MCP client for the tools Gateway using an Identity-managed token."""

    def _transport():
        return streamablehttp_client(GATEWAY_URL, headers={"Authorization": f"Bearer {access_token}"})

    return MCPClient(_transport)


AGENT_SYSTEM_PROMPT = """You are the expense-approval agent for the company.
Given a free-text expense report, you must:
1. Identify the employee, expense category (meals, travel, office, software), amount, and currency.
2. Convert the amount to USD using the currency conversion tool (skip if already USD).
3. Look up the policy for the category with the policy lookup tool.
4. Decide APPROVED or REJECTED strictly per policy: over the limit means REJECTED;
   if a receipt is required above a threshold and none was mentioned, REJECTED.
5. Record the outcome with the decision recording tool.
Finally reply with a short plain-language summary for the employee: the decision,
the record id, and the reason. Do not mention tools or internal steps.
"""


def _run_agent(report: str) -> str:
    prompt = f"Process this expense report:\n\n{report}"
    model = BedrockModel(model_id=MODEL_ID)

    if not GATEWAY_URL:
        log.info("No Gateway configured; using in-process fallback tools")
        agent = Agent(
            model=model,
            system_prompt=AGENT_SYSTEM_PROMPT,
            tools=[convert_to_usd, lookup_expense_policy, record_decision],
        )
        return str(agent(prompt))

    gateway_client = _build_gateway_client()
    with gateway_client:
        gateway_tools = gateway_client.list_tools_sync()
        log.info("[gateway] tools discovered: %s", [t.tool_name for t in gateway_tools])
        agent = Agent(model=model, system_prompt=AGENT_SYSTEM_PROMPT, tools=[convert_to_usd, *gateway_tools])
        return str(agent(prompt))


# ---------------------------------------------------------------------------
# The single MCP tool the outside world sees.
# ---------------------------------------------------------------------------


@mcp.tool()
def process_expense_report(report: str) -> str:
    """Process an employee expense report end to end: validates it against company
    policy, converts currency if needed, makes an approve/reject decision, and
    records it in the expense system. Pass the expense report as free text, e.g.
    'Jane spent 60 EUR on a team dinner, receipt attached'. Returns the decision
    summary for the employee."""
    return _run_agent(report)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
