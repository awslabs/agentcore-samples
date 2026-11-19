# Pay for x402 APIs on AWS Marketplace with AWS Billing

## Overview

In this sample, an agent finds an API on AWS Marketplace, buys data from it one request at a time,
and the charges go on your AWS bill. You don't set up a wallet, buy stablecoins or sign up with the
seller.

> **Real charges.** Unlike the testnet samples in this folder, every paid request in this sample is
> a real purchase on your AWS bill, at the seller's price.

The agent does two jobs. To **discover** a seller, it searches AWS Marketplace through an Amazon
Bedrock AgentCore Gateway with the [AWS Marketplace
connector](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-aws-marketplace.html)
(`aws-marketplace`), which returns the seller's API endpoint. To **buy**, it pays through Amazon Bedrock AgentCore payments with AWS Billing. The
gateway is optional. Without it, you give the agent a seller URL.

The seller's API speaks [x402](https://x402.org/), an open standard for payments over HTTP. If you
call it without paying, it answers `HTTP 402` with a signed price quote. The agent hands that quote
to AgentCore payments (`ProcessPayment` with `paymentType=AWS_BILLING_X402`). AgentCore payments
checks the quote against the agent's session budget, and AWS Marketplace returns a payment token.
The agent repeats the request with the token attached and gets the content.

The first time your account buys from a seller, AWS Marketplace creates the agreement with that
seller for you. Every payment after that is metered against the same agreement and shows up as an
AWS Marketplace line item on your bill.

### Use case details

| Information | Details |
| --- | --- |
| Use case type | Conversational |
| Agent type | Single agent |
| Use case components | AgentCore payments (AWS Billing connector), AgentCore Gateway (`aws-marketplace` connector), tools |
| Use case vertical | Cross-industry: buying API data per request |
| Example complexity | Intermediate |
| SDK used | boto3, Strands Agents |
| LLM model | Anthropic Claude Sonnet 4.6 on Amazon Bedrock |
| Payment protocol | x402 v2, `exact` scheme, USD |
| Settlement | AWS Marketplace, on your AWS bill |

### Use case architecture

```
User ─► Strands agent
          │
          ├─ discover ─► AgentCore Gateway (aws-marketplace connector)
          │                search_marketplace ─► get_listing ─► get_seller_endpoints ─► seller URL
          │
          └─ buy ──────► fetch_paid_api(url)
                                │
                                ├─1─► GET seller API ──────────────────► 402 + PAYMENT-REQUIRED (quoteToken)
                                ├─2─► ProcessPayment(AWS_BILLING_X402) ► AgentCore payments
                                │                                          ├ checks session budget
                                │                                          └ AWS Marketplace issues payment token
                                ├─3─► GET seller API + PAYMENT-SIGNATURE ► 200 + content
                                │
                                └──── AWS Marketplace (async): agreement on first purchase, MeterUsage ─► your AWS bill
```

### Use case key features

The agent can't spend more than you allow. The sample enforces five limits:

- **Separate roles for budgets and spending.** The script creates the payment session as
  `MarketplaceBuyerSessionRole`, which is explicitly denied `ProcessPayment`. Then it runs the agent
  as `MarketplaceBuyerAgentRole`, which can pay within a session but is explicitly denied creating
  one. The agent can't raise its own budget, and IAM enforces that, not the prompt.
- **Session budget.** Every payment comes out of a payment session's `maxSpendAmount`. AgentCore
  payments enforces it on the server, so a misbehaving agent can't spend past it.
- **Per-request cap.** The client refuses any single quote above `MAX_PRICE_PER_CALL_USD`.
- **Seller allowlist.** The client only pays sellers that AWS Marketplace returned during this run,
  or the one `SELLER_URL` you set. If something the agent reads points it at a different URL, the
  client refuses to pay, even if the model tries.
- **Idempotency.** Every payment carries a `clientToken`. If you retry with the same `clientToken`
  and quote, you get the original payment back and aren't charged twice.

### Files

| File | Purpose |
| --- | --- |
| `setup_aws_billing.py` | One-time setup. Creates the roles, payment manager and AWS Billing connector, then writes `.env`. Running it again reuses what's already there. |
| `setup_gateway.py` | Optional one-time setup. Creates a gateway with the `aws-marketplace` connector and adds `GATEWAY_URL` to `.env`. |
| `strands_marketplace_agent.py` | The agent. |
| `marketplace_payments.py` | A boto3 wrapper for AgentCore payments, the x402 header handling, and the pay-and-retry loop. |
| `marketplace_discovery.py` | A small MCP client for the gateway, plus the search, listing and endpoint lookups. |
| `cleanup.py` | Deletes the connector and the payment manager. Add `--gateway` to delete the gateway, and `--roles` to delete the two buyer roles. |
| `env-sample.txt` | Every variable the sample reads from `.env`. |

## Prerequisites

