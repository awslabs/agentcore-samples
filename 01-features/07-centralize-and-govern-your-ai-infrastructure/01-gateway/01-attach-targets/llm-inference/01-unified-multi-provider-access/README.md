# Unified multi-provider access

Front LLM traffic to model providers using Amazon Bedrock AgentCore Gateway. Clients connect once and switch between models by changing the model string. There are no per-provider SDKs, endpoints, or credentials to manage.

![arch](../images/architecture.png)

## Overview

An inference target turns AgentCore gateway into a unified LLM proxy. You attach one target per model provider, and the gateway routes each request to the right provider based on the `model` field in the request body. This tutorial attaches three **connector** targets (the zero-configuration option) so a single endpoint serves all three providers.

Why route LLM traffic through the gateway:

- **One endpoint, many providers**: clients use the OpenAI or Anthropic SDK against the gateway and reach Bedrock, OpenAI, or Anthropic by changing the model string.
- **Credential abstraction**: clients authenticate to the gateway; the gateway authenticates to each provider on their behalf. Provider API keys never leave the gateway.
- **Aggregated discovery**: list every available model across all providers through a single `/inference/v1/models` call.

## Connector vs provider

Inference targets come in two forms. This tutorial uses **connectors**:

| Form          | When to use                                                                                                                                                                                                                          |
| :------------ | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Connector** | Zero configuration for supported providers (`bedrock-mantle`, `openai`, `anthropic`). The gateway handles operations, model discovery, model-ID translation, and path rewriting automatically. Recommended for most cases.           |
| **Provider**  | Explicit control over endpoint, model mappings, and operations. Use when you need to restrict models or connect a provider without a built-in connector. See [03-model-governance-and-routing](../03-model-governance-and-routing/). |

## Tutorial Details

| Information          | Details                                        |
| :------------------- | :--------------------------------------------- |
| Tutorial type        | Interactive                                    |
| AgentCore components | AgentCore gateway, AgentCore identity          |
| gateway Target type  | Inference (connector)                          |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)                    |
| Outbound Auth        | AWS IAM (Bedrock), API key (OpenAI, Anthropic) |
| Example complexity   | Easy                                           |
| SDK used             | boto3 + OpenAI SDK, Anthropic SDK, LiteLLM     |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Amazon Bedrock model access for the Claude models you intend to call
- (Optional) `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` to add those providers. Bedrock alone needs only AWS credentials.

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

Create a gateway with Cognito inbound authorization. The shared `deploy_gateway.py` script reads the Cognito outputs and writes `GATEWAY_ID` / `GATEWAY_URL` to the tutorial's `.env`:

```bash
uv run python scripts/deploy_gateway.py \
  --name unified-multi-provider-gateway \
  --env-file scripts/unified-multi-provider/.env
```

### Step 3: Attach the inference targets

Each provider is attached as a connector inference target using the [`create_gateway_target`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateGatewayTarget.html) API. The target configuration is identical in shape; only the `connectorId` and the outbound credential differ. The sub-steps below show the `create_gateway_target` request parameters for each provider, and the `deploy.py` script (Step 3.4) calls this API for all three.

#### Step 3.1: Amazon Bedrock (IAM outbound)

Bedrock accepts AWS IAM authentication, so the target uses `GATEWAY_IAM_ROLE` and no secret is stored. The gateway role is granted `bedrock-mantle:ListModels` and `bedrock-mantle:CreateInference`.

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "bedrock-mantle",
  "targetConfiguration": {
    "inference": {
      "connector": { "source": { "connectorId": "bedrock-mantle" } }
    }
  },
  "credentialProviderConfigurations": [{ "credentialProviderType": "GATEWAY_IAM_ROLE" }]
}
```

#### Step 3.2: OpenAI (API key outbound)

OpenAI requires an API key. Store it in an API key credential provider; the gateway injects it as the `Authorization: Bearer <key>` header outbound.

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "openai",
  "targetConfiguration": {
    "inference": {
      "connector": { "source": { "connectorId": "openai" } }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "API_KEY",
      "credentialProvider": {
        "apiKeyCredentialProvider": {
          "providerArn": "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/apikeycredentialprovider/unified-openai-key",
          "credentialLocation": "HEADER",
          "credentialParameterName": "Authorization",
          "credentialPrefix": "Bearer "
        }
      }
    }
  ]
}
```

