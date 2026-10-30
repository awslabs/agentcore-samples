# Tutorial 07 — Multi-Agent Payment Orchestrator

| Information         | Details                                                              |
|:--------------------|:---------------------------------------------------------------------|
| Tutorial type       | Task-based, advanced                                                 |
| Agent type          | Multi-agent (orchestrator + 2 specialists, agents-as-tools)          |
| Agentic Framework   | Strands Agents                                                       |
| LLM model           | Anthropic Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`)       |
| Components          | AgentCore payments (multi-session), AgentCore Runtime, AgentCore CLI |
| Example complexity  | Advanced                                                             |

> **Reads** `PAYMENT_MANAGER_ARN`, `USER_ID`, `COINBASE_INSTRUMENT_ID`, `PRIVY_INSTRUMENT_ID`,
> `COINBASE_CONNECTOR_ID`, `PRIVY_CONNECTOR_ID`, `AWS_REGION` from the shared `.env`. **Does** create
> per-agent spending sessions in-code with the AgentCore SDK, run three local demos, then deploy the
> orchestrator to AgentCore Runtime with online evaluation. → [How the pieces fit together](../README.md#cli-vs-sdk)

## Overview

Build a multi-agent system with per-agent budgets, multi-wallet support, and full spend
attribution — then watch the orchestrator route work between specialists and fail over when one
agent's budget is exhausted. It uses one **PaymentManager** with two connectors (CoinbaseCDP +
StripePrivy), two **payment instruments** (wallets), and two **payment sessions** (one budget each).

The design gives you structural safety, stated positively: **the orchestrator only routes and
monitors — spending authority lives with the specialist agents that hold the payment plugins.** Each
specialist carries its own `AgentCorePaymentsPlugin` scoped to its own session and wallet, so budgets
stay isolated and every dollar is attributable to exactly one agent.

Two tools do complementary jobs here. The **AgentCore CLI** scaffolds, deploys, and evaluates the
runtime (`agentcore create` / `deploy` / `add online-eval` / `invoke`). The **AgentCore SDK** is your
application backend: `PaymentManager.create_payment_session(...)` mints each per-agent spending
session in-code, and each specialist's `AgentCorePaymentsPlugin` binds to its session ID and settles
the x402 payment at request time.

> **Billable resources.** `agentcore deploy` and online evaluation create real AWS resources billed
> per invocation. See [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/). Run
> Clean Up when finished.

> **Testnet only.** Wallets use Base Sepolia (network `ETHEREUM`) with free USDC from
> [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.

> **Supported regions:** `us-east-1`, `us-west-2`, `eu-central-1`, `ap-southeast-2`. Set
> `AWS_REGION` in the shared `.env` to one of these.

### Three demos (`multi_agent_payments.py`)

| Demo | Pattern | What it proves |
|------|---------|---------------|
| **Demo 1** | Spend Attribution | Two wallets, two budgets, full per-agent cost tracking |
| **Demo 2** | Budget Exhaustion + Failover | Orchestrator detects a payment rejection and reroutes to the healthy agent |
| **Demo 3** | Structural Safety | Orchestrator has `http_request` but no plugin — spending authority stays with the specialists |

![Payment Flow — Three Demos](images/payment_flow_three_demos.png)

## Architecture

![Architecture Overview](images/architecture_overview.png)

```
PaymentManager
  ├── CoinbaseCDP Connector → Research Agent  (Session A, $0.50)
  └── StripePrivy Connector → Discovery Agent (Session B, $0.20)

Orchestrator
  ├── research_agent.as_tool()  → Research Agent  (Coinbase plugin)
  ├── discovery_agent.as_tool() → Discovery Agent (Privy plugin)
  └── check_budgets             → monitors both sessions
  [routes and monitors only — spending lives with the specialists]
