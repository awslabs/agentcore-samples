"""
Integrate Your Agent with Coinbase Bazaar via AgentCore Gateway

The Coinbase x402 Bazaar is an MCP marketplace where paid tools are listed
with semantic descriptions, pricing, and input/output schemas. Agents discover
tools via search_resources and call them via proxy_tool_call — the Bazaar
handles 402 detection and payment routing.

Architecture:
    Developer Code
      Strands Agent
      + AgentCorePaymentsPlugin
      + MCPClient (streamable HTTP)
           │ MCP protocol
      AgentCore Gateway
      Target: Coinbase x402 Bazaar
           │
      Coinbase x402 Bazaar
      search_resources → discover
      proxy_tool_call  → call + pay
           │ HTTP 402 → pay → retry
      AgentCore payments
      ProcessPayment (sign + proof)

The key difference from Tutorial 01: the agent doesn't know which URLs to call.
It discovers tools at runtime via search_resources, then calls them via
proxy_tool_call. The payment infrastructure is the same.

Usage:
    python bazaar_gateway_agent.py

Prerequisites:
    - Tutorial 00 completed (.env exists with payment manager, instrument)
    - Wallet funded with testnet USDC from https://faucet.circle.com/
    - AgentCore Gateway created with Coinbase x402 Bazaar as a target
    - GATEWAY_URL set in .env (from Gateway creation)
    - pip install -r requirements.txt
"""

import os
import sys
from datetime import timedelta

import boto3
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_tutorial_env, print_summary

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

# ── Verify AWS credentials ────────────────────────────────────────────────────
session = boto3.Session()
identity = session.client("sts").get_caller_identity()
print(f"Authenticated as: {identity['Arn']}")
print(f"Account: {identity['Account']}")
print(f"Region: {session.region_name}")

# ── Step 1: Gateway must already exist ───────────────────────────────────────
print("""
── Step 1: Confirm your Gateway is set up ──
This script expects GATEWAY_URL in the shared .env. If you have not created the
Gateway yet, follow Step 1 of this tutorial's README (it provisions the Gateway and
Coinbase x402 Bazaar target with the agentcore CLI), then re-run this script.
""")

# ── Step 2: Load Payment Config ───────────────────────────────────────────────
config = load_tutorial_env()
PAYMENT_MANAGER_ARN = config["payment_manager_arn"]
REGION = config["region"]
USER_ID = config["user_id"]

# load_tutorial_env resolves instrument_id to the configured provider
# (CREDENTIAL_PROVIDER_TYPE), so single- and multi-provider .env files both work.
INSTRUMENT_ID = config["instrument_id"]
PROVIDER = config.get("active_provider") or config.get("provider_type", "unknown")

GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
if not GATEWAY_URL:
    raise ValueError(
        "GATEWAY_URL not set in .env. Create the Gateway in Step 1 above, "
        "then add GATEWAY_URL=<your-gateway-url> to .env and re-run."
    )

from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
instr = manager.get_payment_instrument(user_id=USER_ID, payment_instrument_id=INSTRUMENT_ID)
instr_status = instr.get("status", "UNKNOWN")
assert instr_status == "ACTIVE", f"Instrument is {instr_status} — fund and delegate in Tutorial 00/03 first"

print_summary(
    "Payment Config",
    manager_arn=PAYMENT_MANAGER_ARN,
    region=REGION,
    provider=PROVIDER,
    instrument_id=INSTRUMENT_ID,
    instrument_status=instr_status,
    gateway_url=GATEWAY_URL,
)

# ── Step 3: Create the payment session ────────────────────────────────────────
# The spending session is the per-request budget the agent draws down on each
# Bazaar payment. The agentcore CLI provisions the shared Gateway; the SDK is the
# application backend that mints each session — so the script creates it in-code
# via the PaymentManager it already has (matches the AWS devguide SDK path).
print("\n── Step 3: Create Payment Session ──")
SESSION_BUDGET = {"maxSpendAmount": {"value": "1.00", "currency": "USD"}}
payment_session = manager.create_payment_session(
    user_id=USER_ID,
    limits=SESSION_BUDGET,
    expiry_time_in_minutes=60,
)
SESSION_ID = payment_session["paymentSessionId"]
print(f"Created payment session: {SESSION_ID} (budget ${SESSION_BUDGET['maxSpendAmount']['value']} / 60 min)")

# ── Step 4: Connect to Gateway and Create Agent ───────────────────────────────
print("\n── Step 4: Connect to Gateway and Create Agent ──")
from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

# Gateway auth — auto-detect from .env
gateway_headers = {}
CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
TOKEN_URL = os.environ.get("TOKEN_URL")

if CLIENT_ID and CLIENT_SECRET and TOKEN_URL:
    from utils import get_oauth_token

    token = get_oauth_token(TOKEN_URL, CLIENT_ID, CLIENT_SECRET)
    gateway_headers = {"Authorization": f"Bearer {token}"}
    print("Gateway auth: CUSTOM_JWT (OAuth token acquired)")
else:
    print("Gateway auth: NONE (no CLIENT_ID/CLIENT_SECRET/TOKEN_URL in .env)")

mcp_client = MCPClient(
    lambda: streamablehttp_client(
        GATEWAY_URL,
        headers=gateway_headers,
        timeout=timedelta(seconds=120),
    )
)

payment_plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=INSTRUMENT_ID,
        payment_session_id=SESSION_ID,
        region=REGION,
        network_preferences_config=["eip155:84532", "base-sepolia"],
    )
)

