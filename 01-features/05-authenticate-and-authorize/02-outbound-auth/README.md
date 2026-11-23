# Outbound authentication

Use credentials scoped to the downstream resource, not the credentials that authenticated an incoming agent request. These samples illustrate different flows:

| Sample | What you learn |
|---|---|
| [OpenAI](01-outbound-auth-openai/) | Reference an API-key provider for an external model |
| [Google Calendar](02-outbound-auth-3lo/) | Verify delegated user consent for the read-only calendar scope |
| [GitHub](03-outbound-auth-github/) | Verify delegated user consent for the `read:user` scope |
| [Self-hosted](04-outbound-auth-self-hosted/) | Workload identity and an application-managed downstream resource |

The Google and GitHub paths use [one bounded, single-user consent client](DELEGATED_CONSENT.md), not separate deployed chatbots. A cached user grant and an interactive authorization session are different outcomes. Neither requires exposing a token to a model or storing the last user's token in a process-global callback server. Review each example's identity prerequisites and teardown responsibilities before making live calls.

For a managed multi-user consent dashboard and MCP target, see [the GitHub consent-portal sample](../07-consent-portal-auth-code-flow-targets/). For the broader feature, see [AgentCore Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html).
