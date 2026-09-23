# Agent with Browser Tool — Pay for Gated Content

> **Pattern reference.** This notebook demonstrates the browser + payment architecture. To run it end-to-end, you need an x402-enabled content endpoint.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value. AgentCore Browser sessions may incur AWS charges based on usage duration.

> See `browser_paywall_payments.ipynb` for the complete step-by-step tutorial.

## Overview

AgentCore Browser enables agents to autonomously access paywalled websites that support x402, securely through the AgentCore Browser and payments combination. This tutorial builds a custom Strands `@tool` that uses AgentCore Browser (managed cloud Chromium) + Playwright to navigate to x402 endpoints, detect 402 responses, sign payments via `PaymentManager.generate_payment_header()`, and retry with proof headers — all within the same browser session.

### What you'll learn

| AgentCore payments feature | What this tutorial demonstrates |
|---------------------------|-------------------------------|
| Payment processing | `PaymentManager.generate_payment_header()` — the manual signing pattern for custom tools |
| Payment limits | Session budget enforcement on browser-based payments |
| Wallet integration | Same code works with Coinbase CDP or Stripe (Privy) — wallet-agnostic |

### Two payment patterns compared

| Pattern | Tool | Payment handling | Best for |
|---------|------|-----------------|----------|
| Plugin (Tutorial 01) | `http_request` or MCP tools | `AgentCorePaymentsPlugin` intercepts 402, retries externally | API endpoints, MCP tools |
| Browser (this tutorial) | Custom `browse_with_payment` | Tool handles 402 internally via Playwright, retries in same session | Browser-rendered content, paywalls |

Use the plugin pattern for API calls. Use the browser pattern when you need to maintain session state (cookies, auth tokens, DOM context) across the payment retry.

### Architecture

```
┌─────────────────────────────────┐
│  Strands Agent                  │
│  + browse_with_payment (@tool)  │
└──────────┬──────────────────────┘
           │
┌──────────▼──────────────────────┐
│  AgentCore Browser              │
│  BrowserClient → Chromium       │
│  Playwright CDP + interception  │
└──────────┬──────────────────────┘
           │ page.goto → 402 → pay → retry
┌──────────▼──────────────────────┐   ┌──────────────────┐
│  AgentCore payments             │──▶│ Wallet Provider   │
│  generate_payment_header()      │   │ Coinbase CDP      │
│  Session budget enforcement     │   │   — or —          │
│                                 │   │ Stripe Privy      │
└─────────────────────────────────┘   └──────────────────┘
```

### Tutorial Details

| Information         | Details                                                                 |
|:--------------------|:------------------------------------------------------------------------|
| Tutorial type       | Pattern reference                                                       |
| Agent type          | Single                                                                  |
| Agentic Framework   | Strands Agents                                                          |
| LLM model           | Anthropic Claude Sonnet                                                 |
| Tutorial components | AgentCore Browser, Playwright, AgentCore payments, x402                 |
| Example complexity  | Intermediate                                                            |
| SDK used            | bedrock-agentcore SDK (BrowserClient + PaymentManager), Strands Agents  |

## Prerequisites

* Tutorial 00 completed (`.env` exists with payment manager, instrument)
* Wallet funded with testnet USDC from https://faucet.circle.com/
* `pip install -r requirements.txt`
* `python -m playwright install chromium`
* An x402-enabled endpoint to browse

This tutorial works with either wallet provider you configured in Tutorial 00 (Coinbase CDP or Stripe/Privy). The agent code is the same regardless of your choice.

> **Testnet only.** All code uses Base Sepolia (Ethereum) with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Cleanup

> **Cost notice:** AgentCore Browser sessions incur charges per minute of browser runtime. Payment sessions are free but the underlying Payment Manager and Instruments incur standard AWS charges until deleted via Tutorial 00.

AgentCore Browser sessions (BrowserClient) expire automatically after the configured timeout. Payment sessions expire after their configured `expiryTimeInMinutes`. No manual cleanup is needed for sessions created in this tutorial.

## Conclusion

This tutorial demonstrates the browser + payment architecture pattern for accessing paywalled x402 content. Use this pattern when browser session state (cookies, auth tokens, DOM context) needs to persist across payment retries. For API-only endpoints, use the plugin pattern from Tutorial 01.
