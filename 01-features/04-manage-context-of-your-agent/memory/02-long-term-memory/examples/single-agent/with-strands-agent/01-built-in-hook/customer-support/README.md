# Long-term memory — Strands customer support (built-in strategies)

A customer-support agent built with **Strands Agents** that uses AgentCore's **built-in** Semantic and User Preference strategies via memory hooks. The agent remembers order history, preferences, and past issues across conversations, and includes a web-search tool for up-to-date product info. Built-in strategies need **no IAM execution role**.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Customer Support |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | Semantic + User Preference (built-in — no IAM role) |
| Memory components | `MemoryManager` / `MemorySessionManager`, memory hooks, web search |
| Complexity | Intermediate |

## What it does

[`customer-support-inbuilt-strategy.py`](./customer-support-inbuilt-strategy.py):

1. Creates memory with built-in Semantic + User Preference strategies (no IAM role).
2. Registers hooks that store each turn and retrieve relevant history automatically.
3. Resolves customer issues with full context from previous interactions.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Amazon Bedrock AgentCore SDK with `MemoryManager` support

## How to run

```bash
pip install -r requirements.txt
python customer-support-inbuilt-strategy.py
```

For the custom-model-override version of this same use case, see [`../../02-custom-hook/customer-support/`](../../02-custom-hook/customer-support/). See the [Strands single-agent README](../../README.md) for all three patterns.
