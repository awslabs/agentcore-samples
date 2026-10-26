# Long-term memory — Strands debugging assistant (episodic)

A debugging assistant built with **Strands Agents** that uses **episodic memory with reflections**. It captures complete debugging sessions — problem statement, actions taken, and outcome — then learns across them: reflections surface recurring issues, successful strategies, and common pitfalls to guide future debugging.

| Information | Details |
|---|---|
| Tutorial type | Episodic memory with reflections |
| Agent type | Debugging Assistant |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | Episodic (with reflection configuration) |
| Memory components | Episodic memory tool, cross-episode reflections |
| Complexity | Intermediate |

## What it does

[`debugging_assistant_episodic_memory.py`](./debugging_assistant_episodic_memory.py):

1. Creates memory with an episodic strategy and reflection configuration.
2. Stores each debugging session as a full episode at `debugging/{actorId}/sessions/{sessionId}`.
3. Generates reflections at `debugging/{actorId}` — synthesized knowledge across episodes.
4. Recalls past episodes and reflections to provide context-aware guidance ("how did I solve X last time?").

Sample debugging data lives in [`data/`](./data/).

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Access to AgentCore services

## How to run

```bash
pip install -r requirements.txt
python debugging_assistant_episodic_memory.py
```

See the [memory-tool README](../README.md) for the preference-based culinary example.
