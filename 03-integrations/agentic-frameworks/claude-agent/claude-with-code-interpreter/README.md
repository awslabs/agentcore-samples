# Claude Agent SDK with Code Interpreter: historical example

For a supported Claude Agent SDK hosting path, start with [the tool-free Runtime example](../claude-sdk/). For the other part of this lesson, see [Code Interpreter samples](../../../../01-features/03-connect-your-agent-to-anything/01-code-interpreter/) for managed execution and lifecycle examples. Neither is a drop-in replacement.

The [historical sources](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/claude-agent/claude-with-code-interpreter) demonstrated an SDK MCP wrapper that exposed code, command and file operations and accepted a session ID from the prompt payload. The replacement does not execute code or continue those sessions.

If you deployed this version, reconcile the original Runtime ID, role, repository and any sessions with their owners. Removing the sample does not clean up an account. Do not delete shared infrastructure by name.
