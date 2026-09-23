# Codex — AgentCore Runtime

OpenAI Codex deployed as an AgentCore Runtime with interactive WebSocket PTY access, powered by **Amazon Bedrock Mantle** for inference.

## Amazon Bedrock Mantle

This agent uses [Amazon Bedrock Mantle](https://aws.amazon.com/blogs/aws/get-started-with-openai-gpt-5-5-gpt-5-4-models-and-codex-on-amazon-bedrock/) — Bedrock's next-generation inference engine that provides access to OpenAI models (GPT-5.5, GPT-5.4, Codex) directly through AWS infrastructure.

All inference requests stay within the Bedrock Region you select — no data leaves AWS.

### Available Models

| Model ID | Description |
|----------|-------------|
| `openai.gpt-5.5` | Most capable — for complex reasoning tasks |
| `openai.gpt-5.4` | Best price-performance ratio |
| `openai.codex` | Coding agent optimized for software development |

### Region Compatibility

At the time this code was written (June 2026), the following region support was available:

| Model | Regions |
|-------|---------|
| GPT-5.5 | `us-east-2` (US East - Ohio) |
| GPT-5.4 | `us-east-2` (US East - Ohio), `us-west-2` (US West - Oregon) |

This is why `run.sh` defaults to `us-east-2` for the Bedrock Mantle endpoint.

Check [AWS docs](https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html) for updated region availability.

### Authentication

Bedrock Mantle uses an OpenAI-compatible API format:

- **Base URL:** `https://bedrock-mantle.<region>.api.aws/openai/v1`
- **API Key:** Short-term Bedrock API key (generated from IAM role credentials), passed as `OPENAI_API_KEY`

In AgentCore, the runtime's IAM role provides credentials automatically. `run.sh` generates a short-term token at each session start.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  Your machine                                             │
│  connect.py ──WebSocket PTY──► AgentCore Runtime (codex)  │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│  Inside the runtime (microVM)                             │
│                                                           │
│  entrypoint.py → starts healthcheck.py on :8080           │
│                                                           │
│  PTY session (connect.py) → /app/run.sh                   │
│    │                                                      │
│    ├─ Generates ~/.codex/config.toml (MCP gateway)        │
│    ├─ Generates short-term Bedrock API key from IAM role  │
│    │    → OPENAI_API_KEY (in-memory only)                 │
│    │                                                      │
│    ├─ OPENAI_BASE_URL=https://bedrock-mantle.us-east-2... │
│    │                                                      │
│    └─ codex exec --model openai.gpt-5.5 ...               │
│                                                           │
│  Bedrock Mantle endpoint: OpenAI-compatible API over AWS  │
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

This creates:
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
python connect.py --model openai.gpt-5.5

# One-shot prompt
python connect.py --model openai.gpt-5.5 --prompt "fix the bug in main.py"
```

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | Build image, push to ECR |
| `deploy.py` | Register/update runtime on AgentCore |
| `cleanup.py` | Delete runtime + IAM role + Identity resources |
| `connect.py` | Interactive WebSocket PTY connection |
| `run.sh` | In-container launcher — configures MCP, generates Bedrock API key, launches codex |
| `healthcheck.py` | `/ping` endpoint for AgentCore health checks |
| `Dockerfile` | Container image (Amazon Linux 2023 + codex + run.sh) |
| `AGENTS.md` | Agent instructions (system prompt for codex) |
| `codex-config.toml` | Base codex configuration |
