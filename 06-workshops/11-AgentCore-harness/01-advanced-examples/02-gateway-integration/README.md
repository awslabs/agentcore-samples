# 02 — AgentCore Gateway Integration

Wire a harness agent to an **AgentCore Gateway** so it can reach external MCP tool servers through a managed proxy — with centralized auth, routing, and observability.

## What is AgentCore Gateway?

Gateway is a managed service that sits between your agent and external tool servers. Instead of the agent calling an MCP endpoint directly, it calls the Gateway, which:

- Handles **authentication** centrally (IAM, OAuth, API keys)
- Applies **routing rules** to direct traffic to the right target
- Emits **observability** data (every tool call is traced in CloudWatch)
- Lets you **swap tool backends** without changing the agent config

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`02_agentcore_gateway_integration.py`](02_agentcore_gateway_integration.py) | CLI script | Full lifecycle demo — creates an IAM role, Gateway, MCP target, routing rule, harness wired to the Gateway, invokes the agent (which discovers & calls tools via the Gateway), then cleans up. |

## End-to-end flow (from the script)

```
1. Create IAM execution role (reuses helper/iam.py)
2. Create Gateway           → IAM auth + MCP protocol
3. Add MCP target           → remote MCP server endpoint (default: Exa)
4. Create routing rule      → directs traffic to the target
5. Create harness           → wired to the Gateway's ARN
6. invoke_harness           → agent discovers tools via Gateway and calls them
7. Cleanup                  → delete harness, target, Gateway, IAM role
```

## How to run

### Main script

```bash
# Default — uses Exa MCP search as the target
python 02_agentcore_gateway_integration.py

# Custom MCP endpoint
python 02_agentcore_gateway_integration.py \
    --mcp-endpoint https://your-mcp-server.example.com/mcp \
    --target-name my-tools

# Keep resources after the demo
python 02_agentcore_gateway_integration.py --skip-cleanup

# Use an existing IAM role (skip role creation)
python 02_agentcore_gateway_integration.py --role-arn arn:aws:iam::123456789012:role/MyRole

# See all options
python 02_agentcore_gateway_integration.py --help
```
