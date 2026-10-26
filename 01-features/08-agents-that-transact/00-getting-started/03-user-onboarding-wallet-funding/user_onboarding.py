"""
User Onboarding and Backend Wallet Operations

Two personas in this script:
  - Application Backend: provisions wallets, checks balances, creates sessions
  - End User: funds the wallet and grants signing consent via WalletHub or Privy UI

Part 1 — Onboarding (per end user): create wallet, fund it, delegate signing,
          optionally add wallets on additional chains.
Part 2 — Backend operations: balance checks, session creation with budgets,
          instrument listing, remaining-budget queries.

Onboarding flow:
    Backend                     End User UI (WalletHub / Privy frontend)
      │                                │
      ├─ CreatePaymentInstrument ──►   │
      │   (wallet provisioned)          │
      │                                ├─ Fund wallet (faucet / onramp)
      │                                ├─ Grant signing (Connect agent)
      │                                │
      ├─ GetPaymentInstrumentBalance ─ │ (verify funded)
      ├─ CreatePaymentSession ──────── │ (set budget)
      └─ ListPaymentInstruments ─────  │ (account dashboard)

Usage:
    python user_onboarding.py

Prerequisites:
    - Tutorial 00 completed (.env exists with PAYMENT_MANAGER_ARN, PAYMENT_CONNECTOR_ID, LINKED_EMAIL)
    - Testnet USDC from https://faucet.circle.com/ (Base Sepolia or Solana Devnet)
    - pip install -r requirements.txt
"""

import os
import sys

import botocore.exceptions
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bedrock_agentcore.payments import PaymentManager
from utils import client_token, load_tutorial_env, print_summary, wait_for_status

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(ENV_FILE, override=True)

# ── Configuration ─────────────────────────────────────────────────────────────
config = load_tutorial_env()
PAYMENT_MANAGER_ARN = config["payment_manager_arn"]
REGION = config["region"]
USER_ID = config["user_id"]
NETWORK = os.environ.get("NETWORK", "ETHEREUM")

# SDK client for all payment data-plane operations (instruments, balances, sessions)
manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

# load_tutorial_env resolves connector_id / instrument_id to the provider you
# configured (CREDENTIAL_PROVIDER_TYPE), so single- and multi-provider .env files both work.
CONNECTOR_ID = config.get("connector_id")
INSTRUMENT_ID = config.get("instrument_id")
PROVIDER = config.get("active_provider") or config.get("provider_type", "unknown")

print_summary("Config", provider=PROVIDER, instrument=INSTRUMENT_ID)

# ─────────────────────────────────────────────────────────────────────────────
# PART 1 — Onboarding (per end user)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PART 1 — Onboarding (per end user)")
print("=" * 60)

# ── Section 1: Create Embedded Wallet ─────────────────────────────────────────
print("\n── Section 1: Create Embedded Wallet ──")
print("Backend operation: provision wallet for a new user")

# For this tutorial, reuse the developer's LINKED_EMAIL as the end-user identity.
# In your own application, pass each user's own email here.
NEW_USER_ID = "tutorial-03-user"
NEW_EMAIL = os.environ.get("LINKED_EMAIL", "tutorial03@example.com")

inst = manager.create_payment_instrument(
    user_id=NEW_USER_ID,
    payment_connector_id=CONNECTOR_ID,
    payment_instrument_type="EMBEDDED_CRYPTO_WALLET",
    payment_instrument_details={
        "embeddedCryptoWallet": {
            "network": NETWORK,
            "linkedAccounts": [{"email": {"emailAddress": NEW_EMAIL}}],
        }
    },
    client_token=client_token(),
)
NEW_INSTRUMENT_ID = inst["paymentInstrumentId"]
NEW_WALLET = inst["paymentInstrumentDetails"]["embeddedCryptoWallet"]["walletAddress"]

print_summary(
    "New Instrument Created",
    instrument_id=NEW_INSTRUMENT_ID,
    wallet_address=NEW_WALLET,
    network=NETWORK,
    status=inst.get("status", "unknown"),
)

