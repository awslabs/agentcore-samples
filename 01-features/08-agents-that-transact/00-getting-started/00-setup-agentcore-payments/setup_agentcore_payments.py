"""
Set Up Amazon Bedrock AgentCore payments

Creates the complete payment stack that all downstream tutorials depend on:
  IAM roles → CredentialProvider → PaymentManager → PaymentConnector
  → PaymentInstrument (embedded wallet) → PaymentSession (budget)

Architecture
------------
```
┌───────────────────────────────────────────────────────────┐
│                   Developer / Admin                       │
│                 (ControlPlaneRole)                        │
│                                                           │
│  CredentialProvider → PaymentManager → PaymentConnector   │
│  One-time setup. Creates the payment stack.               │
└─────────────────────────────┬─────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────┐
│             Application Backend                           │
│                 (ManagementRole)                          │
│                                                           │
│  CreateInstrument (wallet) → CreateSession (budget)       │
│  Cannot call ProcessPayment.                              │
└─────────────────────────┬─────────────────────────────────┘
                          │ passes sessionId + instrumentId
                          ▼
┌───────────────────────────────────────────────────────────┐
│                    Agent Runtime                          │
│               (ProcessPaymentRole)                        │
│                                                           │
│  ProcessPayment only. Cannot create sessions/instruments. │
└───────────────────────────────────────────────────────────┘
```

Usage:
    # Copy and fill in your .env first (note: .env lives in the parent
    # 00-getting-started/ directory, shared across all tutorials):
    cp .env.coinbase.sample ../.env   # for Coinbase CDP
    # OR
    cp .env.privy.sample ../.env      # for Stripe (Privy)

    python setup_agentcore_payments.py

Prerequisites:
    - AWS CLI configured (aws sts get-caller-identity)
    - Wallet provider credentials in .env (run providers/coinbase_cdp_account_setup.py
      or providers/stripe_privy_account_setup.py first)
    - pip install -r requirements.txt

Role Separation
---------------
| Role                  | Permissions                          | Purpose         |
|-----------------------|--------------------------------------|-----------------|
| ControlPlaneRole      | Create/manage Managers, Connectors   | Admin setup     |
| ManagementRole        | Instrument/Session CRUD. Deny ProcessPayment | App backend |
| ProcessPaymentRole    | ProcessPayment + read queries        | Agent execution |
| ResourceRetrievalRole | Secrets Manager, sts:SetContext      | Service role    |
"""

import os
import sys
import time
import uuid

import boto3
import botocore.exceptions
from bedrock_agentcore.payments import PaymentManager
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import (
    CONTROL_PLANE_ROLE,
    MANAGEMENT_ROLE,
    PROCESS_PAYMENT_ROLE,
    RESOURCE_RETRIEVAL_ROLE,
    assume_role,
    client_token,
    enable_observability,
    idempotent_create,
    pp,
    require_env,
    setup_payment_roles,
    update_env_file,
    wait_for_status,
)

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv(ENV_FILE, override=True)

# ── Region ────────────────────────────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
os.environ["AWS_REGION"] = AWS_REGION

PAYMENTS_CP_ENDPOINT = f"https://bedrock-agentcore-control.{AWS_REGION}.amazonaws.com"
PAYMENTS_DP_ENDPOINT = f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com"

session = boto3.Session(region_name=AWS_REGION)
identity = session.client("sts").get_caller_identity()
print(f"Region:   {AWS_REGION}")
print(f"Account:  {identity['Account']}")
print(f"Identity: {identity['Arn']}")


# ── Step 0a — Create IAM Roles ────────────────────────────────────────────────
# Creates 4 IAM roles: ControlPlaneRole, ManagementRole, ProcessPaymentRole,
# ResourceRetrievalRole. Idempotent — safe to run multiple times.
print("\n── Step 0a: Create IAM Roles ──")
roles = setup_payment_roles(region=AWS_REGION)

CONTROL_PLANE_ROLE_ARN = roles["control_plane"]
MANAGEMENT_ROLE_ARN = roles["management"]
PROCESS_PAYMENT_ROLE_ARN = roles["process_payment"]
RESOURCE_RETRIEVAL_ROLE_ARN = roles["resource_retrieval"]

