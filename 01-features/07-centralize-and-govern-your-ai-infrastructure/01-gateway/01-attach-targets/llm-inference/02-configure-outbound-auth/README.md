# Configure outbound authentication

Tutorials for setting up how AgentCore gateway authenticates **outbound** to model providers when serving inference targets.

![arch](../images/architecture.png)

There are two directions of authentication for an inference target:

- **Inbound** (client to gateway): how callers authenticate to the gateway. Covered in [02-set-up-inbound-authorization](../../../02-set-up-inbound-authorization/).
- **Outbound** (gateway to provider): how the gateway authenticates to the model provider on the caller's behalf. That is what these tutorials cover.

This separation is the core of credential abstraction: clients only ever hold a gateway token, and the gateway holds the provider credentials. Choose the outbound mechanism based on what each provider accepts.

## Tutorials

| Section                                   | Description                                                                                           |
| :---------------------------------------- | :---------------------------------------------------------------------------------------------------- |
| [iam-sigv4](iam-sigv4/)                   | AWS IAM (SigV4) outbound auth for Amazon Bedrock using the gateway's IAM role (no stored secret)      |
| [api-key](api-key/)                       | API key outbound auth for providers like OpenAI and Anthropic; the gateway stores and injects the key |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
