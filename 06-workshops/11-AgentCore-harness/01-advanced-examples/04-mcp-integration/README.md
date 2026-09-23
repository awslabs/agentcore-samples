# 04 — MCP Integration

Connect harness agent to **MCP (Model Context Protocol) servers** to extend its capabilities with tools exposed by third-party providers (search, knowledge bases, APIs, etc.) — declaratively, without writing tool-calling code.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`04_mcp_integration`](04_mcp_integration.ipynb) | Notebook | End-to-end examples: basic MCP, multiple MCP tools, authenticated MCP, error handling, advanced research assistant. |

## What you'll learn

- How to connect harness agents to **MCP servers** (Exa, Brave, custom)
- Working with different MCP providers simultaneously
- Passing headers and configuration to authenticated MCP servers
- Error handling and debugging MCP connections
- Best practices for MCP in production

## Notebook structure

- **Part 0-1:** Setup + create a harness for the examples
- **Part 2:** Basic MCP integration — Exa Search
- **Part 3:** Multiple MCP tools — combining search providers
- **Part 4:** MCP with authentication — passing headers
- **Part 5:** Error handling and debugging
- **Part 6:** Best practices (timeouts, auth, naming, logging, testing)
- **Part 7:** Advanced example — Research Assistant using multiple MCP tools
- **Cleanup:** Delete harness + IAM role

## How to run

```bash
cd 04-mcp-integration
jupyter notebook 04_mcp_integration.ipynb
# or open in VSCode
```

Run cells top-to-bottom. Each Part is independent after Part 1 (create harness).

## Key takeaway

MCP lets you connect to any remote MCP-compliant server via a single JSON config — no SDK code needed. The agent discovers available tools and calls them automatically.

```python
# Minimal MCP integration
tools = [{
    "type": "remote_mcp",
    "name": "exa",
    "config": {"remoteMcp": {"url": "https://mcp.exa.ai/mcp"}}
}]
response = client.invoke_harness(
    harnessArn=harness_arn,
    runtimeSessionId=session_id,
    messages=[...],
    tools=tools,
)
```
