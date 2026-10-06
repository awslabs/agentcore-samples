# Fix It Fast: Autonomous Bug Resolution with Claude Code on Amazon Bedrock AgentCore

Deploy Claude Code as a cloud-hosted coding agent on Amazon Bedrock AgentCore, connect it to your GitHub repository via an IAM-authenticated MCP Gateway, and watch it autonomously read bug issues, locate the root cause, commit a fix, and open a pull request — all without leaving your notebook.

### Workshop Details

| Information | Details |
|:---|:---|
| Workshop type | Hands-on lab |
| Level | 300 |
| Agent type | Coding agent (Claude Code) |
| LLM model | Anthropic Claude Opus 4 (via Amazon Bedrock) |
| Workshop components | AgentCore Runtime · AgentCore Gateway · GitHub MCP Tools · S3 Files |
| Example complexity | Advanced |
| SDK used | Amazon BedrockAgentCore Python SDK · boto3 |

### Architecture

```
Your machine
  └─ connect.py ──(SigV4 WebSocket)──► AgentCore Runtime (microVM)
                                            └─ Claude Code container
                                                  ├─ Amazon Bedrock  (model inference, IAM)
                                                  └─ AgentCore Gateway  (GitHub MCP tools)
                                                        └─ GitHub API  (via GitHub App)
```

<div style="text-align:left">
    <img src="images/architecture.png" width="100%"/>
</div>

### What the agent does

1. Receives a prompt: *"Fix issue #N in owner/repo"*
2. Reads the issue body via GitHub MCP tools
3. Lists and fetches relevant source files from the repository
4. Reasons about the root cause and applies the fix
5. Creates a branch `fix/issue-N`, commits the change, and opens a pull request
6. Labels the issue and PR — never merges or closes anything

### Key Features

- **AgentCore Runtime** — managed microVM, scales to zero, no servers to manage
- **AgentCore Gateway** — exposes GitHub as IAM-authenticated MCP tools; no API keys in the container
- **S3 Files mount** — shared filesystem at `/mnt/s3files/` lets you update the MCP proxy without rebuilding the image
- **CLAUDE.md** — baked-in system prompt that enforces guardrails and workflow conventions

---

## Prerequisites

- AWS CLI v2 configured with valid credentials
- Docker running locally (with `buildx` for arm64 builds)
- Python 3.10+
- `jq` installed
- `gh` CLI installed and authenticated (`gh auth login`)
- A GitHub App with repository permissions (Contents, Issues, Pull requests — Read & Write)

---

## Getting Started

### Install dependencies

```bash
pip install -r requirements.txt
```

### Notebooks

| Notebook | Who runs it | Time |
|---|---|---|
| [lab-00-deploy.ipynb](lab-00-deploy.ipynb) | Instructor (or self-paced) — deploys all infrastructure | ~15–25 min |
| [lab-01-triage-agent.ipynb](lab-01-triage-agent.ipynb) | Everyone — classify and label issues autonomously | ~15 min |
| [lab-02-bug-fix-agent.ipynb](lab-02-bug-fix-agent.ipynb) | Everyone — fix triaged bugs and open pull requests | ~20 min |

### Step 1 — Deploy (instructor or self-paced)

Open [lab-00-deploy.ipynb](lab-00-deploy.ipynb) and run all cells:

1. Configure GitHub App credentials and AWS region
2. Create the sample repository and seed 9 bug issues
3. Deploy the GitHub MCP Gateway
4. Deploy shared infrastructure (CloudFormation: VPC + S3 Files)
5. Build and push the Claude Code container to ECR
6. Create the AgentCore Runtime

At the end it prints the values to share with participants.

### Step 2 — Triage issues (everyone)

Open [lab-01-triage-agent.ipynb](lab-01-triage-agent.ipynb). The agent reads each issue, classifies it as `bug`/`feature`/`question`, assigns `P0`–`P3` severity, applies labels, and posts a structured comment.