- Python 3.10 or later
- AWS credentials for an identity that can create IAM roles and AgentCore payments resources, in a
  [Region where AgentCore payments is
  available](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
- Access to `us.anthropic.claude-sonnet-4-6` in Amazon Bedrock
- For discovery, AgentCore Gateway, which `setup_gateway.py` sets up with the AWS Marketplace
  connector. Without the gateway, the URL of an x402-enabled AWS Marketplace seller API

## Use case setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python setup_aws_billing.py --region us-east-1
python setup_gateway.py --region us-east-1      # or: echo "SELLER_URL=https://<seller-api>" >> .env
```

`pip install -r requirements.txt` installs boto3, Strands Agents and the other dependencies from
PyPI. See [boto3 version](#boto3-version) for how the sample handles a boto3 release that doesn't
include the AWS Billing payment operations.

### What setup creates

`setup_aws_billing.py` creates:

1. **`AWSServiceRoleForMarketplaceAgenticCommerce`.** AWS Marketplace uses this service-linked role
   to create your agreement with the seller and to meter usage. You need one per account. See
   [Service-linked role for Amazon Bedrock AgentCore
   payments](https://docs.aws.amazon.com/marketplace/latest/buyerguide/buyer-service-linked-role-agentic-commerce.html).
2. **`AgentCorePaymentsResourceRetrievalRole`.** The payment manager's service role. It's limited to
   your account and to payment managers with this sample's name. It only has workload identity
   permissions, because AWS Billing has no credential provider.
3. **A payment manager with the `AWS_IAM` authorizer.** AWS Billing doesn't work with `CUSTOM_JWT`,
   because the charge goes to the AWS account that signs the request.
4. **An AWS Billing connector.** You get one per manager, and there are no credentials to supply.
5. **`MarketplaceBuyerSessionRole`.** Creates, reads and deletes payment sessions on this payment
   manager. Explicitly denied `ProcessPayment`.
6. **`MarketplaceBuyerAgentRole`.** What the agent runs as. It can call `ProcessPayment` and
   `GetPaymentSession` on this payment manager, and invoke Anthropic models on Amazon Bedrock. It's
   explicitly denied creating or deleting sessions, updating the manager and creating connectors,
   and it isn't granted any other payments actions. `setup_gateway.py` also lets it call the
   gateway.

Both roles trust your account, so whoever runs the sample needs `sts:AssumeRole` on them. After
setup, the agent itself never uses the identity that ran setup.

`setup_gateway.py` creates:

1. **`AgentCoreGatewayMarketplaceRole`.** The gateway's execution role. It can only call three
   read-only AWS Marketplace actions, `SearchListings`, `GetListing` and `ListFulfillmentOptions`,
   on AWS Marketplace catalog listings and products.
2. **A gateway with the `AWS_IAM` authorizer**, in us-east-1 unless you pass `--region`. The
   connector is available in every commercial Region where AgentCore Gateway is.
3. **A target on the `aws-marketplace` connector** that exposes those three operations as MCP tools.
   Each tool configuration needs `parameterValues`, even when it's empty. See [Add a Connector target
   with AWS
   Marketplace](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-api-target-config.html#gateway-add-target-api-connector-aws-marketplace).
4. **Permission for `MarketplaceBuyerAgentRole` to call the gateway**
   (`bedrock-agentcore:InvokeGateway` on this gateway only).

The agent wraps the connector's tools in its own tools, with descriptions that tell the model what
each one returns and what order to call them in.

## Execution instructions

```bash
python strands_marketplace_agent.py
python strands_marketplace_agent.py "Find an x402 search API on AWS Marketplace and buy one result"
```

With the gateway set up, the agent searches, looks at the listings, gets endpoints, and then pays
once. The exact order varies from run to run. In this example it compared two listings before
buying:

```
$ python strands_marketplace_agent.py
...
Tool calls: search_marketplace -> get_remaining_budget -> search_marketplace -> search_marketplace -> get_listing -> get_listing -> get_seller_endpoints -> get_seller_endpoints -> fetch_paid_api -> get_remaining_budget
...
Payments this run: 1, total $0.01
```

Discovery calls are free. Only `fetch_paid_api` costs money.

To see the budget stop the agent, run `SESSION_BUDGET_USD=0.005 python
strands_marketplace_agent.py`. With a $0.01 seller, the payment is refused. The agent tells you why,
and nothing is charged.

## Troubleshooting

Most setup failures come from the AWS Marketplace service-linked role or a Region mismatch, and the
error doesn't always say which one.

| Error | Likely cause |
| --- | --- |
| 400 when creating the connector, saying the AWS Marketplace service-linked role doesn't exist | `AWSServiceRoleForMarketplaceAgenticCommerce` isn't in the account. Setup creates it unless you pass `--skip-slr`. |
| 403 when creating the connector: `Not authorized to perform iam:GetRole on the AWS Marketplace service-linked role` | The identity running setup can't read the service-linked role. |
| 403 `Payment manager not found` from `ProcessPayment` | `PAYMENT_MANAGER_ARN` is from a different Region than `AWS_REGION`. Payment managers are Regional. |
| 403 from `ProcessPayment` for one seller only | Your organization's Private Marketplace may block that seller's product. |
| Any other 400, 403 or 404 from `ProcessPayment` | The product may have been withdrawn, or its listing may not support AWS Billing. Check the listing on AWS Marketplace. |

## How the x402 exchange works

You only need this section if you're building the integration yourself.

Most sellers send the challenge as base64 JSON in the `PAYMENT-REQUIRED` response header, with a
body of just `{}`. Some send `PAYMENT-REQUIRED: true` and put the challenge JSON in the body, so
read the body when the header doesn't decode. The AWS Marketplace option is the `accepts` entry
whose `network` is `aws:base`, with a USD asset and an `extra.quoteToken`. A seller can list other
options, such as on-chain ones, alongside it, so pick this entry and ignore the rest:

```json
{
  "x402Version": 2,
  "resource": { "url": "https://<seller-api>/", "description": "..." },
  "accepts": [{
    "scheme": "exact", "network": "aws:base", "asset": "iso4217:USD", "amount": "0.01",
    "payTo": "<seller-defined>", "maxTimeoutSeconds": 300,
    "extra": { "quoteToken": "<quoteToken>" }
  }]
}
```

Pay the quote. AWS Billing uses the fixed instrument ID `aws.agentcorepayments.awsbilling.v1`, so
there's no instrument to create, and `payload` is the `accepts` entry with `network: aws:base`,
unchanged:

```http
POST /payments/processPayment
{
  "paymentManagerArn": "...", "paymentSessionId": "...",
  "paymentType": "AWS_BILLING_X402",
  "paymentInstrumentId": "aws.agentcorepayments.awsbilling.v1",
  "paymentInput": { "awsBillingX402": { "version": "2", "payload": <accepts[i]> } },
  "clientToken": "<uuid>"
}
```

A successful response has `status` set to `PROOF_GENERATED` and the payment token, a signed JWT, in
`paymentOutput.awsBillingX402.paymentToken`.

Then send the original request again with an x402 v2 payment payload in the `PAYMENT-SIGNATURE`
header, as base64 JSON. `accepted` must be the same entry whose quote you paid:

```text
{ "x402Version": 2, "resource": <resource>, "accepted": <accepts[i]>, "payload": { "paymentToken": "<token>" } }
```

The seller sets each quote's expiry, which can be only minutes, and payment tokens are short-lived,
so get the quote, pay it and send the retry in one pass.

## Before you take this to production

- **Move session creation into your backend.** In the sample, one script assumes both roles, one
  after the other. In production, create sessions in your backend with the session role, and give
  the agent's process only the agent role, so the agent's environment never holds credentials that
  can set a budget. Hosting the agent on AgentCore Runtime makes this the default, because the agent
  only ever runs as its execution role. See [IAM roles for AgentCore
  payments](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-iam-roles.html).
- **Don't log payment tokens.** Anyone holding a token can redeem it, so treat it like a password.
- **Private Marketplace still applies.** If your organization's Private Marketplace blocks the
  seller's product, the payment fails with a 403.
- **Payments are processed in us-east-1.** AWS Marketplace handles every payment there, which is why
  the sample defaults to us-east-1. Other Regions work, but their payment calls go cross-Region and
  add a little latency.

### boto3 version

The sample makes every AgentCore payments call through boto3. If your boto3 release doesn't include
the AWS Billing operations (creating the `AwsBilling` connector, and `ProcessPayment` with
`AWS_BILLING_X402`), `AgentCorePaymentsClient` logs a warning and sends just those two calls as
SigV4-signed HTTP requests, in the same request format. Run `pip install -U boto3 botocore` to pick
up a release that includes them.

## Clean up instructions

```bash
python cleanup.py
```

This deletes the connector and payment manager. `--gateway` also deletes the gateway and its target,
and `--roles` also deletes `MarketplaceBuyerSessionRole` and `MarketplaceBuyerAgentRole`.

The service-linked role and the payment manager's service role always stay, because other payment
managers may use them. To remove the service-linked role, follow the AWS Marketplace link under
[What setup creates](#what-setup-creates). You can only delete it after AWS Marketplace has finished
metering your payments. `cleanup.py` also leaves the gateway's execution role,
`AgentCoreGatewayMarketplaceRole`, so delete it yourself if you don't need it.

## Disclaimer

The examples provided in this repository are for experimental and educational purposes only. They
demonstrate concepts and techniques but are not intended for direct use in production environments.
Make sure to have Amazon Bedrock Guardrails in place to protect against [prompt
injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).
