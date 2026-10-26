# Long-term memory — LangGraph episodic callback

A nutrition assistant built with **LangGraph** that uses the **episodic memory strategy** to capture complete meal-planning sessions as structured episodes. Unlike preference extraction, episodic memory preserves full conversation flow, intent, and outcome — enabling temporal queries ("What did I plan last week?") and dietary-pattern analysis over time.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Nutrition Assistant (episodic) |
| Framework | LangGraph |
| LLM model | Anthropic Claude Sonnet 3.7 |
| Strategies | Episodic (extraction → consolidation → reflection) |
| Memory components | Session-based episodes, pre/post model hooks, cross-episode reflection |
| Complexity | Intermediate |

## What it does

[`nutrition-assistant-with-episodic-memory.py`](./nutrition-assistant-with-episodic-memory.py):

1. Creates memory with an episodic strategy, using the prompts in [`custom_memory_prompts.py`](./custom_memory_prompts.py).
2. Captures each meal-planning conversation as a complete episode (recipes, substitutions, feedback).
3. Generates reflections across episodes to surface evolving dietary patterns.
4. Recalls past episodes to ground future recommendations.

## Prerequisites

- Python 3.10+
- AWS account with AgentCore Memory permissions and an IAM execution role
- Access to Amazon Bedrock models

## How to run

```bash
pip install -r requirements.txt
python nutrition-assistant-with-episodic-memory.py
```

See the sibling [`custom-user-preferences/`](../custom-user-preferences/) for the preference-saving variant, or the [LangGraph single-agent README](../../README.md) for all three patterns.
