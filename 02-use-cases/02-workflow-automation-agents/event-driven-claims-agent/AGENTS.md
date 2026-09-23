# Event-Driven Claims Agent — AI Coding Assistant Context

> **For humans:** This file provides context for AI coding assistants (Kiro, Cursor, Claude Code, GitHub Copilot). For the human-readable documentation, see [docs/](./docs/README.md), [README.md](./README.md), or [docs/tutorial.md](./docs/tutorial.md).

This project is an **event-driven insurance claims processor** built on Amazon Bedrock AgentCore. It uses a dual-agent architecture (Claims Processor + Validation Agent) with cost-based model routing (Sonnet for reasoning, Haiku for validation) and a deterministic execution phase. Deploys as a single CloudFormation stack (`AgentCore-ClaimsAgent-dev`) via the AgentCore CLI.

> **Important:** AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval) are declared in `agentcore/agentcore.json` and managed by the AgentCore CLI. Supplementary infrastructure (DynamoDB, Lambda tools, SNS, S3, EventBridge) is defined in the TypeScript CDK app at `agentcore/cdk/lib/infra-construct.ts`. The Cognito User Pool (Gateway M2M auth) is managed by `scripts/setup_cognito.sh` (AWS CLI, not CDK). Use `agentcore validate` and `agentcore dev` while iterating; run `agentcore deploy --target dev` to deploy everything together.

---

## Architecture

```
S3 upload (claims-inbox/)
  → EventBridge rule
    → Trigger Lambda (lambdas/trigger/handler.py)
        reads S3 object, invokes Runtime via SigV4-signed HTTPS (fire-and-forget)
      → AgentCore Runtime (Container: app/claimsagent/)
          Phase 1: Claims Processor (Sonnet) → lookup_policy → ACCEPT/REJECT decision
          Phase 2: Validation Agent (Haiku) → reviews decision → CONFIDENCE + ROUTING
          Phase 3: Deterministic Execution (no LLM) → create_claim / human_review / send_notification
        → AgentCore Gateway (MCP, Cognito CUSTOM_JWT auth, Cedar policy enforcement)
            → 6 Lambda tool functions (lambdas/<tool>/handler.py)
```

**Auth (two separate paths):**
- **Inbound to Runtime (Trigger Lambda → Runtime):** AWS_IAM (SigV4). The Trigger Lambda's execution role has `bedrock-agentcore:InvokeAgentRuntime` permission granted by CDK via `runtime.grantInvoke(triggerFn)`. No Cognito credentials needed.
- **Outbound from Runtime to Gateway (Runtime → MCP Gateway):** Cognito M2M JWT via `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` decorator. Secrets managed by AgentCore Identity vault (registered via `agentcore add credential`). The Gateway validates JWT via CUSTOM_JWT authorizer (Cognito OIDC discovery).

---

## Directory Structure

