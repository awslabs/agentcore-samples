# Build a new agent that can transact

Scaffold a new agent with the AgentCore CLI and add x402 payment capability so it
can pay for premium APIs, MCP tools, or web content autonomously.

## Prerequisites

See the [parent README](../README.md) for environment setup and plugin installation.

## Prompt

Point your coding assistant at this folder and use:

> *"Create a new agent that can fetch premium weather data and add AgentCore x402 payment capability."*

The assistant reads [`../AGENTS.md`](../AGENTS.md), loads the `aws-agents` payments
skill, and drives the entire process. It will pause only for your provider
credentials and wallet funding.

## What happens

The skill (via the assistant) will:

1. Verify your CLI, credentials, and region
2. Scaffold a new agent project (`agentcore create`)
3. Create the payment manager and connector (you enter credentials manually)
4. Deploy payment resources to AWS
5. Wire payments into your agent (native plugin for Strands/LangGraph, or generic
   tool for other frameworks)
6. Provision a per-user wallet and budget
7. Ask you to authorize and fund the wallet
8. Run a test fetch to confirm everything works

## Verify

Run the agent and prompt:

```
Fetch https://sandbox.node4all.com/v1/x402-test and tell me what you find.
```

Expect `402` -> payment -> `200 OK`. Cleanup and debugging: see the
[parent README](../README.md#cleanup) and [`../AGENTS.md`](../AGENTS.md).
