# Short-term memory — Strands branching

A travel-planning assistant that uses AgentCore Memory **branching** to fork conversation history. Branches let the agent explore alternative "what-if" paths (e.g. separate flight and hotel threads) while preserving — and staying grounded in — the original main-branch conversation.

| Information | Details |
|---|---|
| Tutorial type | Short-term conversational with memory branching |
| Agent type | Travel Planning Assistant |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Memory components | Short-term memory, conversation branching, branch-scoped `get_last_k_turns` |
| Complexity | Beginner |

## What it does

[`travel-planning-agent-with-memory-branching.py`](./travel-planning-agent-with-memory-branching.py):

1. Runs a main-branch conversation where all turns are stored.
2. Forks a **flight** branch and a **hotel** branch from the main conversation.
3. Each branch follows its own path while still reading the shared main-branch history.
4. Shows switching between branches and inspecting branch-specific history.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Access to Amazon Bedrock models

## How to run

```bash
pip install -r requirements.txt
python travel-planning-agent-with-memory-branching.py
```

See the [Strands single-agent README](../README.md) for the non-branching baseline.
