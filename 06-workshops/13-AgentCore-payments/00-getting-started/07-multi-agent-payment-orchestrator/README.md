# Multi-Agent Payment Orchestrator

## Overview

This tutorial builds a multi-agent system with per-agent budgets, multi-wallet support, and full spend attribution — then deploys it to AgentCore Runtime with role separation, observability, and online evaluation.

> See `multi_agent_payments.ipynb` for the complete step-by-step tutorial.

### What you'll learn

| AgentCore payments feature | What this tutorial demonstrates |
|---------------------------|-------------------------------|
| Payment processing | Two specialist agents call x402 endpoints independently, each with `AgentCorePaymentsPlugin` |
| Payment limits | Per-agent session budgets ($0.50 and $0.20), independent spend tracking, budget exhaustion handling |
| Wallet integration | One PaymentManager, two connectors (Coinbase CDP + Stripe Privy), two instruments — multi-wallet |
| Observability | Per-agent payment traces and budget progression on CloudWatch GenAI Observability Dashboard |

### Architecture

```
┌─────────────────────────────────────┐
│  App Backend (ManagementRole)       │
│  Creates Session A ($0.50, Coinbase)│
│  Creates Session B ($0.20, Privy)   │
│  Invokes Orchestrator               │
└──────────┬──────────────────────────┘
           │ payload: sessions + instruments
┌──────────▼──────────────────────────┐
│  AgentCore Runtime                  │
│  (ProcessPaymentRole)               │
│                                     │
│  Orchestrator (NO plugin)           │
│    ├── Research Agent               │
│    │   Coinbase wallet, Session A   │
│    ├── Discovery Agent              │
│    │   Privy wallet, Session B      │
│    └── check_budgets tool           │
└──────────┬──────────────────────────┘
           │
┌──────────▼──────────────────────────┐
│  AgentCore payments                 │
│  Session A ←→ Coinbase CDP          │
│  Session B ←→ Stripe Privy          │
│  Independent budget enforcement     │
└──────────┬──────────────────────────┘
           │
┌──────────▼──────────────────────────┐
│  CloudWatch + Evaluations           │
│  Per-agent payment traces           │
│  Online eval scores                 │
└─────────────────────────────────────┘
```

### Tutorial Details

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Task-based                                                                       |
| Agent type          | Multi-agent (orchestrator + 2 specialists)                                       |
| Agentic Framework   | Strands Agents (agents-as-tools pattern)                                         |
| LLM model           | Anthropic Claude Sonnet                                                          |
| Tutorial components | AgentCore payments, AgentCore Runtime, AgentCore CLI, AgentCore Evaluations      |
| Example complexity  | Advanced                                                                         |
| SDK used            | bedrock-agentcore SDK, Strands Agents SDK, AgentCore CLI (`@aws/agentcore`)      |

## Prerequisites

* Tutorial 00b completed (multi-provider `.env` with both Coinbase and Privy)
* Both wallets funded with testnet USDC from https://faucet.circle.com/
* AgentCore CLI: `npm install -g @aws/agentcore@0.30.0` (requires Node.js 20+)
* Docker installed (for container build during deploy)
* `pip install -r requirements.txt`

Your AWS credentials need the IAM permissions created by Tutorial 00 (`setup_payment_roles()`). If you completed Tutorial 00b successfully, you have the required permissions.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Files

| File | Description |
|------|-------------|
| `multi_agent_payments.ipynb` | Tutorial notebook (local + deploy + eval) |
| `payment_orchestrator.py` | Agent code for AgentCore Runtime (payload-based, stateless) |
| `requirements.txt` | Python dependencies |

## Cleanup

> **Cost notice:** AgentCore Runtime deployments, online evaluations, and CloudWatch observability incur AWS charges. Run cleanup when finished to avoid ongoing costs.

AgentCore Runtime and online evaluations incur charges. Remove when done:

```bash
cd PaymentAgent && agentcore remove all -y
```

This removes the Runtime deployment, evaluation configuration, CloudWatch log groups, and associated resources.

**Payment sessions** — Expire automatically after their configured `expiryTimeInMinutes` (60 minutes in this tutorial). No manual deletion needed.

## Conclusion

This tutorial demonstrates multi-agent payment orchestration with per-agent budgets, multi-wallet support, and full spend attribution. It shows how to build an orchestrator that coordinates specialist agents with independent payment sessions and handles budget exhaustion with intelligent failover.