```

| Who | Does what | Role |
|-----|-----------|------|
| App backend | Verifies wallets, mints per-agent sessions in-code (`manager.create_payment_session`), invokes orchestrator | Your AWS credentials |
| Orchestrator | Routes tasks and monitors budgets | None (routing/monitoring only) |
| Research Agent | Calls paid endpoints, spends from Session A | ProcessPaymentRole (Coinbase wallet) |
| Discovery Agent | Calls paid endpoints, spends from Session B | ProcessPaymentRole (Privy wallet) |

`payment_orchestrator.py` is the runtime entrypoint (`BedrockAgentCoreApp` + `@app.entrypoint`): the
app backend passes two session IDs and two instrument IDs in the invocation payload, and each
specialist receives its own plugin scoped to its own budget.

## Prerequisites

- **Tutorial 00 shared stack + multi-provider setup completed.** In
  [`../00-setup-agentcore-payments/`](../00-setup-agentcore-payments/), run
  `setup_agentcore_payments.py` first (provisions the manager and base infrastructure), then
  `multi_provider_setup.py` (adds the second connector — one manager, two connectors: Coinbase +
  Privy). Together they write `PAYMENT_MANAGER_ARN`, `USER_ID`, `COINBASE_INSTRUMENT_ID`,
  `PRIVY_INSTRUMENT_ID`, `COINBASE_CONNECTOR_ID`, and `PRIVY_CONNECTOR_ID` to the shared `.env` (one
  directory up, at `00-getting-started/.env`). This tutorial reads them via
  `utils.load_tutorial_env()`.
- **Both wallets funded** with testnet USDC from [faucet.circle.com](https://faucet.circle.com/)
  (Base Sepolia), with delegated signing granted — both instruments must be `ACTIVE`.
- **Python 3.10+** and AWS CLI configured (`aws sts get-caller-identity`).
- **Node.js 20+ and the AgentCore CLI** (the runtime deploy uses it):
  ```bash
  npm install -g @aws/agentcore@0.30.0
  node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
    || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
  agentcore --version | grep -q '^0\.' \
    || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
  ```
- Python deps:
  ```bash
  pip install -r requirements.txt
  ```

## Walkthrough

### Step 1 — Run the local demos

Run all three demos locally against the live payment infrastructure:

```bash
python multi_agent_payments.py
```

What it does, in order:

1. Verifies your AWS identity and loads the multi-provider config from `.env` (fails fast with a
   clear `ValueError` if the multi-provider setup hasn't been run).
2. Confirms both instruments are `ACTIVE` via `PaymentManager.get_payment_instrument(...)`.
3. Mints three budgeted sessions in-code via `manager.create_payment_session(...)` — the research
   agent's ($0.50), the discovery agent's ($0.20), and a deliberately tiny one ($0.0005) that Demo 2
   uses to trigger a budget-exhaustion failover — and binds each to its specialist's
   `AgentCorePaymentsPlugin`. The budgets are simple constants near the top of the script.
4. Builds two specialist agents (each with its own `AgentCorePaymentsPlugin`) plus an orchestrator
   that routes and monitors, then runs Demo 1 → Demo 2 → Demo 3, printing a per-agent spend report
   after each.

The payment **instruments** (wallets) came from Tutorial 00's multi-provider setup; this tutorial
reads their IDs from `.env`. Each spend report reads live balances with
`manager.get_payment_session(user_id=..., payment_session_id=...)` — inspect
`["availableLimits"]["availableSpendAmount"]` for any session the same way in your own code.

### Step 2 — Test the runtime entrypoint locally (optional)

`payment_orchestrator.py` is the **deploy artifact** — the `BedrockAgentCoreApp` + `@app.entrypoint`
you'll ship to the Runtime in Step 3. It reads `PAYMENT_MANAGER_ARN` (and `AWS_REGION`) from the
environment rather than calling `load_dotenv`, which is exactly how it will run in the Runtime. To
exercise the `@app.entrypoint` on a local port, export the two values from the shared `.env` first:

```bash
export $(grep -E '^(PAYMENT_MANAGER_ARN|AWS_REGION)=' ../.env | xargs)
python payment_orchestrator.py
```

For the full three-demo walkthrough, use `multi_agent_payments.py` from Step 1 — it loads `.env`
and mints the per-agent sessions in-code.

### Step 3 — Deploy to AgentCore Runtime (CLI)

`agentcore create` scaffolds a runtime project; you wire `payment_orchestrator.py` in as the
entrypoint and declare its dependencies before deploying (this follows the same model as Tutorial 02):

```bash
# Scaffold the runtime project
agentcore create --name PaymentOrchestrator --framework Strands --protocol HTTP \
  --model-provider Bedrock --memory none
