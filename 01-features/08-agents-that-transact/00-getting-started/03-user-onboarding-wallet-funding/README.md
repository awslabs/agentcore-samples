# Tutorial 03 — User Onboarding and Backend Wallet Operations

| Information         | Details                                                             |
|:--------------------|:--------------------------------------------------------------------|
| Tutorial type       | Backend operations / lifecycle (no agent)                          |
| Agent type          | None — per-user wallet operations only                             |
| Tooling             | AgentCore SDK (`PaymentManager`) for all per-user ops; `user_onboarding.py` shows the full flow |
| LLM model           | None                                                                |
| Components          | PaymentInstrument, PaymentSession, instrument balance, multi-network |
| Example complexity  | Intermediate                                                        |

> **Reads** the shared `.env` one directory up (`PAYMENT_MANAGER_ARN`, `PAYMENT_CONNECTOR_ID`,
> `USER_ID`, `INSTRUMENT_ID`, `NETWORK`, `LINKED_EMAIL`). **Does** the per-user backend work on top
> of Tutorial 00's shared stack: creates a new user's embedded wallet, checks balances, and mints
> spending sessions with budgets. → [How the pieces fit together](../README.md#cli-vs-sdk)

## Overview

Tutorial 00 provisioned your shared payment infrastructure — the payment manager, connector, and
IAM roles — with the AgentCore CLI, and created one wallet for you as the developer. In a real
application, every end user gets their own embedded wallet, and your backend manages the
session budgets that govern what the agent spends on their behalf. Wallets and sessions are
per-user, so you create them in your backend with the AgentCore SDK (`PaymentManager`), scoped to
the individual user you serve.

Running `user_onboarding.py` walks that full backend lifecycle: it calls `create_payment_instrument`
to provision a new user's wallet (a `PaymentInstrument`), reads balances via
`get_payment_instrument_balance`, creates three `PaymentSession`s with different budgets and
expiries, and lists instruments per user. The same flow works for both wallet providers — Coinbase
CDP and Stripe (Privy) — with provider-specific onboarding steps (funding + delegated signing)
called out where they differ.

> **Billable resources.** Creating instruments and sessions touches real AgentCore payments
> resources. See [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/).

> **Testnet only.** Use Base Sepolia (`NETWORK=ETHEREUM`) or Solana Devnet (`NETWORK=SOLANA`) with
> free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.

> **Supported regions:** `us-east-1`, `us-west-2`, `eu-central-1`, `ap-southeast-2`. Set
> `AWS_REGION` in the shared `.env` to one of these.

## Two personas

| Persona | Who | What they do |
|---------|-----|-------------|
| **Application backend** | You (developer / backend code) | Provisions wallets, checks balances, creates sessions |
| **End user** | User of your app | Funds the wallet, grants consent via WalletHub or Privy reference frontend |

For simplicity, the tutorial reuses the developer's `LINKED_EMAIL` as the end-user identity. In a
real application each user has their own email and their own wallet.

## Architecture

![Onboarding Flow](images/onboarding_flow.png)

```
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
```

### Wallet providers

![Wallet Providers](images/wallet_provider_paths.png)

### Session patterns

| Pattern | Budget | Expiry | Use case |
|---------|--------|--------|----------|
| Quick lookup | $0.10 | 15 min | Single API call |
| Research task | $1.00 | 60 min | Multi-endpoint research |
| Deep analysis | $5.00 | 480 min | Extended workflow |
| No budget cap | omit `limits` | 60 min | Trusted internal agents |

### Supported networks

| Network setting | Chain | CAIP-2 | Faucet |
|----------------|-------|--------|--------|
| `ETHEREUM` | Base Sepolia | `eip155:84532` | faucet.circle.com → Base Sepolia |
| `SOLANA` | Solana Devnet | `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` | faucet.circle.com → Solana Devnet |

## Prerequisites

- **Tutorial 00 completed** — the shared `.env` (one directory up, at
  `00-getting-started/.env`) is populated with `PAYMENT_MANAGER_ARN`, `PAYMENT_CONNECTOR_ID`,
  `USER_ID`, `INSTRUMENT_ID`, `NETWORK`, and `LINKED_EMAIL`. This tutorial reads them via
  `utils.load_tutorial_env()`.
- **Testnet USDC** from [faucet.circle.com](https://faucet.circle.com/) for the network in your
  `.env` (Base Sepolia for `ETHEREUM`, Solana Devnet for `SOLANA`).
- **Python 3.10+** and AWS CLI configured (`aws sts get-caller-identity`).
- **Python deps:**
  ```bash
  pip install -r requirements.txt
  ```
- **AgentCore CLI** (used only to inspect the shared stack from Tutorial 00's project dir) —
  `npm install -g @aws/agentcore@0.30.0`.

## Walkthrough

You own the per-user backend operations — instrument creation, balance checks, and session budgets —
and run them from your backend with the AgentCore SDK (`PaymentManager`), which wraps every payment
data-plane call. Each snippet below is copy-paste ready. (Prefer to
run all of them at once? `python user_onboarding.py` does the same operations end to end — see
[What the script does](#what-the-script-does).)

### Step 1 — Confirm the shared stack from Tutorial 00

The `agentcore status` command reads a scaffolded project's config, so run it from Tutorial 00's
project directory, where that config lives. This confirms the manager and connector you'll build on
are live.

```bash
cd ../00-setup-agentcore-payments/PaymentSetup
agentcore status --type payment
cd -            # back to the tutorial-03 folder
```

Load the shared `.env` (one directory up) so the payment IDs are in scope for the commands below:

```bash
set -a && source ../.env && set +a
```

### Step 2 — Run the per-user wallet operations

Work through the SDK snippets in
[Per-user wallet operations](#per-user-wallet-operations) below — create a new user's wallet, fund it,
check its balance, and create a budgeted session. A brand-new wallet starts at `0.00 USDC`, so its
first balance check reads zero until you fund it at [faucet.circle.com](https://faucet.circle.com/);
the already-funded Tutorial 00 instrument shows a non-zero balance.

## What the script does

`python user_onboarding.py` runs eight sections across two parts. (The **Onboarding Flow** diagram
under [Architecture](#architecture) above shows this backend ↔ end-user lifecycle end to end.)

**Part 1 — Onboarding (per end user)**
1. **Create embedded wallet** — `manager.create_payment_instrument(...)` for a new `user_id`,
   printing the wallet address and WalletHub `redirectUrl`.
2. **Fund the wallet** — prints faucet instructions for the configured network (end-user action).
3. **Delegate signing** — prints provider-specific consent steps (Coinbase WalletHub / Privy
   frontend). This is what authorizes the agent to sign later.

**Part 2 — Backend operations**
4. **Check balance** — `manager.get_payment_instrument_balance(...)` for both the Tutorial 00
   instrument and the new one.
5. **Multi-network** — reference snippet showing how to add a second wallet on another chain.
6. **Create sessions** — three `create_payment_session` calls ($0.10/15 min, $1.00/60 min,
   $5.00/480 min).
7. **List instruments** — `list_payment_instruments` per user.
8. **Check remaining budget** — `get_payment_session` for each session's available spend.

## Per-user wallet operations

These are the individual building blocks the script runs — each SDK snippet can be run on its own.
Set up the client and config once (the script reads these from `utils.load_tutorial_env()`; here we
read them from the shared `.env`):

```python
import os
from bedrock_agentcore.payments import PaymentManager
from utils import client_token  # uuid4 helper

PAYMENT_MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
REGION = os.environ["AWS_REGION"]
CONNECTOR_ID = os.environ["PAYMENT_CONNECTOR_ID"]
NETWORK = os.environ.get("NETWORK", "ETHEREUM")
NEW_EMAIL = os.environ["LINKED_EMAIL"]

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
```

**Create a per-user embedded wallet (instrument):**

```python
inst = manager.create_payment_instrument(
    user_id="tutorial-03-user",
    payment_connector_id=CONNECTOR_ID,
    payment_instrument_type="EMBEDDED_CRYPTO_WALLET",
    payment_instrument_details={
        "embeddedCryptoWallet": {
            "network": NETWORK,  # "ETHEREUM" (Base Sepolia) or "SOLANA" (Solana Devnet)
            "linkedAccounts": [{"email": {"emailAddress": NEW_EMAIL}}],
        }
    },
    client_token=client_token(),
)
instrument_id = inst["paymentInstrumentId"]
wallet_address = inst["paymentInstrumentDetails"]["embeddedCryptoWallet"]["walletAddress"]
redirect_url = inst["paymentInstrumentDetails"]["embeddedCryptoWallet"].get("redirectUrl")
```

The response includes `paymentInstrumentId`, the wallet address to fund, and the WalletHub
`redirectUrl` for consent. Set `network` to `ETHEREUM` (Base Sepolia) or `SOLANA` (Solana Devnet).

**Create a spending session with a custom budget and expiry:**

```python
session = manager.create_payment_session(
    user_id="tutorial-03-user",
    limits={"maxSpendAmount": {"value": "1.00", "currency": "USD"}},
    expiry_time_in_minutes=60,
)
session_id = session["paymentSessionId"]
```

Sessions are **wallet-blind** — `create_payment_session` takes no instrument id. At `ProcessPayment`
time the service picks the user's instrument whose network matches the merchant's x402 challenge, so
one session can spend across a user's Ethereum and Solana wallets. Omit `limits` for an uncapped
session.

**Read an instrument balance** with the SDK:

```python
chain = "BASE_SEPOLIA" if NETWORK == "ETHEREUM" else "SOLANA_DEVNET"
resp = manager.get_payment_instrument_balance(
    payment_connector_id=CONNECTOR_ID,
    payment_instrument_id=instrument_id,
    chain=chain,
    token="USDC",
    user_id="tutorial-03-user",
)
amount = int(resp["tokenBalance"]["amount"]) / 1_000_000  # micro-USDC → USDC
```

## Delegation: grant signing permission

Before the agent can sign transactions, the end user grants permission once per wallet.

| | Coinbase CDP | Stripe (Privy) |
|---|---|---|
| **Mechanism** | Project-level delegated signing | Authorization key as an additional signer on the wallet |
| **User action** | Grant consent via WalletHub `redirectUrl` | Log in at `http://localhost:3000`, choose **Connect agent → Give access** |
| **Scope** | All wallets under the project | Per-wallet |
| **Without it** | `ProcessPayment` fails with a signing error | `ProcessPayment` fails |

## Inspect / verify

To confirm the managers, connectors, and live payment status, re-run the
`agentcore status --type payment` check from [Step 1](#step-1--confirm-the-shared-stack-from-tutorial-00).
The script prints each new instrument id, wallet address, session id, and remaining budget as it runs.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: .env not found. Run Tutorial 00 first.` | Tutorial 00 not completed — `load_tutorial_env()` raises when the shared `.env` is missing | Run Tutorial 00 so the shared `.env` is created and populated |
| Wallet balance shows `0.00 USDC` | Wallet not funded (expected for the brand-new Section 1 wallet) | Fund at [faucet.circle.com](https://faucet.circle.com/) for the network in `.env`, paste the address printed in Section 1, then re-run |
| `list_payment_instruments` returns empty | Wrong `payment_connector_id` | Instruments are scoped to a connector — pass `PAYMENT_CONNECTOR_ID` from `.env` |
| `ProcessPayment` fails with a signing error (in a later tutorial) | Delegation not completed | Coinbase: CDP Portal → Wallets → Embedded Wallet → Policies. Privy: complete **Connect agent → Give access** |
| `ImportError: PaymentManager` | Wrong import path | Import from `bedrock_agentcore.payments` (not `...payments.manager`) |
| `agentcore: command not found` | CLI not installed (inspection step only) | `npm install -g @aws/agentcore@0.30.0` |

## Clean Up

Sessions created here expire automatically — no teardown needed. Instruments and the shared payment
manager/connector are torn down by Tutorial 00's cleanup. Delete each instrument with the SDK
(`manager.delete_payment_instrument`), then remove the shared stack:

```python
# Delete an instrument with the SDK:
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
manager.delete_payment_instrument(
    payment_instrument_id=INSTRUMENT_ID,
    payment_connector_id=CONNECTOR_ID,
    user_id=USER_ID,
)
```

Then remove the shared stack with the AgentCore CLI:

```bash
cd ../00-setup-agentcore-payments/PaymentSetup

agentcore remove payment-connector --manager MyPaymentManager --name MyCoinbaseConnector -y   # or MyPrivyConnector
agentcore remove payment-manager --name MyPaymentManager -y
agentcore deploy -y            # applies the removal in AWS
agentcore remove all -y        # removes the scaffolded runtime project
```

## Next steps

- **Tutorial 04** — [`../04-agent-with-coinbase-bazaar-via-gateway/`](../04-agent-with-coinbase-bazaar-via-gateway/) — Discover and call paid MCP tools on Coinbase Bazaar through AgentCore Gateway
- **Tutorial 05** — [`../05-agent-with-browser-tool-pay-for-content/`](../05-agent-with-browser-tool-pay-for-content/) — Browser + paywall payment pattern
- **Tutorial 06** — [`../06-research-agent-with-payment-memory/`](../06-research-agent-with-payment-memory/) — Recall past data and skip redundant paid calls with AgentCore Memory
- **Tutorial 07** — [`../07-multi-agent-payment-orchestrator/`](../07-multi-agent-payment-orchestrator/) — Multi-agent orchestration with per-agent budgets
