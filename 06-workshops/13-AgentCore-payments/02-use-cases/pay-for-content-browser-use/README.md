# Pay for Content — Browser Use Case (AgentCore Runtime)

## Overview

"Amazon Bedrock AgentCore payments enables AI agents to make autonomous payments for
digital services — without ever holding private keys or requiring human approval for
each transaction."

Without AgentCore payments, an agent that needs to pay for content must either hold
a private key (exposing credentials to the model) or interrupt the user to complete
the payment manually. This use case shows a third path: the agent **leverages AgentCore
Payments for payment processing**, stays within human-set payment limits, and completes
the entire browse-pay-extract flow autonomously from a managed Runtime container.

The agent is **deployed to AgentCore Runtime** under `ProcessPaymentRole`, uses the
**AgentCore Browser Tool** to navigate a paywalled website, reads the embedded x402
payment requirement from the page DOM, calls `ProcessPayment` to generate a payment
proof, interacts with the paywall UI, and returns the unlocked content — without any
private key exposure or human intervention.

### Use Case Details

| Information         | Details                                                       |
|:--------------------|:--------------------------------------------------------------|
| Use case type       | Agentic browser automation with autonomous micropayment       |
| Agent type          | Single                                                        |
| Hosting             | AgentCore Runtime (managed microVM, role-segregated)          |
| Payment protocol    | x402 (HTTP 402 Payment Required)                              |
| Agentic Framework   | Strands Agents                                                |
| LLM model           | Anthropic Claude Sonnet 4.6                                   |
| Complexity          | Intermediate                                                  |
| SDK used            | boto3 + AgentCore SDK + AgentCorePaymentsPlugin (Strands) + AgentCore CLI |
| Wallet type         | Embedded crypto wallet (AgentCore-provisioned, Coinbase CDP)  |
| Network             | Base Sepolia testnet (`eip155:84532`); Solana Devnet available |

---

## Architecture

There are four distinct phases: **resource provisioning** (runs once), **session setup**
(runs before each agent invocation), **deploy** (runs on agent code change), and
**invoke** (the live payment flow). The content provider is a separate piece of
infrastructure that you deploy from this repo's `content-provider/` CDK stack — it
is not created by the notebook.

> **Note on SDK choice:** the notebook uses boto3 clients (`bedrock-agentcore-control`
> and `bedrock-agentcore`) for Payments resource management because the AgentCore
> Python SDK does not yet expose `CreatePaymentManager` / `CreatePaymentSession`
> / `CreatePaymentInstrument`. The agent itself (running on Runtime) uses the
> `bedrock-agentcore[strands-agents]` SDK and `AgentCorePaymentsPlugin` for
> payment processing — that side is fully SDK-driven.

