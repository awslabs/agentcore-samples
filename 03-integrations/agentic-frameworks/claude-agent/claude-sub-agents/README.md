# Claude Agent SDK subagents: historical example

For a supported Claude Agent SDK hosting path, start with [the tool-free Runtime example](../claude-sdk/). For the other part of this lesson, see [OpenAI Agents handoffs](../../openai-agents/) for a different SDK’s explicit specialist-routing example. Neither is a drop-in replacement.

The [historical sources](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/claude-agent/claude-sub-agents) demonstrated delegation to explorer and writer assistants that could inspect and change local files. The replacement does not spawn specialists or write reports.

If you deployed this version, reconcile the original Runtime ID, role, repository and any sessions with their owners. Removing the sample does not clean up an account. Do not delete shared infrastructure by name.
