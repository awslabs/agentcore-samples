# Pay-For-API

## Overview

**Amazon Bedrock AgentCore Payments** enables AI agents to make autonomous
payments for digital services. Agents never hold private keys or require
human approval for each transaction.

This use case builds a Strands agent that autonomously pays for
metered access to an HTTP API through AgentCore Payments. The agent
signs on either the Ethereum Virtual Machine (EVM) (Base Sepolia) or
Solana (Solana Devnet), driven by the configured `PaymentInstrument`.
The seller is a minimal "Fun Facts" service deployed via AWS CDK: an
Amazon API Gateway HTTP API backed by an AWS Lambda function that
charges **$0.01** per call and accepts either network in the x402
response.

When the agent requests a fact, the seller returns HTTP 402 with a
payment requirement. The agent forwards the requirement to AgentCore
Payments' `ProcessPayment` operation and receives a signed proof. It
then retries the request with the proof attached and returns the
content. The agent is designed never to touch a private key.

Internally, AgentCore Payments manages the wallet, the signing keys,
and the on-chain settlement. Whether the `PaymentManager` is wired to
**Coinbase Developer Platform (CDP)** or **Stripe via Privy**, the
agent code is identical. The service picks the right signer from the
connector tied to the instrument.

This notebook is **self-contained**. It provisions a full AgentCore
Payments stack inline (§5), creates two `EMBEDDED_CRYPTO_WALLET`
instruments under the same connector (ETHEREUM + SOLANA), and deploys
the seller from a CDK stack that lives alongside it (§3). If a
`PaymentManager` and at least one `PaymentInstrument` already exist,
the notebook detects them in §4 and skips the inline setup.


### Use Case Details

| Information         | Details                                                               |
|:--------------------|:----------------------------------------------------------------------|
| Use case type       | Agentic HTTP API consumption with autonomous micropayment             |
| AgentCore components| Amazon Bedrock AgentCore Payments                                     |
| Wallet providers    | Coinbase CDP ✅   ·   Stripe via Privy ✅                             |
| Payment protocol    | x402 (HTTP 402 Payment Required) on the wire                          |
| Agent type          | Single                                                                |
| Agentic Framework   | Strands Agents                                                        |
| LLM model           | Anthropic Claude Sonnet 4.5 (Amazon Bedrock, `us.` inference profile) |
| Example complexity  | Intermediate                                                          |
| SDK used            | boto3                                                                 |

### Architecture

Three parties participate in every paid request:

1. **Strands agent** — the only tool it calls is `http_request`. The
   `AgentCorePaymentsPlugin` intercepts HTTP 402 responses and handles
   the payment handshake transparently.
2. **Amazon Bedrock AgentCore Payments** — receives `ProcessPayment`,
   returns a signed x402 proof using the wallet tied to the instrument
   (Coinbase CDP or Privy).
3. **Seller (CDK stack)** — AWS Lambda function behind Amazon API
   Gateway that issues the 402 challenge, verifies the proof, and
   serves the content.

Four IAM roles separate concerns operationally, following the
**principle of least privilege**: each role has only the permissions
required for its specific operation, with explicit `Deny` statements
on actions reserved for other roles:

- `AgentCorePaymentsControlPlaneRole` — manages Manager, Connector, Credential Provider
- `AgentCorePaymentsManagementRole` — manages Instrument and Session (explicit `Deny` on `ProcessPayment`)
- `AgentCorePaymentsProcessPaymentRole` — signs payments, reads Instrument and Session
- `AgentCorePaymentsResourceRetrievalRole` — assumed by AgentCore Payments at runtime to retrieve credentials

`test/integration/setup-roles.sh` creates all four with the right
policies. See the public [IAM roles for AgentCore Payments](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-iam-roles.html)
reference for the full policy details and an explanation of the
separation-of-duties model.

