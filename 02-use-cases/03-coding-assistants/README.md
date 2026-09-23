# Coding Agents

Agents that help developers write, execute, or fix code. Tasks are typically longer-running than a chat response and are scoped to a project or repository. AgentCore Code Interpreter runs user-submitted code in an isolated container, and AgentCore Gateway can aggregate multiple developer tool APIs behind a single MCP endpoint.

## Service configuration

| Service | Typical setup for coding agents |
|---------|--------------------------------|
| Identity | Developer identity or a CI service account |
| Memory | Project-scoped context: codebase conventions, past decisions, open issues |
| Runtime | Long-running tasks (minutes to hours), Code Interpreter for sandboxed execution |
| Guardrails | Block malicious code generation, enforce code quality requirements |
| Observability | Per-task traces showing which files were touched and which tools were called |
| Gateway | Aggregated MCP endpoint for GitHub, Jira, AWS, and internal APIs |

## Common patterns

| Pattern | When it fits |
|---------|-------------|
| Plan-Act-Reflect | Generate code, run tests, review output, iterate until passing |
| Swarm | Parallel agents handling different modules in a large refactor |
| Human-in-the-loop | Agent opens a pull request; a human reviews and merges |

## Samples

| Sample | Use case | Complexity | AgentCore features |
|--------|----------|------------|-------------------|
| [text-to-python-ide](./text-to-python-ide/) | Full-stack IDE where users describe what they want in plain text and the agent writes and runs the Python code | Intermediate | Runtime, Code Interpreter, Memory, Policy (Guardrails) |
| [claude-code-gateway-mcp-server](./claude-code-gateway-mcp-server/) | Consolidate multiple MCP servers behind one AgentCore Gateway endpoint for use with Claude Code | Intermediate | Gateway, Identity |

## See also

- [01-conversational-agents](../01-conversational-agents/) - agents that interact with users in real time
- [02-workflow-automation-agents](../02-workflow-automation-agents/) - event-driven and background agents
