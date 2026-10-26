"""
Multi-Agent Payment Orchestrator

Build a multi-agent system with per-agent budgets, multi-wallet support,
and full spend attribution — then demonstrate intelligent failover when
a budget is exhausted.

Three demos:
    Demo 1: Spend Attribution — two wallets, two budgets, full per-agent cost tracking
    Demo 2: Budget Exhaustion + Failover — orchestrator detects payment rejection, reroutes
    Demo 3: Structural Safety — orchestrator literally cannot spend (no plugin)

Resource hierarchy:
    PaymentManager
      ├── CoinbaseCDP Connector → Research Agent (Session A, $0.50)
      └── StripePrivy Connector → Discovery Agent (Session B, $0.20)

Usage:
    python multi_agent_payments.py

Prerequisites:
    - Tutorial 00b (multi_provider_setup.py) completed — .env has both Coinbase + Privy instruments
    - Both wallets funded with testnet USDC from https://faucet.circle.com/
    - pip install -r requirements.txt
"""

import json
import os
import sys

import boto3
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_tutorial_env, print_summary

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

# ── Step 1: Verify AWS credentials ────────────────────────────────────────────
session = boto3.Session()
identity = session.client("sts").get_caller_identity()
print(f"Authenticated as: {identity['Arn']}")
print(f"Region: {session.region_name}")

# ── Step 2: Load Multi-Provider Config ────────────────────────────────────────
config = load_tutorial_env()

if not config.get("multi_provider"):
    raise ValueError(
        "This tutorial requires a multi-provider config. "
        "Run multi_provider_setup.py first to create Coinbase + Privy connectors."
    )

PAYMENT_MANAGER_ARN = config["payment_manager_arn"]
REGION = config["region"]
USER_ID = config["user_id"]

COINBASE = config["instruments"]["coinbase"]
PRIVY = config["instruments"]["stripe_privy"]

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# Per-agent budgets (USD). Each specialist gets its own budgeted session, created in-code below.
RESEARCH_BUDGET = "0.50"  # research agent (Coinbase wallet)
DISCOVERY_BUDGET = "0.20"  # discovery agent (Privy wallet)
FAILOVER_BUDGET = "0.0005"  # deliberately tiny — Demo 2 uses this to trigger a budget-exhaustion failover

print_summary(
    "Multi-Provider Config",
    manager_arn=PAYMENT_MANAGER_ARN,
    coinbase_instrument=COINBASE["instrument_id"],
    privy_instrument=PRIVY["instrument_id"],
)

# ── Step 3: Verify Instruments and Create Per-Agent Sessions ──────────────────
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

for label, instr_id in [
    ("Coinbase", COINBASE["instrument_id"]),
    ("Privy", PRIVY["instrument_id"]),
]:
    instr = manager.get_payment_instrument(user_id=USER_ID, payment_instrument_id=instr_id)
    status = instr.get("status", "UNKNOWN")
    assert status == "ACTIVE", f"{label} instrument {instr_id} is {status} — fund and delegate first"
    print(f"  {label} instrument {instr_id} is {status}")

# Each agent gets its own budgeted session, created in-code via the SDK
# (manager.create_payment_session). One session per agent, each capped to its own budget.
SESSION_A_ID = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": RESEARCH_BUDGET, "currency": "USD"}},
    expiry_time_in_minutes=60,
)["paymentSessionId"]
SESSION_B_ID = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": DISCOVERY_BUDGET, "currency": "USD"}},
    expiry_time_in_minutes=60,
)["paymentSessionId"]

print_summary(
    "Per-Agent Sessions",
    session_a=f"{SESSION_A_ID} (Coinbase)",
    session_b=f"{SESSION_B_ID} (Privy)",
)

# ── Step 4: Create Plugins and Agents ─────────────────────────────────────────
from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool
from strands_tools import http_request

research_plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=COINBASE["instrument_id"],
        payment_session_id=SESSION_A_ID,
        region=REGION,
        network_preferences_config=["eip155:84532", "base-sepolia"],
    )
)

discovery_plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=PRIVY["instrument_id"],
        payment_session_id=SESSION_B_ID,
        region=REGION,
        network_preferences_config=["eip155:84532", "base-sepolia"],
    )
)


@tool
def check_budgets() -> str:
    """Check remaining budget for each specialist agent.

    Returns:
        JSON with per-agent spend and remaining budget.
    """
    results = {}
    for label, sid in [
        ("research_agent", SESSION_A_ID),
        ("discovery_agent", SESSION_B_ID),
    ]:
        info = manager.get_payment_session(user_id=USER_ID, payment_session_id=sid)
        results[label] = {
            "session_id": sid,
            "available": info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
            "budget": info.get("limits", {}).get("maxSpendAmount", "N/A"),
        }
    return json.dumps(results, indent=2)


model = BedrockModel(model_id=MODEL_ID, streaming=True)

