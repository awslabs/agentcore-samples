# Weather Agent — Harness + Evaluations + Gateway + Observability

![Weather Agent App](images/app_example.png)

## Overview

A full-stack weather agent web app that integrates **six AgentCore capabilities** in a single demo:

1. **AgentCore Gateway** — Creates a Gateway resource with an Exa MCP target, routing all tool calls through the managed proxy for centralized observability
2. **Guardrails** — Bedrock guardrail that anonymizes PII (email, phone, address) in agent responses
3. **Observability** — CloudWatch traces with full agent loop visibility
4. **Skills** — Generate weather forecast Excel spreadsheets using the xlsx skill (fetched from Git at invocation time)
5. **Evaluations** — Batch evaluation scoring with built-in evaluators (Helpfulness, Correctness, Coherence, etc.)
6. **Optimization** — AI-generated system prompt recommendations based on agent traces

The web app features:
- A **chat interface** where users ask weather questions
- **Weather data cards** that update in real time (temperature, wind, UV, sunrise/sunset)
- A **Traces panel** showing live trace IDs from CloudWatch (searchable in GenAI Observability)
- A **Skills panel** to generate weather forecast XLSX reports
- An **Evaluations panel** that triggers batch evaluations and displays scores
- An **Optimization panel** that generates AI-improved system prompts from your traces

## Quick Start

```bash
./start.sh
```

One command: installs dependencies, provisions AWS resources (Gateway, Harness, Guardrail), starts the backend and frontend. Open **http://localhost:5173**.

To stop servers: `Ctrl+C`. To delete AWS resources: `./cleanup.sh`.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite) — http://localhost:5173                 │
│                                                                  │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐  │
│  │     Chat Panel       │   │  Weather / Traces / Evaluations │  │
│  │   (send queries)     │   │  (live cards, trace IDs, scores)│  │
│  └──────────┬───────────┘   └────────────────┬────────────────┘  │
└─────────────┼─────────────────────────────────┼──────────────────┘
              │                                 │
              │  POST /api/chat (SSE)           │  GET /api/traces
              │                                 │  POST /api/evaluate
              ▼                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Backend (FastAPI) — http://localhost:8000                       │
│                                                                  │
│  ┌────────────┐  ┌────────┐  ┌──────────────┐  ┌─────────────┐   │
│  │ resources  │  │ agent  │  │observability │  │ evaluation  │   │
│  │    .py     │  │  .py   │  │     .py      │  │    .py      │   │
│  └─────┬──────┘  └───┬────┘  └──────┬───────┘  └──────┬──────┘   │
└────────┼──────────────┼──────────────┼─────────────────┼─────────┘
         │              │              │                  │
         ▼              ▼              ▼                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  AWS (AgentCore + Bedrock + CloudWatch)                          │
│                                                                  │
│  AC Gateway ──► Exa MCP ──► Web Search (live weather data)       │
│  Harness ─────► Claude Haiku 4.5 (agent orchestration)           │
│  Guardrail ───► PII anonymization (email, phone, address)        │
│  Skills ──────► xlsx skill (Git-fetched, weather report gen)     │
│  CloudWatch ──► Trace observability (GenAI Observability)        │
│  Batch Eval ──► Built-in evaluators (Helpfulness, Correctness…)  │
│  Optimization ► System prompt recommendations from traces        │
└──────────────────────────────────────────────────────────────────┘
```

## How It Works

### Web App Flow

1. **Start** — `./start.sh` provisions Gateway + Harness + Guardrail (or reuses existing ones)
2. **Chat** — User asks weather questions; agent searches via Gateway's Exa MCP target
3. **Weather Cards** — Parsed metrics (temperature, wind, UV, etc.) appear as visual cards
4. **Traces** — Each invocation generates traces visible in the Traces tab and in CloudWatch > GenAI Observability > Bedrock AgentCore > Traces
5. **Skills** — Click "Generate Report" to create an XLSX weather forecast using the xlsx skill
6. **Evaluations** — Click "Run Eval" to trigger a batch evaluation; results show scores for Helpfulness, Correctness, Coherence, and more (also visible in Bedrock AgentCore > Evaluations > Batch evaluation)
7. **Optimization** — Click "Optimize" to generate an AI-improved system prompt from your traces (also visible in Bedrock AgentCore > Optimizations > Recommendations)
8. **Cleanup** — `./cleanup.sh` deletes all AWS resources including batch evaluations


## Key Features

### AgentCore Gateway
The demo creates an AgentCore Gateway resource (`create_gateway` + `create_gateway_target`) and passes it to the harness as `type: "agentcore_gateway"`. The Gateway acts as a managed proxy between the agent and external tool servers:
- Centralized routing for MCP tool traffic
- Automatic observability (every tool call through the Gateway is traced)
- Configurable auth (NONE in this demo, supports IAM/OAuth)

### Bedrock Guardrails
A guardrail anonymizes PII in agent responses. If you ask the agent to include personal info (email, phone), the guardrail masks it before the response reaches you.

### Observability
Every `invoke_harness` call automatically generates traces in CloudWatch. The Traces tab shows trace IDs that you can search in:
- **CloudWatch > GenAI Observability > Bedrock AgentCore > Traces**

### Skills (xlsx)
The "Generate Report" button creates a 7-day weather forecast Excel spreadsheet using the AgentCore xlsx skill. The skill is fetched from Git (`https://github.com/anthropics/skills`) at invocation time — no container setup or pre-installation required. The report uses the last city you asked about.