### Step 3 — Fix bugs (everyone)

Open [lab-02-bug-fix-agent.ipynb](lab-02-bug-fix-agent.ipynb). Pick any issue labelled `bug` by the triage agent, run the fix agent, and review the pull request it opens.

---

## Folder Structure

```
.
├── lab-00-deploy.ipynb             # Infrastructure deployment (instructor/self-paced)
├── lab-01-bug-fix-agent.ipynb      # Workshop experience (everyone)
├── requirements.txt                # Python dependencies
├── images/                         # Architecture diagrams
│
├── sample-project/                 # Buggy task-manager app (pushed to GitHub)
│   ├── README.md
│   ├── backend/                    # Python/Flask API
│   └── frontend/                   # Vanilla JS + HTML
│
├── gateway_mcp/                    # GitHub MCP Gateway
│   ├── app/                        # MCP server (FastMCP + GitHub API)
│   │   ├── main.py
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── config.sh                   # Shared config (names, region)
│   ├── deploy-all.sh               # Full gateway deploy
│   ├── delete-all.sh               # Full gateway teardown
│   └── seed-issues.sh              # Create 9 bug issues in the repo
│
├── infra/                          # Shared VPC + S3 Files (CloudFormation)
│   ├── cfn-vpc.yaml
│   ├── setup.sh
│   └── cleanup.sh
│
├── coding_agents/
│   ├── requirements.txt
│   └── claude-code/                # Claude Code agent
│       ├── Dockerfile              # Container image
│       ├── CLAUDE.md               # Agent system prompt (baked into image)
│       ├── run.sh                  # Container launch script
│       ├── settings.json           # Claude Code settings
│       ├── setup.sh                # Build + push to ECR
│       ├── deploy.py               # Create AgentCore Runtime
│       ├── connect.py              # Send prompts / open interactive PTY
│       └── cleanup.py              # Teardown runtime + IAM role
│
└── git_mcp_skill/                  # MCP Gateway Proxy (uploaded to S3 Files)
    ├── index.js                    # stdio → StreamableHTTP bridge with SigV4 signing
    ├── package.json
    └── github-mcp.md               # Skill reference doc (mounted at /mnt/s3files/skills/)
```

---

## The Sample Project

The `sample-project/` folder contains a simple task-manager app (Python/Flask backend + vanilla JS frontend). The repository is seeded with **10 issues** of mixed types for the triage lab, including **5 bugs** for the bug-fix lab:

| # | Type | Area | Description |
|---|---|---|---|
| 1 | bug | Backend | Task ID never increments — all tasks get `id=1` |
| 2 | bug | Backend | Delete filter inverted — removes everything except the target |
| 3 | bug | Backend | Stats counter always sets to 1 instead of incrementing |
| 4 | bug | Backend | `updated_at` timestamp never refreshed on update |
| 5 | bug | Backend | Status filter is case-sensitive |
| 6 | enhancement | Backend | Add due-date field to tasks |
| 7 | enhancement | Backend/Frontend | Support task priorities (low / medium / high) |
| 8 | enhancement | Backend | Add pagination to GET /tasks |
| 9 | question | — | How do I export tasks to CSV? |
| 10 | question | — | What is the maximum number of tasks the API supports? |

---

## Security

See [CONTRIBUTING](../../CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.

> **Note:** This is sample code for non-production usage. You should work with your security and legal teams to meet your organizational security, regulatory, and compliance requirements before deployment.

---

## Cleanup

Run the teardown cells in [lab-00-deploy.ipynb](lab-00-deploy.ipynb) (Step 9), or manually:

```bash
# Remove Claude Code runtime + IAM role
cd coding_agents/claude-code && python cleanup.py

# Remove shared VPC + S3 Files
cd infra && ./cleanup.sh

# Remove Gateway MCP
cd gateway_mcp && ./delete-all.sh
```
