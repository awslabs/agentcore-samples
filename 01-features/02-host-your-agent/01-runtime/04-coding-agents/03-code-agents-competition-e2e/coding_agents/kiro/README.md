# Kiro — AgentCore Runtime

Kiro CLI deployed as an AgentCore Runtime with interactive WebSocket PTY access and secure API key management via AgentCore Identity.

## Inference

Kiro CLI uses Claude frontier models for inference. The API key (stored in AgentCore Identity Token Vault) authenticates requests to Kiro's backend.

### Default Model

| Model ID | Description |
|----------|-------------|
| `auto` | Default — Kiro's model router, picks the optimal model per task |

`auto` is Kiro's intelligent model router. It combines multiple frontier models (Claude Sonnet 4 and similar) to deliver the best quality-to-cost ratio — automatically choosing the optimal model for each task. It serves as the 1.0x cost baseline. You can also specify a model directly (e.g. `claude-opus-4.8`, `claude-sonnet-4.6`, `claude-haiku-4.5`).

Override with `--model` via `connect.py` or the frontend model input.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│  Your machine                                             │
│  connect.py ──WebSocket PTY──► AgentCore Runtime (kiro)   │
└───────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────┐
│  Inside the runtime (microVM)                             │
│                                                           │
│  entrypoint.py → starts healthcheck.py on :8080           │
│                                                           │
│  PTY session (connect.py) → /app/run.sh                   │
│    │                                                      │
│    ├─ Generates ~/.kiro/settings/mcp.json (MCP gateway)   │
│    ├─ Generates ~/.kiro/settings/cli.json (model config)  │
│    │                                                      │
│    ├─ python3 boto3:                                      │
│    │    get_workload_access_token("kiro-coding-agent")    │
│    │    get_resource_api_key(token, "kiro-api-key")       │
│    │    → KIRO_API_KEY (in-memory only, never on disk)    │
│    │                                                      │
│    └─ KIRO_API_KEY=xxx kiro-cli                           │
│                                                           │
│  Key lives only in the shell's memory for that session.   │
└───────────────────────────────────────────────────────────┘
```

## Security Model

The API key is **never stored in plaintext** in:
- Runtime environment variables (AWS console/API)
- Container image layers or filesystem
- CloudTrail logs
- runtime_config.json or /etc/profile.d/

Instead, it's encrypted at rest in AWS Secrets Manager (via KMS) through AgentCore's credential provider system. The key is fetched **on-demand** at the start of each PTY session by `/app/run.sh`, lives only in shell memory, and is discarded when the session ends. Only the runtime's IAM role can retrieve it.

## Setup

### Prerequisites

- `../infra.config` exists (shared VPC, S3 Files — run `../infra/setup.sh`)
- Docker with buildx (for arm64 images)
- A Kiro API key (see below)

### Generating a Kiro API key

Kiro CLI requires an API key for non-interactive (headless) usage. To generate one:

1. Install Kiro locally: `curl -fsSL https://cli.kiro.dev/install | bash`
2. Log in: `kiro-cli login --license free --use-device-flow`
   - This opens a browser for Builder ID OAuth. Complete the sign-in.
   - For Pro/Enterprise: `kiro-cli login --license pro --identity-provider <start-url> --region <region>`
3. Once logged in, generate an API key: `kiro-cli api-key create`
   - This outputs a key like `ksk_xxxxxxxxxxxx`
   - Copy it — you'll need it for setup.sh

If `kiro-cli api-key create` is not available in your version, check the Kiro
docs at https://kiro.dev or use the Kiro dashboard to generate a token.

### 1. Build image + store API key in Token Vault

```bash
# Interactive — prompts for the key
./setup.sh

# Non-interactive
KIRO_API_KEY=ksk_xxx ./setup.sh

# Build only, skip Identity (if already configured)
./setup.sh --skip-identity
```

This creates:
- ECR repository + pushes the arm64 image
- AgentCore workload identity: `kiro-coding-agent`
- AgentCore credential provider: `kiro-api-key` (encrypted in Secrets Manager)

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
# Opens a bash shell on the microVM
python connect.py
```

Once inside the microVM shell:

```bash
# Launch kiro-cli (fetches API key from Token Vault + starts kiro)
/app/run.sh

# Or run a specific kiro command
/app/run.sh chat "fix the bug in main.py"

# Or manually: switch to agent user and run kiro directly
su - agent
export KIRO_API_KEY=$(python3 -c "
import boto3
from botocore.config import Config
c = boto3.client('bedrock-agentcore', region_name='us-west-2', config=Config(connect_timeout=5, read_timeout=10))
t = c.get_workload_access_token(workloadName='kiro-coding-agent')['workloadAccessToken']
print(c.get_resource_api_key(workloadIdentityToken=t, resourceCredentialProviderName='kiro-api-key')['apiKey'], end='')
")
kiro-cli
```

## Updating the API key

Re-run setup with the new key — it updates the credential provider in-place:

```bash
KIRO_API_KEY=ksk_new_key ./setup.sh
```

No redeploy needed — each PTY session fetches a fresh key from Token Vault on connect.

## Teardown

```bash
# Full cleanup (runtime + IAM role + Identity resources)
python cleanup.py

# Keep Identity (useful if you want to redeploy with same key later)
python cleanup.py --keep-identity
```

## Files

| File | Purpose |
|------|---------|
| `setup.sh` | Build image, push to ECR, setup AgentCore Identity |
| `deploy.py` | Register/update runtime on AgentCore |
| `connect.py` | Interactive WebSocket PTY connection |
| `run.sh` | In-container launcher — configures MCP, fetches key from Token Vault, launches kiro-cli |
| `healthcheck.py` | `/ping` endpoint for AgentCore health checks |
| `cleanup.py` | Teardown runtime, IAM role, and Identity |
| `Dockerfile` | Container image (Amazon Linux 2023 + kiro-cli + run.sh) |
| `steering/agent.md` | Agent instructions (loaded by kiro-cli at session start) |