# (agentcore create --name PaymentOrchestrator --defaults is also valid)
cd PaymentOrchestrator

# Wire our entrypoint into the scaffold (overwrites the generated stub)
cp ../payment_orchestrator.py app/PaymentOrchestrator/main.py

# Declare the entrypoint's dependencies, then refresh the lockfile.
# Edit app/PaymentOrchestrator/pyproject.toml so its [project] dependencies include:
#   "bedrock-agentcore[strands-agents]", "strands-agents", "strands-agents-tools", "boto3"
rm -f app/PaymentOrchestrator/uv.lock
```

The entrypoint reads `PAYMENT_MANAGER_ARN` (and `AWS_REGION`) from the runtime environment, so give
the deployed runtime both values. Pull them from the shared getting-started `.env` into the
scaffold's environment file:

```bash
# from inside PaymentOrchestrator/, copy the two values into the scaffold's .env
grep -E '^(PAYMENT_MANAGER_ARN|AWS_REGION)=' ../../.env >> app/PaymentOrchestrator/.env
```

Now deploy — this provisions the IAM roles (`ProcessPaymentRole`, `ResourceRetrievalRole`) and the
runtime:

```bash
agentcore deploy -y
```

First-time deploy takes a few minutes while IAM roles propagate; subsequent deploys are faster.

### Step 4 — Add online evaluation (CLI)

Score every invocation automatically with built-in evaluators, then redeploy to apply:

```bash
agentcore add online-eval \
  --name PaymentMonitor \
  --runtime PaymentOrchestrator \
  --evaluator Builtin.GoalSuccessRate Builtin.ToolSelectionAccuracy Builtin.Helpfulness \
  --sampling-rate 100 \
  --enable-on-create
agentcore deploy -y
```

| Evaluator | Level | What it checks |
|-----------|-------|---------------|
| `Builtin.GoalSuccessRate` | Session | Did the orchestrator complete both research and discovery tasks? |
| `Builtin.ToolSelectionAccuracy` | Tool | Did it route to the right specialist for each task? |
| `Builtin.Helpfulness` | Trace | Was the spend report clear and useful? |

### Step 5 — Invoke the deployed orchestrator (CLI)

The app backend passes the session and instrument IDs (from Step 1 / your `.env`) in the payload.
The entrypoint unwraps this JSON and hands each specialist its scoped budget:

```bash
agentcore invoke '{"prompt": "Search Bazaar and call the found endpoints, then report spend.", "user_id": "test-user-001", "research_session_id": "<SESSION_A>", "research_instrument_id": "<COINBASE_INSTRUMENT_ID>", "discovery_session_id": "<SESSION_B>", "discovery_instrument_id": "<PRIVY_INSTRUMENT_ID>"}'
```

## What the agent does

See `payment_orchestrator.py` for the runtime wiring described in the Overview and Architecture
above. Two details worth calling out: the orchestrator exposes each specialist to itself as a tool
via `research_agent.as_tool()` / `discovery_agent.as_tool()`, and it holds no payment plugin of its
own — so the paying capability stays entirely with the specialists (structural safety by
construction).

## Inspect / verify

```bash
# Managers, connectors, and live payment status (run from the scaffolded project dir)
cd PaymentOrchestrator
agentcore status --type payment