#### Step 3.3: Anthropic (API key outbound)

Anthropic also uses an API key, but injected as the `x-api-key` header (no prefix).

```json
{
  "gatewayIdentifier": "GATEWAY_ID",
  "name": "anthropic",
  "targetConfiguration": {
    "inference": {
      "connector": { "source": { "connectorId": "anthropic" } }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "API_KEY",
      "credentialProvider": {
        "apiKeyCredentialProvider": {
          "providerArn": "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/apikeycredentialprovider/unified-anthropic-key",
          "credentialLocation": "HEADER",
          "credentialParameterName": "x-api-key"
        }
      }
    }
  ]
}
```

| Provider       | `connectorId`    | Outbound auth      | Credential header             |
| :------------- | :--------------- | :----------------- | :---------------------------- |
| Amazon Bedrock | `bedrock-mantle` | `GATEWAY_IAM_ROLE` | none (SigV4)                  |
| OpenAI         | `openai`         | `API_KEY`          | `Authorization: Bearer <key>` |
| Anthropic      | `anthropic`      | `API_KEY`          | `x-api-key: <key>`            |

#### Step 3.4: Create all three targets

Provide provider API keys for any non-Bedrock provider you want to include:

```bash
export OPENAI_API_KEY="sk-..."         # optional
export ANTHROPIC_API_KEY="sk-ant-..."  # optional
```

The `deploy.py` script creates the API key credential providers, grants the gateway role the required permissions, and creates all three connector targets shown above:

```bash
uv run python scripts/unified-multi-provider/deploy.py
```

The script skips OpenAI or Anthropic if the corresponding API key is not set, and the gateway still works for the providers you configured.

## Inference APIs

You reach the gateway over its `/inference` path. Which API you call depends on what you want to do and which model family you target. The gateway forwards each to the matching provider operation.

```bash
https://{gatewayId}.gateway.bedrock-agentcore.{region}.amazonaws.com/inference/{path}
```

Replace {path} with the inference operation path (for example, v1/chat/completions, v1/responses, or v1/messages).

Because the gateway is OpenAI and Anthropic-compatible, you use the standard provider SDKs, just pointed at the gateway:

```python
from openai import OpenAI
import anthropic
from inference_demo import gateway_token

token = gateway_token(cognito_stack_name)  # Cognito gateway JWT (inbound credential)

# OpenAI SDK client: base /inference/v1, JWT as api_key
openai_client = OpenAI(
    base_url=f"{gateway_url}/inference/v1",
    api_key=token,
)

# Anthropic SDK client: base /inference, JWT as auth_token
anthropic_client = anthropic.Anthropic(
    base_url=f"{gateway_url}/inference",
    auth_token=token,
)
```

The `SDK call` column below refers to these two clients:

| Path                             | API                       | Style                                                                    | Model families                                                                             | SDK call                                                          |
| :------------------------------- | :------------------------ | :----------------------------------------------------------------------- | :----------------------------------------------------------------------------------------- | :---------------------------------------------------------------- |
| `/inference/v1/models`           | Models                    | Discovery                                                                | all                                                                                        | `openai_client.models.list()` or `anthropic_client.models.list()` |
| `/inference/v1/chat/completions` | Chat Completions (OpenAI) | Stateless multi-turn. You send the full message history each time.       | OpenAI-compatible (`openai.*`, `mistral.*`, `qwen.*`, `deepseek.*`, `google.gemma-*`, ...) | `openai_client.chat.completions.create()`                         |
| `/inference/v1/responses`        | Responses (OpenAI)        | Stateful and agentic. Server-side conversation state and built-in tools. | OpenAI-compatible families                                                                 | `openai_client.responses.create()`                                |
| `/inference/v1/messages`         | Messages (Anthropic)      | Claude-native request/response format                                    | Anthropic Claude (`anthropic.*`)                                                           | `anthropic_client.messages.create()`                              |

