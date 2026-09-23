# Hermes — AgentCore Runtime

Hermes Agent deployed as an AgentCore Runtime with interactive WebSocket PTY access, powered by **Amazon Bedrock** for inference.

## Inference

Hermes uses Amazon Bedrock directly (`HERMES_INFERENCE_PROVIDER=bedrock`). The runtime's IAM role has `bedrock:InvokeModel` permissions — no API key is needed. AWS credentials are provided automatically by the instance metadata service.

### Default Model

| Model ID | Description |
|----------|-------------|
| `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Default — Claude Sonnet 4.5 (cross-region) |

Override with `--model` via `connect.py` or the frontend model input.

### Region

Inference runs in `us-west-2` by default (cross-region inference profile routes to the best available region).

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  Your machine                                             │
│  connect.py ──WebSocket PTY──► AgentCore Runtime (hermes) │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│  Inside the runtime (microVM)                             │
│                                                           │
│  entrypoint.py → starts healthcheck.py on :8080           │
│                                                           │
│  PTY session (connect.py) → /app/run.sh                   │
│    │                                                      │
│    ├─ Generates ~/.hermes/config.yaml                     │
│    │    (provider, model, region, MCP gateway)            │
│    │                                                      │
│    └─ hermes -m <model> --provider bedrock                │
│                                                           │
│  No API key needed — IAM role provides credentials.       │
└───────────────────────────────────────────────────────────┘
```

## Setup

### Prerequisites

- `../infra.config` exists (shared VPC, S3 Files — run `../infra/setup.sh`)
- Docker with buildx (for arm64 images)

### 1. Build image

```bash
./setup.sh
```

Creates:
- ECR repository + pushes the arm64 image

### 2. Deploy runtime

```bash
python deploy.py
```

Creates/updates the AgentCore Runtime with:
- VPC networking (subnets + security group from infra.config)
- S3 Files mount at `/mnt/s3files`
- IAM role with Bedrock InvokeModel permissions

### 3. Connect

```bash
# Interactive TUI mode
python connect.py

# With a specific model
python connect.py --model global.anthropic.claude-opus-4-8

# One-shot prompt
python connect.py --prompt "fix the bug in main.py"
```

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | Build image, push to ECR |
| `deploy.py` | Register/update runtime on AgentCore |
| `cleanup.py` | Delete runtime + IAM role |
| `connect.py` | Interactive WebSocket PTY connection |
| `run.sh` | In-container launcher — generates config.yaml, launches hermes |
| `healthcheck.py` | `/ping` endpoint for AgentCore health checks |
| `Dockerfile` | Container image (Amazon Linux 2023 + hermes + run.sh) |
| `steering/agent.md` | Agent instructions (loaded by hermes at session start) |