```
event-driven-claims-agent/
├── AGENTS.md                          # This file
├── CLAUDE.md                          # Claude Code guidance
├── README.md                          # Full project documentation
├── deploy.sh                          # One-command deploy (runs CDK)
├── app/claimsagent/
│   ├── Dockerfile                     # Multi-stage, Python 3.12, ARM64
│   ├── main.py                        # All agent logic: prompts, agents, Identity-managed Gateway OAuth
│   ├── config.py                      # Centralized env var reads (Gateway, Memory, Model)
│   ├── routing.py                     # Phase-3 routing: decide_action, resolve_decision/routing
│   ├── memory/session.py             # AgentCoreMemorySessionManager (graceful degradation)
│   ├── tools/structured_output.py     # @tool decorators: submit_decision, submit_validation
│   └── pyproject.toml                 # Dependencies (uv-managed)
├── lambdas/                           # One directory per Gateway tool
│   ├── schemas/                       # MCP tool schemas (JSON) — matched by CDK
│   ├── trigger/handler.py             # EventBridge → Runtime invocation (SigV4 auth)
│   ├── create_claim/handler.py        # DDB put on ClaimsTable
│   ├── policy_lookup/handler.py       # DDB get on PoliciesTable
│   ├── list_pending_claims/handler.py # DDB scan for pending_review claims
│   ├── resolve_claim/handler.py       # DDB update on ClaimsTable + ReviewsTable
│   ├── human_review/handler.py        # DDB put on ReviewsTable + SNS publish
│   └── notification/handler.py        # SES send email
├── agentcore/
│   ├── agentcore.json                 # Declarative AgentCore resources (Runtime/Gateway/Memory/PolicyEngine/Eval)
│   ├── aws-targets.json               # Deployment targets (account + region)
│   └── cdk/lib/
│       ├── infra-construct.ts         # Supplementary AWS infra (DynamoDB, S3, SNS, EventBridge, Lambdas — Cognito is script-managed)
│       └── cdk-stack.ts               # Integration: wires infra ARNs + JWT authorizer + runtime env vars
├── scripts/
│   ├── deploy.sh                      # Deploy helper
│   ├── destroy.sh                     # Unified teardown (observability → stack → orphans → Cognito → state)
│   ├── cleanup_agentcore.py           # Delete orphaned AgentCore control-plane resources (boto3)
│   ├── setup_cognito.sh               # Create Cognito User Pool via AWS CLI (not CDK)
│   ├── teardown_cognito.sh            # Delete Cognito if script-created
│   ├── enable_observability.py        # Enable Transaction Search + Gateway/Memory deliveries
│   ├── disable_observability.py       # Clean up observability deliveries
│   ├── seed_dynamodb.py              # Populate test policies
│   ├── test_invoke.py                # Direct Runtime invocation (SigV4 auth)
│   ├── test_auth.py                  # Authentication pattern tests (6 scenarios)
│   ├── test_e2e.py                   # Full E2E test suite (5 scenarios)
│   ├── test_cedar.py                 # Cedar policy enforcement tests
│   ├── test_local.py                 # Local dev invocation helper
│   └── lint.sh                       # py_compile + ruff checks
├── tests/                             # Offline unit tests (pytest)
│   ├── test_routing.py                # Phase-3 routing logic
│   ├── test_structured_output.py      # submit_decision / submit_validation tools
│   ├── test_lambda_handlers.py        # Lambda tool handlers
│   ├── test_trigger.py                # Trigger Lambda
│   └── sample-claim-email.txt        # Email for E2E test 5 (uses POL-67890)
├── docs/
│   ├── ARCHITECTURE.md               # System design and data flows
│   ├── deployment.md                 # Step-by-step deploy, verify, teardown
│   ├── decisions/                    # Architectural decision records (ADR-0001..0010)
│   └── CONFIGURATION.md             # All config surfaces reference
```

---

## Build, Test, Deploy

### Deploy everything
```bash
./deploy.sh [region]          # defaults to us-west-2
```

This runs: configure target → npm install (CDK) → uv sync (agent) → `agentcore validate` → cdk bootstrap → `agentcore deploy --target dev` → seed DynamoDB → prints test commands.

### Manual AgentCore / CDK operations
```bash
agentcore validate                       # validate agentcore.json
agentcore deploy --target dev --yes      # deploy everything

# NOTE: agentcore CLI does NOT have a destroy command. Use the destroy script:
./scripts/destroy.sh us-west-2           # full teardown (handles DELETE_FAILED + orphans)

# Or drive the underlying TypeScript CDK directly:
cd agentcore/cdk && npm install && npx cdk diff
```

### Invoke the agent (requires deployed stack)
```bash
python3 scripts/test_invoke.py --region us-west-2
python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. $5000 storm damage.'
```

### Run E2E tests
```bash
python3 scripts/test_e2e.py --region us-west-2
python3 scripts/test_e2e.py --region us-west-2 --test 2   # Cedar block test
```

### Run authentication pattern tests
```bash
python3 scripts/test_auth.py --region us-west-2          # All 6 auth tests
python3 scripts/test_auth.py --region us-west-2 --test 3  # Single test (Runtime→Gateway M2M)
```
Validates: (1) SigV4→Runtime succeeds, (2) JWT→IAM Runtime rejected, (3) Runtime→Gateway M2M
works via tool call, (4) no-auth rejected, (5) invalid JWT rejected, (6) wrong scope rejected.

### Run unit tests (offline)
```bash
python3 -m pytest tests/          # routing, structured output, lambda handlers, trigger
```

### Lint
```bash
./scripts/lint.sh
# or manually:
find app/ lambdas/ scripts/ -name "*.py" -exec python3 -m py_compile {} \;
```