research_agent = Agent(
    model=model,
    tools=[http_request],
    plugins=[research_plugin],
    system_prompt=(
        "You are a research specialist. Use http_request to access paid endpoints "
        "on the Coinbase Bazaar (Base Sepolia testnet). "
        "IMPORTANT: Only use GET requests. Never use POST, PUT, or DELETE. "
        "When you discover endpoints from the Bazaar, look for the URL in the 'resource' field of the response. "
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
        "When you discover endpoints from the Bazaar, look for the URL in the 'resource' field of the response. "
        "Payment is handled automatically via x402. Report what you found and the cost."
    ),
)

# Orchestrator has NO payment plugin — structural safety enforcement
orchestrator = Agent(
    model=model,
    tools=[
        research_agent.as_tool(
            name="research_agent",
            description="Research specialist with Coinbase wallet and $0.50 budget. Use for paid data lookups.",
        ),
        discovery_agent.as_tool(
            name="discovery_agent",
            description="Discovery specialist with Privy wallet and $0.20 budget. Use for cheap paid endpoints.",
        ),
        check_budgets,
    ],
    system_prompt=(
        "You are an orchestrator that coordinates specialist agents.\n"
        "- research_agent: paid data lookups (budget: $0.50, Coinbase wallet)\n"
        "- discovery_agent: cheap paid endpoints (budget: $0.20, Privy wallet)\n"
        "- check_budgets: monitor spend across both agents\n\n"
        "You cannot make payments yourself. Only the specialists can spend.\n"
        "If one agent's budget is exhausted, route remaining work to the other.\n"
        "After tasks complete, check budgets and report total spend."
    ),
)

print("  Research Agent: Session A + Coinbase + $0.50 budget")
print("  Discovery Agent: Session B + Privy + $0.20 budget")
print("  Orchestrator: NO plugin (cannot spend)")

# ── Demo 1: Spend Attribution ─────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Demo 1 — Spend Attribution")
print("=" * 60)
print("Two agents, two wallets, independent budgets, full per-agent tracking")

result = orchestrator(
    "I need two things:\n"
    "1. Ask the research_agent to search the Bazaar for weather endpoints on Base Sepolia "
    "(GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=weather&network=base-sepolia&limit=3). "
    "From the results, find the endpoint URL in the 'resource' field and call it with http_request using GET. "
    "Report the weather data and cost.\n"
    "2. Ask the discovery_agent to search the Bazaar for market news endpoints on Base Sepolia "
    "(GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=market+news&network=base-sepolia&limit=3). "
    "From the results, find the endpoint URL in the 'resource' field and call it with http_request using GET. "
    "Report the data and cost.\n\n"
    "After both tasks complete, check the budgets and give me a spend report showing what each agent spent."
)
print(result.message)

print("\nSpend Report — Demo 1:")
for label, sid, wallet_provider in [
    ("Research Agent (Coinbase)", SESSION_A_ID, "Coinbase"),
    ("Discovery Agent (Privy)", SESSION_B_ID, "Privy"),
]:
    info = manager.get_payment_session(user_id=USER_ID, payment_session_id=sid)
    print_summary(
        label,
        session_id=sid,
        wallet=wallet_provider,
        available=info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
        budget=info.get("limits", {}).get("maxSpendAmount", "N/A"),
    )

# ── Demo 2: Budget Exhaustion + Failover ──────────────────────────────────────
print("\n" + "=" * 60)
print("Demo 2 — Budget Exhaustion + Failover")
print("=" * 60)
print("Tiny research budget ($0.0005) → payment rejected → reroute to discovery agent")

# This demo needs a deliberately tiny research session so the paid call is rejected. Create it
# in-code via the SDK, capped to the tiny FAILOVER_BUDGET.
TINY_SESSION_ID = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": FAILOVER_BUDGET, "currency": "USD"}},
    expiry_time_in_minutes=60,
)["paymentSessionId"]
print(f"  Tiny research session: {TINY_SESSION_ID} (budget: ${FAILOVER_BUDGET})")
print(f"  Discovery session: {SESSION_B_ID}")

tiny_research_plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=COINBASE["instrument_id"],
        payment_session_id=TINY_SESSION_ID,
        region=REGION,
        network_preferences_config=["eip155:84532", "base-sepolia"],
    )
)

tiny_research_agent = Agent(
    model=model,
    tools=[http_request],
    plugins=[tiny_research_plugin],
    system_prompt=(
        "You are a research specialist. Use http_request to access paid endpoints "
        "on the Coinbase Bazaar (Base Sepolia testnet). "
        "IMPORTANT: Only use GET requests. Never use POST, PUT, or DELETE. "
        "Payment is handled automatically via x402. "
        "Report what data you found and what it cost. If payment fails, report the failure clearly."
    ),
)


@tool
def research_agent_tool(task: str) -> str:
    """Research specialist with VERY SMALL budget ($0.0005, Coinbase wallet). Likely to fail on paid calls due to budget exhaustion."""
    try:
        result = tiny_research_agent(task)
        return result.message.get("content", [{}])[0].get("text", str(result))
    except Exception as e:  # noqa: BLE001 - surfaced to the agent as a tool result
        return f"PAYMENT FAILED — budget exhausted. Error: {e!s}"


