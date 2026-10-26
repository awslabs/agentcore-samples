# Pay for Data — Heurist Finance Agent (AgentCore Runtime)

| Information         | Details                                                                |
|:--------------------|:-----------------------------------------------------------------------|
| Use case type       | Agentic data retrieval with autonomous micropayments                   |
| Agent type          | Single                                                                 |
| Hosting             | AgentCore Runtime (managed microVM, role-segregated)                   |
| Payment protocol    | x402 (HTTP 402 Payment Required)                                       |
| Agentic Framework   | Strands Agents                                                         |
| LLM model           | Anthropic Claude Sonnet 4.6                                            |
| Complexity          | Intermediate                                                           |
| SDK used            | boto3 + AgentCore SDK + AgentCorePaymentsPlugin (Strands) + AgentCore CLI |
| Wallet type         | Embedded crypto wallet (AgentCore-provisioned, Coinbase CDP)           |
| Network             | Base mainnet (`eip155:8453`) — real USDC                               |

## Overview

A finance research agent that calls paid [Heurist](https://heurist.xyz) x402 endpoints
for live market prices, SEC filings, and macro indicators. The `AgentCorePaymentsPlugin`
intercepts HTTP 402 responses, generates a USDC payment proof via the AgentCore payment
manager, attaches it, and retries — tool code stays a plain `http_request` call. Data is
analyzed with AgentCore Code Interpreter and exported as charts and reports to S3.

> ⚠️ **Mainnet sample.** Every invocation settles real USDC on Base mainnet. Typical
> per-call prices are $0.002–$0.005; $1 USDC covers ~200 calls. Fund your wallet before
> running — see Step 3 output for the wallet address.

## Architecture

```
RESOURCE PROVISIONING  (pay_for_data.py Step 3, ControlPlaneRole)
─────────────────────────────────────────────────────────────────────────────────

  cp_client   ──► bedrock-agentcore-control ──► CreatePaymentCredentialProvider,
                                                CreatePaymentManager,
                                                CreatePaymentConnector
  mgmt_client ──► bedrock-agentcore         ──► CreatePaymentInstrument

  Result: MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID


SESSION + INVOKE  (pay_for_data.py Step 6, ManagementRole)
─────────────────────────────────────────────────────────────────────────────────

  App backend (ManagementRole)
   │
   │ CreatePaymentSession(budget=$0.25)
   │ InvokeAgentRuntime(arn, session_id, instrument_id, prompt)
   ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  AgentCore Runtime microVM  (ProcessPaymentRole)             │
  │                                                              │
  │  Strands Agent  (Claude Sonnet 4.6)                          │
  │   Tool 1: http_request ──► Heurist x402 endpoints            │
  │   Tool 2: code_interpreter ──► AgentCore Code Interpreter    │
  │   Tool 3: export_artifact_to_s3 ──► S3 presigned URLs        │
  │   Plugin: AgentCorePaymentsPlugin (402 intercept + retry)    │
  └───────────┬──────────────────────────────┬───────────────────┘
              │ HTTPS (x402)                 │ AWS API (ambient creds)
              ▼                              ▼
  ┌───────────────────────┐      ┌───────────────────────────────┐
  │  Heurist Mesh         │      │  AgentCore Payments           │
  │  (x402 endpoints)     │      │  ProcessPayment API           │
  │                       │      │                               │
  │  HTTP 402 → proof     │      │  ┌────────────────────────┐   │
  │  → HTTP 200 + data    │      │  │  Embedded Wallet       │   │
  └───────────────────────┘      │  │  (Coinbase CDP)        │   │
                                 │  │  Base mainnet USDC     │   │
                                 │  └────────────────────────┘   │
                                 └───────────────────────────────┘
```

**Key design points:**

- **Hosted on AgentCore Runtime.** The agent runs inside a managed microVM under
  `ProcessPaymentRole`. The container assumes the role directly — no `sts:AssumeRole`
  in agent code.
- **App backend pattern.** `pay_for_data.py` (under `ManagementRole`) creates the
  session with a budget, then calls `InvokeAgentRuntime`. The agent is stateless.
- **Parallel paid calls.** The agent fans out independent data fetches in the same
  tool-use round. Payment is handled per-call by the plugin.
- **Code Interpreter + S3.** Analysis runs in a remote sandbox; charts and reports
  are uploaded to S3 and returned as presigned download URLs.

## IAM Role Design

| Role | Operations allowed | Used by |
|:-----|:-------------------|:--------|
| `ControlPlaneRole` | Create credential provider, manager, connector, instrument | Script (Step 3) |
| `ManagementRole` | Create/get sessions, `InvokeAgentRuntime` | Script (Steps 4, 6) |
| `ProcessPaymentRole` | `ProcessPayment`, Code Interpreter, S3, Bedrock model | **AgentCore Runtime** execution role |
| `ResourceRetrievalRole` | Service-side token retrieval | AgentCore service |

## Prerequisites

- Python 3.10+
- Node.js 20+ (for AgentCore CLI)
- AWS CLI v2 configured
- AWS CDK v2 installed
- AgentCore CLI: `npm install -g @aws/agentcore@0.30.0`
- Coinbase CDP account — `CDP_API_KEY_NAME`, `CDP_API_KEY_PRIVATE_KEY`, `CDP_WALLET_SECRET`
  - **Enable Delegated Signing**: project → Wallet → Embedded Wallets → Policies
- IAM roles created (see `.env.sample` for required role ARNs)

## Running

```bash
pip install -r requirements.txt
cp .env.sample .env
# Edit .env: fill in CDP credentials and IAM role ARNs

python pay_for_data.py
```

> **First run:** Step 3 provisions the wallet and exits. Fund the wallet with USDC on
> Base mainnet, grant signing via WalletHub, save the resource IDs to `.env`, then re-run.

## CLI Commands

```bash
# Deploy manually (handled by pay_for_data.py Step 5)
cd payfordata && agentcore deploy -y

# Invoke directly
agentcore invoke '{
  "prompt": "Fetch AAPL and NVDA quotes from YahooFinanceAgent. Summarize prices.",
  "payment_manager_arn": "<MANAGER_ARN>",
  "user_id": "<USER_ID>",
  "payment_session_id": "<SESSION_ID>",
  "payment_instrument_id": "<INSTRUMENT_ID>"
}'

# View traces
agentcore traces list --limit 20

# Clean up
agentcore remove all -y
```

## Observability

![AgentCore Observability](images/obs-dashboard.png)

| Layer | Source | Where it shows |
|-------|--------|---------------|
| Runtime | `opentelemetry-instrument` CMD in Dockerfile | AgentCore observability → All traces |
| Agent (Strands) | Strands OTEL spans via runtime distro | Trace waterfall |
| Payments | Vended log delivery | AgentCore observability → Payments tab |
| Code Interpreter | boto3 child spans via W3C traceparent | Trace waterfall |

## Clean Up

```bash
# 1. Remove runtime deployment
cd payfordata && agentcore remove all -y

# 2. Delete S3 artifacts bucket
aws s3 rb s3://<ARTIFACTS_BUCKET> --force

# 3. Payment session expires automatically after SESSION_EXPIRY_MINUTES
```