The Models API is provider-agnostic: either client's `models.list()` calls the same `/inference/v1/models` endpoint and returns the same aggregated catalog. The model family only determines the **invocation** API. Use **Chat Completions** for simple stateless chat, **Responses** when you want the model to manage conversation state or use built-in tools, and **Messages** for Claude models that expect the Anthropic-native format. All three accept `"stream": true` for incremental responses.

> [!NOTE]
> Inbound auth is the gateway JWT, passed as the SDK credential. The OpenAI SDK sends it as `Authorization: Bearer` via `api_key`. The Anthropic SDK needs `auth_token` instead, because its `api_key` is sent as `x-api-key`, which the gateway does not use for inbound auth.

## Demo

Install Python dependencies (first time only):

```bash
uv sync
```

Every script shares the same setup: fetch the Cognito gateway JWT, then build the two SDK clients pointed at the gateway:

```python
from openai import OpenAI
import anthropic
from inference_demo import gateway_token

token = gateway_token(cognito_stack)  # Cognito gateway JWT (inbound credential)

openai_client = OpenAI(base_url=f"{gateway_url}/inference/v1", api_key=token)
anthropic_client = anthropic.Anthropic(base_url=f"{gateway_url}/inference", auth_token=token)
```

The per-pattern snippets below use `openai_client` and `anthropic_client` from this setup.

### The four inference APIs

[`invoke.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke.py) walks through all four APIs in labeled parts:

```python
# Part 1: Models (discovery)
for model in openai_client.models.list().data:
    print(model.id)

# Part 2: Chat Completions (stateless, OpenAI-compatible families)
openai_client.chat.completions.create(
    model="bedrock-mantle/openai.gpt-oss-120b",
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)

# Part 3: Responses (stateful / agentic)
openai_client.responses.create(
    model="bedrock-mantle/openai.gpt-oss-120b", input="Reply with one short sentence."
)

# Part 4: Messages (Anthropic-native, Claude models)
anthropic_client.messages.create(
    model="bedrock-mantle/anthropic.claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)
```

```bash
uv run python scripts/unified-multi-provider/invoke.py
```

### Streaming responses

[`invoke_streaming.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke_streaming.py) runs the same four APIs with `stream=True`, printing tokens as they arrive. Each API has a different streaming shape:

```python
# Chat Completions: iterate chunks, read delta.content
for chunk in openai_client.chat.completions.create(
    model="bedrock-mantle/openai.gpt-oss-120b",
    messages=[{"role": "user", "content": "Count from 1 to 5."}],
    stream=True,
):
    delta = chunk.choices[0].delta.content
    if delta:
        print(delta, end="", flush=True)

# Responses: iterate events, read response.output_text.delta
for event in openai_client.responses.create(
    model="bedrock-mantle/openai.gpt-oss-120b", input="Count from 1 to 5.", stream=True
):
    if event.type == "response.output_text.delta":
        print(event.delta, end="", flush=True)

# Messages (Anthropic): use the stream() context manager and text_stream
with anthropic_client.messages.stream(
    model="bedrock-mantle/anthropic.claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Count from 1 to 5."}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

```bash
uv run python scripts/unified-multi-provider/invoke_streaming.py
```

### Native provider SDKs through the gateway

[`invoke_native_sdk.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke_native_sdk.py) shows you keep using the OpenAI and Anthropic SDKs you already know, pointed at the gateway. The same OpenAI client reaches a model on Bedrock by changing only the model string:

