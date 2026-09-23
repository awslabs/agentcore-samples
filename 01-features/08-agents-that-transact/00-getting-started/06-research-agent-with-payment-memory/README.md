# Tutorial 06 — Research Agent with Payment Memory

| Information         | Details                                                              |
|:--------------------|:---------------------------------------------------------------------|
| Tutorial type       | Conversational                                                       |
| Agent type          | Single                                                               |
| Agentic Framework   | Strands Agents                                                       |
| LLM model           | Anthropic Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`)       |
| Components          | AgentCore payments, AgentCore Memory, `AgentCorePaymentsPlugin`      |
| Example complexity  | Intermediate                                                         |

> **Reads** the shared `.env` from Tutorial 00 (`PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`, `PAYMENT_CONNECTOR_ID`, `USER_ID`, `NETWORK`, `AWS_REGION`). **Does**: creates an AgentCore Memory resource, mints a payment session, runs a Strands research agent that recalls prior research and skips re-paying, then deletes the Memory resource on exit. → [How the pieces fit together](../README.md#cli-vs-sdk)

## Overview

In earlier tutorials the agent is stateless — every session starts from scratch and pays from scratch. This tutorial adds **AgentCore Memory** so a Strands agent builds intelligence across sessions: it recalls topics it already paid to research (and skips re-paying), learns user preferences (budget tolerance, topic interests), and tracks which endpoints were useful versus expensive.

The runnable script creates one AgentCore **Memory** resource (semantic strategy, namespace `/actor/{USER_ID}/facts/`), a **payment session** ($0.20 / 60 min) via `PaymentManager`, and a Strands `Agent` wired with a `recall_user_context` tool, `http_request`, and the `AgentCorePaymentsPlugin`. Before each paid call the agent recalls prior research, decides per topic whether to reuse memory or pay for fresh data, and reports cost transparently. The shared payment stack (manager, connector, instrument) comes from [Tutorial 00](../00-setup-agentcore-payments/).

> **Billable resources.** AgentCore Memory incurs AWS charges for storage and retrieval, and each x402 call spends testnet USDC from your session budget. See [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/). The script deletes the Memory resource on exit (including on crash).

> **Testnet only.** Uses Base Sepolia (`NETWORK=ETHEREUM`) or Solana Devnet (`NETWORK=SOLANA`) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.

> **Supported regions:** `us-east-1`, `us-west-2`, `eu-central-1`, `ap-southeast-2`. Set `AWS_REGION` in the shared `.env` to one of these.

## Architecture

```
Strands Agent
  + recall_user_context (@tool)
  + http_request
  + AgentCorePaymentsPlugin
       │              │
       │              │
  AgentCore        AgentCore payments
  Memory           ProcessPayment
  (recall)         Session budget
                          │
                   Wallet Provider
                   Coinbase CDP — or — Stripe Privy
```

Workflow per request: **RECALL** (search memory) → **DECIDE** (pay or skip) → **FETCH** (plugin handles the 402) → **REPORT** (cost transparency).

### How Payments + Memory work together

```
Session 1 (new user)                    Session 2 (returning user)
  │                                       │
  │ "Research renewable energy outlook"    │ "Research renewable energy AND AI market trends"
  │                                       │
  ├─► Pay $0.05 — renewable energy        ├─► Recall per topic:
  ├─► Return summary                      │     • renewable energy → already in memory ✓
  │                                       │     • AI market trends   → not in memory ✗
  │                                       ├─► Skip payment for renewable energy (free)
  │                                       ├─► Pay $0.05 only for AI market trends
  │                                       ├─► Return both summaries + savings report
  │                                       │
  └─► Memory extracts:                    └─► Result: paid $0.05 instead of $0.10 — memory saved $0.05
      • renewable energy researched ($0.05)
