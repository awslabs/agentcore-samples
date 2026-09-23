# Model governance and routing

Use explicit **provider** inference targets to control which models callers can reach, translate model IDs, and route deterministically across multiple providers behind one gateway endpoint. Provider configuration trades the connector's zero-config convenience for precise control, and lets you attach providers that have no built-in connector (such as Gemini).

## Overview

A connector target works out of the box. A **provider** target gives you explicit control through three fields: `endpoint`, `modelMapping`, and `operations`. This tutorial attaches three provider targets to one gateway and shows how the gateway routes between them:

- **bedrock**: Amazon Bedrock via `bedrock-mantle`, API key (Bedrock bearer token) outbound.
- **openai**: OpenAI directly, API key outbound.
- **gemini**: Google Gemini via its OpenAI-compatible endpoint, API key outbound. Gemini has no built-in connector, so a provider config is the only way to attach it.

The connector contrast (zero-config, single endpoint) is shown in [01-unified-multi-provider-access](../01-unified-multi-provider-access/).

![arch](./images/architecture.png)

## Connector vs provider

|                      | Connector               | Provider                                            |
| :------------------- | :---------------------- | :-------------------------------------------------- |
| Setup                | Zero config             | Explicit `endpoint` + `operations` + `modelMapping` |
| Model catalog        | Provider's full catalog | The models your `operations` advertise              |
| Path rewriting       | Automatic               | You configure `providerPath` overrides              |
| Unsupported provider | Not available           | Attach any OpenAI-compatible endpoint (e.g. Gemini) |

## Target configuration

Each target is created with the [`create_gateway_target`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateGatewayTarget.html) API. The `targetConfiguration.inference.provider` block sets the endpoint, model mapping, and operations allow-list.

### bedrock (API key outbound)

The gateway authenticates to `bedrock-mantle` with a Bedrock API key, passed as `Authorization: Bearer <key>` (the same outbound pattern as the OpenAI target). `modelMapping.providerPrefix` lets callers omit the `anthropic.` / `openai.` prefix. The `operations` allow-list advertises Claude and OpenAI-OSS families. The second `/v1/messages` operation overrides `providerPath` to Anthropic's native path.

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "bedrock",
  "targetConfiguration": {
    "inference": {
      "provider": {
        "endpoint": "https://bedrock-mantle.us-east-1.api.aws",
        "modelMapping": { "providerPrefix": { "strip": true, "separator": "." } },
        "operations": [
          {
            "path": "/v1/chat/completions",
            "models": [
              { "model": "anthropic.claude-opus-*" },
              { "model": "anthropic.claude-sonnet-*" },
              { "model": "openai.gpt-oss-*" }
            ]
          },
          {
            "path": "/v1/messages",
            "providerPath": "/anthropic/v1/messages",
            "models": [{ "model": "anthropic.claude-opus-*" }, { "model": "anthropic.claude-sonnet-*" }]
          }
        ]
      }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "API_KEY",
      "credentialProvider": {
        "apiKeyCredentialProvider": {
          "providerArn": "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/apikeycredentialprovider/governance-bedrock-key",
          "credentialLocation": "HEADER",
          "credentialParameterName": "Authorization",
          "credentialPrefix": "Bearer "
        }
      }
    }
  ]
}
```

### openai (API key outbound)

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "openai",
  "targetConfiguration": {
    "inference": {
      "provider": {
        "endpoint": "https://api.openai.com",
        "operations": [{ "path": "/v1/chat/completions", "models": [{ "model": "gpt-*" }] }]
      }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "API_KEY",
      "credentialProvider": {
        "apiKeyCredentialProvider": {
          "providerArn": "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/apikeycredentialprovider/governance-openai-key",
          "credentialLocation": "HEADER",
          "credentialParameterName": "Authorization",
          "credentialPrefix": "Bearer "
        }
      }
    }
  ]
}
```

### gemini (API key outbound)