```python
# OpenAI SDK -> OpenAI model hosted on Amazon Bedrock
openai_client.chat.completions.create(
    model="bedrock-mantle/openai.gpt-oss-120b",
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)

# Anthropic SDK -> Claude model hosted on Amazon Bedrock
anthropic_client.messages.create(
    model="bedrock-mantle/anthropic.claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)
```

```bash
uv run python scripts/unified-multi-provider/invoke_native_sdk.py
```

### Direct to the provider (not through Bedrock)

[`invoke_direct.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke_direct.py) routes to the providers' own APIs through the `openai` and `anthropic` connector targets. The code is identical; only the target prefix in the model string changes from `bedrock-mantle/` to `openai/` or `anthropic/`:

```python
# OpenAI SDK -> OpenAI directly (the `openai` connector target)
openai_client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)

# Anthropic SDK -> Anthropic directly (the `anthropic` connector target)
anthropic_client.messages.create(
    model="anthropic/claude-haiku-4-5",
    max_tokens=256,
    messages=[{"role": "user", "content": "Reply with one short sentence."}],
)
```

```bash
uv run python scripts/unified-multi-provider/invoke_direct.py
```

### One interface for everything with LiteLLM

[`invoke_litellm.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke_litellm.py) uses [LiteLLM](https://github.com/BerriAI/litellm) to reach OpenAI-compatible gateway models through one `completion()` call. Point LiteLLM at the gateway with the `openai/` provider prefix plus `api_base` and `api_key`, then address any model by its gateway id (OpenAI, Qwen, DeepSeek, and more, all served by Bedrock):

```python
import litellm
from inference_demo import inference_base_url

base_url = inference_base_url(gateway_url, "/inference/v1")

# LiteLLM's completion() does not list models. Use the OpenAI SDK for the
# catalog (the gateway is OpenAI-compatible).
for model in openai_client.models.list().data:
    print(model.id)

# One call shape for any OpenAI-compatible model behind the gateway. The
# `openai/` prefix selects LiteLLM's OpenAI-compatible client for the custom
# api_base; everything after it is the gateway model id.
for model in [
    "bedrock-mantle/openai.gpt-oss-120b",
    "bedrock-mantle/qwen.qwen3-32b",
    "bedrock-mantle/deepseek.v3.2",
]:
    litellm.completion(
        model=f"openai/{model}",
        messages=[{"role": "user", "content": "Reply with one short sentence."}],
        api_base=base_url,
        api_key=token,
    )
```

```bash
uv run python scripts/unified-multi-provider/invoke_litellm.py
```

To stream LiteLLM responses, pass `stream=True` and iterate the chunks (`chunk.choices[0].delta.content`). [`invoke_litellm_streaming.py`](../../../gatewaylabproject/scripts/unified-multi-provider/invoke_litellm_streaming.py) shows this across the same model families:

```bash
uv run python scripts/unified-multi-provider/invoke_litellm_streaming.py
```

> [!NOTE]
> Claude (`anthropic.*`) models use the Anthropic Messages API. The OpenAI SDK and LiteLLM use Chat Completions, so reach Claude with the Anthropic SDK as shown above.

### How routing works

The gateway routes by the `model` field in the request body:

1. **Qualified** (`{targetName}/{modelId}`, e.g. `openai/gpt-4o-mini`) routes to that named target.
2. **Unqualified** (`gpt-4o-mini`) is matched against all targets; an exact match beats a glob pattern.
3. **Collision**: when multiple targets serve the same model, the gateway prefers the `bedrock-mantle` target. Otherwise it routes randomly among matches. Send a qualified model ID to override.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, run the cleanup script. It deletes the three inference targets, the OpenAI/Anthropic credential providers, the gateway, the gateway IAM role, and the tutorial's `.env` file:

```bash
uv run python scripts/unified-multi-provider/cleanup.py
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Summary

You fronted three model providers with a single gateway endpoint using connector inference targets, routed requests by model string, and listed every available model through one aggregated call, all while keeping provider credentials inside the gateway.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
