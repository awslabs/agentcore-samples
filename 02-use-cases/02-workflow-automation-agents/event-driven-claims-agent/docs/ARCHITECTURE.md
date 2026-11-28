# Architecture: Event-Driven Claims Agent

> Custom CLI 1.2.0 v1 port, fresh stacks only; cloud is NOT RUN.
> See [MIGRATION.md](MIGRATION.md). Resource names in historical diagrams/tables
> are shorthand; actual names are project/target-qualified as in CONFIGURATION.md.

## System Overview

The Event-Driven Claims Agent is an insurance claims processor built on Amazon Bedrock AgentCore. It accepts claim submissions via email (through S3/EventBridge) or direct API invocation, processes them through a dual-agent pipeline, and routes outcomes to auto-approval or human review.

This document explains:
- **Components** — what each piece does and how they connect
- **Data flows** — how a claim moves from submission to resolution
- **Authentication** — how services authenticate to each other (Cognito M2M, JWT)
- **Tool architecture** — how the agent calls Lambda tools via the MCP Gateway
- **Memory and evaluation** — how the system remembers and monitors quality

## Architecture Diagrams

### High-Level Architecture

![High-Level Architecture](../architecture.png)

### Event-Driven Ingress Flow

![Event-Driven Flow](diagrams/event-driven-flow.png)

### Dual-Agent Processing Pipeline

![Dual-Agent Flow](diagrams/dual-agent-flow.png)

---

## Component Descriptions

### AgentCore Runtime

- **Build type:** Container (ARM64/Graviton, Python 3.12)
- **Framework:** Strands Agents SDK
- **Auth (inbound):** AWS_IAM (SigV4) — callers sign requests with AWS credentials (Lambda execution role or IAM user)
- **Auth (outbound to Gateway):** Cognito M2M JWT via `@requires_access_token` decorator — secrets managed by AgentCore Identity vault (no env vars)
- **Entrypoint:** `app/claimsagent/main.py`
- **Agents:** Two per-claim `Agent` instances (Claims Processor, Validation Agent). Cross-session state belongs in Memory, not cached agent histories; Gateway tokens are not held across claims.
- **Model (cost routing):** Claims Processor uses `global.anthropic.claude-sonnet-4-6`; Validation Agent uses `FAST_MODEL_ID` (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) with only the local structured-output tool. Phase 3 makes no model call.
- **Co-located tools:** `submit_decision`, `submit_validation` (Strands `@tool` decorators for structured output)

### AgentCore Gateway

- **Protocol:** MCP (Model Context Protocol) over streamable HTTP
- **Auth:** Cognito CUSTOM_JWT (validates JWT tokens via OIDC discovery URL from Cognito User Pool)
- **Search type:** SEMANTIC (tool discovery by description)
- **Targets:** 6 Lambda functions (see tool table below)
- **Policy enforcement:** Cedar Policy Engine runs before every tool call (ENFORCE mode)

### Cedar Policy Engine

Two policies are enforced at the Gateway level:

| Policy | Effect | Condition |
|--------|--------|-----------|
| `AllowAllTools` | permit | Any authenticated principal, any tool, on this gateway |
| `BlockExcessiveClaims` | forbid | action `create-claim___create_claim` when `context.input.estimated_amount >= 100000` |

Policy Engine operates in `ENFORCE` mode — blocked tool calls return an authorization error to the agent.

### Lambda Tool Functions

| Tool | Lambda | DynamoDB | Notes |
|------|--------|----------|-------|
| `lookup_policy` | `ClaimsAgent-PolicyLookup` | PoliciesTable (read) | Returns policy details or "not found" |
| `create_claim` | `ClaimsAgent-CreateClaim` | ClaimsTable (write) | Agent passes `status` + `decision` |
| `request_human_review` | `ClaimsAgent-HumanReview` | ReviewsTable (write) | Also publishes to SNS |
| `send_notification` | `ClaimsAgent-Notification` | — | SES send email |
| `list_pending_claims` | `ClaimsAgent-ListPending` | ClaimsTable (`status-index` query) | Filters `status=pending_review` |
| `resolve_claim` | `ClaimsAgent-ResolveClaim` | ClaimsTable + ReviewsTable (update) | Human operator resolves |

All handlers return `json.dumps({...})` directly — no HTTP envelope.

### Trigger Lambda

