# Choosing your access surface

AgentCore Memory exposes the same APIs through three surfaces. Any of them can do almost anything the others can — the choice is about ergonomics.

## The three surfaces at a glance

| Surface | What it is | Best for |
|---|---|---|
| **AWS CLI** (`aws bedrock-agentcore-control`, `aws bedrock-agentcore`) | Shell commands over the REST APIs | Onboarding, inspection, one-shot ops, scripting |
| **boto3** (`bedrock-agentcore-control`, `bedrock-agentcore` clients) | Raw Python AWS SDK | Full control / data plane, batch & streaming ops, platform/ops code |
| **AgentCore SDK** (`bedrock_agentcore.memory.MemoryClient` + framework hooks/tools) | Higher-level Python client plus framework adapters | Agent code — Strands, LangGraph, LlamaIndex integrations, memory-as-tool |

They are interchangeable: a resource created in the CLI is usable from boto3 and from the AgentCore SDK. Pick per task, not per project.

> **Framework adapters aren't all in the AgentCore SDK.** Only the Strands adapter ships in
> `bedrock-agentcore`; LangGraph needs `langgraph-checkpoint-aws` and LlamaIndex needs
> `llama-index-memory-bedrock-agentcore`. Class names and packages per framework:
> [06-usage-patterns.md](./06-usage-patterns.md).

## When to use each

### Use the CLI when...
- You're learning the service and want to see requests/responses with no Python noise.
- You need to inspect or repair a resource in production (list events, peek at records, delete a stale session).
- You're writing a one-off shell script or CI check.

### Use boto3 when...
- You're building platform code: resource lifecycle, IAM, KMS, streaming to Kinesis, batch create/update/delete, redrive.
- You want behaviour that maps 1:1 to the API reference docs.
- Your code path is in a service/Lambda that isn't an agent.

### Use the AgentCore SDK when...
- You're writing agent code with Strands, LangGraph, or LlamaIndex.
- You want framework hooks / callbacks / memory blocks / memory-as-tool wired up for you.
- You prefer ergonomic helpers like `create_memory_and_wait`, `save_conversation`, `get_last_k_turns`.

## Decision quick-check

```
Is this an agent using Strands/LangGraph/LlamaIndex?
│
├─ Yes → AgentCore SDK + that framework's adapter (see 06-usage-patterns.md)
│
└─ No  → Is it a one-off inspection or onboarding step?
         │
         ├─ Yes → CLI
         │
         └─ No  → boto3
```

## Mixing surfaces

It is common — and expected — to mix:

- Create the memory resource in **boto3** (platform code), then use it from **AgentCore SDK** (agent code).
- Use the **CLI** to inspect state during development, then automate with **boto3** in CI.
- Batch backfills and streaming pipelines are almost always **boto3**, regardless of what the runtime agent uses.

## Next

Pick one of the three quickstarts and run the same flow (create memory → write event → add strategy → retrieve record):

- [03-quickstart-cli.md](./03-quickstart-cli.md)
- [04-quickstart-boto3.py](./04-quickstart-boto3.py)
- [05-quickstart-agentcore-sdk.py](./05-quickstart-agentcore-sdk.py)
