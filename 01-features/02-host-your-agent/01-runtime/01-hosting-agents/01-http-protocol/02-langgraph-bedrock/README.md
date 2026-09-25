# LangGraph with Amazon Bedrock on AgentCore New Runtime

## Overview

Deploy a [LangGraph](https://langchain-ai.github.io/langgraph/) agent using an Amazon Bedrock model (Claude) to AgentCore New Runtime. This example shows that AgentCore runtime is framework-agnostic — the deployment process is identical regardless of which agent framework you use. Only the agent code changes.

```
┌─────────────┐     invoke_agent_runtime()     ┌──────────────────────────┐
│   Client    │ ──────────────────────────────▶│  AgentCore runtime       │
│  (boto3)    │◀────────────────────────────── │  ┌──────────────────┐    │
│             │         JSON response          │  │  LangGraph Agent │    │
└─────────────┘                                │  │  + Bedrock LLM   │    │
                                               │  └──────────────────┘    │
                                               └──────────────────────────┘
```

## New Runtime in one field

AgentCore prepares your execution environment once at create/update time, snapshots it, and every new execution environment resumes from that snapshot instead of loading your code again. Cold starts get faster; how much depends on how much your agent loads at import.

You select it with one field on `create_agent_runtime`:

```python
platformVersion="V2"
```

**It must be set explicitly** — omitting it does not give you New Runtime, and the create response does not tell you which platform version you got. `deploy.py` calls `get_agent_runtime` afterwards to confirm.

The trade-off: **create and update take minutes rather than seconds**, because the snapshot is prepared during the call. Measured on this sample in `us-west-2`, `create_agent_runtime` reached `READY` in roughly 2–3.5 minutes and the endpoint in another 1–3. Raise any client or CI timeout accordingly.

> For the full walkthrough of `platformVersion` — accepted values, which operations carry it, the error behaviour, the complete parameter reference, and the rules for writing snapshot-safe agent code — see [`../01-strands-bedrock/README.md`](../01-strands-bedrock/README.md). This README covers what is specific to LangGraph.

## Prerequisites

- Python 3.10+ (3.12+ recommended)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (for building the arm64 deployment package)
- AWS CLI configured with credentials
- boto3 1.43.95 or later — the first public release that models `platformVersion`
- Access to Amazon Bedrock models (Claude) in your region
- An AWS account and region where New Runtime is enabled (an account without it returns `ValidationException: platformVersion is not enabled for this account.`)

## Step 1: Write the Agent (`agent.py`)

LangGraph uses an explicit **state graph** to define the agent loop — unlike Strands where the loop is automatic. You define nodes (functions), edges (routing), and state (typed dict):

```python
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

@tool
def calculator(expression: str) -> str:
    """Calculate the result of a mathematical expression."""
    ...

llm = ChatBedrock(
    model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    model_kwargs={"temperature": 0.1},
)
llm_with_tools = llm.bind_tools([calculator, weather])

graph_builder = StateGraph(MessagesState)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", ToolNode([calculator, weather]))
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
graph_builder.set_entry_point("chatbot")
agent = graph_builder.compile()
```

The `@app.entrypoint` wrapper is the same as any other framework:

```python
app = BedrockAgentCoreApp()

@app.entrypoint
def langgraph_bedrock(payload):
    response = agent.invoke({"messages": [HumanMessage(content=payload.get("prompt"))]})
    return response["messages"][-1].content
```

Test locally:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
python agent.py
# In another terminal:
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 25 * 17 + 42?"}'
```

### Graph compilation and the snapshot

`graph_builder.compile()` runs at **import time**, which on New Runtime is exactly where you want it. Import-time work happens once, before the snapshot is taken, and every resumed environment inherits the already-compiled graph rather than rebuilding it on each cold start. Graph construction is precisely the kind of startup cost New Runtime pre-pays. Note the dependency tree here is not the larger one: this sample's deployment zip measures 43.1 MB against 46.9 MB for the Strands example, so if anything there is slightly less import cost to move.

The graph in this sample is snapshot-safe because it is pure in-process state — nodes, edges and a bound LLM client, with no I/O until `invoke()` is called. Two things to watch if you extend it:

- **A checkpointer backed by a live connection.** `MemorySaver` is fine, but understand what it means under New Runtime: it is in-process, so its contents are captured in the snapshot and shared by every environment resumed from it. Treat it as per-request state you cannot rely on, not a cache. A Postgres or Redis checkpointer constructed at import will hold a connection that is dead by the time an environment resumes — build those lazily inside the entrypoint.
- **Anything that must be unique per environment** — a thread id or run id generated at import is identical in every resumed environment, permanently. Generate them per invocation.
- **Do not draw uniqueness from the `random` module.** Its state is captured in the snapshot, so even a per-request `random.random()` repeats across environments resumed from the same snapshot — verified. Use `uuid.uuid4()` or `secrets`, which read from the OS.

Note that this sample's entrypoint passes a single `HumanMessage` per call and configures no checkpointer, so it does not carry conversation state between invocations. That is a property of the agent, not of the platform version.

## Step 2: Create an IAM Execution Role (`deploy.py`)

The IAM role uses the official [AgentCore direct deploy execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) policy. This includes permissions for CloudWatch Logs, X-Ray tracing, CloudWatch Metrics, and Bedrock model invocation. Without all of these, the runtime fails to initialize.

`platformVersion` introduces no new IAM actions or resource types. The deploy script creates the role automatically — see `create_execution_role()` in `deploy.py`.

## Step 3: Build Deployment Package and Upload to S3 (`deploy.py`)

AgentCore runtime runs on **arm64** microVMs. The deployment zip must include pre-compiled arm64 dependencies — the runtime does NOT run `pip install` at startup. This is unchanged by `platformVersion`, which governs how the runtime starts your code rather than how you package it. arm64 is required either way: it is an AgentCore runtime requirement, not something New Runtime introduces.

```bash
# What deploy.py does under the hood:
uv pip install \
  --python-platform aarch64-manylinux2014 \
  --python-version 3.13 \
  --target deployment_package \
  --only-binary :all: \
  -r requirements.txt

cd deployment_package && zip -r ../deployment_package.zip . && cd ..
zip deployment_package.zip agent.py
# Then uploads it to s3://<bucket>/<agent-name>/code.zip
```

| Flag | Purpose |
|:-----|:--------|
| `--python-platform aarch64-manylinux2014` | Download wheels for arm64 Linux |
| `--python-version 3.13` | Match the `PYTHON_3_13` runtime |
| `--only-binary :all:` | Only pre-built wheels (no source compilation) |
| `--target deployment_package` | Install into a local directory |

## Step 4: Create the AgentCore runtime (`deploy.py`)

```python
control.create_agent_runtime(
    agentRuntimeName="langgraph_bedrock_12345",
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": "agent/code.zip"}},
            "runtime": "PYTHON_3_13",
            "entryPoint": ["agent.py"],
        }
    },
    roleArn=role_arn,
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "HTTP"},

    # ─── New Runtime. Must be set explicitly. ───
    platformVersion="V2",
)
```

The runtime name must use **alphanumeric characters and underscores only** (no hyphens). The deploy script appends a timestamp for uniqueness.

Then poll `get_agent_runtime` until the status is `READY` — **minutes on New Runtime**, because the snapshot is prepared during create — and read `platformVersion` back from that same response to confirm it took effect. From boto3 1.43.95 the response models `platformVersion`, so an absent field means the service returned none rather than your SDK having dropped it — either way it means "unknown" rather than a specific version. `deploy.py` treats confirmed, mismatched and unconfirmable as three distinct outcomes and only fails on a genuine mismatch.

## Step 5: Create an Endpoint (`deploy.py`)

```python
control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
```

Poll `list_agent_runtime_endpoints` until the endpoint status is `READY`. This is also slower on New Runtime — measured at 1–3 minutes for this sample — which is easy to miss because guidance usually mentions only the runtime create/update. Endpoints have no `platformVersion` field; the platform version belongs to the runtime.

## Step 6: Invoke the Agent (`invoke.py`)

The data plane is unchanged by `platformVersion`, so this file is identical to a deployment without New Runtime:

```python
client = boto3.client("bedrock-agentcore")
response = client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    payload=json.dumps({"prompt": "What is 25 * 17 + 42?"}).encode(),
    contentType="application/json",
    accept="application/json",
)
body = response["response"].read().decode()
```

If you pass `runtimeSessionId` explicitly, note it must be **33–256 characters** — a single `uuid4().hex` is 32 and will be rejected.

## Step 7: Clean Up (`cleanup.py`)

Delete in reverse order: endpoints → runtime → S3 artifact → IAM role. There is no snapshot to tear down separately; deleting the runtime disposes of it.

## What's Different from the Strands Example

The **deployment and invocation code is identical**, including the `platformVersion` handling. Only the agent code and dependencies change:

| Aspect | Strands Example | This Example |
|:-------|:----------------|:-------------|
| Framework | `strands.Agent` | `langgraph.StateGraph` |
| Model | `strands.models.BedrockModel` | `langchain_aws.ChatBedrock` |
| Tool definition | `@tool` (Strands) | `@tool` (LangChain) |
| Agent loop | Automatic | Explicit graph with nodes and edges |
| Dependencies | `strands-agents` | `langgraph`, `langchain-aws`, `langchain-core` |
| `platformVersion` | `"V2"` | **Exactly the same** |

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | LangGraph agent with a graph loop and calculator tool |
| `requirements.txt` | `langgraph`, `langchain-aws`, `langchain-core`, `bedrock-agentcore`, and `boto3>=1.43.95` — the floor that makes `platformVersion` available |
| `deploy.py` | Full deployment: IAM role → arm64 zip → S3 → create runtime → create endpoint, with `platformVersion="V2"` |
| `invoke.py` | Invoke the deployed agent with sample math prompts |
| `cleanup.py` | Delete endpoint → runtime → S3 → IAM role |

## Quick Start

```bash
python deploy.py         # Deploy to AgentCore New Runtime (several minutes)
python invoke.py         # Invoke with math questions
python cleanup.py        # Clean up all resources
```