Handles the event-driven path from S3 → Runtime (fire-and-forget):
1. Receives EventBridge event with S3 object details
2. Reads the file from S3 (email format or raw JSON)
3. Parses email headers to extract `claimant_email` and `subject`
4. Signs the request with SigV4 using the Lambda's execution role credentials (IAM auth)
5. Invokes the Runtime via HTTPS POST to `/runtimes/{arn}/invocations`
6. Confirms HTTP 200 and reads first few lines to verify the agent started streaming
7. Closes the connection immediately — does NOT wait for the full response

The agent processes the claim asynchronously after the Lambda returns. Results are written to DynamoDB by the agent's tool calls (`create_claim`, `request_human_review`, `send_notification`), not returned to this Lambda.

**Auth:** The Trigger Lambda uses AWS_IAM (SigV4) authentication. CDK grants `bedrock-agentcore:InvokeAgentRuntime` permission via `runtime.grantInvoke(triggerFn)`. No Cognito credentials needed for this path.

---

## Data Flows

### Event-Driven Path (Email → S3 → EventBridge → Agent)

```mermaid
sequenceDiagram
    participant Claimant
    participant SES as Amazon SES
    participant S3 as S3 Bucket
    participant EB as EventBridge
    participant Trigger as Trigger Lambda
    participant Runtime as AgentCore Runtime

    Claimant ->> SES: Send claim email
    SES ->> S3: Store in claims-inbox/
    S3 ->> EB: Object Created event
    EB ->> Trigger: Invoke (bucket + key)
    Trigger ->> S3: Read email object
    S3 -->> Trigger: Raw email content
    Trigger ->> Trigger: Parse headers + body
    Trigger ->> Runtime: POST /runtimes/{arn}/invocations (SigV4-signed)
    Note right of Trigger: Uses Lambda execution role credentials
    Runtime -->> Trigger: SSE streaming response
```

### Direct Invocation Path (API → Agent)

```mermaid
sequenceDiagram
    participant Caller as Caller (script / app)
    participant Runtime as AgentCore Runtime

    Caller ->> Runtime: POST /runtimes/{arn}/invocations (SigV4-signed)
    Note right of Caller: Uses IAM credentials (aws-sigv4)
    Note right of Caller: Body: {"prompt": "File a claim..."}
    Runtime -->> Caller: SSE streaming response
```

---

## Dual-Agent Processing Pipeline

```mermaid
flowchart TB
    direction TB

    Payload["Incoming claim payload"]

    subgraph Phase1["Phase 1: Claims Processor"]
        direction TB
        P1_Input["Extract claim details"]
        P1_Tool["Call lookup_policy tool"]
        P1_Output["Output: DECISION, AMOUNT, POLICY, CATEGORY, REASONING"]
        P1_Input --> P1_Tool --> P1_Output
    end

    subgraph Phase2["Phase 2: Validation Agent"]
        direction TB
        P2_Input["Review processor decision independently"]
        P2_Output["Output: CONFIDENCE score, ROUTING, CONCERNS"]
        P2_Input --> P2_Output
    end

    subgraph Phase3["Phase 3: Deterministic Execution (no LLM)"]
        direction LR
        Reject["REJECT: send_notification"]
        AutoApprove["AUTO_APPROVE: create_claim + notify"]
        HumanReview["HUMAN_REVIEW: create_claim + escalate + notify"]
    end

    Payload --> Phase1
    Phase1 --> Phase2
    Phase2 -->|"REJECT"| Reject
    Phase2 -->|"confidence >= 80"| AutoApprove
    Phase2 -->|"confidence < 80"| HumanReview
```

### Routing Logic

The routing logic lives in `app/claimsagent/routing.py` (extracted from `main.py` for testability). The confidence threshold is configurable via the `AUTO_APPROVE_THRESHOLD` environment variable (default: 80).

Both agents MUST call their respective structured-output tools (`submit_decision`, `submit_validation`). If they fail to do so, routing defaults to safe outcomes:
- Missing `submit_decision` → defaults to REJECT (no unintended approvals)
- Missing `submit_validation` → defaults to HUMAN_REVIEW (ensures human oversight)

### Phase 3: Deterministic Execution

Phase 3 does **not** use an LLM call. Once routing is resolved (REJECT / AUTO_APPROVE / HUMAN_REVIEW), the execution is deterministic — we have all required data from the structured output tools and call Gateway tools directly via `MCPClient.call_tool_async()`. This eliminates one Sonnet invocation (~6–16s and ~$0.01 per request).

