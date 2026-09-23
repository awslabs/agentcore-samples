# Research Agent with Payment Memory

> See `research_agent_with_memory.ipynb` for the complete step-by-step tutorial.

## Overview

This tutorial combines AgentCore payments and AgentCore Memory to build a research agent that gets smarter over time. The agent checks what it already knows before paying for fresh data — saving money across sessions.

### What you'll learn

| AgentCore payments feature | What this tutorial demonstrates |
|---------------------------|-------------------------------|
| Payment processing | `AgentCorePaymentsPlugin` handles x402 automatically for fresh data calls |
| Payment limits | Session budget ($0.20) enforced at the API level; memory optimizes spend within that budget |
| Wallet integration | Same code works with Coinbase CDP or Stripe (Privy) — wallet-agnostic |

### How payments + memory work together

| Without Memory | With Memory |
|---------------|-------------|
| Agent re-pays for same data every session | Agent checks memory first, pays only for new data |
| No cost awareness across sessions | Agent compares current vs past session costs |
| Every session starts cold | Agent remembers user preferences and tool quality |

### Architecture

```
┌─────────────────────────────────┐
│  Strands Agent                  │
│  + recall_user_context (@tool)  │
│  + http_request                 │
│  + AgentCorePaymentsPlugin      │
└──────┬──────────┬───────────────┘
       │          │
┌──────▼────┐  ┌──▼──────────────────┐
│  AgentCore│  │  AgentCore payments  │
│  Memory   │  │  ProcessPayment      │
│  (recall) │  │  Session budget      │
└───────────┘  └──────────┬──────────┘
                          │
               ┌──────────▼──────────┐
               │  Wallet Provider     │
               │  Coinbase CDP — or — │
               │  Stripe Privy        │
               └─────────────────────┘
```

Workflow: RECALL (check memory) → DECIDE (pay or skip) → FETCH (plugin handles 402) → REPORT (cost transparency)

### Tutorial Details

| Information         | Details                                                         |
|:--------------------|:----------------------------------------------------------------|
| Tutorial type       | Conversational                                                  |
| Agent type          | Single                                                          |
| Agentic Framework   | Strands Agents                                                  |
| LLM model           | Anthropic Claude Sonnet                                         |
| Tutorial components | AgentCore payments, AgentCore Memory, AgentCorePaymentsPlugin   |
| Example complexity  | Intermediate                                                    |
| SDK used            | bedrock-agentcore SDK, Strands Agents SDK                       |

## Prerequisites

* Tutorial 00 completed (`.env` exists with payment manager, instrument)
* Wallet funded with testnet USDC from https://faucet.circle.com/
* `pip install -r requirements.txt`

This tutorial works with either wallet provider you configured in Tutorial 00 (Coinbase CDP or Stripe/Privy). The agent code is identical regardless of your choice.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

### IAM permissions

The caller identity that runs this notebook needs AgentCore Memory permissions in addition to the payments permissions from Tutorial 00. On a local laptop with an admin profile this is automatic. On SageMaker or other restricted environments, attach the following actions to the execution role.

Scope the resource ARN to your AWS account and region. `CreateMemory` and `ListMemories` are account-level actions and require `Resource: "*"` — narrow them further with condition keys (e.g. `aws:RequestTag`) if needed.

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

## Files

| File | Description |
|------|-------------|
| `research_agent_with_memory.ipynb` | Tutorial notebook (local, memory + payments flow) |
| `requirements.txt` | Python dependencies |

## Cleanup

The memory resource is created by this tutorial and is deleted by the cleanup cell at the end of the notebook. Payment sessions expire automatically after their configured `expiryTimeInMinutes`. Payment resources (Manager, Connector, Instrument) belong to Tutorial 00 — delete them via the cleanup cell in Tutorial 00 when you are done with all tutorials.

AgentCore Memory may incur AWS charges based on storage and retrieval usage. Run the notebook cleanup cell to remove the memory resource when done.

## Conclusion

This tutorial combines AgentCore payments with AgentCore Memory to build an agent that gets smarter — and cheaper — over time. Memory optimizes spend within the session budget by recalling past data and user preferences, while the session budget remains the hard limit enforced at the API level.

## Next Steps

- **Tutorial 07** — `../07-multi-agent-payment-orchestrator/` — Multi-agent orchestration with per-agent budgets and provider-separated wallets