```
RESOURCE PROVISIONING  (notebook Step 3, ControlPlaneRole)
─────────────────────────────────────────────────────────────────────────────

  cp_client   ──► bedrock-agentcore-control ──► CreatePaymentCredentialProvider,
                                                CreatePaymentManager,
                                                CreatePaymentConnector
  mgmt_client ──► bedrock-agentcore         ──► CreatePaymentInstrument

  Result: CREDENTIAL_PROVIDER_ARN, MANAGER_ARN, PAYMENT_CONNECTOR_ID, PAYMENT_INSTRUMENT_ID


SESSION SETUP  (notebook Step 4, ManagementRole)
─────────────────────────────────────────────────────────────────────────────

  Notebook (ManagementRole)              AgentCore payments
  ─────────────────────────              ──────────────────────────────
  CreatePaymentSession ─────────────────► budget=$1.00 USD, expiry=60 min
                                          paymentSessionId


DEPLOY AGENT TO RUNTIME  (notebook Step 5, AgentCore CLI)
─────────────────────────────────────────────────────────────────────────────

  agent/payment_agent.py            agentcore CLI                 AWS
  agent/requirements.txt          + agentcore deploy            (CodeBuild builds
  agent/Dockerfile                                               from Dockerfile)
  (BedrockAgentCoreApp +    ──►    create / deploy     ──►   AgentRuntime
   AgentCoreBrowser +                                          (execution role:
   process_x402_payment)                                       ProcessPaymentRole)
                                                               + ECR image
                                                               + CodeBuild project
                                                               + CloudWatch logs


INVOKE  (notebook Step 6, ManagementRole → AgentCore Runtime)
─────────────────────────────────────────────────────────────────────────────

  Notebook (ManagementRole)
   │
   │ InvokeAgentRuntime(arn,
   │     paywall_url, session_id,
   │     instrument_id, manager_arn)
   ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  AgentCore Runtime microVM  (ProcessPaymentRole)             │
  │                                                              │
  │  Strands Agent  (Claude Sonnet 4.6)                          │
  │   Tool 1: AgentCoreBrowser ──► managed cloud Chromium        │
  │   Tool 2: process_x402_payment ──► PaymentManager            │
  │   Plugin: AgentCorePaymentsPlugin (payment query tools)      │
  └───────────┬──────────────────────────────┬───────────────────┘
              │ HTTPS                        │ AWS API (ambient creds)
              ▼                              ▼
  ┌───────────────────────┐      ┌───────────────────────────────┐
  │  Content Provider     │      │  AgentCore payments           │
  │  (team-hosted demo or │      │  ProcessPayment API           │
  │   your own deploy)    │      │                               │
  │                       │      │  ┌────────────────────────┐   │
  │  HTTP 200             │      │  │  Embedded Wallet        │   │
  │  x402 requirement     │      │  │  (Coinbase CDP)         │   │
  │  in DOM script tag    │      │  │  Base Sepolia testnet   │   │
  │                       │      │  └────────────────────────┘   │
  │  proof submitted via  │ ◄────┤  status: PROOF_GENERATED      │
  │  paywall UI → unlock  │      └───────────────────────────────┘
  └───────────────────────┘
   │ article text
   ▼
  Agent returns content + amount paid to caller


OBSERVABILITY  (Step 7, automatic)
─────────────────────────────────────────────────────────────────────────────

  Each invocation emits a CloudWatch GenAI Observability trace covering
  the agent loop, tool calls, payment SDK calls, and ProcessPayment API
  latency. Metrics in the bedrock-agentcore namespace.


CLEANUP
─────────────────────────────────────────────────────────────────────────────

  agentcore remove all -y       — tears down Runtime, ECR, log groups
  Session expiry                — agent can no longer spend after expiry
```

**Key design points:**

- **Hosted on AgentCore Runtime.** The agent runs inside a managed microVM under
  `ProcessPaymentRole`. Role separation is enforced by infrastructure: the container
  assumes `ProcessPaymentRole` directly, and that role has an explicit Deny on session
  and instrument management. The agent code never calls `sts:AssumeRole`.
- **Notebook = app backend.** The notebook (under `ManagementRole`) creates the session
  with a budget, then calls `InvokeAgentRuntime` with the session/instrument/manager
  context in the payload. The agent is stateless and wallet-agnostic — the same
  deployment serves any user the backend authorises.
- **Embedded wallet.** AgentCore provisions the on-chain wallet — no pre-existing CDP
  wallet or funded account is required. The `linkedAccounts` email field ties the wallet
  to a user identity. Coinbase embedded wallets are provisioned synchronously (no OTP step).
- **Browser tool.** `AgentCoreBrowser` is a managed cloud Chromium session reached over
  WebSocket from inside the Runtime container. The content provider must be deployed to
  a public HTTPS URL — the browser cannot reach `localhost`.
- **No private keys.** Signing is delegated to the AgentCore-managed embedded wallet.
  Coinbase CDP today; swap the credential provider configuration in Step 3 for StripePrivy.

---

## Prerequisites

