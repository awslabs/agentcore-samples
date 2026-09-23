# lab1

An MCP (Model Context Protocol) server deployed on Amazon Bedrock AgentCore.

## Overview

This project implements an MCP server using FastMCP. MCP servers expose tools that can be consumed by MCP clients (other agents or applications).

## Local Development

```bash
# Install dependencies
uv sync

# Run the MCP server locally
uv run python main.py
```

The server starts on port 8000 with Streamable HTTP transport.

## Adding Tools

Define tools using the `@mcp.tool()` decorator in `main.py`:

```python
@mcp.tool()
def my_tool(param: str) -> str:
    """Description of what the tool does."""
    return f"Result: {param}"
```

## Deploy

```bash
agentcore deploy
```
