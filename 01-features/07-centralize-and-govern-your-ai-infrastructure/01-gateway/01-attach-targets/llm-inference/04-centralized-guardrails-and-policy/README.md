# Centralized guardrails and policy

> [!NOTE]
> This workshop lab is coming soon.

Because all LLM traffic flows through the gateway, you can apply governance once and have it enforced consistently across every model provider. Apply [Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html) and [Amazon Bedrock AgentCore Policy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) to inference targets so that content filtering, denied topics, PII handling, and authorization rules apply uniformly whether a request is served by Amazon Bedrock, OpenAI, or Anthropic.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
