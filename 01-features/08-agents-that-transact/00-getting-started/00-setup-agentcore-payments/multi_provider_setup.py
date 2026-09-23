"""
Multi-Provider Setup: Coinbase + Stripe (Privy) on One Manager

One Payment Manager with both Coinbase CDP and Stripe (Privy) connectors.
Different users (or the same user) can have wallets from different providers,
all managed through the same payment stack.

```
Payment Manager (shared)
  ├── Coinbase CDP Connector
  │     └── Embedded Wallet (user A)
  ├── StripePrivy Connector
  │     └── Embedded Wallet (user B)
  └── Payment Session (budget — works with either wallet)
```

Usage:
    python multi_provider_setup.py

Prerequisites:
    - Tutorial 00 IAM roles already created (run setup_agentcore_payments.py first)
    - Both Coinbase CDP and Privy credentials in .env
    - pip install -r requirements.txt
"""

import os
import sys
import uuid

import boto3
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import (
    assume_role,
    client_token,
    idempotent_create,
    print_summary,
    require_env,
    save_tutorial_config,
    wait_for_status,
)

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
CP_ENDPOINT = os.environ.get(
    "PAYMENTS_CP_ENDPOINT",
    f"https://bedrock-agentcore-control.{AWS_REGION}.amazonaws.com",
)
DP_ENDPOINT = os.environ.get("PAYMENTS_DP_ENDPOINT", f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com")
NETWORK = os.environ.get("NETWORK", "ETHEREUM")
LINKED_EMAIL = os.environ.get("LINKED_EMAIL", "")
USER_ID = os.environ.get("USER_ID", "test-user-001")

CP_ROLE_ARN = os.environ["CONTROL_PLANE_ROLE_ARN"]
MGMT_ROLE_ARN = os.environ["MANAGEMENT_ROLE_ARN"]
RR_ROLE_ARN = os.environ["RESOURCE_RETRIEVAL_ROLE_ARN"]

assert LINKED_EMAIL and not LINKED_EMAIL.startswith("<") and LINKED_EMAIL != "user@example.com", (
    "Set LINKED_EMAIL in .env to your real email before running this script."
)

session = boto3.Session(region_name=AWS_REGION)

# ── Step 1 — Create One Shared Payment Manager ────────────────────────────────
print("── Step 1: Create Shared Payment Manager ──")
cp_session = assume_role(session, CP_ROLE_ARN, "multi-provider-cp")
cp_client = cp_session.client("bedrock-agentcore-control", endpoint_url=CP_ENDPOINT)
cred_client = cp_session.client("bedrock-agentcore-control", endpoint_url=CP_ENDPOINT)

suffix = uuid.uuid4().hex[:8]
MANAGER_NAME = f"MultiProviderMgr{suffix}"

resp = idempotent_create(
    cp_client.create_payment_manager,
    f"Manager '{MANAGER_NAME}' already exists",
    name=MANAGER_NAME,
    authorizerType="AWS_IAM",
    roleArn=RR_ROLE_ARN,
    clientToken=client_token(),
)
MANAGER_ID = resp["paymentManagerId"]
MANAGER_ARN = resp["paymentManagerArn"]
print(f"  ✅ Manager: {MANAGER_ID}")

wait_for_status(cp_client.get_payment_manager, "READY", paymentManagerId=MANAGER_ID)

# ── Step 2 — Attach Coinbase CDP Connector ────────────────────────────────────
print("\n── Step 2: Attach Coinbase CDP Connector ──")
cb_cred = cred_client.create_payment_credential_provider(
    name=f"CoinbaseCdp{suffix}",
    credentialProviderVendor="CoinbaseCDP",
    providerConfigurationInput={
        "coinbaseCdpConfiguration": {
            "apiKeyId": require_env("COINBASE_API_KEY_ID"),
            "apiKeySecret": require_env("COINBASE_API_KEY_SECRET"),
            "walletSecret": require_env("COINBASE_WALLET_SECRET"),
        }
    },
)
CB_CRED_ARN = cb_cred["credentialProviderArn"]

cb_conn = cp_client.create_payment_connector(
    paymentManagerId=MANAGER_ID,
    name=f"CoinbaseConn{suffix}",
    type="CoinbaseCDP",
    credentialProviderConfigurations=[{"coinbaseCDP": {"credentialProviderArn": CB_CRED_ARN}}],
    clientToken=client_token(),
)
CB_CONNECTOR_ID = cb_conn["paymentConnectorId"]
print(f"  ✅ Coinbase connector: {CB_CONNECTOR_ID}")
wait_for_status(
    cp_client.get_payment_connector,
    "READY",
    paymentManagerId=MANAGER_ID,
    paymentConnectorId=CB_CONNECTOR_ID,
)

# ── Step 3 — Attach StripePrivy Connector ────────────────────────────────────
print("\n── Step 3: Attach StripePrivy Connector ──")
sp_cred = cred_client.create_payment_credential_provider(
    name=f"StripePrivy{suffix}",
    credentialProviderVendor="StripePrivy",
    providerConfigurationInput={
        "stripePrivyConfiguration": {
            "appId": require_env("PRIVY_APP_ID"),
            "appSecret": require_env("PRIVY_APP_SECRET"),
            "authorizationId": require_env("PRIVY_AUTHORIZATION_ID"),
            "authorizationPrivateKey": require_env("PRIVY_AUTHORIZATION_PRIVATE_KEY"),
        }
    },
)
SP_CRED_ARN = sp_cred["credentialProviderArn"]

sp_conn = cp_client.create_payment_connector(
    paymentManagerId=MANAGER_ID,
    name=f"StripePrivyConn{suffix}",
    type="StripePrivy",
    credentialProviderConfigurations=[{"stripePrivy": {"credentialProviderArn": SP_CRED_ARN}}],
    clientToken=client_token(),
)
SP_CONNECTOR_ID = sp_conn["paymentConnectorId"]
print(f"  ✅ StripePrivy connector: {SP_CONNECTOR_ID}")
wait_for_status(
    cp_client.get_payment_connector,
    "READY",
    paymentManagerId=MANAGER_ID,
    paymentConnectorId=SP_CONNECTOR_ID,
)

# ── Step 4 — Create Instruments for Both Providers ───────────────────────────
# Same manager, different connectors → different wallet providers.
# Both use EMBEDDED_CRYPTO_WALLET.
print("\n── Step 4: Create Instruments for Both Providers ──")
mgmt_session = assume_role(session, MGMT_ROLE_ARN, "multi-provider-mgmt")
dp_client = mgmt_session.client("bedrock-agentcore", endpoint_url=DP_ENDPOINT)

instruments = {}
for label, conn_id in [
    ("coinbase", CB_CONNECTOR_ID),
    ("stripe_privy", SP_CONNECTOR_ID),
]:
    resp = dp_client.create_payment_instrument(
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=conn_id,
        userId=USER_ID,
        paymentInstrumentType="EMBEDDED_CRYPTO_WALLET",
        paymentInstrumentDetails={
            "embeddedCryptoWallet": {
                "network": NETWORK,
                "linkedAccounts": [{"email": {"emailAddress": LINKED_EMAIL}}],
            }
        },
        clientToken=client_token(),
    )
    inst = resp["paymentInstrument"]
    inst_id = inst["paymentInstrumentId"]
    wallet = inst["paymentInstrumentDetails"]["embeddedCryptoWallet"].get("walletAddress", "pending...")
    instruments[label] = {
        "instrument_id": inst_id,
        "wallet_address": wallet,
        "connector_id": conn_id,
    }
    print(f"  ✅ {label}: {inst_id} → {wallet}")

    wait_for_status(
        dp_client.get_payment_instrument,
        "ACTIVE",
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=conn_id,
        paymentInstrumentId=inst_id,
        userId=USER_ID,
    )

# ── Step 5 — Fund Both Wallets ────────────────────────────────────────────────
print("\n✋ ACTION REQUIRED — Fund both wallets at https://faucet.circle.com/\n")
for label, inst in instruments.items():
    print(f"  {label:15s} → {inst['wallet_address']}")
print(f"\n  Network: {NETWORK}")
print("""
  StripePrivy: reload the Privy reference frontend (http://localhost:3000),
  log in with your LINKED_EMAIL, and choose Connect agent once to grant
  AgentCore signer access on all Privy wallets.
""")

# ── Step 6 — Create Session + Save Config ────────────────────────────────────
print("── Step 6: Create Session + Save Config ──")
resp = dp_client.create_payment_session(
    paymentManagerArn=MANAGER_ARN,
    userId=USER_ID,
    expiryTimeInMinutes=60,
    limits={"maxSpendAmount": {"value": "1.0", "currency": "USD"}},
    clientToken=client_token(),
)
SESSION_ID = resp["paymentSession"]["paymentSessionId"]
print(f"  ✅ Session: {SESSION_ID} (budget: $1.00)")

save_tutorial_config(
    {
        "PAYMENT_MANAGER_ARN": MANAGER_ARN,
        "PAYMENT_MANAGER_ID": MANAGER_ID,
        "USER_ID": USER_ID,
        "SESSION_ID": SESSION_ID,
        "NETWORK": NETWORK,
        "CREDENTIAL_PROVIDER_TYPE": "MultiProvider",
        "COINBASE_INSTRUMENT_ID": instruments["coinbase"]["instrument_id"],
        "COINBASE_WALLET_ADDRESS": instruments["coinbase"]["wallet_address"],
        "COINBASE_CONNECTOR_ID": CB_CONNECTOR_ID,
        "PRIVY_INSTRUMENT_ID": instruments["stripe_privy"]["instrument_id"],
        "PRIVY_WALLET_ADDRESS": instruments["stripe_privy"]["wallet_address"],
        "PRIVY_CONNECTOR_ID": SP_CONNECTOR_ID,
    }
)

print_summary(
    "Multi-Provider Setup Complete",
    manager_arn=MANAGER_ARN,
    coinbase_instrument=instruments["coinbase"]["instrument_id"],
    stripe_privy_instrument=instruments["stripe_privy"]["instrument_id"],
    session_id=SESSION_ID,
)
print("Downstream tutorials pick a provider via env vars:")
print("  COINBASE_INSTRUMENT_ID / PRIVY_INSTRUMENT_ID")
print("\nNext: python ../07-multi-agent-payment-orchestrator/multi_agent_payments.py")
