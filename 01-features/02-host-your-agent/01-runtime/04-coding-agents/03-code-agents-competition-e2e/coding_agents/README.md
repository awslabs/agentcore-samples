# Coding Agents on AgentCore

Deploy coding agents (Claude Code, Kiro, Codex, Cursor, Hermes, OpenCode) on AWS Bedrock AgentCore with native MCP tool access via a shared gateway proxy.

## Architecture

```
coding_agents/
├── infra/                      # Shared infrastructure (deploy once)
│   ├── cfn-vpc.yaml            # CloudFormation: VPC + S3 Files
│   ├── setup.sh                # Create bucket, deploy stack, upload skills
│   └── cleanup.sh              # Tear down VPC/S3 Files (keeps bucket)
├── claude-code/                # Claude Code (Bedrock native)
│   ├── Dockerfile
│   ├── CLAUDE.md               # Agent instructions
│   ├── setup.sh                # Build & push image
│   ├── deploy.py               # Create/update runtime
│   ├── cleanup.py              # Delete runtime + role
│   └── connect.py              # Interactive PTY via WebSocket Shell
├── kiro/                       # Kiro agent (Token Vault API key)
│   ├── Dockerfile
│   ├── setup.sh                # Build image + Identity setup
│   ├── deploy.py
│   ├── cleanup.py
│   └── connect.py
├── codex/                      # Codex agent (Token Vault API key)
│   ├── Dockerfile
│   ├── setup.sh
│   ├── deploy.py
│   └── connect.py
├── cursor/                     # Cursor agent (Token Vault API key)
│   ├── Dockerfile
│   ├── setup.sh
│   ├── deploy.py
│   └── connect.py
├── hermes/                     # Hermes agent (Bedrock native)
│   ├── Dockerfile
│   ├── steering/agent.md       # Agent instructions
│   ├── setup.sh                # Build & push image (no API key needed)
│   ├── deploy.py
│   └── connect.py
├── open-code/                  # OpenCode agent (Bedrock native)
│   ├── Dockerfile
│   ├── AGENTS.md               # Agent instructions
│   ├── opencode.json           # Provider config (amazon-bedrock)
│   ├── setup.sh                # Build & push image (no API key needed)
│   ├── deploy.py
│   └── connect.py
└── frontend/                   # Local comparison UI (Flask + xterm.js)
    ├── app.py
    ├── static/
    └── templates/
```

All agents mount the same S3 Files filesystem at `/mnt/s3files/`, giving them access to the MCP Gateway Proxy and shared skills without rebuilding containers. Each agent configures the proxy as a native MCP server at startup via `run.sh`.

## Prerequisites

```bash
pip install -r requirements.txt
```

## Deployment

### 1. Shared infrastructure (once)

```bash
cd infra
./setup.sh us-west-2
```

### 2. Deploy an agent

Each agent folder is self-contained:

```bash
cd claude-code
./setup.sh         # Build & push Docker image
python deploy.py   # Create IAM role + AgentCore runtime
python connect.py  # Interactive session via WebSocket Shell
```

```bash
cd kiro
./setup.sh         # Build image + setup Token Vault identity
python deploy.py
python connect.py
```

```bash
cd hermes
./setup.sh         # Build & push (no API key needed — uses Bedrock)
python deploy.py
python connect.py
```

```bash
cd open-code
./setup.sh         # Build & push (no API key needed — uses Bedrock)
python deploy.py
python connect.py
```

### Authentication Models

| Agent | Auth Method | Notes |
|-------|-------------|-------|
| Claude Code | Bedrock native | IAM role via IMDS, no API key |
| Hermes | Bedrock native | IAM role via IMDS, no API key |
| OpenCode | Bedrock native | IAM creds fetched from IMDS at boot |
| Kiro | Token Vault | API key stored encrypted in Secrets Manager |
| Codex | Token Vault | OpenAI API key via Identity |
| Cursor | Token Vault | Cursor API key via Identity |

### Interactive Sessions (WebSocket Shell)

```bash
cd claude-code
python connect.py                          # New interactive session
python connect.py --session <session-id>   # Resume existing session
python connect.py --prompt "fix the bug"   # One-shot headless mode
python connect.py --cmd "ls /mnt/s3files/" # Run a raw shell command
```

## Teardown

```bash
# Agent-specific (keeps shared infra)
cd claude-code && python cleanup.py

# Shared infra (removes VPC, S3 Files — keeps bucket)
cd infra && ./cleanup.sh
```

## Adding a new agent

1. Create a new folder (e.g. `my-agent/`)
2. Add `Dockerfile` (with `USER agent` uid 1000), `run.sh`, `setup.sh`, `deploy.py`, `connect.py`
3. In `run.sh`, generate the agent's MCP config pointing to `/mnt/s3files/mcp/index.js` with `--gateway-url` and `--region`
4. If the agent uses Bedrock natively, the IAM role provides `bedrock:InvokeModel` — no API key needed
5. If the agent needs an external API key, use AgentCore Identity (Token Vault) — see `kiro/setup.sh` for reference
6. Add it to `frontend/app.py` in the `AGENTS` dict (with `default_model`) to include it in the comparison UI
7. Support `--model` flag in `run.sh` for model override from the frontend

No need to redeploy infra or re-upload the MCP proxy.

## Adding a new skill

Drop a `.md` file in `infra/skills/` and re-run `infra/setup.sh`, or upload directly:

```bash
aws s3 cp my-skill.md s3://coding-agents-<account-id>/agents/mnt/s3files/skills/my-skill.md
```
