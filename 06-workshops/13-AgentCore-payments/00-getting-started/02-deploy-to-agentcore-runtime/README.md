# Deploy Payment Agent to AgentCore Runtime

## Overview

Deploy a payment-enabled Strands agent to AgentCore Runtime with role separation and observability. The agent runs under a dedicated execution role — the plugin calls `ProcessPayment` on behalf of the agent within the budget set by the app backend. The agent (LLM) never calls `ProcessPayment` directly.

The agent code is stateless and wallet-agnostic. All payment context (manager ARN, session ID, instrument ID) comes from the invocation payload. The same deployed agent serves Coinbase CDP and Stripe (Privy) users without code changes.

### What you'll learn

| AgentCore payments feature | What this tutorial demonstrates |
|---------------------------|-------------------------------|
| Payment processing | Agent calls x402 endpoints, `AgentCorePaymentsPlugin` handles 402 automatically |
| Payment limits | App backend creates sessions with budgets, service enforces spend per session |
| Payment connection | PaymentManager + Connector from Tutorial 00, credentials in AgentCore Identity |
| Payment instrument | Embedded wallet passed to agent via invocation payload |
| Observability | Runtime traces on GenAI Observability Dashboard |

### Tutorial Details

| Information         | Details                                                                      |
|:--------------------|:-----------------------------------------------------------------------------|
| Tutorial type       | Task-based                                                                   |
| Agent type          | Single                                                                       |
| Agentic Framework   | Strands Agents                                                               |
| LLM model           | Anthropic Claude Sonnet                                                      |
| Tutorial components | AgentCore Runtime, AgentCorePaymentsPlugin, AgentCore CLI                    |
| Example complexity  | Medium                                                                       |
| SDK used            | bedrock-agentcore SDK, Strands Agents SDK, AgentCore CLI (`@aws/agentcore`)  |

## Prerequisites

* Tutorial 00 completed (`.env` has manager ARN, connector, instrument)
* Tutorial 01 completed (understand the local agent + plugin flow)
* Wallet funded with testnet USDC from https://faucet.circle.com/
* Python 3.10+
* Node.js 20+ (for the AgentCore CLI)
* [AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html) installed
* AWS CLI configured (`aws configure`)

This tutorial works with either wallet provider - Coinbase CDP or Stripe (Privy). The agent code is the same; only the `.env` values from Tutorial 00 differ.

> **Testnet only.** All code uses Base Sepolia with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Deployment Flow

```
agentcore create → agentcore dev → agentcore deploy → agentcore invoke
```

| Step | Command | What it does |
|------|---------|-------------|
| Install CLI | `npm install -g @aws/agentcore@0.30.0` | Install the AgentCore CLI |
| Scaffold | `agentcore create --name PaymentAgent` | Generate project structure |
| Test locally | `agentcore dev` | Start local dev server on :8080 |
| Deploy | `agentcore deploy` | Package + deploy to AWS via CDK |
| Invoke | `agentcore invoke '{...}'` | Call the deployed agent |
| Cleanup | `agentcore remove all -y` | Tear down all resources |

## Architecture

```
App Backend (ManagementRole)              AgentCore Runtime (Execution Role)
  │                                        ┌──────────────────────────────┐
  │ create_session(budget=$0.50)           │  payment_agent.py            │
  │                                        │  BedrockAgentCoreApp         │
  │── invoke(manager_arn, session_id, ──►  │  + AgentCorePaymentsPlugin   │
  │         instrument_id, prompt)         │                              │
  │                                        │  Plugin calls: ProcessPayment│
  │◄── result ────────────────────────     │  Cannot: CreateSession       │
  │                                        │  Cannot: Override budget     │
  │ get_session(check spend)               └──────────────────────────────┘
```

## File Structure

```
02-deploy-to-agentcore-runtime/
├── deploy_payment_agent.ipynb    # Step-by-step walkthrough
├── payment_agent.py              # Agent code (BedrockAgentCoreApp + plugin)
├── requirements.txt              # Dependencies (references shared wheels)
├── README.md
└── images/
```

## Quick Start (without notebook)

```bash
# Install CLI (requires Node.js 20+)
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

# Scaffold project
agentcore create --name PaymentAgent --framework Strands --protocol HTTP --model-provider Bedrock --memory none

# Copy agent code + deps into the project
cp payment_agent.py PaymentAgent/app/PaymentAgent/main.py
cp -r deps PaymentAgent/app/PaymentAgent/deps/

# Test locally
cd PaymentAgent
agentcore dev
# In another terminal: agentcore dev "Hello, what can you do?"

# Deploy to AWS
agentcore deploy

# Invoke
agentcore invoke '{"prompt": "...", "payment_manager_arn": "...", "user_id": "...", "payment_session_id": "...", "payment_instrument_id": "..."}'

# Cleanup
agentcore remove all -y
```

## Cleanup

> **Cost notice:** AgentCore Runtime deployments, payment sessions, and CloudWatch observability incur AWS charges.

AgentCore Runtime deployments incur charges for compute and storage. Remove when done:

```bash
cd PaymentAgent && agentcore remove all -y
```

This removes the Runtime deployment, CloudWatch log groups, and associated resources.

**Payment sessions** — The sessions created in this tutorial expire automatically after their configured `expiryTimeInMinutes` (60 minutes). No manual cleanup needed.

## Conclusion

This tutorial deploys a payment-enabled Strands agent to AgentCore Runtime with proper role separation. The deployed agent runs under ProcessPaymentRole and can only spend within budgets set by the app backend (ManagementRole).
