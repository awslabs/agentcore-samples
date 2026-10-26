# Long-term memory — Strands customer support (custom strategy override)

The same customer-support agent as the [built-in version](../../01-built-in-hook/customer-support/), but using **custom-override** Semantic and User Preference strategies. Custom strategies let you specify your own Bedrock models for extraction and consolidation — at the cost of **requiring an IAM execution role**.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Customer Support |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | Semantic + User Preference (**custom override** — requires IAM role) |
| Memory components | `MemoryManager` / `MemorySessionManager`, custom extraction/consolidation models, memory hooks, web search |
| Complexity | Advanced |

## What it does

[`customer-support-override-strategy.py`](./customer-support-override-strategy.py):

1. Creates an IAM execution role for custom-strategy model invocation.
2. Configures custom Bedrock models for extraction and consolidation.
3. Registers hooks that store and retrieve memory automatically.
4. Resolves customer issues with context from previous interactions.

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- An IAM execution role for custom-strategy model invocation
- Amazon Bedrock AgentCore SDK with `MemoryManager` support

## How to run

```bash
pip install -r requirements.txt
python customer-support-override-strategy.py
```

For the no-IAM-role built-in version, see [`../../01-built-in-hook/customer-support/`](../../01-built-in-hook/customer-support/). See the [Strands single-agent README](../../README.md) for all three patterns.