All Phase 3 tool calls are non-fatal: a failed create is reported but cannot
trigger escalation or an approval/review notification with a nonexistent claim
ID. Strands dictionary error/status fields are checked. Each direct call holds
an MCP session and uses its qualified target/tool name. The Phase 1 model is
only offered policy lookup; mutation tools are reserved for Phase 3.

---

## Memory Strategy

- **SEMANTIC** — stores and retrieves facts about claims and policies across sessions
- **SUMMARIZATION** — compresses session history for repeat claimants
- Expiration: 90 days
- Graceful degradation: if Memory is not deployed or throws `ResourceNotFoundException`, the agent continues without memory (no crash)

---

## Observability

- **X-Ray tracing:** enabled on the Runtime via `instrumentation.enableOtel: true`
- **CloudWatch logs:** CFN-owned Runtime/Lambda/build and Gateway/Memory groups (1-week retention)
- **Gateway/Memory deliveries:** CFN-owned TRACES + APPLICATION_LOGS; account Transaction Search must already be separately configured. Optional identity-only deliveries use tagged exact-ID receipts via `enable_observability.py --allow-cloud`.
- **Online Evaluation:** 3 built-in metrics (Helpfulness, Correctness, Tool Selection Accuracy) at 100% sampling, bound to the Runtime endpoint
- **Automatic OTEL configuration:** Runtime auto-configures exporters, propagators, and instrumentation when `enableOtel: true` is set

---

## Cognito Authentication Architecture

```mermaid
flowchart LR
    direction LR

    subgraph CognitoPool["Cognito User Pool (script-managed)"]
        direction TB
        RS["Resource Server: agentcore (scope: invoke)"]
        Client["App Client: M2M (client_credentials)"]
        Domain["Domain: claims-{suffix}-{account}.auth.{region}.amazoncognito.com"]
    end

    subgraph IdentityVault["AgentCore Identity<br/>(Secrets Manager)"]
        Cred["OAuth provider: Claims{suffix}_dev_cognito-gateway-m2m"]
    end

    RuntimeMCP["Runtime<br/>(MCP Gateway calls)"] -->|"@requires_access_token(provider_name)"| IdentityVault
    IdentityVault -->|"fetches token via client_credentials"| CognitoPool
    RuntimeMCP -->|"Authorization: Bearer {token}"| Gateway["MCP Gateway (CUSTOM_JWT authorizer)"]
    CognitoPool -->|"OIDC discovery validates JWT"| Gateway
```

### Auth Summary

| Path | Method | Mechanism |
|------|--------|-----------|
| Trigger Lambda → Runtime | AWS_IAM (SigV4) | Lambda execution role signs request with `bedrock-agentcore` service |
| Test scripts → Runtime | AWS_IAM (SigV4) | User/role credentials sign request with `bedrock-agentcore` service |
| Runtime → MCP Gateway | Cognito M2M JWT | `@requires_access_token` decorator → Identity vault → `client_credentials` flow → `Authorization: Bearer {token}` |
| Gateway → Lambda tools | IAM invoke | Gateway role has `lambda:InvokeFunction` on each tool function |

The dedicated Cognito User Pool and Identity provider are managed by the
ownership-checking `scripts/lifecycle.py` (the shell helpers delegate to it).
They are tagged, journaled and never reused by name. The client secret is passed
to Identity in memory, not exposed to CDK. See [migration boundaries](MIGRATION.md).
All Runtime callers use IAM/SigV4, never the Gateway JWT. Test mode retains
SNS/SES tools but suppresses sends and omits send permissions.

### Validating Authentication

`scripts/test_auth.py` checks Runtime JWT/no-auth rejection, direct Gateway M2M
and invalid-token behavior, and Cognito invalid-scope issuance. The positive
Runtime IAM/model path is in the separate bounded E2E check. All require authorization:

```bash
python3 scripts/test_auth.py --allow-cloud
```

| # | Test | Expected |
|---|------|----------|
| 1 | SigV4 → Runtime | ✅ Accepted (correct inbound path) |
| 2 | JWT → Runtime | ❌ Rejected (Runtime is AWS_IAM inbound, not CUSTOM_JWT) |
| 3 | Runtime → Gateway (M2M) | ✅ Tool call succeeds (Identity-managed token) |
| 4 | No auth → Runtime | ❌ Rejected |
| 5 | Invalid JWT → Runtime | ❌ Rejected |
| 6 | Wrong scope → Cognito | ❌ Rejected at token endpoint |