```

### Two layers of budget control

| Layer | Controls | Enforced by |
|-------|----------|-------------|
| **Session budget** ($0.20, 60 min expiry) | Hard ceiling — cannot be exceeded | AgentCore payments service (IAM + API) |
| **Memory intelligence** | Soft optimization — skip redundant paid calls | Agent logic (system prompt + recall tool) |

Budget enforcement is structural; memory is additive intelligence layered on top. The agent code is wallet-agnostic — the same code runs whether Tutorial 00 configured Coinbase CDP or Stripe/Privy; only the `.env` values differ.

## Choosing how to provision Memory

AgentCore gives you two ways to create the Memory resource, and both are first-class:

- **AgentCore CLI** — `agentcore add memory --name ResearchFacts --strategies SEMANTIC --expiry 30` provisions a semantic memory in one line. This is the right choice for most agents.
- **AgentCore SDK** — `MemoryControlPlaneClient.create_memory(...)` lets you set a **custom per-user `namespaceTemplates`** (`/actor/{USER_ID}/facts/`) and hands you the returned `MEMORY_ID` to use with `MemoryClient` for `batch_create_memory_records` and `retrieve_memory_records`.

This tutorial needs that fine-grained namespace control (memory is scoped per end user, `/actor/{USER_ID}/facts/`), so it uses the SDK path. The payment session is minted with the `PaymentManager` SDK for the same reason — sessions are per-user, so your backend creates them scoped to the user you serve, with a custom budget and expiry.

## Prerequisites

- **Tutorial 00 completed** — the shared `.env` (one directory up, at `00-getting-started/.env`) is populated with `PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`, `PAYMENT_CONNECTOR_ID`, `USER_ID`, `NETWORK`, and `AWS_REGION`. This tutorial reads them via `utils.load_tutorial_env()`.
- **Funded wallet** — the instrument from Tutorial 00 is `ACTIVE` and holds testnet USDC from [faucet.circle.com](https://faucet.circle.com/). (The script asserts `status == ACTIVE` and exits early otherwise.)
- **IAM permissions for AgentCore Memory** in addition to Tutorial 00's payments permissions (see below).
- **Python deps** for this tutorial:
  ```bash
  pip install -r requirements.txt
  ```

This tutorial's runnable flow is pure Python/SDK — the shared payment infrastructure already exists from Tutorial 00, so no `agentcore` CLI commands are needed to run it.

### IAM permissions for AgentCore Memory

The caller identity running the script needs Memory permissions on top of Tutorial 00's payments permissions. On a laptop with an admin profile this is automatic. On SageMaker or other restricted environments, attach the policy below (scope the resource ARN to your account/region). `CreateMemory` and `ListMemories` are account-level actions and require `Resource: "*"`.

```json
[
  {
    "Effect": "Allow",
    "Action": [
      "bedrock-agentcore:CreateMemory",
      "bedrock-agentcore:ListMemories"
    ],
    "Resource": "*"
  },
  {
    "Effect": "Allow",
    "Action": [
      "bedrock-agentcore:GetMemory",
      "bedrock-agentcore:DeleteMemory",
      "bedrock-agentcore:BatchCreateMemoryRecords",
      "bedrock-agentcore:RetrieveMemoryRecords"
    ],
    "Resource": "arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:memory/*"
  }
]
```

Without these, `create_memory` returns `AccessDeniedException` in Step 3.

## Walkthrough

### Step 1 — Run the research agent

Everything the agent needs already exists from Tutorial 00, so the whole flow runs with one command:

```bash
python research_agent_with_memory.py
```

The rest of this section describes exactly what the script does, in order; the SDK snippets
match the code and use the repo import conventions `from bedrock_agentcore.payments import PaymentManager`
and `from bedrock_agentcore.memory import MemoryClient, MemoryControlPlaneClient`.
The script loads the resource IDs Tutorial 00 wrote to `.env` (`PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`,
`USER_ID`, region; `MODEL_ID` is optional, defaulting to `us.anthropic.claude-sonnet-4-6`), verifies
the instrument is `ACTIVE`, then continues with the session and Memory:

### Step 2 — Verify the instrument and create the session

After confirming the instrument is `ACTIVE`, the script creates a $0.20 / 60-minute session in-code
with the `PaymentManager` SDK and uses the returned `paymentSessionId` for the rest of the run:

```python
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
assert manager.get_payment_instrument(user_id=USER_ID, payment_instrument_id=INSTRUMENT_ID)["status"] == "ACTIVE"

