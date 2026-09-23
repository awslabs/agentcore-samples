# Autonomous Coding Agent — Durable Orchestration on AgentCore Runtime

An event-driven, headless coding backend that receives a ticket, clones a repo, writes code, runs tests in an isolated sandbox, gets a review from an evaluator agent, and retries until tests pass — all orchestrated by a **Lambda Durable Function** with zero-cost suspension.

Uses **4 AgentCore Runtimes**, Cedar-based authorization policies, and cross-ticket memory.

## Architecture

```
EventBridge ──► Durable Orchestrator (Lambda Durable Function)
   {ticketId}     ┌─────────────────────────────────────────────────────────┐
                  │ 1. ADMISSION   validate ticket schema                   │
                  │ 2. HYDRATE     git clone repo + recall memory lessons    │
                  │ 3. CODE_LOOP   wait_for_callback (SUSPEND, $0 compute)  │
                  │ 4. REVIEW      evaluator agent (read-only, Haiku)        │
                  │ 5. FINALIZE    write memory lessons + SNS notify         │
                  └───────┬─────────────────────────────────────────────────┘
                          │
            ┌─────────────┼──────────────────────────────────┐
            ▼             ▼                                  ▼
   Coding Agent     Swift Sandbox            Evaluator Agent
   (Opus 4)         (non-LLM executor)       (Haiku, read-only)
   writes code      runs `swift test`        structured verdict
   ──► Sandbox      .build persisted to      request_changes → retry
       via MCP      /mnt/workspace
```

| Runtime | Role | Model | Network |
|---------|------|-------|---------|
| Coding Agent | Plan + write code, drive sandbox via MCP | Claude Opus 4 | VPC (private) |
| Sandbox | Execute commands, path-confined to ticket dir | None (plain executor) | VPC (private) |
| Swift Sandbox | Swift-specific sandbox with `.build` persistence | None | VPC (private) |
| Evaluator Agent | Read-only code review, structured verdict | Claude Haiku | VPC (private) |

> **Note:** The included sandbox images cover **Python** (pytest) and **Swift** (SwiftPM). The architecture is framework-agnostic — to support additional languages or frameworks (e.g. Java/Gradle, TypeScript/Jest, Go), add a new Dockerfile with the required toolchain and register it as an additional sandbox runtime in the CDK stack.

## Key features

- **Zero-cost suspension** — Durable Function suspends at `wait_for_callback`; no compute charges while the coding agent works asynchronously
- **Retry loop** — if `swift test` (or `pytest`) fails, orchestrator retries with feedback (up to MAX_ATTEMPTS)
- **Cedar policies** — Gateway authorization via Cedar; sandbox enforces path confinement
- **Cross-ticket memory** — AgentCore Memory stores per-repo lessons; recalled at hydrate, written at finalize
- **Control/data separation** — coding agent cannot execute locally (Bash, WebFetch disallowed); all execution delegated to sandbox
- **Session isolation** — different tickets = different microVMs (state doesn't leak)

## Repository layout

```
cdk/                   CDK app (8 stacks, production deployment)
coding-agent/          Control plane — Claude Agent SDK + sandbox MCP tools
demo/                  Demo UI (index.html) for submitting tickets and viewing results
sandbox/               Data plane — command executor + Cedar policy engine
evaluator-agent/       Read-only review agent
orchestrator/          Lambda Durable Function handler
gateway-policies/      Cedar policies for AgentCore Gateway
shared/                Shared libraries (memory, audit, validation, logging)
scripts/               Helper scripts (fire_ticket, build_images)
tests/                 Unit + integration tests (pytest)
```

## Prerequisites

- AWS account with Bedrock AgentCore access (us-east-1)
- Python 3.12+, AWS CLI v2, AWS CDK CLI (`npm install -g aws-cdk`)
- Bedrock model access: Claude Opus 4, Claude Haiku
- Docker with buildx (for local builds) or CodeBuild (recommended)

## Deployment

```bash
# 1. Configure AWS credentials
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=us-east-1

# 2. Install CDK dependencies
cd cdk
pip install -r requirements.txt

# 3. Bootstrap CDK (once per account/region)
cdk bootstrap aws://$AWS_ACCOUNT_ID/$AWS_REGION -c account=$AWS_ACCOUNT_ID

# 4. Deploy all stacks
cdk deploy --all --require-approval broadening -c account=$AWS_ACCOUNT_ID

# 5. Build and push ARM64 container images (via CodeBuild)
cd ..
bash scripts/build_images.sh all
```

CDK stacks (deployed in dependency order):

| Stack | Resources |
|-------|-----------|
| `cagent-network` | VPC, 2 AZ, NAT, SG, VPC endpoints |
| `cagent-storage` | S3 bucket (versioned), shared filesystem, mount targets |
| `cagent-build` | CodeBuild projects for native ARM64 builds |
| `cagent-runtime` | IAM exec roles + 4 AgentCore runtimes |
| `cagent-gateway-policy` | Cedar authorization policies |
| `cagent-memory` | AgentCore Memory store (per-repo lessons) |
| `cagent-orchestrator` | Lambda Durable Function + EventBridge + SNS |
| `cagent-monitoring` | CloudWatch alarms + dashboard |

## Running a ticket

```bash
# Fire a ticket via EventBridge
bash scripts/fire_ticket.sh TICKET-1

# Or invoke directly
printf '{"ticketId":"MY-TICKET"}' > /tmp/payload.json
aws lambda invoke --function-name cagent-orchestrator \
  --payload fileb:///tmp/payload.json /tmp/result.json
cat /tmp/result.json | python3 -m json.tool
```

### Ticket format

Upload to `s3://<bucket>/tickets-source/<ticketId>.json`:

```json
{
  "id": "TICKET-101",
  "title": "Add sorting to user list API",
  "description": "Implement sortable user list endpoint. Clone repo, add sort parameter, write tests. Done when swift test passes.",
  "repo_url": "https://github.com/example/my-swift-api.git"
}
```

### What to expect

1. Orchestrator validates ticket, clones repo, recalls memory lessons
2. Coding agent plans and writes code (~30-120s)
3. Sandbox runs tests — on failure, orchestrator retries with feedback
4. Evaluator reviews final code, may request changes (→ another loop)
5. Memory lessons saved, SNS notification sent (PASS/FAIL + summary)

## Security model

| Layer | Mechanism |
|-------|-----------|
| Cedar gateway policy | Authorizes which agents can invoke which runtimes |
| Path confinement | Sandbox validates all paths via `realpath` + prefix check |
| Env denylist | Blocks LD_PRELOAD, PATH, AWS_* from override |
| S3 access point boundary | `rootDirectory=/work` prevents bucket escape |
| Session isolation | Different sessions = different microVMs |
| Control/data separation | Coding agent cannot execute locally |
| Evaluator read-only | Review agent has no write tools |

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Cleaning up

```bash
cd cdk
cdk destroy --all
```

> **Note:** The S3 bucket is retained by default (contains artifacts). Delete manually if no longer needed.
