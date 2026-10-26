# Inference targets

Add inference targets to your gateway to route LLM traffic to model providers. The gateway acts as a unified LLM proxy layer: clients connect to a single endpoint, and the gateway routes each request to the correct provider based on the model in the request. Inference targets provide model-based routing, credential abstraction, and centralized governance for LLM traffic across multiple providers.

![arch](./images/architecture.png)

Adding inference targets is useful when you want to:

- Provide a single endpoint that routes to multiple model providers (Amazon Bedrock, OpenAI, Anthropic, or other OpenAI-compatible services) without requiring clients to manage provider-specific configurations.
- Abstract credential management so clients authenticate to the gateway while the gateway authenticates to providers on their behalf.
- Apply centralized governance through Amazon Bedrock Guardrails and AgentCore Policy consistently across all LLM calls, regardless of provider.
- Use the OpenAI SDK or Anthropic SDK directly with the gateway endpoint, switching between models by changing the model string.
- List available models across all configured providers through a single aggregated endpoint.

## Connector vs provider

Inference targets use the `inference` key in the target configuration. You can configure a target in one of two ways:

| Form          | Description                                                                                                                                                                                                                      |
| :------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Connector** | Zero-configuration setup for supported providers (`bedrock-mantle`, `openai`, `anthropic`). The gateway handles operations, model discovery, model-ID translation, and path rewriting automatically. Recommended for most cases. |
| **Provider**  | Explicit control over the endpoint, model mappings, and operations. Use when you need to restrict models, customize routing, or connect a provider without a built-in connector (such as Gemini).                                |

This tutorial set teaches connectors first (Tutorial 01) and provider configuration where you need governance (Tutorial 03).

## Invoking an inference target

Send requests to the gateway's `/inference` path. The gateway routes to the correct provider based on the `model` field in the request body.

```text
https://{gatewayId}.gateway.bedrock-agentcore.{region}.amazonaws.com/inference/{path}
```

Replace `{path}` with the operation path. The gateway supports four inference APIs:

| Path                  | API                       | Style                                         | Model families                                                           | SDK call                                  |
| :-------------------- | :------------------------ | :-------------------------------------------- | :----------------------------------------------------------------------- | :---------------------------------------- |
| `v1/models`           | Models                    | Discovery                                     | all                                                                      | `openai_client.models.list()`             |
| `v1/chat/completions` | Chat Completions (OpenAI) | Stateless multi-turn                          | OpenAI-compatible (`openai.*`, `mistral.*`, `qwen.*`, `deepseek.*`, ...) | `openai_client.chat.completions.create()` |
| `v1/responses`        | Responses (OpenAI)        | Stateful / agentic (tools, server-side state) | OpenAI-compatible families                                               | `openai_client.responses.create()`        |
| `v1/messages`         | Messages (Anthropic)      | Claude-native format                          | Anthropic Claude (`anthropic.*`)                                         | `anthropic_client.messages.create()`      |

The gateway's inbound authorizer validates the caller. With Cognito (CUSTOM_JWT), pass the gateway JWT as the SDK credential:

```python
from openai import OpenAI

openai_client = OpenAI(
    base_url="https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/inference/v1",
    api_key="<gateway-auth-token>",
)
response = openai_client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

The Anthropic SDK uses the `/inference` base URL (without `/v1`), and the gateway JWT is passed as `auth_token` rather than `api_key` (the Anthropic SDK sends `api_key` as `x-api-key`, which the gateway does not use for inbound auth). See [02-set-up-inbound-authorization/inference](../../02-set-up-inbound-authorization/inference/) for inbound authentication options, including AWS IAM (SigV4).

## Model-based routing

The gateway accepts these `model` formats and routes accordingly:

| Format                               | Example                             |
| :----------------------------------- | :---------------------------------- |
| Unqualified                          | `gpt-4o-mini`                       |
| Unqualified with prefix              | `openai.gpt-4o-mini`                |
| Qualified (`{targetName}/{modelId}`) | `bedrock-mantle/gpt-4o-mini`        |
| Qualified with prefix                | `bedrock-mantle/openai.gpt-4o-mini` |

1. **Qualified** routes to the named target.
2. **Unqualified** is matched against all targets; an exact match beats a glob.
3. **Collision**: when multiple targets serve the same model, the gateway prefers the `bedrock-mantle` target. Otherwise it routes randomly among matches. Send a qualified model ID to override.

> With/without-prefix matching works only when `modelMapping` is configured on a provider target. Connectors configure this for you.

## Outbound authorization

Inference targets support two outbound authorization types:

- **IAM (SigV4)**: use `GATEWAY_IAM_ROLE` for providers that accept IAM authentication (such as Amazon Bedrock). No secret is stored.
- **API key**: use `API_KEY` for providers that require an API key (such as OpenAI and Anthropic). The gateway injects the stored key into outbound requests.

See [02-configure-outbound-auth](02-configure-outbound-auth/) for hands-on tutorials.

## Samples

| Sample                                                                        | Description                                                                                      |
| :---------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------- |
| [01-unified-multi-provider-access](01-unified-multi-provider-access/)         | Front Bedrock, OpenAI, and Anthropic with one endpoint using connectors; switch models by string |
| [02-configure-outbound-auth](02-configure-outbound-auth/)                     | How the gateway authenticates outbound to providers (IAM SigV4, API key)                         |
| [03-model-governance-and-routing](03-model-governance-and-routing/)           | Explicit provider configuration: restrict models, translate IDs, rewrite paths                   |
| [04-centralized-guardrails-and-policy](04-centralized-guardrails-and-policy/) | Apply Guardrails and Policy uniformly across all providers (coming soon)                         |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
