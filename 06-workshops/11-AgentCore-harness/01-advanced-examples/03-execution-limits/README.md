# 03 — Execution Limits

Limit how much work a harness agent can do per invocation by setting **execution limits**. Useful for predictable latency, bounded cost, and guardrails against runaway agents.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`03_execution_limits.ipynb`](03_execution_limits.ipynb) | Notebook | Demonstrates each of the three limit parameters individually and combined, showing what happens when the agent hits them. |

## The three limits

The `invoke_harness` API accepts these optional parameters:

| Parameter | What it controls | Example use case |
|---|---|---|
| `maxIterations` | Max agent loop iterations (think → act → observe cycles) | Force a quick bounded answer; prevent multi-step exploration |
| `timeoutSeconds` | Wall-clock time limit for the entire invocation | Keep p99 latency predictable in production |
| `maxTokens` | Max tokens the model can generate per invocation | Control cost or keep responses concise |

The agent stops as soon as **any** limit is hit. `messageStop.stopReason` tells you which one.

## What you'll see in the notebook

```python
# Bounded — agent gets a single tool call before it must respond
invoke("Create 3 files with content.", maxIterations=1)

# Unbounded — agent can take as many steps as it wants
invoke("Create 3 files with content.", maxIterations=10)

# Tight timeout — agent gets cut off mid-task
invoke("Write and run a Python script for 50 primes.", timeoutSeconds=5)

# Tight token budget — response is truncated
invoke("Explain Python history in detail.", maxTokens=10)

# All three together — whichever hits first wins
invoke("...", maxIterations=3, timeoutSeconds=30, maxTokens=1024)
```

## Key takeaways

- **`maxIterations=1`** is the most impactful limit for *behavior* — the agent must answer after one tool call, so it becomes more "one-shot"
- **`timeoutSeconds`** is the right lever for production **latency SLOs**
- **`maxTokens`** is about **cost** and **response brevity** — doesn't shortcut the agent loop itself
- Combine all three to create hard guardrails (e.g., "this agent has 30 seconds, 5 iterations, and 2K output tokens — period")

## How to run

```bash
cd 03-execution-limits
jupyter notebook 03_execution_limits.ipynb
# or open in VSCode
```

Each section is independent — you can run them in any order.

## Production pattern

A common pattern for user-facing agents:

```python
response = client.invoke_harness(
    harnessArn=harness_arn,
    runtimeSessionId=session_id,
    messages=[...],
    maxIterations=10,       # limit runaway loops
    timeoutSeconds=60,      # 60s SLO
    maxTokens=4096,         # bounded cost
)
```

For batch / research agents, keep them open: omit the limits or set them generously.