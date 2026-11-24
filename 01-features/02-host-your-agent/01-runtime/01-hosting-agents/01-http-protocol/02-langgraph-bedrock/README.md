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

### Deployment caller versus execution role

The **deployment caller** builds and uploads the ZIP, provisions the Runtime and endpoint, invokes the deployed agent, and cleans up. The **execution role** is assumed by AgentCore to read the ZIP and invoke the model. The caller does not need administrator access or direct Bedrock model access. Have the sandbox owner provision the caller separately. To scope permissions to one run, agree on the generated sample name before granting access.

A hosted test against an existing code bucket exposed three additional authorization checks during `CreateAgentRuntime`:

- The service checked `bedrock-agentcore:CreateAgentRuntimeEndpoint` against `runtime/*` in the selected account and Region, not just the final Runtime ARN. This is broader than a single Runtime; use a dedicated sandbox if that permission is inappropriate in a shared account.
- The caller needed read access to the uploaded ZIP, not only permission to write it. Scope object access to that key. The test used an unversioned bucket; it did not validate a KMS-encrypted or versioned artifact.
- `bedrock-agentcore:CreateWorkloadIdentity` was checked against **both** the default workload identity directory and the Runtime-generated identity under it. Restrict the latter to the unique sample name and generated suffix. Do not grant all identity operations.

Scope `iam:PassRole` to the one execution role and `iam:PassedToService = bedrock-agentcore.amazonaws.com`. Scope role and policy management to that role, object operations to the deployment key, and Runtime operations to the selected Region and sample wherever supported. If creation is denied after the upload, keep the receipt: a pending create intentionally blocks automatic deletion. Verify service-side absence and the recorded role identity and object ETag before reconciling. Do not clear the pending marker or widen access to administrator just to retry.

## Step 1: Write the Agent (`agent.py`)

LangGraph uses an explicit **state graph** to define the agent loop — unlike Strands where the loop is automatic. You define nodes (functions), edges (routing), and state (typed dict):

```python
from langgraph.graph import StateGraph, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_aws import ChatBedrockConverse
from typing import Literal
from langchain_core.tools import tool

@tool
def calculator(operation: Literal["add", "subtract", "multiply", "divide", "sqrt"], left: float, right: float | None = None) -> str:
    """Apply a single operation to finite numbers; never evaluate a Python expression."""
    ...

llm = ChatBedrockConverse(
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    temperature=0.1,
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
    return response["messages"][-1].text
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

`graph_builder.compile()` runs at **import time**, which on New Runtime is exactly where you want it. Import-time work happens once, before the snapshot is taken, and every resumed environment inherits the already-compiled graph rather than rebuilding it on each cold start. Graph construction is one of the startup costs New Runtime pre-pays. Package sizes depend on the resolved wheels.

The graph in this sample is snapshot-safe because it is pure in-process state — nodes, edges and a bound LLM client, with no I/O until `invoke()` is called. Two things to watch if you extend it:

- **A checkpointer backed by a live connection.** `MemorySaver` is fine, but understand what it means under New Runtime: it is in-process, so its contents are captured in the snapshot and shared by every environment resumed from it. Treat it as per-request state you cannot rely on, not a cache. A Postgres or Redis checkpointer constructed at import will hold a connection that is dead by the time an environment resumes — build those lazily inside the entrypoint.
- **Anything that must be unique per environment** — a thread id or run id generated at import is identical in every resumed environment, permanently. Generate them per invocation.
- **Do not draw uniqueness from the `random` module.** Its state is captured in the snapshot, so even a per-request `random.random()` repeats across environments resumed from the same snapshot — verified. Use `uuid.uuid4()` or `secrets`, which read from the OS.

Note that this sample's entrypoint passes a single `HumanMessage` per call and configures no checkpointer, so it does not carry conversation state between invocations. That is a property of the agent, not of the platform version.

## Step 2: Create an IAM Execution Role (`deploy.py`)

The IAM role uses the official [AgentCore direct deploy execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) guidance, with access to the sample’s exact S3 ZIP and selected Bedrock inference profile and destination model. CloudWatch Logs, X-Ray and CloudWatch Metrics support Runtime observability. This tutorial assumes the code object does not require a customer-managed KMS grant. If you use such a key, have its owner review an exact-key, S3-mediated grant and the key policy before deploying; do not grant unrestricted decryption.

`platformVersion` introduces no new IAM actions or resource types. The deploy script creates a fresh role and records the IAM `RoleId` in `runtime_config.json`. It never adopts an existing role or overwrites a previous deployment receipt. Use one working directory per deployment and keep the receipt for cleanup.

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

The runtime name must use **alphanumeric characters and underscores only** (no hyphens). The deploy script appends a random suffix for uniqueness.

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

Run `python cleanup.py` with the original account and receipt. It removes the recorded endpoint, Runtime, ZIP and execution role in that order. The bucket is retained; deleting the Runtime also disposes of its snapshot. The caller needs `iam:GetRole` to verify identity and, for a versioned ZIP, `s3:DeleteObjectVersion`. Deployment checks the code bucket with `HeadBucket` (requires `s3:ListBucket` on an existing bucket).

If a deletion fails, keep `runtime_config.json` and retry after resolving the error. Cleanup stops on a replaced role or unexpected inline policies; it does not detach unrelated policies. Do not modify or reuse these resources concurrently. For an older receipt without a role ID or an interrupted create marked `pending`, verify what was actually created against your deployment and CloudTrail records and reconcile manually. Do not fill in a missing ID from a same-name role. After a hard process interruption, remove `runtime_config.lock` only after confirming no deploy or cleanup is running.

## What's Different from the Strands Example

The **deployment and invocation code is identical**, including the `platformVersion` handling. Only the agent code and dependencies change:

| Aspect | Strands Example | This Example |
|:-------|:----------------|:-------------|
| Framework | `strands.Agent` | `langgraph.StateGraph` |
| Model | `strands.models.BedrockModel` | `langchain_aws.ChatBedrockConverse` |
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
