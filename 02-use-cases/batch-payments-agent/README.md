# Batch Payments Agent with Spraay x402 Gateway

A production-ready agent that uses **Amazon Bedrock AgentCore Payments** to autonomously execute **multi-recipient batch payments** — paying N wallets in a single blockchain transaction through the [Spraay x402 gateway](https://spraay.app).

Traditional on-chain transfers require one transaction per recipient. Spraay's batch payment infrastructure collapses that into a single atomic operation: one agent call, one x402 micropayment, N recipients funded. This is the primitive that payroll agents, airdrop agents, grant distribution agents, and refund agents all need — and it doesn't exist in AgentCore samples today.

## Why Batch Payments Matter for Agents

| Scenario | Without Batch | With Spraay Batch |
|---|---|---|
| **Payroll agent** pays 50 contractors | 50 transactions, 50 gas fees, 50 confirmations | 1 transaction, 1 gas fee, 1 confirmation |
| **Airdrop agent** rewards 200 users | 200 tx, minutes of wait, partial failure risk | 1 atomic tx, all-or-nothing settlement |
| **Refund agent** processes 30 returns | 30 individual sends, manual tracking | 1 batch call, single tx hash for audit |
| **Grant agent** distributes to 10 teams | 10 separate approvals and sends | 1 batch with per-recipient amounts |

The agent pays a single x402 micropayment ($0.01–$0.05 USDC) to Spraay, which handles the on-chain batching, gas optimization, and atomic execution across 16 supported chains.

## What This Demonstrates

| Capability | How It's Used |
|---|---|
| **AgentCore Payments** | Autonomous x402 micropayments via Coinbase CDP or Stripe/Privy wallet |
| **AgentCore Gateway** | MCP tool discovery for Spraay's batch payment endpoints |
| **AgentCore Runtime** | Serverless agent hosting with session isolation |
| **AgentCore Observability** | End-to-end payment tracing via CloudWatch |
| **x402 Protocol** | HTTP 402 → payment → settlement → batch execution loop |
| **Coinbase x402 Bazaar** | Agent-driven service discovery of Spraay endpoints |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Amazon Bedrock AgentCore                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │   Gateway     │  │   Runtime     │  │   Payments       │  │
│  │  (MCP Tools)  │──│  (Strands)    │──│  (ProcessPayment)│  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                    │             │
│         │          ┌──────┴───────┐            │             │
│         │          │ Strands Agent │            │             │
│         │          │  Claude 4.5   │            │             │
│         │          └──────┬───────┘            │             │
└─────────┼─────────────────┼────────────────────┼─────────────┘
          │                 │                    │
          │      ┌──────────▼──────────┐         │
          │      │   Spraay Gateway    │         │
          │      │  gateway.spraay.app │         │
          │      │                     │         │
          │      │  ┌───────────────┐  │         │
          │      │  │ 402 Response  │◄─┼─────────┘
          │      │  │ + x402 header │  │    (USDC micropayment on Base)
          │      │  └───────┬───────┘  │
          │      │          │          │
          │      │  ┌───────▼───────┐  │
          │      │  │ Batch Execute │  │
          │      │  │ (N recipients │  │
          │      │  │  in 1 tx)     │  │
          │      │  └───────────────┘  │
          │      └─────────────────────┘
          │
   ┌──────▼──────────────┐
   │  Coinbase x402      │
   │  Bazaar (Discovery) │
   └─────────────────────┘
```

### Payment Flow

1. User tells agent to pay multiple recipients (e.g., "Pay these 5 wallets 0.01 ETH each on Base")
2. Agent calls Spraay's batch payment endpoint with recipient list
3. Spraay returns HTTP 402 with x402 payment header ($0.01–$0.05 USDC service fee)
4. AgentCore Payments checks session budget, signs USDC transaction on Base
5. Agent retries with payment proof header
6. Spraay verifies payment, executes the batch transfer as a single on-chain transaction
7. Agent receives the transaction hash and per-recipient confirmation

### Batch Payment Contract

Spraay's batch contract on Base (`0x1646452F98E36A3c9Cfc3eDD8868221E207B5eEC`) handles:
- Multi-recipient ETH and ERC-20 transfers in one atomic transaction
- Gas optimization (one base fee instead of N)
- All-or-nothing execution (no partial failures)
- On-chain verifiability via single transaction hash

## Prerequisites

- **AWS Account** with Amazon Bedrock AgentCore access (preview regions: us-east-1, us-west-2, eu-central-1, ap-southeast-2)
- **Coinbase Developer Platform** API keys — [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com/)
- **Python 3.12+**
- **AWS CLI** configured (`aws configure`)
- **AgentCore CLI** installed (`npm install -g @aws/agentcore`)
- **Docker** (for AgentCore Runtime deployment)
- **USDC on Base Sepolia** — fund via [faucet.circle.com](https://faucet.circle.com/)

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/awslabs/amazon-bedrock-agentcore-samples.git
cd amazon-bedrock-agentcore-samples/02-use-cases/batch-payments-agent

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your configuration
```

Required variables:

| Variable | Description |
|---|---|
| `AWS_REGION` | AWS region (e.g., `us-east-1`) |
| `PAYMENT_MANAGER_ARN` | ARN from CreatePaymentManager |
| `PAYMENT_CONNECTOR_ID` | ID from CreatePaymentConnector |
| `PAYMENT_INSTRUMENT_ID` | ID from CreatePaymentInstrument |
| `SPRAAY_GATEWAY_URL` | `https://gateway.spraay.app` |
| `MODEL_ID` | Bedrock model (default: `us.anthropic.claude-sonnet-4-5-20250514-v1:0`) |

### 3. Set Up AgentCore Payments

Create the payment resources (one-time setup):

```bash
python scripts/setup_payments.py
```

This script creates:
- PaymentManager with IAM authorizer
- PaymentConnector for Coinbase CDP
- PaymentInstrument (agent wallet)
- PaymentSession with spending limits

### 4. Run Locally

```bash
# Interactive mode
python -m agent.main

# Single prompt
python -m agent.main --prompt "Send 0.001 ETH to 3 wallets on Base"

# AgentCore CLI local dev
agentcore dev
```

### 5. Deploy to AgentCore Runtime

```bash
agentcore deploy
```

## Sample Prompts

### Batch Payments (Core Use Case)
```
Send 0.001 ETH to each of these three wallets on Base:
0xAbc...123, 0xDef...456, 0x789...abc
```

```
Distribute 100 USDC equally among these 5 team wallets on Ethereum.
```

```
Process this payroll batch: Alice 0.5 ETH, Bob 0.3 ETH, Carol 0.2 ETH on Base.
```

### Cost Estimation
```
How much would it cost to batch-pay 20 recipients on Base?
```

### Discovery
```
What batch payment services are available? What chains are supported?
```

### Supporting Operations
```
What's the current price of ETH?
Check the USDC balance of 0xAbc...123 on Base.
```

## Project Structure

```
batch-payments-agent/
├── agent/
│   ├── __init__.py
│   ├── main.py              # Agent entry point with AgentCore Payments plugin
│   ├── tools.py             # Spraay batch payment and supporting tools
│   └── config.py            # Configuration management
├── scripts/
│   ├── setup_payments.py    # One-time AgentCore Payments setup
│   ├── invoke_agent.py      # Test deployed agent
│   └── cleanup_payments.py  # Tear down payment resources
├── tests/
│   ├── test_402_handling.py  # x402 payment flow tests
│   ├── test_tools.py        # Tool unit tests
│   └── conftest.py          # Test fixtures
├── .env.example              # Environment template
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container for AgentCore Runtime
└── README.md                 # This file
```

## Spraay x402 Gateway

[Spraay](https://spraay.app) is a live x402 payment gateway whose primary capability is **batch payments — pay N recipients in one transaction**. It provides 170+ paid endpoints across 37 categories and 16 blockchain networks, and is indexed on the [Coinbase x402 Bazaar](https://docs.cdp.coinbase.com/x402/bazaar) for autonomous agent discovery.

### Batch Payment Pricing

| Operation | Price (USDC) | What You Get |
|---|---|---|
| Batch ETH transfer (N recipients) | $0.01–$0.05 | Single atomic tx, all recipients funded |
| Batch ERC-20 transfer (N recipients) | $0.01–$0.05 | Single atomic tx with token approvals |
| Batch payroll (N recipients, mixed amounts) | $0.05–$0.25 | Payroll-optimized with receipt data |
| Escrow batch | $0.05–$0.25 | Escrow creation for multiple parties |

### Supported Chains

Primary: **Base, Ethereum, Solana**

Also: Arbitrum, Optimism, Polygon, Avalanche, BSC, Fantom, Gnosis, Celo, Linea, Scroll, zkSync, Canton Network, and more (16 total).

## Key Implementation Details

### AgentCore Payments Plugin (Strands)

The agent uses the built-in `AgentCorePaymentsPlugin` for seamless x402 payment handling:

```python
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.payments.integrations.strands.plugin import (
    AgentCorePaymentsPlugin,
)
from bedrock_agentcore.payments.integrations.config import (
    AgentCorePaymentsPluginConfig,
)

config = AgentCorePaymentsPluginConfig(
    payment_manager_arn=PAYMENT_MANAGER_ARN,
    user_id="batch-agent-user",
    payment_instrument_id=PAYMENT_INSTRUMENT_ID,
    payment_session_id=PAYMENT_SESSION_ID,
    region=AWS_REGION,
)

plugin = AgentCorePaymentsPlugin(config)

agent = Agent(
    model=BedrockModel(model_id=MODEL_ID, region=AWS_REGION),
    tools=[batch_transfer, estimate_batch_cost, get_supported_chains],
    plugins=[plugin],
    system_prompt=SYSTEM_PROMPT,
)
```

### The x402 Loop for Batch Payments

When the agent calls Spraay's batch endpoint:

1. Spraay returns `HTTP 402` with `PAYMENT-REQUIRED` header (base64 JSON)
2. Header contains: scheme, network (`eip155:8453` for Base), asset (USDC), recipient (Spraay), amount ($0.01–$0.05), timeout
3. `AgentCorePaymentsPlugin` intercepts, calls `ProcessPayment` via AgentCore
4. AgentCore signs a USDC transfer on Base — agent never touches private keys
5. Agent retries with `X-PAYMENT` proof header
6. Spraay verifies via x402 facilitator, then executes the batch transfer on-chain
7. Agent receives: transaction hash, per-recipient status, gas used

### Budget Controls

```python
session = payments_client.create_payment_session(
    paymentManagerId=manager_id,
    paymentInstrumentId=instrument_id,
    maxSpendAmount="1.00",   # $1.00 USDC max per session
    currency="USDC",
    expiresAt=expiry_time,
)
```

At $0.01–$0.05 per batch call, a $1.00 session budget supports 20–100 batch operations.

## Testing

```bash
# Unit tests
pytest tests/ -v

# Test 402 handling
pytest tests/test_402_handling.py -v

# Test with live Spraay endpoints (requires funded wallet)
pytest tests/ -v -m integration
```

## Clean Up

```bash
# Delete AgentCore Runtime
agentcore runtime delete --agent-name batch-payments-agent

# Delete payment resources
python scripts/cleanup_payments.py

# Or manually via AWS CLI:
aws bedrock-agentcore delete-payment-session \
  --payment-manager-id <manager-id> \
  --payment-session-id <session-id>

aws bedrock-agentcore delete-payment-instrument \
  --payment-manager-id <manager-id> \
  --payment-instrument-id <instrument-id>

aws bedrock-agentcore delete-payment-connector \
  --payment-manager-id <manager-id> \
  --payment-connector-id <connector-id>

aws bedrock-agentcore delete-payment-manager \
  --payment-manager-id <manager-id>
```

Also delete any CloudWatch log groups created during testing.

## References

- [Amazon Bedrock AgentCore Payments Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments.html)
- [AgentCore Payments Technical Deep Dive](https://aws.amazon.com/blogs/machine-learning/technical-deep-dive-agentcore-payments-and-innovation-in-agentic-commerce/)
- [x402 Protocol Specification](https://github.com/coinbase/x402/tree/main/specs)
- [Coinbase x402 Bazaar](https://docs.cdp.coinbase.com/x402/bazaar)
- [Spraay Documentation](https://docs.spraay.app)
- [Spraay BPA 1.0 Specification](https://docs.spraay.app/bpa/1.0/)
- [Strands Agents Documentation](https://strandsagents.com/latest/documentation/docs/)

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