redirect_url = inst["paymentInstrumentDetails"]["embeddedCryptoWallet"].get("redirectUrl")
if redirect_url:
    print(f"\n  WalletHub: {redirect_url}")
    print("  Share this URL with the end user to fund the wallet and grant signing permission.")

# ── Section 2: Fund the Wallet ────────────────────────────────────────────────
print("\n── Section 2: Fund the Wallet ──")
faucet_network = "Base Sepolia" if NETWORK == "ETHEREUM" else "Solana Devnet"
print("End-user action: fund the wallet via the provider UI or Circle faucet")
print()
print("For testnet (tutorial use):")
print("  1. Go to https://faucet.circle.com/")
print(f"  2. Select: {faucet_network}")
print(f"  3. Paste wallet address: {NEW_WALLET}")
print("  4. Request 20 USDC (covers all tutorials)")
if NETWORK == "ETHEREUM":
    print(f"  5. Verify: https://sepolia.basescan.org/address/{NEW_WALLET}")
else:
    print(f"  5. Verify: https://explorer.solana.com/address/{NEW_WALLET}?cluster=devnet")
print()
print("Funding options by provider:")
print("  Coinbase: WalletHub URL above → fund + delegate in one UI (Coinbase managed)")
print("  Privy: Privy reference frontend at http://localhost:3000 → Add funds →")
print("         Pay with card (Stripe onramp), Transfer from wallet, or Receive funds (QR)")
print()
print("ACTION REQUIRED: Fund the wallet before continuing.")

# ── Section 3: Delegation — Grant Signing Permission ──────────────────────────
print("\n── Section 3: Delegation — Grant Signing Permission ──")
print("End-user action: grant the agent permission to sign transactions")
print()
print("Provider-specific steps:")
print()
print("  Coinbase CDP:")
print("    1. Open the WalletHub URL (printed above, or from Setup Tutorial 00 Step 3)")
print("    2. Log in with LINKED_EMAIL")
print("    3. Consent to delegated signing")
print("    (Or: CDP Portal → Wallets → Embedded Wallet → Policies → Enable Delegated Signing)")
print()
print("  Stripe (Privy):")
print("    1. Open http://localhost:3000 in your browser")
print("    2. Log in with LINKED_EMAIL")
print("    3. Confirm the wallet address matches the one printed in Section 1")
print("    4. Choose 'Connect agent' → 'Give access'")
print()
print("  Without delegation: ProcessPayment fails with a signing error.")

input("\nPress Enter after funding and delegating in Sections 2 and 3... ")

# ── Section 3b: Wait for ACTIVE (instrument is funded + delegated) ───────────
print("\n── Section 3b: Wait for ACTIVE ──")
print("Waiting for instrument to become ACTIVE (funded and delegated)...")
wait_for_status(
    manager.get_payment_instrument,
    "ACTIVE",
    user_id=NEW_USER_ID,
    payment_instrument_id=NEW_INSTRUMENT_ID,
)
print("  ✅ Instrument is ACTIVE (funded and delegated)")

# ─────────────────────────────────────────────────────────────────────────────
# PART 2 — Backend Operations
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PART 2 — Backend Operations")
print("=" * 60)
print("The following operations run from your application backend, not the end user.")

# ── Section 4: Check Wallet Balance ──────────────────────────────────────────
print("\n── Section 4: Check Wallet Balance ──")
chain = "BASE_SEPOLIA" if NETWORK == "ETHEREUM" else "SOLANA_DEVNET"

for label, inst_id, user_id in [
    ("Tutorial 00 instrument", INSTRUMENT_ID, USER_ID),
    ("New instrument", NEW_INSTRUMENT_ID, NEW_USER_ID),
]:
    try:
        resp = manager.get_payment_instrument_balance(
            payment_connector_id=CONNECTOR_ID,
            payment_instrument_id=inst_id,
            chain=chain,
            token="USDC",
            user_id=user_id,
        )
        balance = resp.get("tokenBalance", {})
        amount = int(balance.get("amount", "0")) / 1_000_000
        print(f"  {label}: {amount:.2f} USDC on {chain}")
        if amount == 0:
            print("     Fund at: https://faucet.circle.com/")
    except botocore.exceptions.ClientError as e:
        print(f"  {label}: balance check failed — {e}")

