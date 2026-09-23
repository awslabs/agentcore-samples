# Enable Payment Limits on an Agent

## Overview

This tutorial builds a payment-enabled agent that accesses paid x402 endpoints on Coinbase Bazaar. Two notebooks show the same payment flow with different frameworks — proving AgentCore payments is framework-agnostic.

| Notebook | Framework | Payment handling |
|----------|-----------|-----------------|
| `strands_payment_agent.ipynb` | Strands Agents | `AgentCorePaymentsPlugin` (automatic, zero payment code) |
| `langgraph_payment_agent.ipynb` | LangGraph | `wrap_with_auto_402()` using `PaymentManager.generate_payment_header()` |

The payment infrastructure (PaymentManager, sessions, instruments, payment limits) is the same in both. Only the agent framework integration differs.

### What you'll learn

| Feature | What the tutorial demonstrates |
|---------|-------------------------------|
| Payment processing | Agent calls Coinbase Bazaar x402 endpoints, plugin/wrapper handles 402 automatically |
| Payment limits | Create sessions with budgets ($1.00, $0.50, $0.01), track spend, see overspend rejection |
| Built-in tools (Strands) | Agent queries its own budget, lists wallets, inspects instrument details at runtime |
| Wallet-agnostic design | Same agent code works with Coinbase CDP or Stripe (Privy) |

### Tutorial Details

| Information         | Details                                                         |
|:--------------------|:----------------------------------------------------------------|
| Tutorial type       | Conversational                                                  |
| Agent type          | Single                                                          |
| Agentic Framework   | Strands Agents + LangGraph                                      |
| LLM model           | Anthropic Claude Sonnet                                         |
| Tutorial components | PaymentManager, AgentCorePaymentsPlugin, x402 endpoints         |
| Example complexity  | Easy                                                            |
| SDK used            | bedrock-agentcore SDK, Strands Agents SDK, LangGraph             |

## Prerequisites

* Tutorial 00 completed (`.env` has manager ARN, connector ID, instrument ID)
* Wallet funded with testnet USDC from https://faucet.circle.com/
* For Strands: `pip install 'bedrock-agentcore[strands-agents]'`
* For LangGraph: `pip install langchain-aws langgraph bedrock-agentcore pydantic requests python-dotenv`

Sessions are created fresh in each notebook — no stale session from `.env` needed.

This tutorial works with either wallet provider (Coinbase CDP or Stripe/Privy). The agent code is the same; only the `.env` values from Tutorial 00 differ.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Verification

After running the notebook, verify payment limits are enforced by:

1. Checking the session spend output shows amounts deducted after each x402 call.
2. Confirming the overspend rejection message appears when the session budget is exhausted.

## Cleanup

Payment sessions expire automatically after their configured `expiryTimeInMinutes`. To delete all payment resources (Manager, Connector, Instrument), run the cleanup cell in Tutorial 00 after completing experimentation with all notebooks.

## Conclusion

This tutorial demonstrates payment-enabled agents using two frameworks. The Strands agent uses a plugin for automatic 402 handling, while the LangGraph agent uses a wrapper pattern. Payment limits are enforced at the infrastructure level regardless of the framework.