Gemini exposes an OpenAI-compatible endpoint, so it attaches as a provider target with an API key credential.

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "gemini",
  "targetConfiguration": {
    "inference": {
      "provider": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai",
        "operations": [{ "path": "/v1/chat/completions", "models": [{ "model": "gemini-*" }] }]
      }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "API_KEY",
      "credentialProvider": {
        "apiKeyCredentialProvider": {
          "providerArn": "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/apikeycredentialprovider/governance-gemini-key",
          "credentialLocation": "HEADER",
          "credentialParameterName": "Authorization",
          "credentialPrefix": "Bearer "
        }
      }
    }
  ]
}
```

### Configuration fields

- **endpoint** (required): the HTTPS URL of the model provider.
- **modelMapping.providerPrefix**: when `strip` is `true`, callers can omit the provider prefix (`claude-sonnet-4-6` instead of `anthropic.claude-sonnet-4-6`); `separator` is the character between prefix and model name.
- **operations**: maps request paths to the models advertised on them. Each `model` is an exact ID or glob (`anthropic.claude-sonnet-*`). This allow-list governs what **unqualified** routing can match (see below).
- **providerPath**: forwards to a different path on the provider. Here the `/v1/messages` operation routes Anthropic-native requests to `/anthropic/v1/messages`.

## Model-based routing

The gateway routes by the `model` field in the request body:

1. **Qualified** (`{targetName}/{modelId}`): the prefix matches a target name, so the request goes to that target. For example, `bedrock/openai.gpt-oss-120b` goes to the `bedrock` target. Qualified requests address a target directly.
2. **Unqualified** (`gpt-oss-120b`): matched against the `operations` allow-lists of all targets. An exact match beats a glob. A model that no target advertises returns `404 "not found on any target"`.
3. **Collision**: when multiple targets match the same model at the same specificity, the gateway prefers the `bedrock` target. If no Bedrock target matches, it returns `409` asking you to qualify the model with a target prefix.

> Governance note: the `operations` allow-list shapes what unqualified routing can reach. To restrict the unqualified catalog, advertise only approved models in `operations`.

## Tutorial Details

| Information          | Details                               |
| :------------------- | :------------------------------------ |
| Tutorial type        | Interactive                           |
| AgentCore components | AgentCore gateway, AgentCore identity |
| gateway Target type  | Inference (provider)                  |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)           |
| Outbound Auth        | API key (Bedrock, OpenAI, Gemini)     |
| Example complexity   | Intermediate                          |
| SDK used             | boto3 + OpenAI SDK                    |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A **short-term** Amazon Bedrock API key for the `bedrock` target. Create one in the [Bedrock console](https://console.aws.amazon.com/bedrock) under **API keys** > **Short-term API keys**, or generate it programmatically with the [`aws-bedrock-token-generator`](https://pypi.org/project/aws-bedrock-token-generator/) package. The key inherits the permissions of the IAM principal that creates it, so ensure that principal has Bedrock model access.
- (Optional) `OPENAI_API_KEY` and/or `GEMINI_API_KEY` to add those targets.

> [!NOTE]
> Short-term Bedrock API keys are valid for up to 12 hours (or the duration of your session, whichever is shorter). For production workloads, refresh the key programmatically rather than pasting a static value. See [Production: rotating short-term keys](#production-rotating-short-term-keys) below.

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on inference patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../00-optional-setup/). Once deployed, capture the stack name:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
```

### Step 2: Create the gateway (boto3)

```bash
uv run python scripts/deploy_gateway.py \
  --name model-governance-gateway \
  --env-file scripts/model-governance/.env
```

### Step 3: Attach the provider targets

Provide the Bedrock API key (required for the `bedrock` target). Optionally add keys for the non-Bedrock providers; the script skips a target if its key is not set:

```bash
export AWS_BEARER_TOKEN_BEDROCK="..."   # required, for the bedrock target
export OPENAI_API_KEY="sk-..."          # optional, for the openai target
export GEMINI_API_KEY="..."             # optional, for the gemini target

uv run python scripts/model-governance/deploy.py
```

The script creates an API key credential provider for each key, grants the gateway role permission to fetch the stored keys, and creates the provider targets. The gateway injects each key as `Authorization: Bearer <key>` on outbound requests, so callers never hold the provider credentials.

### Production: rotating short-term keys

Short-term Bedrock API keys expire within 12 hours, so a static value is fine for this tutorial but not for long-running production workloads. The [`aws-bedrock-token-generator`](https://pypi.org/project/aws-bedrock-token-generator/) package issues and refreshes them: call `provide_token()` before each request, and it returns a cached token if still valid or generates a fresh one.

```python
from aws_bedrock_token_generator import provide_token
import requests

url = "https://bedrock-runtime.us-west-2.amazonaws.com/model/us.anthropic.claude-sonnet-4-6/converse"
payload = {"messages": [{"role": "user", "content": [{"text": "Hello"}]}]}

# Call provide_token() before each request; it handles caching and refresh.
token = provide_token()
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
response = requests.post(url, headers=headers, json=payload)
print(response.json())
```

Through the gateway, the same idea applies on the control-plane side. Store the token in AWS Secrets Manager and put a rotation schedule on the secret (a rotation interval well under 12 hours). The rotation function generates a fresh token with `provide_token()`, writes it to the secret, and updates the stored credential provider, so the gateway keeps injecting a valid key on outbound calls. See [Rotate AWS Secrets Manager secrets](https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html).

## Demo

Install Python dependencies (first time only):

```bash
uv sync
```

Run the demo. It shows unqualified routing (in vs not in the allow-list) and qualified routing:

```bash
uv run python scripts/model-governance/invoke.py
```

### What to expect

```text
Unqualified, in allow-list: model='gpt-oss-120b'
  OK: <one-sentence reply>

Unqualified, not in allow-list: model='deepseek.v3.2'
  Rejected (NotFoundError): Model 'deepseek.v3.2' not found on any target.

Qualified routing (bedrock): model='bedrock/openai.gpt-oss-120b'
  OK: <one-sentence reply>

Qualified routing (openai): model='openai/gpt-4o-mini'
  OK: <one-sentence reply>   # or "not found on any target" if the openai target was not deployed
```

`gpt-oss-120b` routes to the `bedrock` target because it advertises `openai.gpt-oss-*` and `modelMapping` strips the prefix. `deepseek.v3.2` is a real Bedrock model, but no target advertises it, so unqualified routing finds nothing. Qualifying with `bedrock/` addresses the target directly.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, run the cleanup script. It deletes the inference targets, the Bedrock/OpenAI/Gemini credential providers, the gateway, the gateway IAM role, and the tutorial's `.env` file:

```bash
uv run python scripts/model-governance/cleanup.py
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Summary

You attached three provider inference targets (Bedrock, OpenAI, Gemini) to one gateway, translated model IDs with prefix-stripping, advertised approved models through `operations`, and routed requests across targets by model string. Provider targets give you the explicit control and unsupported-provider reach that connectors abstract away.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
