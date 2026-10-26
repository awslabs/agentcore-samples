# API key outbound auth

Configure an inference target for a provider that requires an API key (such as OpenAI or Anthropic). The organization stores one provider key in the gateway, and the gateway injects it into outbound requests. Clients never see it.

## Overview

Many model providers authenticate with an API key rather than IAM. With an API key credential provider, the gateway holds the key and adds it to each outbound request. Callers only ever present a gateway token, so you can rotate or revoke the provider key in one place without touching any client.

## How it works

1. The client authenticates **inbound** with a Cognito JWT (the SDK credential).
2. The gateway stores the provider key in an API key credential provider.
3. On each outbound request, the gateway injects the key into the configured header: `Authorization: Bearer ...` for OpenAI, `x-api-key` for Anthropic.

## Target configuration

Use `API_KEY` as the credential provider type and reference the stored credential provider by ARN. The `credentialParameterName`, `credentialPrefix`, and `credentialLocation` control how the gateway injects the key. These are the [`create_gateway_target`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateGatewayTarget.html) request parameters.

OpenAI injects the key as `Authorization: Bearer <key>`:

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

Anthropic injects the key as the `x-api-key` header (no prefix):

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

## Try it

The [unified multi-provider tutorial](../../01-unified-multi-provider-access/) creates these OpenAI and Anthropic API key targets and invokes them end to end (Steps 3.2 and 3.3 show the same configuration).

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
