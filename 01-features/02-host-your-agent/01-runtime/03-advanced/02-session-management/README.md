# Session Management

## Overview

AgentCore runtime provides session-based isolation — each session runs in a dedicated microVM. By reusing the same `runtimeSessionId`, you maintain agent state, conversation history, and filesystem across multiple invocations.

## Key Concepts

### Session Lifecycle

```
First invocation (new session ID)  →  microVM created
    ↓
Same session ID, next invocation   →  same microVM, state preserved
    ↓
Different session ID               →  new microVM, completely isolated
    ↓
Idle timeout (default 15 min)      →  microVM terminated
```

### Session Isolation

```
Session A (microVM-1)          Session B (microVM-2)
┌─────────────────────┐       ┌─────────────────────┐
│ Agent state A        │       │ Agent state B        │
│ Conversation A       │       │ Conversation B       │
│ Filesystem A         │       │ Filesystem B         │
└─────────────────────┘       └─────────────────────┘
     Completely isolated — no shared state
```

### How Sessions Work in boto3

The `runtimeSessionId` parameter on `invoke_agent_runtime` controls which session your request goes to:

```python
# First message — creates a new session
response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=b'{"prompt": "My name is Alice"}',
    runtimeSessionId='session-123',  # ← specify session ID
)

# Second message — same session, agent remembers context
response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=b'{"prompt": "What is my name?"}',
    runtimeSessionId='session-123',  # ← same session ID
)
# Agent responds: "Your name is Alice"
```

### Configuring Session Timeouts

Set timeouts when creating the runtime via `lifecycleConfiguration`:

```python
control.create_agent_runtime(
    # ... other params ...
    lifecycleConfiguration={
        'idleRuntimeSessionTimeout': 1800,  # 30 min idle timeout (default: 900)
        'maxLifetime': 14400,               # 4 hour max lifetime (default: 28800)
    },
)
```

| Parameter | Default | Range | Description |
|:----------|:--------|:------|:------------|
| `idleRuntimeSessionTimeout` | 900s (15 min) | 60–28800s | Session terminates after this idle period |
| `maxLifetime` | 28800s (8 hours) | 60–28800s | Session terminates after this total lifetime |

### Stopping Sessions Early

Save costs by stopping sessions when you're done:

```python
client.stop_runtime_session(
    agentRuntimeArn=arn,
    runtimeSessionId='session-123',
)
```

## What This Demo Shows

The `invoke.py` script demonstrates three things:

1. **Session continuity** — sends two messages with the same session ID, shows the agent remembers context
2. **Session isolation** — sends a message with a different session ID, shows the agent has no context
3. **Session cleanup** — stops both sessions to release resources

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | Simple conversational agent that retains context across messages |
| `requirements.txt` | Dependencies |
| `deploy.py` | Deploys with 30-minute idle timeout for experimentation |
| `invoke.py` | Demonstrates session continuity, isolation, and cleanup |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role |

## Quick Start

```bash
python deploy.py     # Deploy agent with 30-min session timeout
python invoke.py     # Run the session demo
python cleanup.py    # Clean up
```
