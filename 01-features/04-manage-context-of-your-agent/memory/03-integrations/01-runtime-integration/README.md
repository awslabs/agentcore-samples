# Runtime + Memory integration

Deploy a memory-enabled Strands agent behind an AgentCore Runtime endpoint. The runtime hosts the agent process; AgentCore Memory persists conversation turns so each invocation continues where the last one left off — even after the runtime session expires.

## What you learn

- Wire `AgentCoreMemorySessionManager` into a Strands agent so save/retrieve runs automatically per turn
- Build, push, and deploy the agent container to AgentCore Runtime
- Invoke the runtime endpoint with a stable `sessionId` to demonstrate cross-session re-hydration

## Architecture

![Runtime + Memory](./RuntimeMemoryIntegration.png)

The runtime invokes the Strands agent. Memory hooks fire at two lifecycle points:

- **Agent Initialized** — pull the last-K conversation turns from short-term memory and inject them into context before the first LLM call.
- **Message Added** — append every new user/assistant message back to short-term memory.

When the runtime session expires, the next invocation re-hydrates the conversation from memory, so the user experience is seamless.

## Run

```bash
pip install -r requirements.txt
python runtime_memory_integration.py
```

The script creates the memory resource, packages the agent (`runtime_memory_agent.py`) into a container, deploys it to AgentCore Runtime, and invokes it twice on the same `sessionId` to verify continuity.

## Best practices

- **Use a stable `sessionId` per real conversation.** Don't generate a new id on every invocation, or memory re-hydration won't fire.
- **Pin a stable `actorId`** to the authenticated user (Cognito `sub`, internal id) — the same id should travel across sessions so long-term memory namespaces stay consistent.
- **Set `eventExpiryDuration`** on the memory resource to bound storage cost — the runtime itself doesn't garbage-collect events.
- **Wire IAM scoping next.** Runtime → memory access without `actorId`/`namespace` IAM conditions means any actor can read any other actor's events. See [`../../05-security/01-iam-scoped-access/`](../../05-security/01-iam-scoped-access/).

## Where to go next

- Add per-user identity isolation: [`../02-identity-integration/`](../02-identity-integration/)
- Filter retrieved memory and generated responses with Bedrock Guardrails: [`../03-guardrails-integration/`](../03-guardrails-integration/)
- Inspect what landed in memory: [`../04-memory-browser/`](../04-memory-browser/)
