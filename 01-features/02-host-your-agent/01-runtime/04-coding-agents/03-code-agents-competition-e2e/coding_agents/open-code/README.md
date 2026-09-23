# OpenCode — AgentCore Runtime

OpenCode deployed as an AgentCore Runtime with interactive WebSocket PTY access, powered by **Amazon Bedrock** for inference.

## Inference

OpenCode uses Amazon Bedrock via the `amazon-bedrock` provider. Since OpenCode does not read IMDS directly, `run.sh` fetches IAM credentials from the instance metadata service (IMDS v2) and exports them as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`.

### Default Model

| Model ID | Description |
|----------|-------------|
| `amazon-bedrock/anthropic.claude-sonnet-4-5-20250929-v1:0` | Default — Claude Sonnet 4.5 |

Small model (for summaries/indexing): `amazon-bedrock/anthropic.claude-haiku-4-5-20251001-v1:0`

Override with `--model` via `connect.py` or the frontend model input.

### Region

Inference runs in `us-west-2` by default.

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Your machine                                                  │
│  connect.py ──WebSocket PTY──► AgentCore Runtime (open-code)   │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Inside the runtime (microVM)                                  │
│                                                                │
│  entrypoint.py → starts healthcheck.py on :8080                │
│                                                                │
│  PTY session (connect.py) → /app/run.sh                        │
│    │                                                           │
│    ├─ Fetches AWS credentials from IMDS v2                     │
│    │    → AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,            │
│    │      AWS_SESSION_TOKEN (in-memory)                        │
│    │                                                           │
│    ├─ Generates ~/.config/opencode/opencode.json               │
│    │    (provider config, model, MCP gateway)                  │
│    │                                                           │
│    └─ opencode run --dangerously-skip-permissions -m <model>   │
│                                                                │
│  No API key needed — IAM role provides credentials via IMDS.   │
└────────────────────────────────────────────────────────────────┘
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
python connect.py --model amazon-bedrock/anthropic.claude-opus-4-8

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
| `run.sh` | In-container launcher — fetches IMDS creds, generates config, launches opencode |
| `healthcheck.py` | `/ping` endpoint for AgentCore health checks |
| `Dockerfile` | Container image (Amazon Linux 2023 + opencode + run.sh) |
| `AGENTS.md` | Agent instructions (system prompt for opencode) |
| `opencode.json` | Base opencode configuration template |
