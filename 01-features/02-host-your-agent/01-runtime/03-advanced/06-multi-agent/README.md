# Multi-Agent Orchestration

## Overview

Deploy multiple agents to separate AgentCore Runtimes and orchestrate them from a supervisor agent. Each agent runs in its own isolated environment with its own tools and system prompt.

## Architecture

```
                    ┌──────────────────────────┐
                    │  Orchestrator runtime     │
                    │  (Strands Agent)          │
                    │                           │
                    │  Routes questions to:     │
                    │  • Tech Agent             │
                    │  • HR Agent               │
                    └─────────┬────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼                               ▼
┌──────────────────────┐       ┌──────────────────────┐
│  Tech Agent runtime  │       │  HR Agent runtime     │
│                      │       │                       │
│  Tools:              │       │  Tools:               │
│  - search_docs       │       │  - lookup_benefit     │
│  - check_error_code  │       │  - lookup_policy      │
└──────────────────────┘       └──────────────────────┘
```

## How It Works

### 1. Specialist agents are standard agents

Each specialist is a normal AgentCore runtime agent with its own tools:

```python
# tech_agent.py
@tool
def search_docs(query: str) -> str:
    """Search technical documentation."""
    ...

agent = Agent(model=model, tools=[search_docs, check_error_code])
```

### 2. The orchestrator calls specialists via `invoke_agent_runtime`

The orchestrator agent has tools that invoke other runtimes:

```python
# orchestrator_agent.py
TECH_AGENT_ARN = os.environ.get("TECH_AGENT_ARN")

@tool
def ask_tech_agent(question: str) -> str:
    """Route technical questions to the tech specialist."""
    client = boto3.client('bedrock-agentcore')
    response = client.invoke_agent_runtime(
        agentRuntimeArn=TECH_AGENT_ARN,
        payload=json.dumps({"prompt": question}).encode(),
    )
    return response["response"].read().decode()
```

The specialist ARNs are passed as **environment variables** when creating the orchestrator runtime.

### 3. The deploy script wires everything together

```python
# deploy.py (simplified)

# Deploy specialists first
tech = deploy_runtime("multi-tech-agent", ...)
hr = deploy_runtime("multi-hr-agent", ...)

# Deploy orchestrator with specialist ARNs as env vars
deploy_runtime("multi-orchestrator", ...,
    environmentVariables={
        "TECH_AGENT_ARN": tech["runtime_arn"],
        "HR_AGENT_ARN": hr["runtime_arn"],
    },
)
```

### 4. IAM: orchestrator needs `InvokeAgentRuntime` permission

The orchestrator's IAM role needs an additional permission to call other runtimes:

```python
{
    "Effect": "Allow",
    "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
    "Resource": "*"
}
```

## Benefits of Multi-runtime Architecture

| Benefit | Description |
|:--------|:------------|
| **Independent scaling** | Each runtime scales based on its own traffic |
| **Fault isolation** | One agent's failure doesn't affect others |
| **Independent deployment** | Update a specialist without redeploying the orchestrator |
| **Framework diversity** | Each agent can use a different framework or model |
| **Security isolation** | Each agent has its own IAM role with least-privilege permissions |

## Files

| File | Description |
|:-----|:------------|
| `tech_agent.py` | Tech support specialist — `search_docs`, `check_error_code` tools |
| `hr_agent.py` | HR specialist — `lookup_benefit`, `lookup_policy` tools |
| `orchestrator_agent.py` | Supervisor — routes to specialists via `invoke_agent_runtime` |
| `requirements.txt` | Shared dependencies |
| `deploy.py` | Deploys all three agents, wires ARNs via environment variables |
| `invoke.py` | Sends mixed questions to the orchestrator |
| `cleanup.py` | Deletes all three runtimes, endpoints, S3 artifacts, IAM roles |

## Quick Start

```bash
python deploy.py     # Deploy all three agents (~5-10 minutes)
python invoke.py     # Send questions — watch the orchestrator route them
python cleanup.py    # Clean up everything
```
