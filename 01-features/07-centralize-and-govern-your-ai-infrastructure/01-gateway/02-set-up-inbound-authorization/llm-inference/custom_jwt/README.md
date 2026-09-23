# Amazon Cognito (CUSTOM_JWT) inbound auth

The default inbound authorizer across this lab. Callers authenticate to the gateway with a JWT from an OpenID Connect provider (Amazon Cognito here), and the gateway validates it before routing to an inference target.

## Overview

With `authorizerType: CUSTOM_JWT`, the gateway trusts a configured OIDC provider. The caller obtains a JWT (a Cognito client-credentials token in this lab) and presents it on each request. Because inference targets are invoked with the OpenAI or Anthropic SDK, the JWT goes in the SDK credential field, not a header you set yourself.

## Where the credential goes

The OpenAI SDK sends `api_key` as `Authorization: Bearer`, which is exactly what the gateway expects:

```python
from openai import OpenAI

openai_client = OpenAI(
    base_url="https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/inference/v1",
    api_key="<gateway-jwt>",  # inbound credential, NOT a provider key
)
```

The Anthropic SDK sends `api_key` as `x-api-key`, which the gateway does not accept for inbound auth, so use `auth_token` instead:

```python
import anthropic

anthropic_client = anthropic.Anthropic(
    base_url="https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/inference",
    auth_token="<gateway-jwt>",
)
```

## See it in action

Every runnable demo under [01-attach-targets/llm-inference](../../../01-attach-targets/llm-inference/) uses Cognito CUSTOM_JWT inbound auth. Rather than repeat the flow here, follow one of those tutorials:

- [01-unified-multi-provider-access](../../../01-attach-targets/llm-inference/01-unified-multi-provider-access/): front Bedrock, OpenAI, and Anthropic with one endpoint and call them with the OpenAI and Anthropic SDKs.
- [03-model-governance-and-routing](../../../01-attach-targets/llm-inference/03-model-governance-and-routing/): provider targets, model-based routing, and governance.

Each fetches a Cognito gateway JWT (see `gateway_token` in [`inference_demo.py`](../../../gatewaylabproject/inference_demo.py)) and passes it as the SDK credential shown above.

For inbound auth without an identity provider, see [iam](../iam/) (AWS IAM / SigV4).

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
