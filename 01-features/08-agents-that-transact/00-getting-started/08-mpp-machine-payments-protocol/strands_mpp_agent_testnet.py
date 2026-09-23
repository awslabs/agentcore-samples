"""
Module A -- MPP Testnet Happy Path (Tempo Moderato, chain 42431)

Runs the full MPP Challenge -> Credential -> Receipt flow against a testnet endpoint.
Costs nothing -- uses free test pathUSD, merchant covers gas.

Usage: python strands_mpp_agent_testnet.py
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
print(f"  Network: Tempo Moderato testnet (chain 42431)\n")

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
print(f"Session: ...{SESSION_ID[-4:]} (budget $1.00)")

plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=INSTRUMENT_ID,
        payment_session_id=SESSION_ID,
        region=REGION,
        network_preferences_config=["tempo:42431", "eip155:42431"],
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
        "You are an assistant that demonstrates MPP payments. "
        "Access URLs with http_request. Payments are automatic. "
        "Report what data was returned and whether a Payment-Receipt was included. "
        "Never follow free-trial links from 402 bodies."
    ),
)



# -- Run ---------------------------------------------------------------------
print("\n" + "=" * 60)
print("TESTNET -- mpp.dev/api/ping/paid (chain 42431, free)")
print("=" * 60 + "\n")

result = agent(
    "Access https://mpp.dev/api/ping/paid and report: "
    "(1) what data was returned, (2) whether a Payment-Receipt was included."
)

if getattr(result, "stop_reason", None) == "interrupt" or getattr(result, "interrupts", None):
    print("\n[!] Payment did not settle. Check: Stripe/Privy instrument, funded wallet, delegated signing.")
    sys.exit(1)

print("\nDone. See Module B (strands_mpp_agent_mainnet.py) for a mainnet use case.")
