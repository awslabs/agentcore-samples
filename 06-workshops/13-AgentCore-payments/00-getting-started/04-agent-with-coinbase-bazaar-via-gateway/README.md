# Integrate Your Agent with Coinbase Bazaar via AgentCore Gateway

## Overview

The Coinbase x402 Bazaar is an MCP marketplace exposing 10,000+ pay-per-use x402 endpoints. Agents discover tools via semantic search and pay per call using x402. This tutorial connects a Strands agent to the Bazaar through AgentCore Gateway, combining endpoint discoverability with automatic payment handling.

### What you'll learn

| AgentCore payments feature | What this tutorial demonstrates |
|---------------------------|-------------------------------|
| Endpoint discoverability | Discover paid MCP tools on Coinbase x402 Bazaar through AgentCore Gateway |
| Payment processing | Agent calls discovered tools, `AgentCorePaymentsPlugin` handles 402 automatically |
| Payment limits | Session budget tracks cumulative spend across multiple Bazaar tool calls |
| Wallet integration | Same code works with Coinbase CDP or Stripe (Privy) — only `.env` values differ |

### Architecture

```
┌─────────────────────────────────┐
│  🧑‍💻 Developer Code              │
│  Strands Agent                  │
│  + AgentCorePaymentsPlugin      │
│  + MCPClient (streamable HTTP)  │
└──────────┬──────────────────────┘
           │ MCP protocol
┌──────────▼──────────────────────┐
│  🔀 AgentCore Gateway            │
│  Target: Coinbase x402 Bazaar   │
│  (No outbound auth)             │
└──────────┬──────────────────────┘
           │
┌──────────▼──────────────────────┐
│  🌐 Coinbase x402 Bazaar        │
│  search_resources → discover    │
│  proxy_tool_call  → call + pay  │
└──────────┬──────────────────────┘
           │ HTTP 402 → pay → retry
┌──────────▼──────────────────────┐   ┌──────────────────┐
│  ☁️ AgentCore payments           │──▶│ 🏦 Wallet Provider│
│  Payment Manager + Session      │   │ Coinbase CDP     │
│  Payment Instrument             │   │   — or —         │
│  ProcessPayment (sign + proof)  │   │ Stripe Privy     │
└─────────────────────────────────┘   │ (routed by       │
                                      │  PaymentConnector)│
                                      └──────────────────┘
```

### Tutorial Details

| Information         | Details                                                         |
|:--------------------|:----------------------------------------------------------------|
| Tutorial type       | Task-based                                                      |
| Agent type          | Single                                                          |
| Agentic Framework   | Strands Agents                                                  |
| LLM model           | Anthropic Claude Sonnet                                         |
| Tutorial components | AgentCore Gateway, Coinbase Bazaar MCP, AgentCorePaymentsPlugin |
| Example complexity  | Intermediate                                                    |
| SDK used            | AgentCore CLI (`@aws/agentcore`), bedrock-agentcore SDK, Strands Agents SDK |

## Prerequisites

* Tutorial 00 completed (`.env` exists)
* Wallet funded with testnet USDC from https://faucet.circle.com/
* AgentCore CLI: `npm install -g @aws/agentcore@0.30.0` (requires Node.js 20+)
* AWS CLI configured (`aws configure`)

This tutorial works with either wallet provider you configured in Tutorial 00 (Coinbase CDP or Stripe/Privy). The agent code is the same regardless of your choice.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Gateway Setup

### Option A: AgentCore Console (recommended)

1. Open the [Amazon Bedrock AgentCore console](https://console.aws.amazon.com/bedrock-agentcore/)
2. Navigate to Gateway → Create Gateway → Add Target
3. Target type: **Integrations**
4. Select **Coinbase x402 Bazaar**
5. No outbound auth needed (No Authorization is the default)

### Option B: AgentCore CLI

```bash
agentcore create --name BazaarAgent --defaults
agentcore add gateway --name BazaarGateway
agentcore add gateway-target \
  --name CoinbaseBazaar \
  --type mcp-server \
  --endpoint https://api.cdp.coinbase.com/platform/v2/x402/discovery/mcp \
  --gateway BazaarGateway
agentcore deploy -y
agentcore fetch access --name BazaarGateway --type gateway
```

Add the `GATEWAY_URL` from the output to your `.env` file.

## Cleanup

> **Cost notice:** AgentCore Gateway incurs AWS charges for requests and data transfer. Run cleanup when finished to avoid ongoing costs.

Remove the Gateway when done:

```bash
agentcore remove gateway --name BazaarGateway -y
```

Payment sessions expire automatically. Payment resources are managed via Tutorial 00's cleanup.

## Conclusion

This tutorial integrates an agent with Coinbase Bazaar through AgentCore Gateway, combining MCP-based tool discoverability with automatic x402 payment handling. The Gateway pattern provides centralized management of paid MCP tools while the AgentCorePaymentsPlugin handles payment logic automatically.