# ── Section 5: Multi-Network Wallets (reference) ──────────────────────────────
print("\n── Section 5: Multi-Network Wallets ──")
print("To add wallets on additional chains, call create_payment_instrument twice")
print("with different network values (ETHEREUM, SOLANA).")
print("Same user, same manager, same connector — new wallet address per chain.")
print()
print("Example (not executed — requires additional faucet funding):")
print("  sol_instrument = manager.create_payment_instrument(")
print("      user_id=USER_ID,")
print("      payment_connector_id=CONNECTOR_ID,")
print("      payment_instrument_type='EMBEDDED_CRYPTO_WALLET',")
print("      payment_instrument_details={'embeddedCryptoWallet': {")
print("          'network': 'SOLANA',  # ← only this changes")
print("          'linkedAccounts': [{'email': {'emailAddress': LINKED_EMAIL}}],")
print("      }},")
print("      client_token=client_token(),")
print("  )")

# ── Section 6: Create Sessions with Different Budgets ─────────────────────────
print("\n── Section 6: Create Sessions with Different Budgets ──")

# Quick lookup
quick = manager.create_payment_session(
    user_id=USER_ID,
    expiry_time_in_minutes=15,
    limits={"maxSpendAmount": {"value": "0.10", "currency": "USD"}},
    client_token=client_token(),
)
print(f"  Quick lookup: {quick['paymentSessionId']} ($0.10 / 15 min)")

# Research task
research = manager.create_payment_session(
    user_id=USER_ID,
    expiry_time_in_minutes=60,
    limits={"maxSpendAmount": {"value": "1.00", "currency": "USD"}},
    client_token=client_token(),
)
print(f"  Research:     {research['paymentSessionId']} ($1.00 / 60 min)")

# Deep analysis
deep = manager.create_payment_session(
    user_id=USER_ID,
    expiry_time_in_minutes=480,
    limits={"maxSpendAmount": {"value": "5.00", "currency": "USD"}},
    client_token=client_token(),
)
print(f"  Deep analysis: {deep['paymentSessionId']} ($5.00 / 480 min)")

print("\nSame user, same wallet, independent budgets.")
print("Pass the appropriate sessionId to the agent based on the task type.")

# ── Section 7: List All Instruments for a User ───────────────────────────────
print("\n── Section 7: List Instruments ──")
for label, user_id in [
    ("Tutorial 00 user", USER_ID),
    ("Section 1 new user", NEW_USER_ID),
]:
    resp = manager.list_payment_instruments(
        user_id=user_id,
        payment_connector_id=CONNECTOR_ID,
    )
    instruments = resp.get("paymentInstruments", [])
    print(f"\n  {label} ({user_id}): {len(instruments)} instrument(s)")
    for instr in instruments:
        print(f"    {instr['paymentInstrumentId']}")
        print(f"      type:    {instr.get('paymentInstrumentType', 'unknown')}")
        print(f"      status:  {instr.get('status', 'unknown')}")
        print(f"      created: {instr.get('createdAt', 'unknown')}")

# ── Section 8: Check Session Remaining Budget ─────────────────────────────────
print("\n── Section 8: Check Session Remaining Budget ──")
for label, created in [
    ("Quick lookup", quick),
    ("Research", research),
    ("Deep analysis", deep),
]:
    sid = created["paymentSessionId"]
    sess = manager.get_payment_session(
        user_id=USER_ID,
        payment_session_id=sid,
    )
    budget = sess.get("limits", {}).get("maxSpendAmount", {})
    available = sess.get("availableLimits", {}).get("availableSpendAmount", {})
    print(f"\n  {label} — {sid}")
    print(f"    Budget:    {budget.get('value', 'N/A')} {budget.get('currency', '')}")
    print(f"    Available: {available.get('value', 'N/A')} {available.get('currency', '')}")
    print(f"    Expiry:    {sess.get('expiryTimeInMinutes', 'N/A')} minutes")

print("\nDone. Sessions expire automatically. Instrument cleanup: run Tutorial 00 cleanup.")
print("Next: python ../04-agent-with-coinbase-bazaar-via-gateway/bazaar_gateway_agent.py")