# Runtime traces and logs
agentcore traces list
agentcore logs
```

Confirm these keys exist in `00-getting-started/.env`: `PAYMENT_MANAGER_ARN`, `USER_ID`,
`COINBASE_INSTRUMENT_ID`, `PRIVY_INSTRUMENT_ID`, `COINBASE_CONNECTOR_ID`, `PRIVY_CONNECTOR_ID`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ValueError: multi-provider config required` | The multi-provider setup hasn't been run | Run `setup_agentcore_payments.py` then `multi_provider_setup.py` in `../00-setup-agentcore-payments/` — they write the Coinbase + Privy IDs to `.env` |
| Instrument not `ACTIVE` | Wallet unfunded or delegated signing not granted | Fund at [faucet.circle.com](https://faucet.circle.com/) (Base Sepolia). Privy: open `localhost:3000`, log in as `LINKED_EMAIL`, choose **Connect agent**. Coinbase: enable Delegated Signing in the CDP Portal |
| Demo 2 failover doesn't trigger | The endpoint cost is below the tiny $0.0005 budget, so no rejection occurs | Point at a pricier endpoint — the demo uses `https://x402-test.genesisblock.ai/api/market-news` (~$0.002) |
| `{"error": "PAYMENT_MANAGER_ARN is not set ..."}` at invoke | The runtime environment is missing the ARN | Add `PAYMENT_MANAGER_ARN` and `AWS_REGION` to `app/PaymentOrchestrator/.env` (Step 3), then `agentcore deploy -y` |
| `agentcore: command not found` | AgentCore CLI not installed | `npm install -g @aws/agentcore@0.30.0` (Node.js 20+) |

## Clean Up

> **Warning:** irreversible.

```bash
# Remove the runtime deployment (from the scaffolded project dir)
cd PaymentOrchestrator && agentcore remove all -y
```

Payment **sessions** expire automatically (the demos set a 60-minute expiry). The shared payment
manager, connectors, and instruments are torn down by **Tutorial 00's Clean Up** — see
[`../00-setup-agentcore-payments/`](../00-setup-agentcore-payments/). To delete a payment
**instrument**, call the AgentCore SDK's `PaymentManager.delete_payment_instrument` (repeat for each
wallet, passing that wallet's connector id), then remove the connectors and manager with the
documented `remove` syntax and apply the teardown:

```python
import os

from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(
    payment_manager_arn=os.environ["PAYMENT_MANAGER_ARN"],
    region_name=os.environ["AWS_REGION"],
)
USER_ID = os.environ["USER_ID"]

# Delete each payment instrument (one per wallet, each with its own connector id)
for instrument_id, connector_id in (
    (os.environ["COINBASE_INSTRUMENT_ID"], os.environ["COINBASE_CONNECTOR_ID"]),
    (os.environ["PRIVY_INSTRUMENT_ID"], os.environ["PRIVY_CONNECTOR_ID"]),
):
    manager.delete_payment_instrument(
        payment_instrument_id=instrument_id,
        payment_connector_id=connector_id,
        user_id=USER_ID,
    )
```

```bash
# Then remove the connectors and manager, and apply the teardown
agentcore remove payment-connector --manager <MANAGER_NAME> --name <CONNECTOR_NAME> -y
agentcore remove payment-manager --name <MANAGER_NAME> -y
agentcore deploy -y   # remove updates local config; deploy applies the teardown in AWS
```

## Next steps

- **Provision the shared stack** → [Tutorial 00 — Setup](../00-setup-agentcore-payments/)
- **Discover 10,000+ paid MCP tools via Gateway** → [Tutorial 04](../04-agent-with-coinbase-bazaar-via-gateway/)
- **Skip redundant paid calls with Memory** → [Tutorial 06](../06-research-agent-with-payment-memory/)
- **End-to-end browser paywall use case** → `../../02-use-cases/pay-for-content-browser-use/`