---

## Key Invariants

1. **Lambda handlers return `json.dumps({...})` directly** — no `{statusCode, body}` envelope. The Gateway strips the HTTP wrapper.
2. **Agent routing controls claim status** — the `create_claim` Lambda accepts `status` and `decision` as optional parameters from the agent. Do not add routing logic to the Lambda itself.
3. **Tool schemas live in `lambdas/schemas/`** — each file maps to a Gateway target in the CDK stack via the `toolSchemaFile` field in `agentcore.json`. Keep schemas in sync with Lambda parameters.
4. **Container build, not CodeZip** — runtime deps go in `app/claimsagent/pyproject.toml` (managed by `uv`). The Dockerfile runs `uv sync --frozen`.
5. **`agentcore/agentcore.json` is the source of truth for AgentCore resources** (Runtime, Gateway, Memory, PolicyEngine, OnlineEval). Supplementary AWS infra is in `agentcore/cdk/lib/infra-construct.ts`; `cdk-stack.ts` wires the two together (patches Lambda ARNs + the Gateway CUSTOM_JWT authorizer discovery URL, injects runtime env vars). Don't hand-edit generated CDK output.
6. **Two auth paths:** Inbound to Runtime uses AWS_IAM (SigV4) — CDK grants `runtime.grantInvoke()` to the Trigger Lambda. Outbound from Runtime to Gateway uses Cognito M2M JWT via `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` — secrets live in AgentCore Identity vault, not env vars.
7. **Structured output tools** (`tools/structured_output.py`) — the agent calls `submit_decision` and `submit_validation` to produce machine-parseable results. When agents fail to call these tools, routing defaults to safe fallbacks (REJECT for missing decision, HUMAN_REVIEW for missing validation).
8. **Phase 3 is deterministic (no LLM call)** — once routing is resolved, tool calls are made directly via `MCPClient.call_tool_async()` using structured data from Phase 1/2. This saves one Sonnet invocation per request. All Phase 3 actions are non-fatal (log + continue on failure).

---

## Environment Variables

### Runtime container (set by CDK)
| Variable | Purpose |
|---|---|
| `AGENTCORE_GATEWAY_URL` | MCP Gateway HTTPS endpoint |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | Identity credential provider name (no secrets) |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | `agentcore/invoke` |
| `AGENT_OBSERVABILITY_ENABLED` | `true` — enables OTEL instrumentation |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` — captures LLM messages in traces |
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` |
| `FAST_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` — used by Validation Agent (Phase 2) |
| `AUTO_APPROVE_THRESHOLD` | `80` — confidence threshold for auto-approval |

### Lambda functions (set by CDK)
| Variable | Lambda(s) | Value |
|---|---|---|
| `CLAIMS_TABLE` | create_claim, list_pending, resolve_claim | `ClaimsAgent-dev-Claims` |
| `POLICIES_TABLE` | policy_lookup | `ClaimsAgent-dev-Policies` |
| `REVIEWS_TABLE` | human_review, resolve_claim | `ClaimsAgent-dev-Reviews` |
| `REVIEW_SNS_TOPIC_ARN` | human_review | SNS topic ARN |
| `SENDER_EMAIL` | notification | SES verified sender |

### Trigger Lambda (set by CDK)
| Variable | Purpose |
|---|---|
| `AGENTCORE_RUNTIME_ARN` | Runtime ARN for SigV4-signed HTTPS invocation |

---

## Test Policies (seeded by `seed_dynamodb.py`)

| Policy Number | Holder | Type | Coverage | Status |
|---|---|---|---|---|
| `POL-12345` | John Smith | auto | $50,000 | active |
| `POL-67890` | Jane Doe | home | $250,000 | active |
| `POL-11111` | Bob Johnson | auto | $75,000 | active |
| `POL-99999` | Alice Williams | auto | $100,000 | expired |

---

## Cedar Policies

Two policies (in `agentcore/agentcore.json` under `policyEngines`) enforce authorization at the Gateway:
- **AllowAllTools** — `permit(principal, action, resource is AgentCore::Gateway)`
- **BlockExcessiveClaims** — `forbid` when `context.toolName == "create-claim"` and `context.input.estimated_amount >= 100000`

Both use `IGNORE_ALL_FINDINGS` validation mode (required for the permit-all policy).
