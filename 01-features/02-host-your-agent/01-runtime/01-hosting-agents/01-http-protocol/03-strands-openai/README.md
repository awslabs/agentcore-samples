# Strands Agents with Azure OpenAI on AgentCore New Runtime

## Overview

Deploy a [Strands Agents](https://strandsagents.com/) agent using **Azure OpenAI** (GPT-4.1-mini via [LiteLLM](https://docs.litellm.ai/)) to AgentCore New Runtime. This demonstrates that AgentCore runtime is model-agnostic — you can use any LLM provider, not just Amazon Bedrock.

```
┌─────────────┐     invoke_agent_runtime()     ┌──────────────────────────┐
│   Client    │ ──────────────────────────────▶│  AgentCore runtime       │
│  (boto3)    │◀────────────────────────────── │  ┌──────────────────┐    │
│             │         JSON response          │  │  Strands Agent   │    │
└─────────────┘                                │  │  + Azure OpenAI  │    │
                                               │  │  (via LiteLLM)   │    │
                                               │  └──────────────────┘    │
                                               └──────────────────────────┘
```

## New Runtime in one field

AgentCore prepares your execution environment once at create/update time, snapshots it, and every new execution environment resumes from that snapshot instead of loading your code again. Cold starts get faster; how much depends on how much your agent loads at import.

You select it with one field on `create_agent_runtime`:

```python
platformVersion="V2"
```

**It must be set explicitly** — omitting it does not give you New Runtime, and the create response does not report which platform version you got. `deploy.py` calls `get_agent_runtime` afterwards to confirm.

The trade-off: **create and update take minutes rather than seconds**, because the snapshot is prepared during the call. This sample has the heaviest dependency tree of the three (`litellm` is substantial), which is exactly the load New Runtime moves off the invocation path — but it also means the packaging step takes a while before the API call even starts.

New Runtime snapshots the *environment*, not the model. Your provider is still called over the network on every invocation, so an external provider works exactly as it does without New Runtime.

> For the full walkthrough of `platformVersion` — accepted values, which operations carry it, the error behaviour, the complete parameter reference, and the rules for writing snapshot-safe agent code — see [`../01-strands-bedrock/README.md`](../01-strands-bedrock/README.md). This README covers what is specific to an external LLM provider.

## ⚠️ Before You Start: Update Your API Credentials

The `agent.py` file contains placeholder Azure OpenAI credentials that **you must replace** with your own before deploying:

```python
# In agent.py — replace these with your actual Azure OpenAI credentials:
os.environ["AZURE_API_KEY"] = "<YOUR_API_KEY>"
os.environ["AZURE_API_BASE"] = "<YOUR_API_BASE>"       # e.g., "https://your-resource.openai.azure.com/"
os.environ["AZURE_API_VERSION"] = "<YOUR_API_VERSION>"  # e.g., "2024-02-01"
```

Without valid credentials the runtime still deploys and reaches `READY` — the snapshot is prepared successfully, because nothing contacts Azure at import time — but **invocations fail with a 500 from the runtime.** If you see that, check CloudWatch before suspecting the platform version.

### Why this matters more under New Runtime

On New Runtime your module-level code runs **once**, before the snapshot is taken, and every environment resumed from that snapshot inherits whatever it left behind. For credentials specifically:

- **Do** pass secrets as `environmentVariables`. The runtime injects them before your code runs, so `os.environ` is correctly populated pre-snapshot and remains correct after a resume. Note that the values **are** returned by `get_agent_runtime`, so anyone who can describe the runtime can read them — this keeps secrets out of your deployment zip, not out of the API.
- **Don't** bake a literal key into the source. That ships the secret inside your deployment zip, and rotating it means rebuilding and re-uploading the package plus an `update_agent_runtime` — which re-prepares the snapshot and takes minutes.
- **Don't** fetch a short-lived token at import — an OAuth access token with a one-hour expiry, say. It is captured once, at snapshot time, and will be long expired by the time an environment resumes. Fetch and cache such tokens lazily inside the entrypoint, with an expiry check.

Azure API keys are long-lived, so this sample works either way. The pattern matters for any provider using short-lived credentials.

## Prerequisites

- Python 3.10+ (3.12+ recommended)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (for building the arm64 deployment package)
- AWS CLI configured with credentials
- boto3 1.43.95 or later — the first public release that models `platformVersion`
- Azure OpenAI API credentials (API key, base URL, API version)
- An AWS account and region where New Runtime is enabled (an account without it returns `ValidationException: platformVersion is not enabled for this account.`)

Note that New Runtime availability is an **AWS** concern — it has nothing to do with your Azure subscription.

## Step 1: Write the Agent (`agent.py`)

The key difference from the Bedrock examples is the model configuration. Instead of `BedrockModel`, we use `LiteLLMModel` which supports 100+ LLM providers:

```python
from strands.models.litellm import LiteLLMModel
import os

# Set Azure OpenAI credentials
os.environ["AZURE_API_KEY"] = "<YOUR_API_KEY>"
os.environ["AZURE_API_BASE"] = "<YOUR_API_BASE>"
os.environ["AZURE_API_VERSION"] = "<YOUR_API_VERSION>"

# Create the model via LiteLLM
model = "azure/gpt-4.1-mini"
litellm_model = LiteLLMModel(model_id=model, params={"max_tokens": 32000, "temperature": 0.7})

# Everything else is identical to the Bedrock example
agent = Agent(model=litellm_model, tools=[calculator, weather], ...)
```

The `@app.entrypoint` wrapper, tools, and `app.run()` are exactly the same as any other Strands agent.

`LiteLLMModel(...)` builds a client object at import but opens no connection to Azure, so it is safe to snapshot. If you swap in a provider SDK that establishes a session or connection pool eagerly at construction, move that construction inside the entrypoint — a socket opened before the snapshot will not survive the resume.

Test locally (after updating credentials):

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
python agent.py
# In another terminal:
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 25 * 17?"}'
```

## Step 2: Create an IAM Execution Role (`deploy.py`)

Since this agent calls Azure OpenAI (not Bedrock), the IAM role does **not** need `bedrock:InvokeModel` permissions. However, the runtime itself still needs CloudWatch Logs, X-Ray, and CloudWatch Metrics permissions to initialize correctly.

`platformVersion` introduces no new IAM actions or resource types. The deploy script creates the role automatically — see `create_execution_role()` in `deploy.py`.

## Step 3: Build Deployment Package and Upload to S3 (`deploy.py`)

Same arm64 packaging as the Bedrock examples — the zip must include pre-compiled `aarch64-manylinux2014` wheels, since the runtime does not run `pip install` at startup. The deploy script handles this with `uv`. Packaging is unchanged by `platformVersion`. arm64 is required either way: it is an AgentCore runtime requirement, not something New Runtime introduces.

## Step 4: Create runtime and Endpoint (`deploy.py`)

Identical to the Bedrock examples, plus the platform version:

```python
control.create_agent_runtime(
    agentRuntimeName="strands_openai_12345",
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

Then poll `get_agent_runtime` until `READY` — minutes on New Runtime — read `platformVersion` back from that response to confirm, and call `create_agent_runtime_endpoint`. The endpoint wait is also longer on New Runtime.

To keep the key out of your deployment zip, pass the credentials here instead of hardcoding them in `agent.py`:

```python
control.create_agent_runtime(
    # ... other params ...
    environmentVariables={
        "AZURE_API_KEY": "your-key",
        "AZURE_API_BASE": "https://your-resource.openai.azure.com/",
        "AZURE_API_VERSION": "2024-02-01",
    },
)
```

Then read them with `os.environ.get("AZURE_API_KEY")` in `agent.py`. The field accepts up to 50 entries.

## Step 5: Invoke the Agent (`invoke.py`)

The data plane is unchanged by `platformVersion`, so this file is identical to a deployment without New Runtime:

```python
client = boto3.client("bedrock-agentcore", region_name=region)

response = client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    payload=json.dumps({"prompt": "What is 25 * 17?"}).encode("utf-8"),
    contentType="application/json",
    accept="application/json",
)
```

A `RuntimeClientError` with a 500 here means the runtime received your request and your agent code raised — most commonly the placeholder Azure credentials. Check CloudWatch at `/aws/bedrock-agentcore/runtimes/*`.

## Step 6: Clean Up (`cleanup.py`)

Delete in reverse order: endpoints → runtime → S3 artifact → IAM role. Deleting the runtime disposes of the snapshot. There is no separate snapshot resource to clean up.

## What's Different from the Bedrock Examples

| Aspect | Bedrock Example | This Example |
|:-------|:----------------|:-------------|
| Model | `strands.models.BedrockModel` | `strands.models.litellm.LiteLLMModel` |
| Model ID | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | `azure/gpt-4.1-mini` |
| Credentials | IAM role (automatic) | Azure API key + base URL + version |
| IAM policy | Includes `bedrock:InvokeModel` | No Bedrock permissions needed |
| Dependencies | `strands-agents` | `strands-agents` + `litellm` |
| `platformVersion` | `"V2"` | **Exactly the same** |

Everything else — deployment flow, invocation, cleanup, platform version — is identical.

## Files

| File | Description |
|:-----|:------------|
| `agent.py` | Strands agent with LiteLLM + Azure OpenAI — **update credentials before deploying** |
| `requirements.txt` | `strands-agents`, `strands-agents-tools`, `litellm`, `bedrock-agentcore`, and `boto3>=1.43.95` — the floor that makes `platformVersion` available |
| `deploy.py` | Full deployment: IAM role → arm64 zip → S3 → create runtime → create endpoint, with `platformVersion="V2"` |
| `invoke.py` | Invoke the deployed agent |
| `cleanup.py` | Delete endpoint → runtime → S3 → IAM role |

## Quick Start

```bash
# 1. Update credentials in agent.py (replace <YOUR_API_KEY>, etc.)

# 2. Deploy to AgentCore New Runtime (several minutes)
python deploy.py

# 3. Invoke
python invoke.py

# 4. Clean up
python cleanup.py
```