update_env_file(
    ENV_FILE,
    {
        "CONTROL_PLANE_ROLE_ARN": CONTROL_PLANE_ROLE_ARN,
        "MANAGEMENT_ROLE_ARN": MANAGEMENT_ROLE_ARN,
        "PROCESS_PAYMENT_ROLE_ARN": PROCESS_PAYMENT_ROLE_ARN,
        "PROCESS_PAYMENT_ROLE_NAME": PROCESS_PAYMENT_ROLE_ARN.split("/")[-1],
        "RESOURCE_RETRIEVAL_ROLE_ARN": RESOURCE_RETRIEVAL_ROLE_ARN,
    },
)

# ── Step 1 — Configure Environment ───────────────────────────────────────────
print("\n── Step 1: Configure Environment ──")


# Capture LINKED_EMAIL interactively if not already set in .env.
# This email is used to:
#   - Create the embedded wallet (linkedAccounts)
#   - Log in to the wallet hub for funding and signing delegation
# Use a real address you can receive mail at — the wallet provider may verify it.
def _is_valid_email(s: str) -> bool:
    s = s.strip()
    return "@" in s and "." in s.split("@")[-1] and not s.startswith("<")


current_email = os.environ.get("LINKED_EMAIL", "").strip()
if not current_email or current_email.startswith("<") or current_email == "user@example.com":
    print()
    print("  We need your email address to set up your embedded wallet.")
    print("  This email is used to:")
    print("    - create your wallet account")
    print("    - log in to the wallet hub for funding and signing approval")
    print("  Use a real address you can receive mail at — the wallet provider")
    print("  may send a verification link.")
    print()
    while True:
        entered = input("  Enter your email: ").strip()
        if _is_valid_email(entered):
            break
        print(f"  '{entered}' does not look like a valid email address. Try again.")
    update_env_file(ENV_FILE, {"LINKED_EMAIL": entered})
    print(f"  ✓ Email saved to {os.path.basename(ENV_FILE)}")

load_dotenv(ENV_FILE, override=True)

CREDENTIAL_PROVIDER_TYPE = os.environ.get("CREDENTIAL_PROVIDER_TYPE", "CoinbaseCDP")
MANAGER_NAME = os.environ.get("DEFAULT_PAYMENT_MANAGER_NAME", "MyPaymentManager")
_derived_connector_name = {
    "CoinbaseCDP": "MyCoinbaseConnector",
    "StripePrivy": "MyPrivyConnector",
}.get(CREDENTIAL_PROVIDER_TYPE, "MyPaymentConnector")
CONNECTOR_NAME = os.environ.get("DEFAULT_PAYMENT_CONNECTOR_NAME") or _derived_connector_name
USER_ID = os.environ.get("USER_ID", "test-user-001")
NETWORK = os.environ.get("NETWORK", "ETHEREUM")

LINKED_EMAIL = os.environ.get("LINKED_EMAIL", "").strip()

print(f"  Provider: {CREDENTIAL_PROVIDER_TYPE}")
print(f"  Region:   {AWS_REGION}")
print(f"  Email:    {LINKED_EMAIL}")

# ── Step 2 — Verify IAM Roles ─────────────────────────────────────────────────
print("\n── Step 2: Verify IAM Roles ──")
iam = session.client("iam")
for name in [
    CONTROL_PLANE_ROLE,
    MANAGEMENT_ROLE,
    PROCESS_PAYMENT_ROLE,
    RESOURCE_RETRIEVAL_ROLE,
]:
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        print(f"  ✅ {name}")
    except botocore.exceptions.ClientError:
        print(f"  ❌ {name} — not found. Re-run Step 0a.")
        raise
print("  ✅ All 4 IAM roles present.")

# ── Step 3 — Create boto3 Clients ─────────────────────────────────────────────
# AgentCore payments uses two separate API endpoints:
#   Control Plane (bedrock-agentcore-control) — manages payment stack
#   Data Plane (bedrock-agentcore) — runs payment operations
print("\n── Step 3: Create boto3 Clients ──")
print("Assuming ControlPlaneRole...")
cp_session = assume_role(session, CONTROL_PLANE_ROLE_ARN, "tutorial-00-cp")
cp_client = cp_session.client("bedrock-agentcore-control", endpoint_url=PAYMENTS_CP_ENDPOINT)
cred_client = cp_session.client("bedrock-agentcore-control", endpoint_url=PAYMENTS_CP_ENDPOINT)

