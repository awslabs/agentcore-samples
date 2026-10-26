# Amazon Bedrock AgentCore payments — Tutorials

Step-by-step Jupyter notebook tutorials for building payment-enabled AI agents with **Amazon Bedrock AgentCore payments**.

AgentCore payments is an Amazon Bedrock AgentCore capability that provides secure, instant microtransaction payments for AI agents to access paid APIs, MCP servers, and content. It handles payment orchestration for the x402 protocol, configurable payment limits, and third-party wallet integration with Coinbase CDP and Stripe (Privy) stablecoin wallets.

**Target Audience**: This tutorial is designed for AI agent developers who want to enable their agents to autonomously perform x402 payments when accessing paid services.

> **Testnet only.** All tutorials use Base Sepolia (Ethereum) or Solana Devnet with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## Prerequisites

- Python 3.10+
- AWS CLI configured (`aws sts get-caller-identity` to verify) with minimum required set of permissions
- AWS account with access to AgentCore payments
- Jupyter (`pip install jupyter`)

## Choose Your Path

There are two paths through these tutorials depending on whether you use one wallet provider or both.

### Path A: Single provider (Tutorials 00–06)

Use this path if you want to learn AgentCore payments with one wallet provider. Most developers start here.

```
1. Pick ONE provider and run its setup guide:
      providers/coinbase_cdp_account_setup.ipynb   ← writes Coinbase keys to .env
   OR providers/stripe_privy_account_setup.ipynb    ← writes Privy keys to .env

2. Run Tutorial 00 (setup_agentcore_payments.ipynb)
      Reads your provider keys from .env
      Creates IAM roles, PaymentManager, Connector, Instrument, Session
      Writes resource IDs back to .env

3. Run Tutorials 01–06 in any order
      Each loads .env and uses the resources Tutorial 00 created
```

The `.env` file is the shared config. The provider notebook writes credentials, Tutorial 00 writes resource IDs, and downstream tutorials read both. Do not run both provider notebooks — the second one overwrites `CREDENTIAL_PROVIDER_TYPE` and Tutorial 00 uses whichever was set last.

### Path B: Multi-provider (Tutorial 07)

Tutorial 07 (multi-agent orchestrator) uses two wallets — one Coinbase, one Privy — with separate budgets per agent. This requires a different setup:

```
1. Run BOTH provider setup guides:
      providers/coinbase_cdp_account_setup.ipynb   ← writes COINBASE_* keys to .env
      providers/stripe_privy_account_setup.ipynb    ← writes PRIVY_* keys to .env

2. Run Tutorial 00b (00b_multi_provider_setup.ipynb) instead of Tutorial 00
      Creates one PaymentManager with two Connectors (Coinbase + Privy)
      Creates two Instruments (one per provider)
      Writes prefixed resource IDs to .env (COINBASE_INSTRUMENT_ID, PRIVY_INSTRUMENT_ID, etc.)

3. Run Tutorial 07
      Reads the prefixed keys and assigns each agent its own wallet + budget
```

You can also run Tutorials 01–06 after Path B — they detect the multi-provider `.env` and pick the first available provider automatically.

## Getting Started

### 1. Install the SDK

```bash
pip install 'bedrock-agentcore[strands-agents]'
```

### 2. Set up your wallet provider

Follow the guide for your chosen provider. Each notebook walks you through creating an account, getting credentials, and saving them to `.env`:

- **Coinbase CDP** — Copy `.env.coinbase.sample` to `.env`, then follow [`providers/coinbase_cdp_account_setup.ipynb`](00-setup-agentcore-payments/providers/coinbase_cdp_account_setup.ipynb)
- **Stripe (Privy)** — Copy `.env.privy.sample` to `.env`, then follow [`providers/stripe_privy_account_setup.ipynb`](00-setup-agentcore-payments/providers/stripe_privy_account_setup.ipynb) (requires Node.js for the one-time Privy reference frontend)

For Path B (multi-provider), run both provider notebooks — they write prefixed keys (`COINBASE_*`, `PRIVY_*`) to the same `.env` without conflicts.

Set `LINKED_EMAIL` in your `.env` to your real email address before running Tutorial 00. This email is used to create the embedded wallet and log into the wallet hub for funding and delegation.

### 3. Run the setup notebook

```bash
cd 00-setup-agentcore-payments

# Path A (single provider):
jupyter notebook setup_agentcore_payments.ipynb

# Path B (multi-provider):
jupyter notebook 00b_multi_provider_setup.ipynb
```

