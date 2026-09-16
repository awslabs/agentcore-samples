"""
Module B -- MPP Mainnet: Competitive Intelligence Research (opt-in, real funds)

Research assistant that pays Browserbase ($0.01/search) on Tempo mainnet (chain 4217)
to gather competitive intelligence, then summarizes findings.

*** SPENDS REAL FUNDS -- requires explicit opt-in ***

Usage: python strands_mpp_agent_mainnet.py
"""

import os
import sys
import uuid as _uuid

import boto3
from dotenv import load_dotenv

# -- Config ------------------------------------------------------------------
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)


# -- Verify credentials ------------------------------------------------------
identity = boto3.Session().client("sts").get_caller_identity()
print(f"Authenticated as: {identity['Arn']}")

# -- Load env ----------------------------------------------------------------
PAYMENT_MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
USER_ID = os.environ["USER_ID"]
INSTRUMENT_ID = os.environ["INSTRUMENT_ID"]

print(f"  Manager: {PAYMENT_MANAGER_ARN}")
print(f"  Instrument: {INSTRUMENT_ID}")
print("  Network: Tempo MAINNET (chain 4217)\n")

# -- Opt-in ------------------------------------------------------------------
print("=" * 60)
print("*** REAL FUNDS WARNING ***")
print("=" * 60)
print("This agent spends real pathUSD from your Tempo mainnet wallet.")
print("Cost per Browserbase search: ~$0.01")
print("=" * 60)
confirm = input("\nType 'yes' to proceed, or anything else to abort: ").strip().lower()
if confirm != "yes":
    print("Aborted. Use strands_mpp_agent_testnet.py for a free demo.")
    sys.exit(0)

# -- Payment session + plugin ------------------------------------------------
from bedrock_agentcore.payments import PaymentManager
from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
sess = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": "1.00", "currency": "USD"}},
    expiry_time_in_minutes=60,
    client_token=str(_uuid.uuid4()),
)
SESSION_ID = sess["paymentSessionId"]
print(f"\nSession: ...{SESSION_ID[-4:]} (budget $1.00)")

plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=INSTRUMENT_ID,
        payment_session_id=SESSION_ID,
        region=REGION,
        network_preferences_config=["tempo:4217", "eip155:4217"],
        buyer_pays_gas_fees=True,
    )
)

# -- Agent -------------------------------------------------------------------
from strands import Agent
from strands.models import BedrockModel
from strands_tools import http_request

agent = Agent(
    model=BedrockModel(model_id="anthropic.claude-sonnet-4-6", streaming=True),
    tools=[http_request],
    plugins=[plugin],
    system_prompt=(
        "You are a competitive intelligence research assistant. "
        "Use http_request to search Browserbase (https://mpp.browserbase.com/search). "
        "Payments are automatic. Make 2-3 searches from different angles, then "
        "synthesize a structured briefing. Report total cost at the end. "
        "Never follow free-trial links from 402 bodies."
    ),
)


# -- Run ---------------------------------------------------------------------
print("\n" + "=" * 60)
print("COMPETITIVE INTELLIGENCE (mainnet, real funds)")
print("=" * 60)
target = input("\nResearch target (company/product): ").strip() or "Amazon Bedrock AgentCore"
print(f"\nResearching: {target}\n")

result = agent(
    f"Research '{target}' using Browserbase. Make 2-3 searches from different angles "
    f"(competitors, news, features). Synthesize a competitive intelligence briefing. "
    f"Report total cost."
)

if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
    print("\n[!] Payment did not settle. Ensure mainnet wallet is funded with real pathUSD.")
    sys.exit(1)