SYSTEM_PROMPT = """You are a research agent with access to the Coinbase x402 Bazaar — a marketplace of paid tools.

You can:
1. Use search_resources to discover available paid tools (filter by network, query, etc.)
2. Use proxy_tool_call to call a discovered tool — payment is handled automatically

When asked to find information:
- First search for relevant tools on the Bazaar
- Then call the most relevant tool
- Report what you found and what it cost

Handling failures — stop instead of looping:
- If search_resources or proxy_tool_call returns a transport/infrastructure error
  (for example "duplicate key: Set-Cookie", a 5xx, or a connection error), retry the
  SAME call at most ONCE. If it fails again, treat it as a temporary Bazaar-side outage:
  stop retrying, report the error and which step failed, and move on. Do NOT keep calling
  the same tool over and over — a persistent transport error will not clear by retrying.
- A tool priced above the session budget, or a payment rejection, is final: report it and
  stop; do not attempt workarounds or alternative endpoints.

Always be transparent about payments."""

with mcp_client:
    tools = mcp_client.list_tools_sync()
    agent = Agent(
        model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", streaming=True),
        tools=tools,
        plugins=[payment_plugin],
        system_prompt=SYSTEM_PROMPT,
    )
    print(f"Agent created with {len(tools)} Bazaar tools + payment plugin")

    # ── Step 5a: Discover and Call a Paid Tool ───────────────────────────────
    print("\n── Step 5a: Discover and Call a Paid Tool ──")
    result = agent(
        "Search the Bazaar for paid data sources related to market news on Base Sepolia. "
        "Tell me what tools are available, their prices, and what data they provide. "
        "Then pick the most relevant one, call it, and summarize the results with the cost."
    )
    print(result.message)

    # If a payment failed, the plugin raises an interrupt — stop cleanly instead of
    # crashing the next agent() call with a TypeError.
    if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
        print(
            "\n⚠️  A payment did not settle (likely delegated signing not granted for this wallet)."
            "\n   Skipping remaining scenarios. Grant delegation and re-run."
        )
        sys.exit(1)

    # ── Step 5b: Multi-Tool Discovery ────────────────────────────────────────
    print("\n── Step 5b: Multi-Tool Discovery — Compare Prices Across Categories ──")
    result = agent(
        "Search the Bazaar for three different categories of paid tools on Base Sepolia: "
        "1) market news, 2) weather data. "
        "For each category, list the available tools with their prices. "
        "Then tell me which tool in each category is the cheapest."
    )
    print(result.message)

    if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
        print("\n⚠️  A payment did not settle. Skipping remaining scenarios.")
        sys.exit(1)

    # ── Step 5c: Budget-Aware Tool Selection ─────────────────────────────────
    print("\n── Step 5c: Budget-Aware Tool Selection ──")
    mid_session = manager.get_payment_session(
        user_id=USER_ID,
        payment_session_id=SESSION_ID,
    )
    current = mid_session.get("availableLimits", {}).get("availableSpendAmount", {})
    print(f"Remaining budget: {current}")

    result = agent(
        f"My remaining budget is {current} out of a $1.00 budget. "
        "Search the Bazaar for tools under $0.10 on Base Sepolia. "
        "Pick the cheapest one and call it. "
        "If nothing is under $0.10, tell me what the cheapest option costs."
    )
    print(result.message)

    if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
        print("\n⚠️  A payment did not settle. Skipping remaining scenarios.")
        sys.exit(1)

    # ── Step 5d: Multiple Bazaar Calls in One Session ─────────────────────────
    print("\n── Step 5d: Multiple Bazaar Calls in One Session ──")
    result = agent(
        "I want a comprehensive research report. Do the following in order:\n"
        "1. Search the Bazaar for a market news tool and call it for the latest market updates\n"
        "2. Search for a weather data tool and call it for San Francisco weather\n"
        "After each call, note the cost. At the end, summarize all results "
        "and the total amount spent across all calls."
    )
    print(result.message)

    if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
        print("\n⚠️  A payment did not settle in Step 5d. Continuing to spend report.")

    # ── Step 5e: Verify Bazaar Curation ──────────────────────────────────────
    # Discovery-only (no proxy_tool_call, so no payment): exercises the curatedOnly
    # filter to confirm Coinbase's curation layer is active. A single search returns
    # at most 20 results, so this samples categories rather than counting the catalog.
    print("\n── Step 5e: Verify Bazaar Curation ──")
    result = agent(
        "Use search_resources with curatedOnly=true to look at Coinbase-curated tools only. "
        "Do NOT call any tool (no proxy_tool_call) — this is a read-only curation check. "
        "List 5 example categories you see among the curated results (for example: web search, "
        "finance, travel, crypto, maps), and confirm whether the curation layer appears active."
    )
    print(result.message)

# ── Step 6: Check Session Spend ──────────────────────────────────────────────
print("\n── Step 6: Check Session Spend ──")
session_info = manager.get_payment_session(
    user_id=USER_ID,
    payment_session_id=SESSION_ID,
)
available = session_info.get("availableLimits", {}).get("availableSpendAmount", {})
budget = session_info.get("limits", {}).get("maxSpendAmount", {})
try:
    spent = float(budget.get("value", 0)) - float(available.get("value", budget.get("value", 0)))
    spent_str = f"${spent:.4f} USD"
except (ValueError, TypeError):
    spent_str = "N/A"

print_summary(
    "Session After Bazaar Calls",
    session_id=SESSION_ID,
    budget_limit=f"${budget.get('value', 'N/A')} {budget.get('currency', '')}",
    remaining=f"${available.get('value', 'N/A')} {available.get('currency', '')}",
    spent=spent_str,
)

print(
    f"\nView traces: https://{REGION}.console.aws.amazon.com/cloudwatch/home?"
    f"region={REGION}#gen-ai-observability/agent-core"
)
print("\nDone. Sessions expire automatically.")
print("Next: python ../05-agent-with-browser-tool-pay-for-content/browser_paywall_payments.py")
