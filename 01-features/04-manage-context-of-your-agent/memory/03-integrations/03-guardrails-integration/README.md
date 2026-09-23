# Memory + Bedrock Guardrails

Run a Bedrock guardrail over the model's **output** in a memory-enabled agent, so unsafe
responses are replaced before the user sees them. Useful as a starting point for layering
content policies onto a conversational agent that also persists context to memory.

> **Scope note:** as written, this example wires the guardrail to the model's *output only*
> (a `AfterModelInvocationEvent` hook). It does **not** gate the memory save/retrieve path —
> `AgentCoreMemorySessionManager` persists and recalls turns independently of the guardrail.
> So unsafe *user input* can still be stored. To filter content into/out of memory you would
> additionally evaluate text before `create_event` and after retrieval (see Best practices).

## What you learn

- Create a Bedrock guardrail with content filters and PII detection
- Apply the guardrail to model output via a custom `AfterModelInvocationEvent` hook and
  replace blocked responses with a safe message
- Run the hook alongside `AgentCoreMemorySessionManager` in a single agent

## Architecture

![Guardrails + Memory](./guardrails_memory_flow.png)

Model output is evaluated by the guardrail after each invocation; blocked content is replaced
with the guardrail's masked output (or a generic refusal) before the agent returns it.

## Run

```bash
pip install -r requirements.txt
python guardrails-memory.py
```

The script creates a guardrail with content filters and PII detection, creates a memory
resource, drives a conversation containing both safe and unsafe inputs, and shows the
guardrail replacing blocked model output.

## Best practices

- **To filter memory I/O, gate it explicitly.** This example only filters model output. If you
  need unsafe content kept out of storage, evaluate user input before `create_event` and
  evaluate retrieved content before injecting it into the prompt — filtering output alone
  still lets unsafe input persist.
- **Pick a guardrail tier matching the workload.** A general-purpose chat agent and a healthcare agent need different policy strengths.
- **Watch for false positives in retrieval.** Aggressive guardrails can suppress relevant memory content; tune content filter strength on a representative dataset.
- **Don't store unsafe text and try to "filter it out later."** Once it's in memory, retrieval can leak it through edge cases. Evaluate content *before* `create_event` rather than relying on output-side filtering.
- **Log guardrail decisions.** Surface blocks in CloudWatch so you can distinguish a memory miss from a guardrail block during debugging.

## Where to go next

- Memory observability and CloudWatch metrics: [`../../04-observability/`](../../04-observability/)
- Production identity isolation: [`../02-identity-integration/`](../02-identity-integration/)
