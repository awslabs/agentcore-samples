# Long-term memory — Strands multi-agent travel booking

A travel-planning system where a coordinator delegates to specialized **flight** and **hotel** booking agents, all sharing one AgentCore long-term memory resource. Each specialist reads and writes its **own namespace**, building persistent understanding of user preferences over time while sharing the same memory infrastructure.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational (multi-agent) |
| Agent type | Travel Booking Assistant (coordinator + specialists) |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | User Preference (built-in — no IAM execution role) |
| Memory components | Shared LTM resource, per-agent namespaces, built-in `AgentCoreMemoryHook` |
| Complexity | Intermediate |

## What it does

[`travel-booking-assistant.py`](./travel-booking-assistant.py):

1. Creates one shared memory resource with a long-term strategy.
2. Defines flight and hotel specialists, each scoped to its own namespace.
3. A coordinator delegates queries to the right specialist.
4. Preferences extracted in one session are recalled in later ones, per namespace.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Amazon Bedrock AgentCore SDK

## How to run

```bash
pip install -r requirements.txt
python travel-booking-assistant.py
```

See the [Strands multi-agent README](../../README.md) for the custom-hook alternative.