- AWS account with Amazon Bedrock AgentCore access
- Python 3.10+ and Jupyter Notebook (or JupyterLab)
- Node.js 20+ (for the AgentCore CLI and content-provider CDK)
- AWS CLI v2 configured with credentials (`aws configure`)
- AWS CDK v2 installed (used by the AgentCore CLI under the hood)
- AgentCore CLI installed: `npm install -g @aws/agentcore@0.30.0`
  > **No local Docker required.** Step 5 builds the agent's container image in
  > AWS CodeBuild via the CLI's CDK app. You only need Docker if you want to use
  > `agentcore dev` for local hot-reload development.
- IAM roles created — run `bash setup_roles.sh` and record the ARNs in `.env`. This
  script configures `ProcessPaymentRole` to also serve as the AgentCore Runtime
  execution role (ECR pull, CloudWatch logs, X-Ray, Bedrock model invocation,
  browser tool), with explicit Deny on session/instrument management. It also adds
  `InvokeAgentRuntime` to `ManagementRole` so the notebook can call the deployed agent.
- Content provider deployed to AWS — run `cd content-provider && PAY_TO=0x<your-wallet> bash deploy.sh` and set `CONTENT_DISTRIBUTION_URL` in `.env` (see [content-provider/README.md](content-provider/README.md))
- A Coinbase Developer Platform (CDP) account with an API key
  - API key name, private key, and wallet secret are required (see `.env.sample`)
  - **Enable Delegated Signing** in your CDP project before running the agent:
    go to [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) → your project → **Wallet** → **Embedded Wallets** → **Policies** → enable **Delegated signing**
  - No pre-existing wallet needed — AgentCore provisions the embedded wallet for you
  - After provisioning, fund the wallet via the Circle faucet (https://faucet.circle.com)

> **Note:** This use case provisions an **embedded crypto wallet** via AgentCore.
> You do not need a pre-existing Coinbase wallet. The credential provider (CDP API key)
> authorizes AgentCore to create and manage the wallet on your behalf. After provisioning,
> Step 3 prints a **WalletHub URL** — open it to fund the wallet and grant signing permission.

> **Important:** `AgentCoreBrowser` is a cloud-managed browser — it cannot reach
> `localhost`. The content provider `CONTENT_DISTRIBUTION_URL` must be a public HTTPS URL.
> Deploy the included CDK stack first — see [content-provider/README.md](content-provider/README.md)
> — then set `CONTENT_DISTRIBUTION_URL` in `.env` to the printed CloudFront URL.

---

## Running the Use Case

### Step 0 — Create IAM roles

Run `setup_roles.sh` to create the required IAM roles (only needed once per account):

```bash
bash setup_roles.sh
```

### Step 1 — Configure your environment

```bash
cp .env.sample .env
# Edit .env and fill in your values
```

Key variables to set:
- `CDP_API_KEY_NAME` / `CDP_API_KEY_PRIVATE_KEY` / `CDP_WALLET_SECRET` — Coinbase CDP API key
- `WALLET_EMAIL` — email address to associate with the embedded wallet
- `CONTROL_PLANE_ROLE_ARN` / `MANAGEMENT_ROLE_ARN` / `PROCESS_PAYMENT_ROLE_ARN` — from `setup_roles.sh`
- `CONTENT_DISTRIBUTION_URL` — set to the CloudFront URL printed after deploying the content provider CDK stack

After the first run, copy `MANAGER_ARN`, `PAYMENT_CONNECTOR_ID`, and `PAYMENT_INSTRUMENT_ID`
from the Step 3 output back into `.env` to skip provisioning on subsequent runs.

### Step 2 — Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Run the notebook

```bash
jupyter notebook pay_for_content_browser.ipynb
```

Run all cells in order. The notebook will:
1. Load configuration and verify environment variables
2. Initialise the two app-backend boto3 clients (`ControlPlaneRole`, `ManagementRole`)
3. Provision the embedded wallet resource stack (once per user):
   CredentialProvider → PaymentManager → PaymentConnector → EmbeddedCryptoWallet Instrument
   — then pause for you to fund the wallet via WalletHub and the Circle faucet
3e. Verify wallet USDC balance via `GetPaymentInstrumentBalance` (briefly assumes
    `ProcessPaymentRole` locally, only for the balance check)
4. Create a payment session with budget and expiry (`ManagementRole`)
4b. **Enable Payment Manager observability** — runs the 4-step vended log
    delivery setup (`PutDeliverySource` × 2 → `PutDeliveryDestination` × 2 →
    `CreateDelivery` × 2) so the Payment Manager shows up in the AgentCore
    Observability → Payments dashboard with sessions, transactions, and
    `Agents using Payments` attribution
5. Deploy `agent/payment_agent.py` to AgentCore Runtime via the AgentCore CLI:
   `agentcore create` + `agentcore add agent --build Container`, copy in
   [`agent/Dockerfile`](agent/Dockerfile), then `agentcore deploy` (CodeBuild builds
   the image, pushes to ECR, creates the AgentRuntime). Pinned to Python 3.13,
   `ProcessPaymentRole` execution role, 10-min idle / 30-min max lifecycle.
6. Invoke the deployed agent via `InvokeAgentRuntime` with the session/instrument
   context in the payload, then verify spend via `GetPaymentSession`
7. View the session trace in CloudWatch GenAI Observability — Runtime, Agent,
   Browser-tool, and Payment Manager telemetry all stitched in one dashboard
8. Cleanup — `agentcore remove all` to tear down the Runtime deployment

### Observability coverage

| Layer | How it's enabled | Where you see it |
|---|---|---|
| Runtime | Auto via `agentcore deploy` (`opentelemetry-instrument` CMD) | All-traces dashboard |
| Agent (Strands) | OTEL spans through the Runtime distro | Inside each trace's waterfall |
| Browser tool | Strands `AgentCoreBrowser` emits client-side spans | Inside each trace's waterfall |
| Payment Manager | Vended log delivery (Step 4b) | **Payments tab** of the AgentCore Observability dashboard |

The dashboard's *Agents using Payments* counter increments only when the SDK
sends the `X-Amzn-Bedrock-AgentCore-Payments-Agent-Name` header, which it does
automatically when `PaymentManager` and `AgentCorePaymentsPluginConfig` are
constructed with `agent_name=`. `agent/payment_agent.py` reads `AGENT_NAME` from
the container environment and passes it to both.

> **Browser observability caveat:** the AgentCore Browser service does not
> currently support per-resource vended log delivery. `PutDeliverySource` rejects
> browser ARNs with: *valid resource types are runtime / gateway / memory /
> payment-manager / code-interpreter / workload-identity*. Browser-tool actions
> still appear as spans inside the agent trace via the OTEL distro
> (`browser session start`, `navigate`, `cleanup`), so the *useful* visibility
> is captured — but no separate Browser-service dashboard exists today.

---

## Key Notes and Caveats

### Endpoints

The notebook constructs both endpoints from the AWS region you set in `AWS_REGION`:
- `CP_ENDPOINT` = `https://bedrock-agentcore-control.{region}.amazonaws.com` — credential provider, manager, connector
- `DP_ENDPOINT` = `https://bedrock-agentcore.{region}.amazonaws.com` — instrument, session, process payment

`CreatePaymentCredentialProvider` lives on the standard `bedrock-agentcore-control` endpoint.
A separate ACPS endpoint is not required.

### Embedded wallet — Coinbase CDP (provider-agnostic design)

This use case provisions an **embedded crypto wallet** via Coinbase CDP. AgentCore
creates and manages the on-chain wallet — you provide CDP API credentials, not a wallet
address. The design is provider-agnostic: swapping to **StripePrivy** requires only
changing the credential provider configuration in Step 3a and 3c; all agent logic and
payment tool code remain unchanged.

After CreatePaymentInstrument, Step 3 prints a **WalletHub URL**. Open this URL to:
- Log in with your `WALLET_EMAIL`
- Fund the wallet with testnet USDC via the Circle faucet (https://faucet.circle.com)
- Grant signing permission to AgentCore payments

> Coinbase embedded wallets are provisioned synchronously — no OTP step required.
> StripePrivy embedded wallets require OTP email verification during provisioning.

### Supported networks

| Network alias  | Chain ID                                   | Status         |
|:---------------|:-------------------------------------------|:---------------|
| `base-sepolia` | `eip155:84532`                             | Default; tested |
| `solana-devnet`| `solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1` | Placeholder; not yet tested |

Set `NETWORK` in `.env` to switch networks. Solana Devnet requires an extra `feePayer`
field in the payment proof — the notebook includes a comment for this.

### Testnet only

This use case targets testnet networks. There is no persistent merchant address
guaranteed by any party — if the content provider's wallet address changes, update
`PAY_TO` in the content provider deployment.

### DOM selectors are sample-specific

The element IDs used by the browser agent (`pay-btn`, `proof-input`, `verify-btn`,
`content`) are specific to the **demo content provider** in `content-provider/`.
Real x402 sites will have different selectors — the agent discovers payment form
elements dynamically using semantic cues (button text, input types, aria-labels)
rather than hardcoded IDs.

---

## IAM Role Design

| Role | Operations allowed | Denied | Used by |
|:-----|:-------------------|:-------|:--------|
| `ControlPlaneRole` | `CreatePaymentCredentialProvider`, `CreatePaymentManager`, `CreatePaymentConnector`, `CreatePaymentInstrument` | `ProcessPayment`, session management | Notebook (Step 3) |
| `ManagementRole` | `CreatePaymentSession`, `GetPaymentSession`, `InvokeAgentRuntime` | `ProcessPayment` | Notebook (Step 4, Step 6) |
| `ProcessPaymentRole` | `ProcessPayment`, `GetPaymentInstrument`, `GetPaymentInstrumentBalance`, browser tool, ECR pull, CloudWatch logs/metrics, X-Ray, Bedrock model invocation | All setup and session management ops (`CreatePaymentSession`, `CreatePaymentInstrument`, etc.) | **AgentCore Runtime** as execution role |
| `ResourceRetrievalRole` | Service-side payment-token retrieval | n/a (assumed by AWS service) | AgentCore service |

---

## Cleanup

Tear down in this order when you're done:

1. **Runtime deployment** — `cd PayForContentRuntime && agentcore remove all -y`
   (removes the AgentRuntime, the ECR repo, the CodeBuild project, and CloudWatch logs).
2. **Payment session** — expires automatically after `SESSION_EXPIRY_MINUTES`
   (60 minutes by default). No API call required to close it.
3. **Payment manager / connector / instrument / credential provider** — delete via the
   AWS CLI or boto3 if you want a fully clean account.
4. **Content provider** — `cd content-provider && cdk destroy` (removes the CloudFront
   distribution and Lambda@Edge function).
5. **IAM roles** — delete the four `AgentCorePayments*` roles from the IAM console
   or via the AWS CLI when no longer needed.

---

## Shared responsibility

| Concern                       | AWS / AgentCore                                          | You (the customer)                                  |
|:------------------------------|:---------------------------------------------------------|:----------------------------------------------------|
| Runtime container isolation   | microVM per session, automatic teardown                  | Set `idleTimeout`, `maxLifetime` to your workload   |
| Payment signing keys          | Held in AgentCore identity / Coinbase CDP delegated      | Enable Delegated Signing in CDP project             |
| Spend limits                  | Service enforces `maxSpendAmount` per session            | Set per-session budget appropriate for the task     |
| IAM role segregation          | Runtime assumes the execution role you specified         | Author least-privilege role policies (see `setup_roles.sh`) |
| Observability ingestion       | Traces + metrics emitted automatically                   | Build alarms on the metrics you care about          |
| Wallet funding                | Embedded wallet provisioned by AgentCore                 | Fund via faucet (testnet) or onramp (production)    |
| Browser session security      | Containerized Chromium, ephemeral, optional recording    | Avoid logging in to production accounts via the agent |
