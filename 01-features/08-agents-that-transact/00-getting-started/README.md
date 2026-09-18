# AgentCore payments — Getting Started Tutorials

Step-by-step Python tutorials for building payment-enabled AI agents with **Amazon Bedrock
AgentCore payments** —  protocol orchestration, configurable spend limits, and third-party
wallet integration (Coinbase CDP, Stripe/Privy).

> **Testnet by default.** Tutorials 00–07 use Base Sepolia (Ethereum) or Solana Devnet with free USDC
> from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.
>
> **Tutorial 09 is the exception.** It runs on Base mainnet and transfers **real USDC** from your
> wallet. It refuses to run until you opt in explicitly — read
> [its README](09-pay-per-use-with-upto/) first.

## How the pieces fit together  <a name="cli-vs-sdk"></a>

AgentCore payments gives you two complementary tools, each with a clear job. Learn this once and
every tutorial reads the same way:

> **The AgentCore CLI provisions your shared payment infrastructure. The AgentCore SDK
> (`PaymentManager`) is your application backend — it creates each end user's wallet and spending
> session, and settles x402 payments at request time.**

| Job | Tool | Role |
|---|---|---|
| Provision the payment manager, connector, gateway, runtime, and IAM roles | **AgentCore CLI** (`agentcore add …` / `deploy`) | Set up your shared infrastructure once; every tutorial reuses it |
| Create a per-user **wallet (instrument)** and **spending session**, sign x402 payment headers, check a wallet balance, and delete an instrument | **AgentCore SDK** (`PaymentManager`) | Your backend mints and manages these per end user, scoped to who you serve |
| Let an agent pay a 402 automatically | **AgentCore SDK** (`AgentCorePaymentsPlugin`) | Handles the pay-and-retry at request time, inside your app |

This mirrors how you'd build a real payment-enabled application: infrastructure is provisioned once
with the CLI, and your application backend uses the SDK to give each user a wallet, set their budget,
and pay on their behalf. **Tutorial 00 provisions the shared infrastructure; the later tutorials use
the SDK to build paying agents on top of it.**

## Start here

Start with Tutorial 00; then work through 01–09 (in order, or dip into any that interests you — each
stands alone). Each folder's `README.md` is a self-contained walkthrough — you run the `agentcore`
commands and Python snippets yourself and learn each piece as you go.

1. **Install the tools once:**
   ```bash
   pip install -r 00-setup-agentcore-payments/requirements.txt
   npm install -g @aws/agentcore          # AgentCore CLI (Node.js 20+)
   ```
2. **Open [Tutorial 00 — Set Up AgentCore payments](00-setup-agentcore-payments/)** and follow it end
   to end. You'll capture wallet-provider credentials, provision the shared payment stack with the
   `agentcore` CLI, create your first wallet and session with the SDK, and fund the wallet. Tutorial
   00 writes the shared resource IDs (`PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`, …) to the shared `.env`.
   Each downstream agent creates its own spending session in-code with the SDK
   (`manager.create_payment_session(...)`), so there's no session ID to carry between tutorials.
3. **Then open any downstream tutorial** ([01](01-agents-payments-and-limits/) is the recommended
   next step). Each one reads that shared `.env` and builds on it.

Every tutorial's README follows the same shape — a short **Reads / Does** strip, prerequisites, a
numbered walkthrough, an inspect step, troubleshooting, and clean-up — so once you've done Tutorial
00 the rest feel familiar.

## Tutorials

Run Tutorial 00 first; then 01–07 and 09 in any order. Each folder's README opens with a
**Reads / Does** strip so you can see its inputs and outputs at a glance.

| # | Folder | What you build | Provisioning |
|---|--------|----------------|:------------:|
| 00 | [`00-setup-agentcore-payments/`](00-setup-agentcore-payments/) | Payment manager, connector, IAM roles (CLI) + wallet & session (SDK) | CLI + SDK |
| 01 | [`01-agents-payments-and-limits/`](01-agents-payments-and-limits/) | Strands & LangGraph agents that pay x402 endpoints with budget limits | SDK |
| 02 | [`02-deploy-to-agentcore-runtime/`](02-deploy-to-agentcore-runtime/) | Deploy a payment agent to AgentCore Runtime | CLI |
| 03 | [`03-user-onboarding-wallet-funding/`](03-user-onboarding-wallet-funding/) | Per-user wallet onboarding, funding, delegation, balances | SDK |
| 04 | [`04-agent-with-coinbase-bazaar-via-gateway/`](04-agent-with-coinbase-bazaar-via-gateway/) | Discover 10,000+ paid MCP tools via AgentCore Gateway | CLI + SDK |
| 05 | [`05-agent-with-browser-tool-pay-for-content/`](05-agent-with-browser-tool-pay-for-content/) | Pay 402 paywalls inside a browser session | SDK |
| 06 | [`06-research-agent-with-payment-memory/`](06-research-agent-with-payment-memory/) | Add AgentCore Memory to skip redundant paid calls | SDK |
| 07 | [`07-multi-agent-payment-orchestrator/`](07-multi-agent-payment-orchestrator/) | Multiple agents, separate wallets, per-agent budgets | CLI + SDK |
| 09 | [`09-pay-per-use-with-upto/`](09-pay-per-use-with-upto/) | Pay a metered seller with the x402 `upto` scheme (real USDC on Base mainnet) | SDK |

MPP Tutorial is coming soon !

## Which tutorial do I need?

- **Just paying an API?** → 01 (local), then 02 (deploy).
- **Onboarding real users / managing their wallets?** → 03.
- **Agent discovers tools at runtime?** → 04 (Gateway).
- **Paying for web/article content?** → 05 (Browser).
- **Personalized agentic payments with memory?** → 06 (Memory).
- **Several agents with independent budgets?** → 07 (needs multi-provider setup).
- **Price not known until the work is done?** → 09 (`upto`, real funds on mainnet).

## Shared files

| File | Purpose |
|------|---------|
| `utils.py` | IAM role helper (`setup_payment_roles()`), `.env` read/write (`load_tutorial_env`, `update_env_file`), observability setup, display helpers |
| `.env` (git-ignored) | Shared config: Tutorial 00 writes resource IDs; every downstream tutorial reads them |

## Prerequisites

- Python 3.10+ and AWS CLI configured (`aws sts get-caller-identity`)
- AWS account with access to AgentCore payments, in a supported region: `us-east-1`, `us-west-2`,
  `eu-central-1`, `ap-southeast-2`
- Node.js 20+ and the AgentCore CLI (`npm install -g @aws/agentcore`) for Tutorials 00, 02, 04, 07
- Wallet-provider credentials (Coinbase CDP or Stripe/Privy) — captured in Tutorial 00

## Cleanup

> **Warning:** irreversible. Run after completing all tutorials.

Tutorial 00's cleanup tears down the shared stack:
```bash
cd 00-setup-agentcore-payments/PaymentSetup
agentcore remove payment-connector --manager MyPaymentManager --name MyCoinbaseConnector -y
agentcore remove payment-manager --name MyPaymentManager -y
agentcore deploy -y            # applies the removal in AWS
agentcore remove all -y        # removes the scaffolded runtime project
```
Delete the payment instrument first with the AgentCore SDK
(`manager.delete_payment_instrument(...)`, see Tutorial 00's Clean Up). Runtime/Gateway
deployments from 02/04/07 are removed with `agentcore remove all -y` in their project dirs. Payment
sessions expire automatically. If you used the boto3 `setup_agentcore_payments.py` reference-setup path instead,
run its cleanup section and delete the four IAM roles + `/aws/vendedlogs/bedrock-agentcore/*` log
groups from the console.