This creates IAM roles, the payment stack, and writes resource IDs to `.env`. All downstream tutorials load this file.

### 4. Additional tools (only for specific tutorials)

| Tool | Tutorials | Install |
|------|-----------|---------|
| AgentCore CLI | 02, 04, 07 | `npm install -g @aws/agentcore@0.30.0` (requires Node.js 20+) |
| Docker | 02, 07 | Required for `agentcore deploy` container builds |
| Playwright | 05 | `pip install playwright && python -m playwright install chromium` |

## Tutorial Storyline

```
Path A (single provider):
  Provider setup ──► T00 Setup ──► T01 Local Agent ──► T02 Deploy to Runtime
                                   │
                                   ├──► T03 Wallet Operations
                                   ├──► T04 Gateway + Bazaar
                                   ├──► T05 Browser + Payments (pattern reference)
                                   └──► T06 Memory + Payments

Path B (multi-provider):
  Both provider setups ──► T00b Multi-Provider Setup ──► T07 Multi-Agent Orchestrator
                                                         │
                                                         └──► T01–T06 also work
```

## Security Notice

These tutorials use `.env` files for credential storage for simplicity. For deployed workloads, store all credentials in [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html) or Systems Manager Parameter Store. Never commit `.env` files to version control. See the [Security](#security) section for additional guidance.

## Tutorials

Each tutorial maps to one or more AgentCore payments features. Start with Tutorial 00, then pick any path.

| # | Tutorial | Features Covered | What You'll Learn |
|---|----------|-----------------|-------------------|
| 00 | [Setup](00-setup-agentcore-payments/) | Wallet integration, Payment limits | Create IAM roles, PaymentManager, Connector, embedded wallet, and a budgeted session from scratch |
| 01 | [Enable Payment Limits on an Agent](01-agents-payments-and-limits/) | Payment processing, Payment limits | Build a Strands agent and a LangGraph agent that call paid endpoints and pay automatically. See payment limits enforcement and overspend rejection in action |
| 02 | [Deploy to Runtime](02-deploy-to-agentcore-runtime/) | Payment processing, Observability | Deploy a payment agent to AgentCore Runtime with role separation. View payment traces and logs in AgentCore Observability |
| 03 | [Wallet Operations](03-user-onboarding-wallet-funding/) | Wallet integration | Full wallet lifecycle: onboard additional users, funding options (testnet faucet and onramps), delegation per provider, balance checks, multi-network wallets, and session budget patterns |
| 04 | [Gateway + Bazaar](04-agent-with-coinbase-bazaar-via-gateway/) | Endpoint discoverability, Payment processing | Discover paid MCP tools on Coinbase x402 Bazaar through AgentCore Gateway (Base Sepolia) and call them with automatic payment |
| 05 | [Browser + Payments](05-agent-with-browser-tool-pay-for-content/) | Payment processing | (Pattern reference) Intercept HTTP 402 responses in a Playwright browser session and pay for paywalled web content |
| 06 | [Research Agent with Payment Memory](06-research-agent-with-payment-memory/) | Payment processing, Payment limits, Memory | Combine AgentCore payments with AgentCore Memory so the agent recalls past data and user preferences across sessions and skips redundant paid calls |
| 07 | [Multi-Agent Orchestrator](07-multi-agent-payment-orchestrator/) | Wallet integration, Payment limits, Observability | Orchestrate multiple agents with separate wallets (Coinbase + Privy), per-agent payment limits, Runtime deploy, and online evaluation |

### AgentCore payments features → tutorial mapping

| Feature | Description | Tutorials |
|---------|-------------|-----------|
| Payment processing | x402 protocol orchestration, transaction signing, proof generation | 01, 02, 04, 05, 06, 07 |
| Payment limits | Session budgets (`maxSpendAmount`), expiry, overspend rejection | 00, 01, 03, 06, 07 |
| Wallet integration | Coinbase CDP and Stripe (Privy) embedded wallets, delegation, funding | 00, 03, 07 |
| Endpoint discoverability | Coinbase x402 Bazaar via AgentCore Gateway, MCP tool search | 04 |
| Memory | AgentCore Memory for cross-session recall and spend optimization | 06 |
| Observability | AgentCore Observability (vended logs, traces via CloudWatch) | 00, 02, 07 |

### Coinbase x402 Bazaar — access patterns

The Bazaar exposes three interfaces:

| Interface | Endpoint | Best for |
|-----------|----------|----------|
| Semantic search (HTTP) | `GET /v2/x402/discovery/search` | Direct HTTP calls — free discovery, paid tool calls return 402 |
| MCP Server | `GET /v2/x402/discovery/mcp` | AI agents via AgentCore Gateway — `search_resources` + `proxy_tool_call` |
| Paginated catalog (HTTP) | `GET /v2/x402/discovery/resources` | Custom UIs and backend integrations |

## Repo Structure

```
├── utils.py                              ← shared helpers (all tutorials import this)
├── .env                                  ← created by Tutorial 00 (git-ignored)
├── 00-setup-agentcore-payments/          ← start here
│   ├── .env.coinbase.sample              ← copy to .env for Coinbase CDP
│   ├── .env.privy.sample                 ← copy to .env for Stripe (Privy)
│   └── providers/                        ← Coinbase + Privy account setup guides
├── 01-agents-payments-and-limits/        ← Strands + LangGraph notebooks
├── 02-deploy-to-agentcore-runtime/
├── 03-user-onboarding-wallet-funding/
├── 04-agent-with-coinbase-bazaar-via-gateway/
├── 05-agent-with-browser-tool-pay-for-content/
├── 06-research-agent-with-payment-memory/
└── 07-multi-agent-payment-orchestrator/
```

## Shared Files

| File | Purpose |
|------|---------|
| `utils.py` | IAM role creation (`setup_payment_roles()`), config persistence, observability setup, display helpers |
| `.env` | Shared config created by Tutorial 00, loaded by all downstream tutorials (git-ignored) |
| `.gitignore` | Excludes `.env`, `private.pem`, and Python artifacts |

## Wallet-Agnostic Design

The tutorials are designed to work with either supported wallet provider you configured in Tutorial 00 - Coinbase CDP or Stripe (Privy). The agent code is the same regardless of your choice — only the `.env` values differ.

## Cleanup

> **Cost notice:** AgentCore Runtime deployments, Gateway, payment sessions, and CloudWatch observability incur AWS charges. Run cleanup after completing experimentation to avoid ongoing costs.

> **Warning:** Cleanup is irreversible and permanently deletes all payment resources, transaction history, and audit logs. Verify you have exported any data you need before proceeding.

When you are done with the tutorials, clean up resources to avoid unnecessary charges:

1. **Runtime deployments** — Remove deployed agents and gateways:
   ```bash
   agentcore remove all -y
   agentcore deploy -y
   ```
2. **Payment resources** (Manager, Connector, Instruments) — Run the cleanup cell at the bottom of Tutorial 00. This deletes the Payment Manager and all child resources (connectors, instruments).
3. **IAM roles** — The four roles created by `setup_payment_roles()` can be deleted from the IAM console if no longer needed.
4. **CloudWatch log groups** — Delete `/aws/vendedlogs/bedrock-agentcore/<manager-id>` from the CloudWatch console if observability was enabled.

*Note*: 1. **Payment sessions** — Expire automatically after their configured `expiryTimeInMinutes`. No action needed.

## Security

These tutorials use testnet resources with no real-world value. When building for real world deployment consider:

- **Credential management** — Store secrets in [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html) or Systems Manager Parameter Store, not `.env` files. Rotate credentials regularly.
- **IAM least privilege** — Scope IAM policies to specific resources rather than `"Resource": "*"`. Use separate roles for control-plane (ManagementRole) and data-plane (ProcessPaymentRole) operations.
- **Network security** — Deploy AgentCore Runtime in private subnets. Use VPC endpoints for AWS service access.
- **Monitoring** — Enable CloudWatch Logs for payment traces. Set up alarms for unusual spending patterns or failed payment attempts.

Follow the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/) — you are responsible for securing your credentials, IAM policies, wallet access, and session budgets.

## Conclusion

These tutorials cover the full lifecycle of payment-enabled AI agents with Amazon Bedrock AgentCore payments: wallet setup, local agent development, Runtime deployment, wallet operations, Gateway integration, browser-based payments, memory-aware agents that skip redundant paid calls, and multi-agent orchestration. Start with Tutorial 00 and the provider setup guide for your chosen wallet, then follow whichever path fits your use case. For production guidance, see the [AgentCore payments documentation](https://docs.aws.amazon.com/bedrock-agentcore/) and review the Security section above.