print("Assuming ManagementRole...")
mgmt_session = assume_role(session, MANAGEMENT_ROLE_ARN, "tutorial-00-mgmt")
dp_client = mgmt_session.client("bedrock-agentcore", endpoint_url=PAYMENTS_DP_ENDPOINT)
print("  ✅ Clients ready")

# ── Step 4 — Create Credential Provider ──────────────────────────────────────
# Stores wallet provider credentials securely inside AgentCore Identity.
# After ingestion, credentials are never returned to your code.
print("\n── Step 4: Create Credential Provider ──")
CRED_PROVIDER_NAME = f"{CREDENTIAL_PROVIDER_TYPE}{uuid.uuid4().hex[:8]}"

if CREDENTIAL_PROVIDER_TYPE == "CoinbaseCDP":
    provider_config = {
        "coinbaseCdpConfiguration": {
            "apiKeyId": require_env("COINBASE_API_KEY_ID"),
            "apiKeySecret": require_env("COINBASE_API_KEY_SECRET"),
            "walletSecret": require_env("COINBASE_WALLET_SECRET"),
        }
    }
elif CREDENTIAL_PROVIDER_TYPE == "StripePrivy":
    provider_config = {
        "stripePrivyConfiguration": {
            "appId": require_env("PRIVY_APP_ID"),
            "appSecret": require_env("PRIVY_APP_SECRET"),
            "authorizationId": require_env("PRIVY_AUTHORIZATION_ID"),
            "authorizationPrivateKey": require_env("PRIVY_AUTHORIZATION_PRIVATE_KEY"),
        }
    }
else:
    raise ValueError(f"Unknown CREDENTIAL_PROVIDER_TYPE: {CREDENTIAL_PROVIDER_TYPE}")

resp = idempotent_create(
    cred_client.create_payment_credential_provider,
    f"Credential provider '{CRED_PROVIDER_NAME}' already exists",
    name=CRED_PROVIDER_NAME,
    credentialProviderVendor=CREDENTIAL_PROVIDER_TYPE,
    providerConfigurationInput=provider_config,
)
if resp:
    CREDENTIAL_PROVIDER_ARN = resp["credentialProviderArn"]
    print(f"  credentialProviderArn: {CREDENTIAL_PROVIDER_ARN}")
else:
    # Re-run: the credential provider already exists, so reuse the ARN from .env.
    CREDENTIAL_PROVIDER_ARN = os.environ["CREDENTIAL_PROVIDER_ARN"]
    print(f"  Reusing existing credential provider: {CREDENTIAL_PROVIDER_ARN}")

# Security best practice: After setup, lock down the credential provider secrets
# in Secrets Manager so only the ResourceRetrievalRole can read them.
# See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-iam-roles.html

# ── Step 5 — Create Payment Manager ───────────────────────────────────────────
# Top-level resource. Uses ResourceRetrievalRole to access credentials at runtime.
# clientToken must be >= 33 chars for idempotency.
print("\n── Step 5: Create Payment Manager ──")
resp = idempotent_create(
    cp_client.create_payment_manager,
    f"Manager '{MANAGER_NAME}' already exists",
    name=MANAGER_NAME,
    description=f"{MANAGER_NAME} Tutorial 00",
    authorizerType="AWS_IAM",
    roleArn=RESOURCE_RETRIEVAL_ROLE_ARN,
    clientToken=client_token(),
)
if resp:
    pp("CreatePaymentManager", resp)
    MANAGER_ID = resp["paymentManagerId"]
    MANAGER_ARN = resp["paymentManagerArn"]
    print(f"\n  ID (control plane): {MANAGER_ID}")
    print(f"  ARN (data plane):   {MANAGER_ARN}")

    print("\nWaiting for READY...")
    wait_for_status(cp_client.get_payment_manager, "READY", paymentManagerId=MANAGER_ID)
    print("  ✅ PaymentManager is READY")

    update_env_file(
        ENV_FILE,
        {
            "AWS_REGION": AWS_REGION,
            "PAYMENT_MANAGER_ARN": MANAGER_ARN,
            "PAYMENT_MANAGER_ID": MANAGER_ID,
            "CREDENTIAL_PROVIDER_ARN": CREDENTIAL_PROVIDER_ARN,
            "CREDENTIAL_PROVIDER_TYPE": CREDENTIAL_PROVIDER_TYPE,
            "USER_ID": USER_ID,
            "NETWORK": NETWORK,
        },
    )