<div style="text-align:left">
    <img src="images/architecture_pay_for_api.png" alt="Pay-for-API architecture diagram: a user prompts a Strands agent on AgentCore Runtime, the agent calls a paid HTTP API on Amazon API Gateway plus AWS Lambda, the seller returns HTTP 402 with a payment requirement, AgentCore Payments signs the payment via Coinbase CDP or Stripe via Privy, the agent retries with the signed proof, the seller settles on chain through the x402 facilitator and returns 200 OK, and the operator audits spend through GetPaymentSession." width="75%"/>
</div>

**Numbered flow (matches the diagram)**

1. **User** sends a query to the **Agent** (AgentCore Runtime + Strands).
2. The agent calls the paid API hosted on **Amazon API Gateway** → **AWS Lambda**.
3. The seller responds with **HTTP 402 Payment Required** and a payment requirement payload.
4. The agent forwards the requirement to **AgentCore Payments**, which selects the
   matching `PaymentInstrument`, checks the session budget, and signs the payment
   through the configured wallet provider (Coinbase CDP or Stripe via Privy).
5. The agent retries the request with the signed `X-PAYMENT` header. The seller
   verifies, settles on-chain through the x402 facilitator, and returns **200 OK** with the content.
6. The agent answers the user. The operator audits spend through `GetPaymentSession`.

### Use Case Key Features

* Agent is designed not to hold private keys — AgentCore Payments
  signs every charge via the configured `PaymentManager` and
  `PaymentConnector`
* Wallet-provider-agnostic — the same agent code runs against a Coinbase CDP
  instrument or a Stripe-via-Privy instrument
* Human-controlled budget via `maxSpendAmount` on the payment session
* IAM role separation: `ManagementRole` creates sessions, `ProcessPaymentRole` signs
  payments (explicit `Deny` in both directions, enforced by IAM rather than
  documentation)
* Full audit trail via `GetPaymentSession` — the operator sees exactly what the
  agent spent
* Self-contained — the notebook runs from a clean AWS account

---

## Payment Protocol Availability

AgentCore Payments supports multiple wallet providers. The wire format
(x402 for crypto settlement) is an implementation detail. The agent
code in this use case does not change based on provider. The service
picks the right signer from the connector tied to the instrument.

| Wallet Provider | Connector Type | Status | Notes |
|:----------------|:---------------|:-------|:------|
| **Coinbase CDP** | `CoinbaseCDP` | ✅ Available — EVM + Solana | API Key ID, API Key Secret, Wallet Secret. **Enable "Delegated signing"** under Project → Wallet → Embedded Wallets → Policies before use. Inline setup in §5 provisions a Coinbase CDP wallet. |
| **Stripe** (via Privy) | `StripePrivy` | ✅ Available — EVM + Solana | App ID, App Secret, Authorization Key ID, P-256 Authorization Private Key. Privy returns the private key prefixed with `wallet-auth:` — **strip the prefix** before storing it. Inline setup in §5 provisions a Privy-backed wallet. No hub redirect is needed for Privy: the authorization key registered on the credential provider is the signing delegation. |

---

## Prerequisites