session = manager.create_payment_session(
    user_id=USER_ID,
    limits={"maxSpendAmount": {"value": "0.20", "currency": "USD"}},
    expiry_time_in_minutes=60,
)
session_id = session["paymentSessionId"]
```

### Step 3 — Create AgentCore Memory (custom per-user namespace)

Because memory here is scoped per end user, create it with the AgentCore SDK's `MemoryControlPlaneClient.create_memory` so you can set a custom `namespaceTemplates` and use the returned `MEMORY_ID` directly. `wait_for_active=True` blocks until the resource is `ACTIVE` (typically 30–90s), so downstream record operations are safe to run:

```python
from bedrock_agentcore.memory import MemoryControlPlaneClient

memory_ctl = MemoryControlPlaneClient(region_name=REGION)
memory = memory_ctl.create_memory(
    name=f"research_memory_{...}",
    description="Research agent memory - tracks topics, costs, and preferences",
    event_expiry_days=30,
    strategies=[{
        "semanticMemoryStrategy": {
            "name": "ResearchFacts",
            "namespaceTemplates": [f"/actor/{USER_ID}/facts/"],
        }
    }],
    wait_for_active=True,
)
memory_id = memory["id"]  # used by batch_create_memory_records / retrieve_memory_records
```

> For a standard agent that doesn't need a custom namespace, the AgentCore CLI one-liner is the simpler path — see [Choosing how to provision Memory](#choosing-how-to-provision-memory).

### Step 4 — Hydrate memory (simulate a returning user)

Pre-populate the memory with four records via `batch_create_memory_records` so the agent behaves like it has research history — **two prior-research entries dated yesterday** (Seattle weather $0.05, renewable energy outlook $0.05), plus a **user-profile** record (interests, budget preference) and a **tool-preference** record (which endpoints to prefer or avoid). Yesterday's date keeps the cached research inside the 7-day freshness window so the agent treats it as authoritative. The records write into `/actor/{USER_ID}/facts/`; after the batch, wait ~25s for indexing before semantic search returns them.

### Step 5 — Build the Strands agent

Wire up an `Agent` with three capabilities: `recall_user_context` (searches memory before paying), `http_request` (calls paid endpoints), and `AgentCorePaymentsPlugin` (settles the x402 402 → payment → retry automatically). The plugin is configured with the manager ARN, user, instrument, session, region, and network preferences:

```python
from bedrock_agentcore.payments.integrations.strands import (
    AgentCorePaymentsPlugin,
    AgentCorePaymentsPluginConfig,
)
from strands import Agent
from strands.models import BedrockModel

payment_plugin = AgentCorePaymentsPlugin(
    config=AgentCorePaymentsPluginConfig(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        user_id=USER_ID,
        payment_instrument_id=INSTRUMENT_ID,
        payment_session_id=session_id,
        region=REGION,
        network_preferences_config=["eip155:84532", "base-sepolia"],  # or Solana devnet
    )
)

