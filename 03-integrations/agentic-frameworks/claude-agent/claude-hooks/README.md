# Claude Agent SDK tool hooks: historical example

For a supported Claude Agent SDK hosting path, start with [the tool-free Runtime example](../claude-sdk/). For the other part of this lesson, see [AgentCore Policy](../../../../01-features/07-centralize-and-govern-your-ai-infrastructure/02-policy/) for service-side tool authorization. Neither is a drop-in replacement.

The [historical sources](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/claude-agent/claude-hooks) demonstrated preToolUse and PostToolUse hooks for local file and shell tools. String-pattern hooks are not a filesystem or operating-system sandbox.

If you deployed this version, reconcile the original Runtime ID, role, repository and any sessions with their owners. Removing the sample does not clean up an account. Do not delete shared infrastructure by name.
