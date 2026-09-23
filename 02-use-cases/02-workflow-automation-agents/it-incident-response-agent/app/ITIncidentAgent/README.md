# IT Incident Response Agent — Runtime Code

This directory contains the agent code deployed to AgentCore Runtime as a container.

## Entrypoint

`main.py` — Handles two modes:
- **Prompt mode** (dev/testing): Responds to `{"prompt": "..."}` payloads
- **Ticket mode** (production): Processes full ticket payloads from the trigger Lambda

## Modules

| Module | Purpose |
|--------|---------|
| `model/load.py` | Loads Bedrock model from `AGENT_MODEL_ID` env var |
| `mcp_client/client.py` | Connects to AgentCore Gateway (MCP) via `GATEWAY_URL` |
| `memory/session.py` | AgentCore Memory session manager for episodic recall |

## Environment Variables (injected by CDK)

| Variable | Description |
|----------|-------------|
| `GATEWAY_URL` | AgentCore Gateway MCP endpoint |
| `MEMORY_ITINCIDENTAGENTMEMORY_ID` | Memory resource ID |
| `AGENT_MODEL_ID` | Bedrock model ID |
| `TICKETS_TABLE` | DynamoDB tickets table name |
| `AWS_REGION` | AWS region |

## Local Development

```bash
agentcore dev                    # Web UI on :8081, runtime container on :8082
agentcore dev "Hello"            # Test with a prompt
```
