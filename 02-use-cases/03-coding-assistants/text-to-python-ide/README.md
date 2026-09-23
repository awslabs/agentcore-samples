# Text-to-Python IDE — AgentCore Demo

A full-stack application demonstrating **Amazon Bedrock AgentCore** capabilities through a Python code generation and execution interface. Built with Strands Agents, FastAPI, and React (AWS Cloudscape).

## What This Project Demonstrates

This project showcases five core AgentCore features working together in a real application:

| Feature | What It Does | How It's Used |
|---------|-------------|---------------|
| **Code Interpreter** | Secure sandboxed Python execution | User code runs in an isolated AWS container — no risk to your infrastructure |
| **Runtime** | Managed agent deployment | The agent is deployed as a containerized service with autoscaling and endpoints |
| **Memory** | Persistent session storage | Conversations survive server restarts; users can resume past sessions |
| **Guardrails** | Content safety filtering | Blocks malicious code requests (keyloggers, ransomware, exploits) |
| **Observability** | Distributed tracing (ready) | Project includes OTel instrumentation; enable via [AgentCore Observability guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html) |

## Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │            AWS Account                       │
                         │                                             │
┌──────────┐   REST     ┌┴──────────┐     invoke      ┌─────────────┐ │
│  React   │ ────────── │  FastAPI  │ ──────────────── │  AgentCore  │ │
│ Frontend │  :3000     │  Backend  │     :8080        │   Runtime   │ │
└──────────┘            └─────┬─────┘                  └──────┬──────┘ │
                              │                               │        │
                              │                        ┌──────┴──────┐ │
                              │                        │    Code     │ │
                              │                        │ Interpreter │ │
                              │                        │  (sandbox)  │ │
                              │                        └─────────────┘ │
                              │                                        │
                         ┌────┴────┐  ┌──────────┐  ┌──────────────┐  │
                         │ Bedrock │  │  Memory  │  │  Guardrails  │  │
                         │ Models  │  │ (sessions│  │  (content    │  │
                         │(Claude) │  │  & turns)│  │   safety)    │  │
                         └─────────┘  └──────────┘  └──────────────┘  │
                         └─────────────────────────────────────────────┘
```

## What Gets Deployed to AWS

Running `start.sh` provisions the following resources in your AWS account:

| Resource | Service | Purpose | Cost |
|----------|---------|---------|------|
| Guardrail | Bedrock | Content safety filtering | Per-assessment |
| Memory | AgentCore | Session persistence | Per-request |
| Runtime + Endpoint | AgentCore | Managed agent service | Per-invocation |
| ECR Repository | ECR | Container image storage | Storage-based |
| Code Interpreter sessions | AgentCore | Sandboxed execution | Per-session |

All resources are created in **us-east-1** by default.

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **Docker Desktop** (must be running — required for runtime deployment)
- **AWS CLI** configured with a profile that has `BedrockAgentCoreFullAccess` permissions
- AWS account with Bedrock model access enabled (Claude Haiku 4.5)

## Quick Start

```bash
# One command does everything: setup, provision AWS resources, start servers
./start.sh
```

This will:
1. Create a Python virtual environment and install dependencies
2. Install frontend Node.js packages
3. Verify AWS credentials
4. Create a Bedrock Guardrail (if not exists)
5. Create AgentCore Memory (if not exists)
6. Build and deploy the AgentCore Runtime via Docker (if Docker is running)
7. Start the FastAPI backend on port 8000
8. Start the React frontend on port 3000

Once running, open **http://localhost:3000**.

## Usage

1. **Generate Code** — Type a natural language prompt (e.g., "create a fibonacci function that takes a number and returns that many steps") and click Generate
2. **Edit Code** — Review and modify the generated code in the Code Editor tab
3. **Execute Code** — Click Execute to run the code in AgentCore's secure sandbox
4. **View Results** — See output, errors, and charts in the Execution Results tab
5. **Session History** — Resume past sessions from AgentCore Memory

## Project Structure

```
├── backend/
│   ├── main.py              # FastAPI app, agent initialization, API endpoints
│   ├── agent_runtime.py     # AgentCore Runtime entrypoint (runs inside container)
│   ├── runtime_proxy.py     # Forwards requests to deployed runtime
│   ├── memory_manager.py    # AgentCore Memory integration
│   └── observability.py     # OpenTelemetry / X-Ray tracing setup
├── frontend/
│   └── src/
│       ├── App.jsx          # Main React app
│       ├── components/      # UI components (CodeEditor, ExecutionResults, etc.)
│       └── services/api.js  # Backend API client
├── Dockerfile               # Container image for AgentCore Runtime
├── deploy_runtime.py        # Build, push, and deploy runtime to AgentCore
├── invoke_runtime.py        # CLI tool to test the deployed runtime
├── setup_guardrails.py      # Create/delete Bedrock Guardrail
├── setup_memory.py          # Create/delete AgentCore Memory
├── start.sh                 # Full setup + start (one command)
├── cleanup.sh               # Stop processes and remove local artifacts
├── requirements.txt         # Python dependencies
└── .env                     # Configuration (auto-generated)
```

## Configuration

Key environment variables in `.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_PROFILE` | AWS credentials profile | `default` |
| `AWS_REGION` | AWS region for all resources | `us-east-1` |
| `AGENTCORE_RUNTIME_ARN` | Deployed runtime ARN (empty = local agents) | — |
| `AGENTCORE_MEMORY_ID` | Memory ID for session persistence | — |
| `BEDROCK_GUARDRAIL_ID` | Guardrail ID for content filtering | — |

## Observability

This project includes OpenTelemetry instrumentation (`backend/observability.py`) with X-Ray-compatible trace IDs, making it ready for AgentCore Observability integration. To enable full observability in CloudWatch (traces, metrics, session monitoring), follow the configuration guide:

https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html

## Cleanup

```bash
# Deletes all AWS resources AND removes local files (one command does everything)
./cleanup.sh
```

## AWS Permissions Required

Your AWS profile needs the following managed policies attached:

| Policy | Purpose |
|--------|---------|
| `BedrockAgentCoreFullAccess` | Runtime, Memory, Code Interpreter |
| `AmazonBedrockFullAccess` | Model invocation, Guardrails |
| `AmazonEC2ContainerRegistryFullAccess` | Push Docker images to ECR |
| `IAMFullAccess` | Create the runtime execution role (only needed on first deploy) |