agent = Agent(
    model=BedrockModel(model_id=model_id, streaming=True),
    tools=[recall_user_context, http_request],
    plugins=[payment_plugin],
    system_prompt=SYSTEM_PROMPT,
)
```

### Step 6 — Run four queries

The agent runs four queries that demonstrate memory-aware spending:

1. **Familiar topic** — asks for Seattle weather; expects a memory hit and reuses prior research instead of paying.
2. **Budget recall** — asks what it has researched, its budget preference, and last session's spend; answered entirely from memory.
3. **Partial memory hit (the payoff)** — asks for two topics; renewable energy is in memory (reused free), the new topic is fetched via a paid x402 call. The agent reports source, resource URL, and price per topic, then totals savings.
4. **Session recap with savings** — enumerates each request, marks memory vs paid, and compares total spend to the prior session and to fetching everything fresh.

### Step 7 — Check session spend

Read the remaining budget with the `PaymentManager` SDK:

```python
info = manager.get_payment_session(user_id=USER_ID, payment_session_id=session_id)
print(info["availableLimits"]["availableSpendAmount"], "of", info["limits"]["maxSpendAmount"])
```

### Step 8 — Budget enforcement (try it)

To prove the budget is a structural hard ceiling, set `SESSION_BUDGET = "0.0001"` near the top of
`research_agent_with_memory.py` (smaller than any priced x402 resource) and re-run the agent. The
session is minted in-code by `manager.create_payment_session`, so no extra setup is needed — the
paid resource call is rejected at the service level regardless of what the LLM decides:

```python
# research_agent_with_memory.py
SESSION_BUDGET = "0.0001"   # was "0.20"; re-run to watch enforcement reject a paid call
```

### Step 9 — View payment traces

The script prints the Amazon CloudWatch GenAI Observability Dashboard URL for your region, where you can inspect payment success rates, session spend, and transaction latency.

### Step 10 — Cleanup (in `finally`)

The Memory resource is deleted whether the run succeeds or crashes. Payment sessions expire on their own after their expiry time; the Manager, Connector, and Instrument belong to Tutorial 00.

> If a Python dependency is missing, the script prints the exact `pip install` command and exits before any AWS resources are created.

## What the agent does

- **`recall_user_context` (`@tool`)** wraps `RetrieveMemoryRecords`. The system prompt mandates a recall before any paid call, once per distinct topic.
- **Freshness rule** — a memory hit dated within 7 days is treated as authoritative; the agent only pays when memory is missing, stale, or the user asks for an update.
- **Two-step paid-call pattern** — the Coinbase x402 *discovery search* endpoint is a free catalog; only calling one of the `resource` URLs it returns triggers the 402 → payment → retry flow the plugin handles.
- **Transparent reporting** — per topic, the agent states whether the answer came from memory or a fresh paid call, the resource URL paid, and the actual price, then totals savings.

## Inspect / verify

- **Payment stack & live status** (`agentcore status` is project-scoped — run it from Tutorial 00's scaffolded project dir):
  ```bash
  cd ../00-setup-agentcore-payments/PaymentSetup && agentcore status --type payment
  ```
- **Session spend** — read the remaining budget for a session with the `PaymentManager` SDK (use the `paymentSessionId` the script printed):
  ```python
  from bedrock_agentcore.payments import PaymentManager

  manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)
  info = manager.get_payment_session(user_id=USER_ID, payment_session_id=SESSION_ID)
  print(info["availableLimits"]["availableSpendAmount"])
  ```
- **Traces** — open the CloudWatch GenAI Observability Dashboard URL the script prints.
- **`.env` keys** — confirm `PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`, `USER_ID`, `NETWORK`, `AWS_REGION` are present (written by Tutorial 00).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `create_memory` returns `AccessDeniedException` | Caller identity missing Memory permissions | Attach the IAM policy from the Prerequisites section (add to the SageMaker execution role if applicable) |
| Instrument assertion fails (`Instrument is <status>`) | Wallet not `ACTIVE` / not funded or delegated | Complete funding + delegated signing in Tutorial 00/03 |
| Memory stays `CREATING` past ~5 min | Slow index build or failure | Call `get_memory` and inspect `failureReason`; check the `bedrock-agentcore` CloudWatch log group |
| `Delegated signing grant is not active` at a paid call | End user hasn't granted delegated signing | Grant it as in Tutorial 00/03 (WalletHub consent / Privy Connect agent) |
| Budget rejection in Step 8 | Expected — $0.0001 session cannot cover any resource call | This is the enforcement demo, not an error |
| `invalid_exact_evm_transaction_failed` / `Settlement failed` | Transient on-chain failure | Retry — funds aren't debited on a failed attempt |

## Clean Up

The script deletes the Memory resource in its `finally` block, so normal runs clean up automatically. Payment sessions expire on their own after `expiryTimeInMinutes`.

If the script aborts before cleanup, delete the Memory resource by hand with the same AgentCore SDK client the script uses:

```python
from bedrock_agentcore.memory import MemoryControlPlaneClient

MemoryControlPlaneClient(region_name=REGION).delete_memory(memory_id="<MEMORY_ID>")
```

The payment **Manager**, **Connector**, and **Instrument** belong to Tutorial 00 — tear them down there (CLI `agentcore remove …` for the manager/connector, SDK instrument delete) when you're finished with all tutorials.

## Next steps

- **Tutorial 07** — [`../07-multi-agent-payment-orchestrator/`](../07-multi-agent-payment-orchestrator/) — multiple agents with per-agent budgets and provider-separated wallets (CLI + SDK; needs multi-provider setup).
- **Use case: Browser paywall** — [`../../02-use-cases/pay-for-content-browser-use/`](../../02-use-cases/pay-for-content-browser-use/) — end-to-end use case with a deployable x402 paywall server on AgentCore Runtime.