else:
    # Re-run: the manager already exists, so reuse the IDs a prior run wrote to .env.
    MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
    MANAGER_ID = os.environ.get("PAYMENT_MANAGER_ID", MANAGER_ARN.split("/")[-1])
    print(f"  Reusing existing PaymentManager: {MANAGER_ID}")

# ── Step 6 — Create Payment Connector ─────────────────────────────────────────
# Links the Manager to the Credential Provider.
print("\n── Step 6: Create Payment Connector ──")
connector_type = "CoinbaseCDP" if CREDENTIAL_PROVIDER_TYPE == "CoinbaseCDP" else "StripePrivy"
cred_key = "coinbaseCDP" if CREDENTIAL_PROVIDER_TYPE == "CoinbaseCDP" else "stripePrivy"

resp = idempotent_create(
    cp_client.create_payment_connector,
    f"Connector '{CONNECTOR_NAME}' already exists",
    paymentManagerId=MANAGER_ID,
    name=CONNECTOR_NAME,
    description=f"{CONNECTOR_NAME} {connector_type}",
    type=connector_type,
    credentialProviderConfigurations=[{cred_key: {"credentialProviderArn": CREDENTIAL_PROVIDER_ARN}}],
    clientToken=client_token(),
)
if resp:
    CONNECTOR_ID = resp["paymentConnectorId"]
    print(f"  paymentConnectorId: {CONNECTOR_ID}")

    print("\nWaiting for READY...")
    wait_for_status(
        cp_client.get_payment_connector,
        "READY",
        paymentManagerId=MANAGER_ID,
        paymentConnectorId=CONNECTOR_ID,
    )
    print("  ✅ PaymentConnector is READY")
    update_env_file(ENV_FILE, {"PAYMENT_CONNECTOR_ID": CONNECTOR_ID})
else:
    # Re-run: the connector already exists, so reuse the id a prior run wrote to .env.
    CONNECTOR_ID = os.environ["PAYMENT_CONNECTOR_ID"]
    print(f"  Reusing existing PaymentConnector: {CONNECTOR_ID}")

