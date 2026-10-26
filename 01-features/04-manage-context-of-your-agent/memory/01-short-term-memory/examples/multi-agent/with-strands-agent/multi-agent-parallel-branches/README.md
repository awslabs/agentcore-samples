# Short-term memory — multi-agent parallel branches

A travel-planning system built on a **Strands Agent Graph** where each agent runs in its own AgentCore Memory **branch**. Branching gives every agent an isolated conversation context within a single shared session — like Git branches for conversation — so agents can execute in parallel without memory conflicts.

| Information | Details |
|---|---|
| Tutorial type | Multi-agent with memory branching |
| Agent type | Travel Planning Assistant (coordinator + specialists) |
| Framework | Strands Agent Graph (parallel execution) |
| LLM model | Anthropic Claude Haiku 4.5 |
| Memory components | Memory branching, branch-isolated contexts, parallel execution |
| Complexity | Intermediate |

## What it does

[`multi-agent-parallel-execution-with-memory-branching.py`](./multi-agent-parallel-execution-with-memory-branching.py) builds three agents, each on its own branch:

1. **Travel Coordinator** (`main` branch) — orchestrates planning.
2. **Flight Booking Assistant** (`flight_agent_memory` branch) — air travel.
3. **Hotel Booking Assistant** (`hotel_agent_memory` branch) — accommodations.

The coordinator delegates to the specialists, which execute in parallel while each keeps its own branch-scoped history. The script also shows inspecting branch-specific conversations.

## Prerequisites

- Python 3.10+
- AWS account with AgentCore Memory permissions
- Access to Amazon Bedrock models

## How to run

```bash
pip install -r requirements.txt
python multi-agent-parallel-execution-with-memory-branching.py
```

See the [Strands multi-agent README](../README.md) for the non-branching baseline.
