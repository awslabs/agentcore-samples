# Memory Browser — backend

The **FastAPI** backend for the [AgentCore Memory Dashboard](../README.md). It wraps `MemoryClient` and exposes REST endpoints the React frontend calls to browse short-term events/turns and query long-term records by namespace. AWS error messages are scrubbed of ARNs and account IDs before being returned.

| Information | Details |
|---|---|
| Component | REST API backend |
| Framework | FastAPI + `bedrock_agentcore.memory.MemoryClient` |
| Endpoints | List events, conversation turns, and long-term records by namespace |
| IAM permissions | `bedrock-agentcore:ListMemoryRecords`, `ListEvents`, `GetLastKTurns`, `RetrieveMemories`, `GetMemoryStrategies` |

## Prerequisites

- Python 3.8+
- AWS credentials configured
- The IAM permissions listed above

## How to run

Normally started together with the frontend via `npm run dev` from the [parent folder](../README.md). To run the backend on its own:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit for a non-default AWS profile or region
uvicorn app:app --host 127.0.0.1 --port 8000
```

See the [Memory Dashboard README](../README.md) for full setup and the frontend.
