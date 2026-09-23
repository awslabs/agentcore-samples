# GitHubMCP — MCP Server

An MCP server deployed on Amazon Bedrock AgentCore that provides GitHub API access (issues, branches, PRs, labels) to coding agents via the AgentCore Gateway.

## Overview

Built with FastMCP, this server authenticates with GitHub using a GitHub App (private key stored in AWS Secrets Manager) and exposes tools over Streamable HTTP transport (MCP protocol version `2025-03-26`).

## Local Development

```bash
# Install dependencies
uv sync

# Run the MCP server locally
uv run python main.py
```

The server starts on port 8000 with stateless Streamable HTTP transport.

## Adding Tools

Define tools using the `@mcp.tool()` decorator in `main.py`:

```python
@mcp.tool()
def my_tool(param: str) -> str:
    """Description of what the tool does."""
    return f"Result: {param}"
```

## Deploy

Deployment is handled by the parent directory scripts:

```bash
cd ..
./deploy-all.sh
```

See the [gateway_mcp README](../README.md) for full deployment instructions.
