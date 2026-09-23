# Cursor — AgentCore Runtime

Cursor Agent deployed as an AgentCore Runtime with interactive WebSocket PTY access and secure API key management via AgentCore Identity Token Vault.

## Inference

Cursor Agent handles model routing internally. The API key (stored in AgentCore Identity Token Vault) authenticates requests to Cursor's backend, which routes to the configured model.

### Default Model

| Model ID | Description |
|----------|-------------|
| `auto` | Default — Cursor selects models balancing intelligence, cost, and reliability |

`auto` is Cursor's intelligent model router — it picks the best model for each task automatically. There's also `premium` which always selects the most capable models (recommended for complex tasks). You can also specify a model directly (e.g. `claude-4.6-sonnet`, `gpt-5.5`, `gemini-3.1-pro`).

Override with `--model` via `connect.py` or the frontend model input.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  Your machine                                             │
│  connect.py ──WebSocket PTY──► AgentCore Runtime (cursor) │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│  Inside the runtime (microVM)                             │
│                                                           │
│  entrypoint.py → starts healthcheck.py on :8080           │
│                                                           │
│  PTY session (connect.py) → /app/run.sh                   │
│    │                                                      │
│    ├─ Generates ~/.cursor/mcp.json (MCP gateway)          │
│    │                                                      │
│    ├─ python3 boto3:                                      │
│    │    get_workload_access_token("cursor-coding-agent")  │
│    │    get_resource_api_key(token, "cursor-api-key")     │
│    │    → CURSOR_API_KEY (in-memory only, never on disk)  │
│    │                                                      │
│    └─ cursor-agent --model auto --workspace /home/agent   │
│                                                           │
│  Key lives only in the shell's memory for that session.   │
└───────────────────────────────────────────────────────────┘
```

## Security Model

The API key is **never stored in plaintext** in:
- Runtime environment variables (AWS console/API)
- Container image layers or filesystem
- CloudTrail logs

It's encrypted at rest in AWS Secrets Manager (via KMS) through AgentCore's credential provider system. The key is fetched **on-demand** at the start of each PTY session, lives only in shell memory, and is discarded when the session ends.

## Setup

### Prerequisites

- `../infra.config` exists (shared VPC, S3 Files — run `../infra/setup.sh`)
- Docker with buildx (for arm64 images)
- A Cursor API key

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
- IAM role with `AgentCoreIdentity` permissions (GetWorkloadAccessToken + GetResourceApiKey)

### 3. Connect

```bash
# Interactive TUI mode
python connect.py

# With a specific model
python connect.py --model claude-sonnet-4-5

# One-shot prompt
python connect.py --prompt "fix the bug in main.py"
```

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | Build image, push to ECR |
| `deploy.py` | Register/update runtime on AgentCore |
| `cleanup.py` | Delete runtime + IAM role + Identity resources |
| `connect.py` | Interactive WebSocket PTY connection |
| `run.sh` | In-container launcher — configures MCP, fetches API key from Token Vault, launches cursor-agent |
| `healthcheck.py` | `/ping` endpoint for AgentCore health checks |
| `Dockerfile` | Container image (Amazon Linux 2023 + cursor-agent + run.sh) |
| `rules/` | Agent instructions (loaded by cursor-agent at session start) |
