# Pay-For-API — Buyer Agent

A minimal Strands Agent, wired for Amazon Bedrock Claude Sonnet 4.5,
that buys a fact from the seller API by delegating the x402 payment to
**Amazon Bedrock AgentCore Payments** through the
`AgentCorePaymentsPlugin`.

Two ways to run the same agent:

| Mode | Where | When |
|------|-------|------|
| **Local** | Notebook cell in `pay-for-api.ipynb` (§8) | Teaching / fast iteration |
| **Runtime** | AgentCore Runtime container deployed via CDK (§9) | Production-shaped deploy |

The agent code is identical in both modes. The container folder wraps
the same `Agent()` construction in a FastAPI `/invocations` endpoint
so it fits the AgentCore Runtime contract.

## Prerequisites

Before deploying the agent runtime, complete the parent use-case
prerequisites in [`../README.md`](../README.md). Specifically:

- AWS account with Amazon Bedrock AgentCore Payments enabled in the
  target region
- Amazon Bedrock model access for `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- AWS CDK v2 (`npm install -g aws-cdk`) and Node.js 18+
- Python 3.10+ with the use-case venv active
- Completed §1-§6 of the parent notebook (so a `PaymentManager`,
  `PaymentInstrument`, and at least one `PaymentSession` exist for the
  runtime to invoke against)

## Folder layout

```
agent/
├── cdk/
│   ├── app.py              CDK app entry point
│   ├── agent_stack.py      ECR + IAM + Runtime
│   ├── cdk.json
│   └── requirements.txt
├── container/
│   ├── Dockerfile
│   ├── agent.py            FastAPI server + Strands Agent
│   └── requirements.txt
└── README.md
```

## How the payment flow works

1. The agent tries `http_request.GET <seller-url>/facts?topic=<x>`.
2. The seller returns **HTTP 402** with an x402 `accepts` array.
3. `AgentCorePaymentsPlugin` intercepts the 402, calls
   **`ProcessPayment`** against the configured Payment Manager,
   Session, and Instrument, receives the signed `CRYPTO_X402` proof,
   base64-encodes it into the `X-PAYMENT` header (per the x402 protocol
   spec), and retries the request transparently.
4. The seller verifies the proof with the x402 facilitator, settles
   on-chain, and returns the paid fact as **HTTP 200**.

The agent never sees a private key, never assembles the `X-PAYMENT`
header, and never touches a boto3 client. The only tool it calls is
`http_request`. The plugin does also register three read-only
management tools (`get_payment_instrument`,
`list_payment_instruments`, `get_payment_session`) but the system
prompt in §7 of the notebook tells the model not to use them — they
are reserved for operator debug flows.

## Identity model

- Every payment operation runs under the **vendor-level user ID** from
  `paymentInstrument.userId` — the value the service returns on
  `CreatePaymentInstrument`. The notebook captures that ID and passes
  it to the agent as `paymentUserId` on invocation.
- For Privy-backed instruments, this is the Privy DID.
- For Coinbase-backed instruments, this is the CDP end-user UUID (hub
  flow).
- There is **no tenant/Cognito sub on the wire** — identity is
  vendor-rooted end to end.

## Deploy

> ⚠️ **Cost notice:** This deploys an AgentCore Runtime, an Amazon ECR
> repository, an AWS CodeBuild project, an AgentCore Memory resource,
> and the supporting CloudWatch log groups. CodeBuild (per-build-minute)
> and the Runtime (per-invocation) are the highest-cost items. Run the
> [Clean up](#clean-up) steps when you are done.

```bash
cd agent/cdk
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap         # only once per account/region
cdk deploy
```

Outputs: `AgentRuntimeArn`, `AgentRuntimeEndpoint`,
`AgentExecutionRoleArn`, `AgentEcrRepoUri`,
`AgentBuildProjectName`, `AgentMemoryId`.

The notebook's §9 calls into the CDK for you. See
`pay-for-api.ipynb`.

## Clean up

Tear the runtime down when you no longer need it. The notebook's §11
runs the same teardown plus the AgentCore Payments resource cleanup.

```bash
bash test/integration/destroy-agent.sh
```

Or directly through CDK:

```bash
cd agent/cdk
source .venv/bin/activate
cdk destroy
```

This removes the AgentCore Runtime, the AgentCore Memory resource, the
ECR repository (with its images), and the CodeBuild project. Verify by
listing CloudFormation stacks:

```bash
aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?starts_with(StackName, 'AgentCorePaymentsBuyerAgent')].StackName"
```

The output should be empty.

## Conclusion

This folder packages the buyer-side half of the Pay-For-API use case
into a deployable AgentCore Runtime. The same Strands Agent pattern
runs locally in §7 of the parent notebook and in production-shaped
fashion through the CDK stack here, demonstrating how to graduate a
local agent prototype to a managed runtime without code changes. The
`AgentCorePaymentsPlugin` makes the x402 payment flow transparent to
the agent, so the same `http_request` tool call pays for content
through whichever wallet provider the operator configured.

For a deeper walkthrough, run `pay-for-api.ipynb` end to end. For the
service-side reference, see the
[AgentCore Payments documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments.html).
