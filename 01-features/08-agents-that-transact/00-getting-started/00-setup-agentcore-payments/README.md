# Tutorial 00 — Set Up AgentCore payments

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | One-time setup (every downstream tutorial builds on it)                           |
| Agent type          | None — this tutorial provisions payment infrastructure                           |
| Framework           | Strands (scaffolded project host; no agent code runs here)                       |
| LLM model           | None                                                                             |
| Components          | AgentCore CLI (`create` / `add payment-manager` / `add payment-connector` / `deploy`) + AgentCore SDK (`PaymentManager.create_payment_instrument` / `create_payment_session`) |
| Example complexity  | Beginner                                                                         |


To learn more about AgentCore CLI Commands, see [this link] (https://github.com/aws/agentcore-cli/blob/main/docs/payments.md)

> **Complementary tools.** The AgentCore CLI provisions your shared payment infrastructure
> (credential provider, payment manager, connector, IAM roles) in one `agentcore deploy`. The AgentCore
> SDK (`PaymentManager`) then creates the per-user wallet (instrument) and spending session,
> scoped to the user you serve. **Reads** provider credentials from `../.env`; **writes**
> `PAYMENT_MANAGER_ARN`, `PAYMENT_MANAGER_ID`, `PAYMENT_CONNECTOR_ID`, `CREDENTIAL_PROVIDER_TYPE`,
> `USER_ID`, `NETWORK`, `INSTRUMENT_ID`, `WALLET_ADDRESS`, `SESSION_ID` back to `../.env`.
> → [How the pieces fit together](../README.md#cli-vs-sdk)

## Overview

This is the one-time setup that Tutorials 01–07 build on. You provision one **PaymentManager**, one
**PaymentConnector** (Coinbase CDP or Stripe/Privy), a **PaymentCredentialProvider**, and the runtime
execution + resource-retrieval **IAM roles** — all with the AgentCore CLI in a single deploy. Then you
create a per-user **PaymentInstrument** (embedded crypto wallet) and a budgeted **PaymentSession** with
the AgentCore SDK (`PaymentManager`). Wallets and sessions are per-user, so you create one of each scoped
to the user you serve — the way you'd build a payment-enabled application. Every resource ID is
written to the shared `../.env` so downstream tutorials pick them up unchanged.

> **Billable resources.** `agentcore deploy` creates real AWS resources (IAM roles, credential
> provider, payment manager/connector). See
> [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/). First-time deploy takes a
> few minutes (IAM role propagation); run [Clean Up](#clean-up) when you finish all tutorials.

> **Testnet only.** Base Sepolia (`NETWORK=ETHEREUM`) or Solana Devnet (`NETWORK=SOLANA`), with free
> USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.

> **Supported regions:** 
Set `AWS_REGION`
> in `../.env` to one of these.

## Architecture

```
Developer machine                        AgentCore CLI                    AWS
  │ agentcore create ─────────────────────────►│  (scaffolds PaymentSetup/)
  │ agentcore add payment-manager ────────────►│                            │
  │ agentcore add payment-connector ──────────►│  (patches project locally, │
  │                                            │   stores creds in .env.local)│
  │ agentcore deploy -y ──────────────────────►│───────────────────────────►│ Creates:
  │                                            │                            │  - ProcessPaymentRole
  │                                            │                            │  - ResourceRetrievalRole
  │                                            │                            │  - PaymentCredentialProvider
  │                                            │                            │  - PaymentManager
  │                                            │                            │  - PaymentConnector
  │ PaymentManager.create_payment_instrument() ───────────────────────────►│ Creates embedded wallet
  │ PaymentManager.create_payment_session() ──────────────────────────────►│ Creates budgeted session
  │ writes PAYMENT_MANAGER_ARN, INSTRUMENT_ID, SESSION_ID, … → ../.env       │
```

![Resource Hierarchy](images/resource_hierarchy.png)

![Role Separation](images/role_separation.png)

The AgentCore CLI provisions the shared stack once, and `agentcore deploy` mints the runtime
execution and resource-retrieval roles for you. Every command in this tutorial runs under your
ambient AWS credentials, which is ideal for learning and prototyping. For the stricter ControlPlane /
Management / ProcessPayment / ResourceRetrieval **4-role separation**, see the boto3
`setup_agentcore_payments.py` reference under [Alternatives](#alternatives).

## Prerequisites

- **AWS account + region** where AgentCore payments is available, and AWS CLI configured (`aws sts get-caller-identity`).
- **Python 3.10+** and dependencies:
  ```bash
  pip install -r requirements.txt
  ```
- **AgentCore CLI** (Node.js 20+) — this tutorial runs `agentcore` commands. These tutorials were
  validated against the 1.0.0-preview line and have not been re-verified on `0.30.0`; run `agentcore --version` to check yours:
  ```bash
  npm install -g @aws/agentcore@0.30.0
  node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
    || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
  agentcore --version | grep -q '^0\.' \
    || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
  ```
- **Wallet-provider credentials in `../.env`** — captured in Step 1 by running one of the
  `providers/*_account_setup.py` scripts. Coinbase CDP needs `COINBASE_API_KEY_ID`,
  `COINBASE_API_KEY_SECRET`, `COINBASE_WALLET_SECRET`; Stripe/Privy needs `PRIVY_APP_ID`,
  `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_ID`, `PRIVY_AUTHORIZATION_PRIVATE_KEY`.
- **A funded testnet wallet** — you fund it in Step 4, after the instrument is created.

The shared `.env` lives at `../.env` (one directory up, `00-getting-started/.env`). A starter is
provided: `cp .env.coinbase.sample ../.env` (or `.env.privy.sample`).

## Walkthrough

Run these steps top to bottom. Steps 1–2 use the AgentCore CLI to provision your shared payment
infrastructure; Steps 3–4 use the AgentCore SDK (`PaymentManager`) to create the per-user wallet and
session, then fund the wallet.

### Step 1 — Capture wallet-provider credentials (pick ONE provider)

These scripts walk you through the provider portal and write the credential keys into `../.env`.

```bash
python providers/coinbase_cdp_account_setup.py     # Coinbase CDP
#   or
python providers/stripe_privy_account_setup.py     # Stripe (Privy)
```

> **Using Coinbase Quick create?** You can skip `coinbase_cdp_account_setup.py` — Quick create provisions
> the CDP API key and Wallet secret for you in Step 2, so there are no keys to generate or paste. Still set
> the `.env` values below.

Then set `AWS_REGION`, `CREDENTIAL_PROVIDER_TYPE` (`CoinbaseCDP` or `StripePrivy`), `USER_ID`,
`LINKED_EMAIL` (a real inbox — used for the wallet and provider OTP), and `NETWORK`
(`ETHEREUM` or `SOLANA`) in `../.env`.

### Step 2 — Provision the shared stack with the AgentCore CLI

Scaffold a project to hold the payment configuration, add the payment manager and connector, then
deploy. Because the manager and connector are added *before* `agentcore deploy`, the CLI creates the
execution role with `ProcessPayment` permissions already attached — no separate IAM patch step.

```bash
# 1. Scaffold a project to hold the payment configuration
agentcore create --name PaymentSetup --framework Strands --protocol HTTP --model-provider Bedrock --memory none
cd PaymentSetup

# 2. Add a payment manager
agentcore add payment-manager --name MyPaymentManager --auto-payment true --default-spend-limit 1.00
```

> **`add payment-manager` defaults to auto-payment ON with a $10.00 spend cap.** If you omit
> `--auto-payment` and `--default-spend-limit`, the CLI defaults to `--auto-payment true` and
> `--default-spend-limit 10.00`. This command explicitly lowers that cap to `1.00` for the tutorial —
> when adapting this command for your own project, set `--default-spend-limit` deliberately rather
> than relying on the $10 default.

Add a payment connector for the provider you chose in Step 1 — run **one** of these:

```bash
# Coinbase CDP — Quick create (recommended): no keys, you authorize through Coinbase at deploy time
agentcore add payment-connector \
  --manager MyPaymentManager \
  --name MyCoinbaseConnector \
  --provider CoinbaseCDP \
  --provision-mode QUICK_CREATE
```

```bash
# Coinbase CDP — existing credentials: paste the keys captured in Step 1
agentcore add payment-connector \
  --manager MyPaymentManager \
  --name MyCoinbaseConnector \
  --provider CoinbaseCDP \
  --api-key-id <YOUR_CDP_API_KEY_ID> \
  --api-key-secret <YOUR_CDP_API_KEY_SECRET> \
  --wallet-secret <YOUR_CDP_WALLET_SECRET>
```

```bash
# OR Stripe/Privy:
agentcore add payment-connector \
  --manager MyPaymentManager \
  --name MyPrivyConnector \
  --provider StripePrivy \
  --app-id <YOUR_PRIVY_APP_ID> \
  --app-secret <YOUR_PRIVY_APP_SECRET> \
  --authorization-id <YOUR_PRIVY_AUTHORIZATION_ID> \
  --authorization-private-key <YOUR_PRIVY_PRIVATE_KEY_BASE64>
```

Validate the payment configuration, then deploy. `validate` catches a missing or bad credential value
early instead of letting deploy fail partway through. `deploy` provisions the IAM roles, credential
provider, manager, and connector.

```bash
# 3. Sanity-check the payment config, then deploy
agentcore validate
agentcore deploy -y

# 4. Read back the created resource ARNs/IDs
agentcore status --type payment
```

> **Using Coinbase Quick create?** `agentcore deploy` creates the connector in `PENDING_AUTHENTICATION`
> and prints an `authorizationUrl`. Open it, sign in to Coinbase, and grant access — the connector then
> moves to `READY`. The link is single-use and short-lived; if it expires before you finish, re-run
> `agentcore deploy` to issue a fresh one. Re-run `agentcore status --type payment` and confirm `READY` before continuing to Step 3.
> Quick create requires an active [Coinbase Wallets for AgentCore Payments Marketplace subscription](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-marketplace-subscription.html);
> without it, deploy fails with `SubscriptionRequiredException` (HTTP 403) and the error message includes the listing URL to subscribe.

From the `agentcore status --type payment` output, copy the **Payment Manager ARN** and **Payment
Connector ID** and export them (you'll pass them to the commands in Step 3):

```bash
export PAYMENT_MANAGER_ARN="arn:aws:bedrock-agentcore:...:payment-manager/..."
export PAYMENT_CONNECTOR_ID="payment-connector-..."
```

Also write `PAYMENT_MANAGER_ARN`, `PAYMENT_MANAGER_ID` (the ARN's last path segment), and
`PAYMENT_CONNECTOR_ID` to `../.env` — downstream tutorials read them from there.

> `--default-spend-limit 1.00` applies **only** to `agentcore invoke --auto-session`, where the CLI
> mints a session from the manager's default. In Step 3 you create an explicit budgeted session so
> you have a `SESSION_ID` the downstream tutorials can use.

### Step 3 — Create the per-user wallet and session with the AgentCore SDK

A wallet (instrument) and spending session are per-user resources, so you create one of each for your
`USER_ID` with the AgentCore SDK's `PaymentManager`. Use the same `USER_ID` you set in `../.env` in
Step 1 — every downstream tutorial reads that same value, so they all resolve to this wallet and
session. Pick the `network` that matches your `NETWORK` (`ETHEREUM` → Base Sepolia,
`SOLANA` → Solana Devnet), and use a real inbox for the linked email. Run this from a Python shell (or
paste into a script) with `PAYMENT_MANAGER_ARN` and `PAYMENT_CONNECTOR_ID` from Step 2 in your
environment:

```python
import os, uuid
from bedrock_agentcore.payments import PaymentManager

REGION = os.environ["AWS_REGION"]
PAYMENT_MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
PAYMENT_CONNECTOR_ID = os.environ["PAYMENT_CONNECTOR_ID"]
USER_ID = os.environ["USER_ID"]          # the same USER_ID from ../.env (Step 1)
NETWORK = os.environ.get("NETWORK", "ETHEREUM")
EMAIL = os.environ["LINKED_EMAIL"]       # a real inbox — used for the wallet and provider OTP

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

# Create the embedded wallet (instrument). Save paymentInstrumentId, walletAddress, and redirectUrl.
instrument = manager.create_payment_instrument(
    user_id=USER_ID,
    payment_connector_id=PAYMENT_CONNECTOR_ID,
    payment_instrument_type="EMBEDDED_CRYPTO_WALLET",
    payment_instrument_details={
        "embeddedCryptoWallet": {
            "network": NETWORK,
            "linkedAccounts": [{"email": {"emailAddress": EMAIL}}],
        }
    },
    client_token=str(uuid.uuid4()),
)
INSTRUMENT_ID = instrument["paymentInstrumentId"]
WALLET_ADDRESS = instrument["paymentInstrumentDetails"]["embeddedCryptoWallet"]["walletAddress"]
REDIRECT_URL = instrument.get("redirectUrl")   # WalletHub link used in Step 4 (Coinbase)
print("INSTRUMENT_ID:", INSTRUMENT_ID)
print("WALLET_ADDRESS:", WALLET_ADDRESS)
print("REDIRECT_URL:", REDIRECT_URL)

# Create a budgeted, time-bounded session. Save paymentSessionId.
session = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": "1.00", "currency": "USD"}},
    expiry_time_in_minutes=60,
)
SESSION_ID = session["paymentSessionId"]
print("SESSION_ID:", SESSION_ID)
```

Write `INSTRUMENT_ID`, the wallet address (`WALLET_ADDRESS`), and `SESSION_ID` (the
`paymentSessionId` from the session response) into `../.env` alongside the manager and connector
IDs. Downstream tutorials read them all via `utils.load_tutorial_env()`.

### Step 4 — Fund the wallet and grant delegated signing (once per user)

1. Fund the wallet at [faucet.circle.com](https://faucet.circle.com/) — pick **Base Sepolia** for
   `ETHEREUM`, **Solana Devnet** for `SOLANA` (~20 USDC covers all tutorials). Verify at
   `https://sepolia.basescan.org/address/<WALLET_ADDRESS>` for Ethereum.
2. Grant delegated signing so the agent can pay on the user's behalf:
   - **Coinbase** — open the WalletHub `REDIRECT_URL` printed in Step 3, sign in as
     `LINKED_EMAIL`, and grant signing.
   - **Stripe/Privy** — open the Privy reference frontend (`http://localhost:3000`), log in as
     `LINKED_EMAIL`, and choose **Connect agent → Give access**.

Until delegated signing is granted, payment attempts report
`Delegated signing grant is not active for the end user wallet.`

## What this setup does

All resource IDs now live in the shared `../.env`, so Tutorials 01–07 reuse the same manager,
connector, wallet, and session with no changes.

## Inspect / verify

```bash
# Run from inside PaymentSetup/ — lists managers, connectors, and live status
cd PaymentSetup
agentcore status --type payment
```

Confirm `../.env` now contains `PAYMENT_MANAGER_ARN`, `PAYMENT_MANAGER_ID`, `PAYMENT_CONNECTOR_ID`,
`CREDENTIAL_PROVIDER_TYPE`, `USER_ID`, `NETWORK`, `INSTRUMENT_ID`, `WALLET_ADDRESS`, and `SESSION_ID`.
The SDK `PaymentManager` covers both the session spend detail and the wallet balance:

```python
import os
from bedrock_agentcore.payments import PaymentManager

REGION = os.environ["AWS_REGION"]
PAYMENT_MANAGER_ARN = os.environ["PAYMENT_MANAGER_ARN"]
PAYMENT_CONNECTOR_ID = os.environ["PAYMENT_CONNECTOR_ID"]
INSTRUMENT_ID = os.environ["INSTRUMENT_ID"]
SESSION_ID = os.environ["SESSION_ID"]
NETWORK = os.environ["NETWORK"]
USER_ID = os.environ["USER_ID"]

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

# Session spend detail — remaining vs. maximum
session = manager.get_payment_session(user_id=USER_ID, payment_session_id=SESSION_ID)
print("available:", session["availableLimits"]["availableSpendAmount"])
print("max:", session["limits"]["maxSpendAmount"])

# Wallet balance — get_payment_instrument_balance takes the chain + token
chain = "BASE_SEPOLIA" if NETWORK == "ETHEREUM" else "SOLANA_DEVNET"
balance = manager.get_payment_instrument_balance(
    payment_connector_id=PAYMENT_CONNECTOR_ID,
    payment_instrument_id=INSTRUMENT_ID,
    chain=chain,
    token="USDC",
    user_id=USER_ID,
)
micro = int(balance["tokenBalance"]["amount"])   # micro-USDC
print(f"balance: {micro / 1_000_000:.2f} USDC")
```

> Payment sessions expire after `expiry_time_in_minutes` (60 min here). If this read returns a
> not-found error, the session expired — create a fresh one with `manager.create_payment_session(...)`.

> Re-running setup (or switching providers) can leave older `INSTRUMENT_ID` / `SESSION_ID` /
> `PAYMENT_CONNECTOR_ID` values in `../.env`. `load_tutorial_env()` resolves the active wallet from
> `CREDENTIAL_PROVIDER_TYPE`, so downstream tutorials use the right one — but if you want a clean
> slate, clear the stale keys.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `agentcore: command not found` | CLI not installed | `npm install -g @aws/agentcore@0.30.0` |
| `add payment-connector` fails on a missing credential | Required provider flag not provided | Re-check the credential keys in `../.env`; re-run with all flags |
| Payment Manager stuck in `CREATING` | IAM propagation | Wait ~2 min; if `CREATE_FAILED`, check the service role |
| Instrument status stays `CREATING` | Wallet provisioning is async | Ensure `LINKED_EMAIL` is a real address; keep polling |
| `Delegated signing grant is not active` | Consent step not completed | Do Step 4 (funding + signing) |
| Deploy fails with CDK bootstrap error | Account/region not bootstrapped | `cdk bootstrap aws://<account-id>/<region>` |

## Clean Up

> **Warning:** irreversible. Only after finishing all downstream tutorials.

1. Delete the payment instrument (wallet) with the SDK `PaymentManager`:

```python
import os
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(
    payment_manager_arn=os.environ["PAYMENT_MANAGER_ARN"],
    region_name=os.environ["AWS_REGION"],
)
manager.delete_payment_instrument(
    payment_instrument_id=os.environ["INSTRUMENT_ID"],
    payment_connector_id=os.environ["PAYMENT_CONNECTOR_ID"],
    user_id=os.environ["USER_ID"],
)
```

2. Remove the connector + manager from the CLI project, then deploy to tear down in AWS:

```bash
cd PaymentSetup
agentcore remove payment-connector --manager MyPaymentManager --name MyCoinbaseConnector -y   # or MyPrivyConnector
agentcore remove payment-manager --name MyPaymentManager -y
agentcore deploy -y            # `remove` only updates local config; deploy tears down the AWS resources
```

3. Remove the scaffolded runtime project:

```bash
agentcore remove all -y
```

Payment sessions expire automatically. If you used the boto3 `setup_agentcore_payments.py` path
instead, run its cleanup section and delete the four IAM roles + `/aws/vendedlogs/bedrock-agentcore/*`
log groups from the console.

## Alternatives

- **Raw boto3 reference (4-role IAM separation):** `python setup_agentcore_payments.py` provisions the
  identical stack via `bedrock-agentcore-control` and writes the **same `../.env` keys**, so
  downstream tutorials are unaffected. Use it to see the explicit ControlPlane / Management /
  ProcessPayment / ResourceRetrieval role model.
- **Multi-provider (Tutorial 07):** `multi_provider_setup.py` creates one manager with **both**
  connectors and writes `COINBASE_`/`PRIVY_`-prefixed `INSTRUMENT_ID` / `CONNECTOR_ID` /
  `WALLET_ADDRESS` keys. It reads the three IAM role ARNs (`CONTROL_PLANE_ROLE_ARN`,
  `MANAGEMENT_ROLE_ARN`, `RESOURCE_RETRIEVAL_ROLE_ARN`) that the boto3 `setup_agentcore_payments.py`
  writes to `../.env`. So for Tutorial 07: run **both** provider scripts in Step 1, then run **`python
  setup_agentcore_payments.py` first** (it creates the four IAM roles and writes their ARNs), and
  **only then** `python multi_provider_setup.py`.

## Next steps

You're ready for the downstream tutorials — all read the shared `../.env`:

- **[Tutorial 01 — Agents, payments, and limits](../01-agents-payments-and-limits/)** (start here)
- **[Tutorial 02 — Deploy to AgentCore Runtime](../02-deploy-to-agentcore-runtime/)**
- **[Tutorial 07 — Multi-agent payment orchestrator](../07-multi-agent-payment-orchestrator/)**
  (needs the multi-provider setup above)
- Full list: the [getting-started index](../README.md#tutorials).
