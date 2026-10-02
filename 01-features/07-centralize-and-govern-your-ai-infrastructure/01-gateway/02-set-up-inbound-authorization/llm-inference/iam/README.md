# AWS IAM (SigV4) inbound auth

Set up an inference gateway whose **inbound** authorizer is AWS IAM. Callers sign each request with AWS Signature V4 using their own AWS credentials. No Cognito or other identity provider is involved.

## Overview

With `authorizerType: AWS_IAM`, the gateway authenticates callers by validating a SigV4 signature instead of a JWT. This suits service-to-service callers that already have AWS credentials. In this tutorial the gateway fronts two inference targets, and the same SigV4 inbound auth covers both:

- **bedrock-mantle**: connector target, IAM (SigV4) outbound to Amazon Bedrock.
- **openai**: connector target, API key outbound (the gateway injects the stored OpenAI key).

> [!NOTE]
> Inbound auth (client to gateway) is independent of outbound auth (gateway to provider). The two targets use different outbound mechanisms, but a caller reaches both the same way: a SigV4-signed request.

## How it works

1. The caller signs the request with AWS SigV4 (service `bedrock-agentcore`) using their AWS credentials.
2. The gateway validates the signature and the caller's IAM permissions.
3. The gateway routes by the `model` field and calls the target with the target's own outbound auth (IAM for bedrock-mantle, injected API key for openai).

The OpenAI and Anthropic SDKs cannot SigV4-sign requests, so this tutorial invokes the gateway with [`awscurl`](https://github.com/okigan/awscurl) (run via `uvx`). Any SigV4-capable HTTP client works.

## Tutorial Details

| Information          | Details                               |
| :------------------- | :------------------------------------ |
| Tutorial type        | Interactive                           |
| AgentCore components | AgentCore gateway, AgentCore identity |
| gateway Target type  | Inference (connector)                 |
| Inbound Auth         | AWS IAM (SigV4)                       |
| Outbound Auth        | AWS IAM (Bedrock), API key (OpenAI)   |
| Example complexity   | Intermediate                          |
| SDK used             | boto3 + awscurl                       |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Amazon Bedrock model access for the models you intend to call
- An OpenAI API key (`OPENAI_API_KEY`), required for the openai target

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Create the gateway with AWS IAM inbound auth (boto3)

No Cognito is needed. Pass `--authorizer-type AWS_IAM` to the shared gateway script:

```bash
uv run python scripts/deploy_gateway.py \
  --name inference-iam-inbound-gateway \
  --authorizer-type AWS_IAM \
  --env-file scripts/inference-iam-inbound/.env
```

### Step 2: Attach the inference targets

Provide the OpenAI key, then create both targets:

```bash
export OPENAI_API_KEY="sk-..."

uv run python scripts/inference-iam-inbound/deploy.py
```

## Demo

The gateway requires SigV4-signed requests. Capture the gateway URL, then call each target with `awscurl` (service `bedrock-agentcore`). The host is the gateway URL without the `/mcp` suffix.

```bash
export GATEWAY_URL=$(grep '^GATEWAY_URL=' scripts/inference-iam-inbound/.env | cut -d= -f2)
export INFERENCE_URL="${GATEWAY_URL%/mcp}/inference/v1/chat/completions"
export AWS_REGION=$(aws configure get region)
```

Call the Bedrock target (IAM outbound):

```bash
uvx awscurl --service bedrock-agentcore --region "$AWS_REGION" -X POST \
  "$INFERENCE_URL" \
  -H "Content-Type: application/json" \
  -d '{"model": "bedrock-mantle/openai.gpt-oss-120b", "messages": [{"role": "user", "content": "Reply with one short sentence."}]}'
```

Call the OpenAI target (API key outbound, injected by the gateway):

```bash
uvx awscurl --service bedrock-agentcore --region "$AWS_REGION" -X POST \
  "$INFERENCE_URL" \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "Reply with one short sentence."}]}'
```

Both calls authenticate inbound with your AWS credentials (SigV4). The gateway routes by the `model` prefix and applies each target's outbound auth.

> [!NOTE]
> Current open-source OpenAI and Anthropic SDKs do not support SigV4 signing. Use `awscurl` or a custom SigV4 HTTP client for IAM inbound auth. For JWT inbound auth (where the SDKs work directly), see [custom_jwt](../custom_jwt/).

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, run the cleanup script. It deletes both inference targets, the OpenAI credential provider, the gateway, the gateway IAM role, and the tutorial's `.env` file:

```bash
uv run python scripts/inference-iam-inbound/cleanup.py
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