@tool
def check_budgets_v2() -> str:
    """Check remaining budget for the research and discovery agents."""
    results = {}
    for label, sid in [
        ("research_agent", TINY_SESSION_ID),
        ("discovery_agent", SESSION_B_ID),
    ]:
        info = manager.get_payment_session(user_id=USER_ID, payment_session_id=sid)
        results[label] = {
            "session_id": sid,
            "available": info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
            "budget": info.get("limits", {}).get("maxSpendAmount", "N/A"),
        }
    return json.dumps(results, indent=2)


failover_orchestrator = Agent(
    model=model,
    tools=[
        research_agent_tool,
        discovery_agent.as_tool(
            name="discovery_agent",
            description="Discovery specialist with healthy budget ($0.20) and Privy wallet. Use as fallback when research_agent fails.",
        ),
        check_budgets_v2,
    ],
    system_prompt=(
        "You are an orchestrator that coordinates specialist agents.\n"
        "- research_agent_tool: research specialist, budget $0.0005 (extremely tight!, Coinbase wallet)\n"
        "- discovery_agent: budget $0.20 (healthy, Privy wallet)\n"
        "- check_budgets_v2: monitor spend across both agents\n\n"
        "You cannot make payments yourself. Only the specialists can spend.\n"
        "IMPORTANT: If research_agent_tool fails due to budget exhaustion, call check_budgets_v2 to confirm, "
        "then route the work to discovery_agent as a fallback.\n"
        "Report what happened — which agent succeeded, which failed, and why."
    ),
)

result = failover_orchestrator(
    "Call GET https://x402-test.genesisblock.ai/api/market-news — this is a paid x402 endpoint ($0.002).\n\n"
    "Try research_agent_tool first. "
    "If it fails (budget exceeded), call check_budgets_v2 to confirm, "
    "then ask the discovery_agent to call the same endpoint instead.\n"
    "Report which agent completed the task and why the other failed."
)
print(result.message)

print("\nSpend Report — Demo 2:")
for label, sid, wallet_provider in [
    ("Research Agent — EXHAUSTED (Coinbase)", TINY_SESSION_ID, "Coinbase"),
    ("Discovery Agent — FALLBACK (Privy)", SESSION_B_ID, "Privy"),
]:
    info = manager.get_payment_session(user_id=USER_ID, payment_session_id=sid)
    print_summary(
        label,
        session_id=sid,
        wallet=wallet_provider,
        available=info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
        budget=info.get("limits", {}).get("maxSpendAmount", "N/A"),
    )

# ── Demo 3: Structural Safety ─────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Demo 3 — Structural Safety (Orchestrator Cannot Spend)")
print("=" * 60)
print("Orchestrator gets http_request but NO plugin → 402 is a dead end")

unsafe_orchestrator = Agent(
    model=model,
    tools=[http_request],  # Has http_request but NO payment plugin
    system_prompt=(
        "You have http_request available. Try to access this paid endpoint: "
        "GET https://x402-test.genesisblock.ai/api/weather. "
        "Report exactly what happens."
    ),
)

result = unsafe_orchestrator(
    "Call GET https://x402-test.genesisblock.ai/api/weather and tell me what you get back. "
    "This is a paid x402 endpoint. Report the HTTP status and response."
)
print(result.message)

print("\nSpend Report — Demo 3 (both budgets unchanged):")
for label, sid, wallet_provider in [
    ("Research Agent (Coinbase)", SESSION_A_ID, "Coinbase"),
    ("Discovery Agent (Privy)", SESSION_B_ID, "Privy"),
]:
    info = manager.get_payment_session(user_id=USER_ID, payment_session_id=sid)
    print_summary(
        f"{label} — NO CHANGE",
        session_id=sid,
        wallet=wallet_provider,
        available=info.get("availableLimits", {}).get("availableSpendAmount", "N/A"),
        budget=info.get("limits", {}).get("maxSpendAmount", "N/A"),
    )
print("Budgets unchanged — the orchestrator's 402 attempt spent nothing.")

# ── Deployment Instructions ───────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Deployment to AgentCore Runtime (optional)")
print("=" * 60)
print("""
# Install AgentCore CLI
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \\
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\\.' \\
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

# Create project
agentcore create --name PaymentOrchestrator --defaults

# Deploy
cd PaymentOrchestrator
agentcore deploy -y

# Add online evaluation
agentcore add online-eval \\
  --name PaymentMonitor \\
  --runtime PaymentOrchestrator \\
  --evaluator Builtin.GoalSuccessRate Builtin.ToolSelectionAccuracy Builtin.Helpfulness \\
  --sampling-rate 100 \\
  --enable-on-create
agentcore deploy -y

# Invoke
agentcore invoke '{"prompt": "Search Bazaar and call found endpoints...", \\
  "user_id": "test-user-001", \\
  "research_session_id": "<SESSION_A>", "research_instrument_id": "<COINBASE_INSTRUMENT>", \\
  "discovery_session_id": "<SESSION_B>", "discovery_instrument_id": "<PRIVY_INSTRUMENT>"}'
""")

print("\nDone. Sessions expire automatically.")
print("Payment resources cleanup: run cleanup in setup_agentcore_payments.py")