# ── Step 7 — Create Payment Instrument (Embedded Wallet) ──────────────────────
# Provisions an embedded USDC wallet linked to a user identity.
# All data plane calls use paymentManagerArn (full ARN), not the short ID.
# USDC amounts use 6 decimal places: 100000 = $0.10
print("\n── Step 7: Create Payment Instrument (Embedded Wallet) ──")
resp = dp_client.create_payment_instrument(
    paymentManagerArn=MANAGER_ARN,
    paymentConnectorId=CONNECTOR_ID,
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
pp("CreatePaymentInstrument", resp)
INSTRUMENT_ID = resp["paymentInstrument"]["paymentInstrumentId"]
WALLET_ADDRESS = resp["paymentInstrument"]["paymentInstrumentDetails"]["embeddedCryptoWallet"]["walletAddress"]
print(f"\n  instrumentId:  {INSTRUMENT_ID}")
print(f"  walletAddress: {WALLET_ADDRESS}")
update_env_file(ENV_FILE, {"INSTRUMENT_ID": INSTRUMENT_ID, "WALLET_ADDRESS": WALLET_ADDRESS})

# ── Step 7a — Fetch WalletHub URL (Coinbase only) ─────────────────────────────
if CREDENTIAL_PROVIDER_TYPE == "CoinbaseCDP":
    pm = PaymentManager(payment_manager_arn=MANAGER_ARN, region_name=AWS_REGION)
    redirect_url = None
    for attempt in range(6):
        instr_details = pm.get_payment_instrument(user_id=USER_ID, payment_instrument_id=INSTRUMENT_ID)
        wallet_info = instr_details.get("paymentInstrumentDetails", {}).get("embeddedCryptoWallet", {})
        redirect_url = wallet_info.get("redirectUrl")
        if redirect_url:
            break
        if attempt < 5:
            time.sleep(5)
    if redirect_url:
        print(f"\n  WalletHub: {redirect_url}")
        print("  Open this URL to fund the wallet and grant signing permission.")
    else:
        print("\n  ⚠️  WalletHub URL not yet available after ~25s. Re-run to retry.")
else:
    print("\n  Skipping WalletHub fetch (StripePrivy uses the Privy reference frontend).")

# ── Step 7b — Fund the Wallet + Delegate Signing (MANUAL STEP) ───────────────
faucet_network = "Base Sepolia" if NETWORK == "ETHEREUM" else "Solana Devnet"
print(f"""
✋ ACTION REQUIRED — Two manual steps before continuing:

  Wallet: {WALLET_ADDRESS}
  Network: {NETWORK}

  STEP 1 — Fund the wallet
  ────────────────────────
    1. Open https://faucet.circle.com/ and select {faucet_network}.
    2. Paste the wallet address above and request testnet USDC.""")
if NETWORK == "ETHEREUM":
    print(f"    3. Verify the funds arrived: https://sepolia.basescan.org/address/{WALLET_ADDRESS}")
else:
    print(f"    3. Verify the funds arrived: https://explorer.solana.com/address/{WALLET_ADDRESS}?cluster=devnet")

if CREDENTIAL_PROVIDER_TYPE == "CoinbaseCDP":
    print("""
  STEP 2 — Delegate signing (Coinbase)
  ────────────────────────────────────
    1. Open the WalletHub URL printed above.
    2. Log in with your email.
    3. Grant signing permission.
    OR: CDP Portal → Wallets → Embedded Wallet → Policies → enable Delegated Signing
""")
else:
    print("""
  STEP 2 — Delegate signing (Stripe/Privy)
  ────────────────────────────────────────
    1. Open http://localhost:3000 in your browser.
    2. Log in with your email.
    3. Choose Connect agent → Give access.
""")

input("  Press Enter when STEP 1 and STEP 2 are complete... ")

# ── Step 7c — Wait for ACTIVE (instrument is funded + delegated) ─────────────
print("\nWaiting for ACTIVE...")
wait_for_status(
    dp_client.get_payment_instrument,
    "ACTIVE",
    paymentManagerArn=MANAGER_ARN,
    paymentConnectorId=CONNECTOR_ID,
    paymentInstrumentId=INSTRUMENT_ID,
    userId=USER_ID,
)
print("  ✅ Instrument is ACTIVE (funded and delegated)")

# ── Step 7d — Verify Wallet Balance (Optional) ───────────────────────────────
chain = "BASE_SEPOLIA" if NETWORK == "ETHEREUM" else "SOLANA_DEVNET"
try:
    balance_resp = dp_client.get_payment_instrument_balance(
        paymentManagerArn=MANAGER_ARN,
        paymentConnectorId=CONNECTOR_ID,
        paymentInstrumentId=INSTRUMENT_ID,
        userId=USER_ID,
        chain=chain,
        token="USDC",
    )
    token_balance = balance_resp.get("tokenBalance", {})
    amount = int(token_balance.get("amount", "0")) / 1_000_000
    print(f"  Wallet balance: {amount:.2f} USDC on {chain}")
    if amount == 0:
        print("  ⚠️  Wallet has no USDC yet. Fund it via the faucet before running Tutorial 01.")
except botocore.exceptions.ClientError as e:
    print(f"  ⚠️  Balance check failed: {e}")
    print("  You can proceed to Step 8 — balance is verified through payment success in Tutorial 01.")

# ── Step 8 — Create Payment Session ──────────────────────────────────────────
# Time-bounded payment limits. value must be a string. currency is USD (not USDC).
print("\n── Step 8: Create Payment Session ──")
resp = dp_client.create_payment_session(
    paymentManagerArn=MANAGER_ARN,
    userId=USER_ID,
    expiryTimeInMinutes=60,
    limits={"maxSpendAmount": {"value": "1.0", "currency": "USD"}},
    clientToken=client_token(),
)
SESSION_ID = resp["paymentSession"]["paymentSessionId"]
print(f"  ✅ Session: {SESSION_ID} (budget: $1.00, expiry: 60 min)")
update_env_file(ENV_FILE, {"SESSION_ID": SESSION_ID})

# ── Step 8b — Enable Observability ────────────────────────────────────────────
print("\n── Step 8b: Enable Observability ──")
account_id = boto3.client("sts").get_caller_identity()["Account"]
try:
    obs_result = enable_observability(
        resource_arn=MANAGER_ARN,
        resource_id=MANAGER_ID,
        account_id=account_id,
        region=AWS_REGION,
        enable_xray_spans=False,
    )
    print(f"  Logs: /aws/vendedlogs/bedrock-agentcore/{MANAGER_ID}")
    print("  View traces: CloudWatch console > X-Ray traces > Traces")
except botocore.exceptions.ClientError as e:
    print(f"  ⚠️  Observability setup failed: {e}")
    print("  This is non-blocking — tutorials will still work without observability.")

# ── Step 9 — Verify Setup ─────────────────────────────────────────────────────
print("\n── Step 9: Verify Setup ──")
resp = dp_client.get_payment_session(paymentManagerArn=MANAGER_ARN, paymentSessionId=SESSION_ID, userId=USER_ID)
sess = resp["paymentSession"]
assert sess["paymentSessionId"] == SESSION_ID
print(f"  paymentSessionId:    {sess['paymentSessionId']}")
print(f"  expiryTimeInMinutes: {sess['expiryTimeInMinutes']}")
budget = sess.get("limits", {}).get("maxSpendAmount", {})
if budget:
    print(f"  budget:              {budget.get('value')} {budget.get('currency')}")
print("\n  ✅ Session is ready for ProcessPayment in Tutorial 01.")

print(f"""
╔══════════════════════════════════════════════════════════╗
  Setup Complete — resources saved to .env
  Payment Manager ARN: {MANAGER_ARN}
  Instrument ID:       {INSTRUMENT_ID}
  Session ID:          {SESSION_ID}
╚══════════════════════════════════════════════════════════╝

Next: python ../01-agents-payments-and-limits/strands_payment_agent.py
""")

# ── Cleanup (uncomment to run) ────────────────────────────────────────────────
# Deletes all payment resources in dependency order.
# WARNING: Irreversible. Run only after completing ALL downstream tutorials.
#
# import botocore.exceptions
# load_dotenv(ENV_FILE, override=True)
# MANAGER_ARN  = os.environ.get("PAYMENT_MANAGER_ARN", "")
# MANAGER_ID   = os.environ.get("PAYMENT_MANAGER_ID", "")
# CONNECTOR_ID = os.environ.get("PAYMENT_CONNECTOR_ID", "")
# CRED_ARN     = os.environ.get("CREDENTIAL_PROVIDER_ARN", "")
# INSTRUMENT_ID = os.environ.get("INSTRUMENT_ID", "")
# SESSION_ID   = os.environ.get("SESSION_ID", "")
# CRED_PROVIDER_NAME = CRED_ARN.rsplit("/", 1)[-1] if CRED_ARN else ""
#
# def safe_delete(fn, label, **kw):
#     try:
#         fn(**kw); print(f"  ✅ Deleted: {label}")
#     except botocore.exceptions.ClientError as e:
#         code = e.response["Error"]["Code"]
#         if code in ("ResourceNotFoundException", "NotFoundException"):
#             print(f"  ⏭️  Already gone: {label}")
#         else:
#             raise
#
# # 1. Sessions → 2. Instruments → 3. Connectors → 4. Manager → 5. Credential Provider
# if MANAGER_ARN and INSTRUMENT_ID:
#     safe_delete(dp_client.delete_payment_instrument, f"Instrument {INSTRUMENT_ID}",
#         paymentManagerArn=MANAGER_ARN, paymentInstrumentId=INSTRUMENT_ID, userId=USER_ID)
# if MANAGER_ID and CONNECTOR_ID:
#     safe_delete(cp_client.delete_payment_connector, f"Connector {CONNECTOR_ID}",
#         paymentManagerId=MANAGER_ID, paymentConnectorId=CONNECTOR_ID, clientToken=client_token())
# if MANAGER_ID:
#     safe_delete(cp_client.delete_payment_manager, f"Manager {MANAGER_ID}",
#         paymentManagerId=MANAGER_ID, clientToken=client_token())
# if CRED_PROVIDER_NAME:
#     safe_delete(cred_client.delete_payment_credential_provider, f"CredProvider {CRED_PROVIDER_NAME}",
#         name=CRED_PROVIDER_NAME)
# print("  🧹 Cleanup complete")
# print("  Also delete IAM roles and CloudWatch log groups manually.")
