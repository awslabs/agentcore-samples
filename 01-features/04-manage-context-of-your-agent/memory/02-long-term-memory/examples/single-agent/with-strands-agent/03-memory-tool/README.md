# Long-term memory — Strands, memory as a tool

Long-term memory exposed as **tools the agent decides to call**. Instead of hooks that save/recall on the lifecycle, the Strands agent itself chooses when to store a durable fact and when to search its past knowledge.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Culinary Assistant |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | User Preference |
| Memory components | Memory tool (`store`/`retrieve`), extraction strategy |
| Complexity | Beginner |

## What it does

[`culinary-assistant.py`](./culinary-assistant.py):

1. Configures memory with a User Preference extraction strategy.
2. Integrates the AgentCore memory tool so the agent stores/retrieves on its own.
3. Hydrates past conversation history and delivers personalized restaurant recommendations across sessions.

## Also here

- [`debugging-agent/`](./debugging-agent/) — a debugging assistant using **episodic** memory with reflections.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Amazon Bedrock AgentCore SDK

## How to run

```bash
pip install -r requirements.txt
python culinary-assistant.py
```

See the [Strands single-agent README](../README.md) for the hook-based patterns.
