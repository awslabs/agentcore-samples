# Pay for Secure Data (x402) — Agent

A Strands Agent, wired for Amazon Bedrock Claude Sonnet 4.5, that pays for
registered x402 services **only after** a t54 x402-secure trust check clears
a configured guardrail. Both the trust check and the target data call are
paid x402 calls; the `AgentCorePaymentsPlugin` handles each HTTP 402 →
`ProcessPayment` → retry transparently. The agent never holds wallet private
keys.

Two ways to run the same agent:

| Mode | Where | When |
|------|-------|------|
| **Local** | Notebook cell in `pay-for-x402-secure-data.ipynb` (§6–§7) | Teaching / fast iteration |
| **Runtime** | AgentCore Runtime container deployed via CDK (§8) | Production-shaped deploy |

The agent code is identical in both modes. The `container/` folder wraps the
same `Agent()` construction in a FastAPI `/invocations` endpoint so it fits
the AgentCore Runtime contract.

## Prerequisites

Before deploying the agent runtime, complete the parent use-case
prerequisites in [`../README.md`](../README.md). Specifically:

- AWS account with Amazon Bedrock AgentCore payments enabled in the target region
- Amazon Bedrock model access for `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- AWS CDK v2 (`npm install -g aws-cdk@2.1131.0`) and Node.js 18+
- Python 3.10+ with the use-case venv active
- Completed §4–§5 of the parent notebook (so a `PaymentManager`,
  `PaymentInstrument`, and `PaymentSession` exist for the runtime to sign
  against), with delegated signing granted and the wallet funded

## Folder layout

```
agent/
├── cdk/
│   ├── app.py              CDK app entry point
│   ├── agent_stack.py      ECR + CodeBuild + IAM + Runtime
│   ├── cdk.json
│   └── requirements.txt
├── container/
│   ├── Dockerfile
│   ├── main.py                  # stable import surface + uvicorn entrypoint
│   ├── http_app.py              # FastAPI /ping and /invocations app
│   ├── runtime_context.py       # request parsing, telemetry, payment-context helpers
│   ├── agent.py                 # Strands agent tools and prompt
│   ├── payments.py              # payment context + plugin config helpers
│   ├── x402_secure.py           # plugin-compatible t54 x402-secure client
│   ├── x402_services.py         # public compatibility exports
│   ├── x402_gateway.py          # trust-gated registered-service gateway
│   ├── x402_service_client.py   # registered target x402 HTTP client
│   ├── x402_service_registry.py # supported service catalog + operation validation
│   ├── x402_trust_state.py      # request-scoped trust state
│   └── requirements.txt
└── README.md
```

## Invocation contract

Paid `/invocations` calls require a per-invocation payment context. A single
deployment serves many users and sessions because the payment identifiers
travel on the request, not in the runtime environment:

```json
{
  "input": {
    "prompt": "Check trust for heurist_yahoo_finance, then fetch a quote snapshot for AAPL.",
    "payment_context": {
      "user_id": "<vendor-level user id>",
      "payment_session_id": "<payment-session-id>",
      "payment_instrument_id": "<payment-instrument-id>"
    }
  }
}
```

`GET /ping` returns `{"status": "healthy"}`.

## How the trust-gated payment flow works

1. The agent calls `check_x402_endpoint_trust` for the exact registered
   target endpoint URL.
2. t54 x402-secure returns **HTTP 402**; `AgentCorePaymentsPlugin` intercepts
   it, calls **`ProcessPayment`**, attaches the signed `X-PAYMENT` header, and
   retries the trust check. The successful trust result is stored in
   request-scoped state.
3. `call_trusted_x402_service` validates the requested `service_id`,
   `operation`, and payload, then checks the cached trust result. If trust is
   missing, expired, low-score, scam-flagged, or URL-mismatched, it returns a
   `blocked` result and **no target payment starts**.
4. If trust passes, the gateway calls the registered target x402 endpoint. The
   target can also return **HTTP 402**; the same plugin pays and retries.

The guardrail is enforced **in code** (`TrustedX402ServiceGateway`), not by
the prompt. The agent never assembles a payment header or touches a private
key — the only paid actions flow through the two tools above and the plugin.

## Identity model

- Every payment operation runs under the **vendor-level user ID** the caller
  supplies as `payment_context.user_id`.
- The runtime uses the **payment execution role** (`ProcessPaymentRole`) to
  call `ProcessPayment` within the session spending limit — it cannot create or modify
  sessions or instruments (explicit IAM `Deny`).

## Deploy

> ⚠️ **Cost notice:** This deploys an AgentCore Runtime, an Amazon ECR
> repository, an AWS CodeBuild project, and the supporting CloudWatch log
> groups. Live invocations settle **real USDC twice** per approved run (the
> t54 trust check and the target data call). Run the [Clean up](#clean-up)
> steps when you are done.

The notebook's §8 calls `deploy-agent.sh` for you. To deploy by hand from the
sample root:

```bash
bash test/integration/deploy-agent.sh
```

The container image is built in **AWS CodeBuild**, so no local Docker is
required. Outputs: `AgentRuntimeArn`, `AgentRuntimeEndpoint`,
`AgentExecutionRoleArn`, `AgentEcrRepoUri`, `AgentBuildProjectName`.

## Local run

From the sample root, with the venv active:

```bash
python -m pip install -r agent/container/requirements.txt
PYTHONPATH="$PWD/agent/container" uvicorn main:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/ping
```

Or run the agent directly as a one-shot CLI (fastest way to demo the paid
flow — settles real USDC when the target is approved). This is a
development/testing helper that prints raw agent output, so it is gated behind
an explicit `X402_ALLOW_DEV_CLI=1` opt-in and must only be used with synthetic
prompts, never production or third-party user data:

```bash
X402_ALLOW_DEV_CLI=1 PYTHONPATH="$PWD/agent/container" python agent/container/agent.py \
    "Check trust for heurist_yahoo_finance, then fetch a quote snapshot for AAPL."
```

> 🔒 **Network binding note.** The container binds uvicorn to `0.0.0.0` because
> AgentCore Runtime routes inbound traffic to the container on all interfaces —
> this is required by the Runtime contract. It is safe **only** because the
> Runtime enforces network-level access controls and the container has no
> inbound path other than the Runtime's authenticated invoke endpoint. Do
> **not** expose the container port directly (for example `docker run -p
> 8080:8080`) or run it on a public host outside the Runtime — all access must
> flow through the AgentCore Runtime invoke API. For production, deploy the
> Runtime in VPC mode (see the parent README's hardening notes) for
> network-level egress control and VPC Flow Logs.

## Clean up

Tear the runtime down when you no longer need it. The notebook's §10 runs the
same teardown plus the AgentCore payments resource cleanup.

```bash
bash test/integration/destroy-agent.sh
```

This removes the AgentCore Runtime, the ECR repository (with its images), and
the CodeBuild project. Verify by listing CloudFormation stacks:

```bash
aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?starts_with(StackName, 'AgentCorePaymentsX402SecureData')].StackName"
```

The output should be empty.

For the full walkthrough, run `pay-for-x402-secure-data.ipynb` end to end. For
the service-side reference, see the
[AgentCore payments documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments.html).
