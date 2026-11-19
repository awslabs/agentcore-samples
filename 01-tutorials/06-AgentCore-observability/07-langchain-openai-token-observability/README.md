# LangChain + Azure OpenAI Agent on AgentCore with GenAI Observability

## Overview

This sample demonstrates how to deploy a LangChain/LangGraph agent backed by **Azure OpenAI** on **Amazon Bedrock AgentCore Runtime**, with full token-level observability surfaced in the **CloudWatch GenAI Observability dashboard**.

The GenAI Observability dashboard is model-agnostic — it reads properly formatted OpenTelemetry spans regardless of whether the model is Amazon Bedrock or Azure OpenAI. This sample shows the exact instrumentation pattern required to make Azure OpenAI token usage visible in the dashboard.

### Tutorial Details

| Information         | Details                                                                 |
|:--------------------|:------------------------------------------------------------------------|
| Tutorial type       | Agent Observability                                                     |
| Agent framework     | LangChain / LangGraph                                                   |
| Model provider      | Azure OpenAI (GPT-4o-mini)                                              |
| Observability       | CloudWatch GenAI Observability dashboard via ADOT + OTEL spans          |
| Example complexity  | Intermediate                                                            |
| SDK used            | Amazon Bedrock AgentCore Python SDK, `opentelemetry-instrumentation-langchain` |

### Architecture

```
Caller
  │
  ▼
AgentCore Runtime (HTTP)
  │
  ├── agent.py  ──►  LangGraph ReAct agent
  │                       │
  │                       ├── AzureChatOpenAI  ──►  Azure OpenAI endpoint
  │                       └── get_weather tool ──►  api.weather.gov
  │
  └── ADOT Collector  ──►  CloudWatch (GenAI Observability dashboard)
                                │
                                └── OTEL spans (token counts, latency, sessions)
```

---

## Why LangChain Needs Explicit Instrumentation

LangChain/LangGraph agents deployed on AgentCore do **not** appear in the dashboard by default because:

- The dashboard reads OTEL **spans**. Without an instrumentor, LangChain emits nothing.
- Azure OpenAI calls bypass the Bedrock SDK, so there is no automatic interception.
- If `LangchainInstrumentor().instrument()` is called **after** the LLM or agent is constructed, spans are not emitted.
- If `session.id` is not propagated via OTEL baggage per invocation, traces arrive ungrouped in the Sessions View.

There is also a **dependency version pitfall**: `opentelemetry-instrumentation-langchain` and `opentelemetry-semantic-conventions-ai` must be on compatible versions. A mismatch causes an `ImportError: cannot import name 'GenAICustomOperationName' from 'opentelemetry.semconv_ai'` at startup, before any telemetry is emitted.

---

## Project Structure

```
langchain_agent/
├── agent.py              # Agent implementation with explicit OTEL instrumentation
├── requirements.txt      # Dependencies with pinned compatible OTEL versions
├── deploy.py             # Deployment script using AgentCore CLI
├── .bedrock_agentcore.yaml
└── README.md
```

---

## Prerequisites

- AWS account with IAM permissions for Amazon Bedrock, CloudWatch, and AgentCore
- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- Python 3.10+
- Azure OpenAI resource with a deployed model (e.g., `gpt-4o-mini`)
- [Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Enable-TransactionSearch.html) enabled in CloudWatch

---

## Key Implementation Details

### 1. Instrument before constructing the agent (`agent.py`)

```python
# Must be called BEFORE the LLM or agent_executor is created
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry import baggage, context

LangchainInstrumentor().instrument()

# Only then construct the model and agent
llm = AzureChatOpenAI(...)
agent_executor = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
```

### 2. Propagate session ID via OTEL baggage per invocation

```python
@app.entrypoint
def invoke(payload):
    session_id = payload.get("session_id", "default-session")
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    try:
        result = agent_executor.invoke(...)
        ...
    finally:
        context.detach(token)
```

The `session.id` baggage value populates the **Sessions View** in the dashboard and groups all spans from a single conversation together.

### 3. Pin compatible OTEL library versions (`requirements.txt`)

```
aws-opentelemetry-distro>=0.10.0
opentelemetry-instrumentation-langchain==0.48.1
opentelemetry-semantic-conventions-ai>=0.4.1
```

`opentelemetry-instrumentation-langchain` and `opentelemetry-semantic-conventions-ai` must resolve to compatible versions. The versions above are known-good. If you unpin them and let pip resolve freely, you risk an `ImportError` at container startup.

---

## Getting Started

### Step 1: Set environment variables

```bash
export AZURE_OPENAI_ENDPOINT="https://<your-resource>.cognitiveservices.azure.com/"
export AZURE_OPENAI_API_KEY="<your-api-key>"
export AZURE_OPENAI_DEPLOYMENT="<your-deployment-name>"
export AZURE_OPENAI_API_VERSION="2025-01-01-preview"
```

### Step 2: Install the AgentCore starter toolkit

```bash
python -m venv .venv && source .venv/bin/activate
pip install bedrock-agentcore-starter-toolkit
```

### Step 3: Create an IAM execution role

Create the [AgentCore Runtime Execution Role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) and note the ARN.

### Step 4: Deploy

```bash
cd langchain_agent
python deploy.py
```

Or manually with the CLI:

```bash
agentcore configure \
  --entrypoint agent.py \
  --name langchain_agent \
  --execution-role <YOUR_IAM_ROLE_ARN> \
  --region us-east-1 \
  --requirements-file requirements.txt \
  --disable-memory \
  --non-interactive

agentcore launch --agent langchain_agent
```

### Step 5: Invoke the deployed agent

```bash
agentcore invoke '{"prompt": "What is the weather in Seattle?", "session_id": "user-123"}'
```

Or using the AWS SDK:

```python
import boto3, json

client = boto3.client("bedrock-agentcore", region_name="us-east-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:<account>:runtime/langchain_agent-<id>",
    qualifier="DEFAULT",
    payload=json.dumps({"prompt": "Will it rain tomorrow?", "session_id": "user-123"}),
)
print(response["response"].read().decode())
```

---

## Viewing Observability Data

Once the agent is invoked, open the **CloudWatch GenAI Observability dashboard**:

1. Go to **CloudWatch → Application Signals → GenAI Observability**
2. Token usage (input/output), latency, and error rates appear per model call
3. Use the **Sessions View** to group traces by `session.id`




