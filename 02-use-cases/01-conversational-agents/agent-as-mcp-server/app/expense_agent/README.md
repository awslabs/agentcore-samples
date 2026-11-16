# expense_agent

A Strands agent wrapped as a single MCP tool (`process_expense_report`), hosted on
Amazon Bedrock AgentCore Runtime in MCP protocol mode. See the sample
[README](../../README.md) for the full architecture and deployment guide.

## Local run

```bash
uv sync
uv run python main.py          # serves http://localhost:8000/mcp
```

Without `AGENTCORE_GATEWAY_URL` set, the agent uses in-process mock tools so you can
iterate locally with only Bedrock model access. When deployed, the AgentCore CLI
injects the Gateway URL and the Identity credential provider name automatically.