- **AWS account** with Amazon Bedrock AgentCore Payments available in your chosen region
- **Amazon Bedrock access** enabled for **Anthropic Claude Sonnet 4.5** in your chosen region (cross-region inference profile `us.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- **Python 3.10+** with a Jupyter kernel. If you hit "Running cells requires the ipykernel package", install it once: `python3 -m pip install ipykernel --user`. Any Jupyter frontend works — JupyterLab (4.0+), classic Jupyter Notebook (7.0+), VS Code, or Kiro.
- **AWS Command Line Interface (AWS CLI) v2** configured with credentials (`aws configure`)
- **AWS Cloud Development Kit (CDK) v2** installed globally (`npm install -g aws-cdk`); used by the notebook to deploy the seller
- **Node.js 18+** — required by CDK
- **A wallet provider account** — Coinbase Developer Platform (CDP) (API Key ID, API Key Secret, Wallet Secret) or Stripe via Privy (App ID, App Secret, Authorization Key ID, P-256 Authorization Private Key)
- **Testnet USD Coin (USDC)** from the [Circle testnet faucet](https://faucet.circle.com/) on both **Base Sepolia** and **Solana Devnet**, because §5 creates one wallet per network

---

## Security

The use case relies on AgentCore Identity's **payment credential provider**
to manage wallet provider secrets. Once `CreatePaymentCredentialProvider`
runs in §4, AgentCore Identity stores the Coinbase / Privy API keys, app
secrets, and wallet or authorization secrets in **AWS Secrets Manager**,
encrypts them with **AWS Key Management Service (KMS)** keys, and surfaces
only the secret ARN to your agents (see [Configure credential
provider](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/resource-providers.html)).
The agent runtime calls `GetResourcePaymentToken` at signing time to
receive a short-lived vendor-specific token; it never sees the raw API key
or wallet secret.

What AgentCore Payments handles for you:

- **Secret storage** — wallet provider secrets land in AWS Secrets Manager
  under AgentCore Identity, encrypted with AWS-owned KMS keys (customer-
  managed KMS keys supported)
- **Secret retrieval** — agents call `GetResourcePaymentToken` and receive a
  vendor token. The agent runtime never receives the underlying API key,
  app secret, or wallet secret
- **Audit trail** — every `ProcessPayment` call writes to AWS CloudTrail
  and to the AgentCore Payments managed log group. Use `GetPaymentSession`
  for operator-visible spend totals
- **Budget enforcement** — the operator sets `maxSpendAmount` on the
  payment session. AgentCore Payments rejects any `ProcessPayment` that
  would exceed it
- **IAM least privilege** — the four roles in §2 each receive only the
  actions and resources required for one operation. Cross-role permissions
  are explicitly denied (`ManagementRole` cannot call `ProcessPayment`,
  `ProcessPaymentRole` cannot manage sessions or instruments)

What you handle locally:

- **Initial credential paste** — Coinbase / Privy secrets are pasted into
  `.env` once, before §4 runs. The notebook reads them only to call
  `CreatePaymentCredentialProvider`. After that call returns, the secrets
  are inside the AgentCore Identity-managed vault (Secrets Manager) and
  the local `.env` copies are no longer needed by the agent. They remain
  in `.env` so re-running §4 is idempotent
- **Encryption in transit** — all calls to AgentCore Payments, Amazon
  Bedrock, and the seller HTTP API run over TLS (`https://`). The
  Dockerfile health check is the only HTTP URL and is loopback-only

### Production hardening

This is an L100 tutorial. Before deploying anything resembling this
sample to production:

- **Drop `.env` after first run.** Once §4 has called
  `CreatePaymentCredentialProvider`, blank the secret values from `.env`.
  Subsequent notebook runs read the credential provider ARN from `.env`
  (which is non-sensitive) and the actual secrets stay in Secrets Manager
- **Use customer-managed KMS keys.** AgentCore Identity defaults to
  AWS-owned KMS keys; switch to customer-managed keys for additional
  audit and rotation control
- **Tighten IAM role wildcards.** Once Manager IDs are stable, replace
  `payment-manager/*` with the specific Manager ARN, or scope by tag
- **Switch the AgentCore Runtime to VPC mode** with private subnets and
  VPC endpoints for AWS APIs (the tutorial uses `networkMode=PUBLIC`)
- **Restrict the seller's Amazon API Gateway CORS** to the specific agent
  runtime domains that need to call it
- **Pin the `bedrock-agentcore` Python SDK and `@x402/*` Node packages**
  to specific versions in production builds

---

## Running the Use Case

Before opening the notebook, create a Python virtual environment so
dependency installs and notebook state stay isolated from the global
Python.

**Option 1 — Terminal (cross-platform)**

```bash
python3 -m venv .venv
source .venv/bin/activate    # On Windows: .venv\Scripts\activate
python3 -m pip install --upgrade pip ipykernel
python3 -m ipykernel install --user --name pay-for-api-venv --display-name "Python (pay-for-api-venv)"
```

**Option 2 — VS Code / Kiro**

1. Open `pay-for-api.ipynb`.
2. Choose the kernel selector in the top-right of the notebook (or the
   Python version indicator in the bottom status bar).
3. Choose **Python: Create Environment...**.
4. Choose **Venv**.
5. Pick a Python 3.10+ interpreter. The IDE creates `.venv/` and selects
   it automatically.
6. When prompted to install kernel dependencies (`ipykernel`), accept.

After the venv is active, open `pay-for-api.ipynb` and run cells in
order. The CLI equivalent of opening the notebook is:

```bash
jupyter notebook pay-for-api.ipynb
```

The notebook handles dependency install, IAM role creation, credential
prompts, seller deploy, payment provisioning, agent runs, and teardown:

- §1 installs the Python dependencies from `requirements.txt`
- §2 creates the four IAM roles and interactively prompts for wallet provider credentials (Coinbase CDP or Stripe via Privy)
- §3 deploys the Fun Facts seller stack via CDK and captures the URL
- §4 decides whether to run inline setup or reuse existing AgentCore Payments infrastructure
- §5 provisions a Credential Provider + Manager + Connector for the chosen provider, then creates two Payment Instruments (ETHEREUM + SOLANA) under the same connector
- §6 creates two budget-limited payment sessions, one per network
- §7 builds the Strands agent factory: one pattern that wraps the `AgentCorePaymentsPlugin` around whichever (instrument, session, network) is passed in
- §8 runs the agent once on EVM and once on Solana against the same seller
- §9 optionally deploys the agent to AgentCore Runtime via `agent/cdk/` and invokes it remotely
- §10 inspects the data plane for both networks: GetPaymentSession, balance, ListPaymentInstruments, ListPaymentSessions
- §11 tears everything down: sessions, seller stack, agent runtime (if §9 was run), and AgentCore Payments resources (optional)

---

## Key Notes

- The seller stack deploys to the same region as AgentCore Payments —
  set by `AWS_REGION` in `.env`.
- USDC amounts use 6 decimal places: `"$0.01"` → `10000` atomic units
  on the wire. The `@x402/hono` library handles the conversion.
- The seller emits multi-network `accepts` — one entry for EVM
  (Base Sepolia) and one for Solana (Devnet) when both payout wallets
  are configured. The agent picks the entry matching the instrument's
  network.
- Responses use the `{ x402_content, x402_meta }` shape so the seller
  is discoverable through the AgentCore Registry / Bazaar Model
  Context Protocol (MCP).
- The `ProcessPaymentRole` has an explicit IAM `Deny` on all session
  and instrument management; the `ManagementRole` has an explicit
  `Deny` on `ProcessPayment`. The trust boundary is enforced by IAM,
  not by documentation.
- The seller verifies payment proofs against the public x402
  facilitator (`https://x402.org/facilitator`). Point it at a private
  facilitator by editing `seller/lambda/index.js` and redeploying.
- When a `StripePrivy` instrument is used, the agent and the seller do
  not change. AgentCore Payments routes the signing request to Privy's
  key-management service transparently. Privy-backed instruments
  settle on both EVM (Base / Base Sepolia) and Solana (Solana / Solana
  Devnet).
- The agent never calls the plugin's read-only management tools
  (`get_payment_instrument`, `list_payment_instruments`,
  `get_payment_session`). Those are reserved for operator debug flows.
  The system prompt in §7 tells the model to use only `http_request`.

---

## Cleanup

> ⚠️ **Cost notice:** The resources deployed in this use case incur
> AWS charges while running. AWS Lambda, Amazon API Gateway, AgentCore
> Runtime, AgentCore Memory, and AgentCore Payments all bill on
> per-request and per-resource models. Run §11 of the notebook to tear
> them down when you are done.

§11 of the notebook handles teardown end-to-end:

| Step | What it does | What it removes |
|------|--------------|-----------------|
| Revoke session | `DeletePaymentSession` on each session created in §6 | Active session budgets (no undelete) |
| Tear down the seller stack | `cdk destroy` on the seller CDK app | Amazon API Gateway HTTP API, AWS Lambda function, IAM execution role |
| Tear down the agent runtime | `cdk destroy` on the agent CDK app (only if §9 was run) | AgentCore Runtime, AgentCore Memory, Amazon ECR repository, AWS CodeBuild project, IAM execution role |
| Tear down AgentCore Payments resources | Calls `DeletePaymentInstrument`, `DeletePaymentConnector`, `DeletePaymentManager`, `DeletePaymentCredentialProvider` in dependency order | All Manager / Connector / Instrument / Credential Provider resources created by §5 |
| Remove local build artifacts | Deletes `.venv/`, `cdk.out/`, `__pycache__/`, `outputs.json`, `privy-delegation/`, `seller/lambda/node_modules/` | Local working-copy files only — no cloud resources |

The IAM roles created by `setup-roles.sh` in §2 have no standing cost
and are retained for re-runs. To delete them by hand:

```bash
aws iam delete-role --role-name AgentCorePaymentsControlPlaneRole
aws iam delete-role --role-name AgentCorePaymentsManagementRole
aws iam delete-role --role-name AgentCorePaymentsProcessPaymentRole
aws iam delete-role --role-name AgentCorePaymentsResourceRetrievalRole
```

CloudWatch log groups under `/aws/bedrock-agentcore/` and `/bedrock-agentcore/payments/`
are retained after teardown so you can review historical traces. Delete
them from the CloudWatch console if you want to clear historical data.

### Manual cleanup (without the notebook)

If the notebook is unavailable, run the same teardown from a shell:

```bash
# 1. Destroy the seller stack
bash test/integration/destroy-seller.sh

# 2. Destroy the agent runtime stack (only if §9 was run)
bash test/integration/destroy-agent.sh

# 3. AgentCore Payments resources require boto3 calls — see §11 of
#    the notebook for the exact API sequence.
```

### Verify cleanup succeeded

Confirm no CloudFormation stacks remain:

```bash
aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?starts_with(StackName, 'AgentCorePayments')].StackName"
```

The output should be empty.

---

## Conclusion

This use case demonstrates how Amazon Bedrock AgentCore Payments
enables an AI agent to make autonomous micropayments for paid HTTP APIs
without holding private keys or requiring per-transaction human
approval. The same agent code paid for the same content through two
different wallet providers (Coinbase CDP and Stripe via Privy) and on
two different networks (EVM and Solana), demonstrating the
provider-agnostic and network-agnostic design.

Key takeaways:

- **Separation of concerns** — IAM roles isolate session creation,
  payment signing, and credential retrieval. The trust boundary is
  enforced by IAM, not by code.
- **Budget control** — operators set a maximum spend per session.
  AgentCore Payments enforces it, and `GetPaymentSession` provides a
  full audit trail.
- **Wire format** — x402 (HTTP 402 Payment Required) is the open spec
  on the wire. The `@x402/hono` library on the seller side and the
  `AgentCorePaymentsPlugin` on the agent side handle the protocol so
  that the application code remains a normal HTTP request.

Use the [Learn more](#learn-more) links to go deeper, and adapt the
patterns in this notebook to your own paid-API integrations.

---

## Learn more

Public AgentCore Payments documentation:

- [Overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments.html)
- [How it works](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-how-it-works.html)
- [Core concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-concepts.html)
- [Prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-prerequisites.html)
- [IAM roles](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-iam-roles.html)
- [Set up a credential provider](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-setup-credential-provider.html)
- [Create a payment manager](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-create-manager.html)
- [Create a payment instrument](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-create-instrument.html)
- [Create a payment session](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-create-session.html)
- [Process a payment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-process-payment.html) — plugin reference, interrupt contract, network preferences, `auto_payment=False` for human-in-the-loop flows
- [Connect to Bazaar](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-connect-bazaar.html) — make a seller discoverable through the AgentCore Registry

Announcement:
[Agents that transact — Introducing Amazon Bedrock AgentCore Payments, built with Coinbase and Stripe](https://aws.amazon.com/blogs/machine-learning/agents-that-transact-introducing-amazon-bedrock-agentcore-payments-built-with-coinbase-and-stripe/)