### Batch Evaluations
The "Run Eval" button triggers a batch evaluation that scores your session using built-in evaluators:
- InstructionFollowing, Helpfulness, Correctness, Faithfulness, ResponseRelevance, Coherence, Conciseness, Refusal

Results appear in the web app and are also visible in:
- **Bedrock AgentCore > Evaluations > Batch evaluation**

### Optimization
The "Optimize" button analyzes your agent's traces and generates an AI-improved system prompt optimized for goal success. It uses the `start_recommendation` API with your harness traces as input. The recommended prompt and explanation are displayed in the web app.

Results are also visible in:
- **Bedrock AgentCore > Optimizations > Recommendations**

## Prerequisites

- Python 3.10+
- Node.js 18+
- AWS CLI configured with credentials (`aws sts get-caller-identity` should work). Recommended region: **us-east-1** (`export AWS_DEFAULT_REGION=us-east-1`)
- Model access enabled for Claude Haiku 4.5 in Amazon Bedrock
- [CloudWatch Transaction Search](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html) enabled — **required** for Traces, Evaluations, and Optimization to work. After enabling, wait 10-15 minutes before using these feature so the Bedrock AgentCore dashboard in CloudWatch can become available. Only traces from invocations *after* enabling will be indexed.

## AWS Permissions Required

> **Note:** The policies below use broad access for simplicity in this demo. In production environments, follow the principle of least privilege and create custom IAM policies scoped to only the specific resources and actions your agent needs.

| Policy | Purpose |
|--------|---------|
| `BedrockAgentCoreFullAccess` | Harness, Gateway, Batch Evaluations |
| `AmazonBedrockFullAccess` | Model invocation, Guardrails |
| `IAMFullAccess` | Create the harness execution role (first run only) |
| `CloudWatchFullAccessV2` | Query traces + batch evaluation output logs |

## Running

### Web App (recommended)

```bash
./start.sh
```

One command: creates a virtual environment, installs Python and Node.js packages, provisions AWS resources, starts the FastAPI backend and React frontend.

Open **http://localhost:5173** once the script prints "App is running!".

```bash
# Stop servers without deleting AWS resources:
# Press Ctrl+C (resources persist for next ./start.sh)

# Stop servers AND delete all AWS resources:
./cleanup.sh
```

## Sample Prompts

- "What's the weather in Tokyo?"
- "What's the wind speed in Vancouver right now?"
- "What's the UV index in Miami today?"
- "When is sunrise and sunset in London?"

<!-- ### CLI-only mode

For a headless demo that runs in the terminal and cleans up after itself. Run this separately — not while the web app is running, since both use the same AWS resources.

```bash
./run.sh
``` -->

## Clean Up

```bash
# Delete all AWS resources (gateway, harness, guardrail, batch evaluations, IAM role):
./cleanup.sh

# To also remove the virtual environment and node_modules:
rm -rf venv frontend/node_modules
```