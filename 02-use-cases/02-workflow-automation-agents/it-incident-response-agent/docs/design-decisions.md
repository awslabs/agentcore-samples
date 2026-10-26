# Design Decisions

This document explains the key architectural decisions made in this project and
the reasoning behind them. It helps newcomers understand *why* things are built
this way — not just *what* they do.

## ADR-1: Container Build over CodeZip

**Decision**: Use `Container` build type (Docker + CodeBuild) instead of `CodeZip`.

**Why**:
- The agent has native dependencies (e.g., `httpx`, `mcp` SDK) that require a
  consistent build environment
- Container allows pinning exact Python version and system libraries
- Hot-reload via volume mounts during `agentcore dev` accelerates local iteration
- Production parity — what you test locally is exactly what deploys
- Enables future additions (e.g., custom OTEL collectors, sidecar processes)

**Trade-off**: Slower first build (~3–5 min in CodeBuild) vs instant CodeZip packaging.
Mitigated by Docker layer caching on subsequent builds (~30s).

---

## ADR-2: Lambda Tools behind Gateway (not inline Python tools)

**Decision**: Each tool is a separate Lambda function registered as a Gateway
target, rather than defining tools inline in the agent's Python code.

**Why**:
- **Isolation**: Each tool has its own IAM role scoped to only the resources it
  needs (DynamoDB table, KB, etc.)
- **Independent scaling**: A slow `query_kb` call doesn't block `lookup_user`
- **Testability**: Each Lambda can be tested independently with simple event payloads
- **Observability**: Per-tool CloudWatch metrics, X-Ray segments, and error rates
- **Policy Engine**: Cedar policies can allow/deny individual tools at the Gateway
  level — impossible with inline tools
- **Reuse**: Other agents or services can call the same Gateway tools

**Trade-off**: Higher latency per tool call (~100–200ms cold start overhead) vs
inline tools (~0ms). Acceptable for async ticket resolution (not real-time chat).

---

## ADR-3: SNS + Trigger Lambda (not direct Runtime invocation)

**Decision**: External systems publish to an SNS topic. A Trigger Lambda validates,
persists to DynamoDB, then asynchronously invokes the Runtime.

**Why**:
- **Decoupling**: The agent doesn't need to know about ticket sources (Jira, PagerDuty,
  ServiceNow, or manual). Any system that can publish JSON to SNS works.
- **Validation + DLQ**: The Trigger Lambda validates required fields and sends
  malformed events to a dead-letter queue — the agent never sees garbage input
- **Persistence before processing**: The ticket is written to DynamoDB *before*
  the agent starts, so state is never lost even if the Runtime crashes
- **Fan-out**: SNS supports multiple subscribers — add a second agent, notification
  Lambda, or audit logger without changing the ticket source
- **Async invocation**: Fire-and-forget at the trigger level means the source
  system gets a fast 200 OK without waiting for diagnosis

**Trade-off**: Extra hop adds ~200ms latency and one more component to manage.
Justified by the resilience and decoupling benefits for production workloads.

---

## ADR-4: SUMMARIZATION Memory Strategy

**Decision**: Use the `SUMMARIZATION` memory strategy (not `SEMANTIC` or `EPISODIC`
alone).

**Why**:
- IT incidents have natural session boundaries (one ticket = one episode)
- SUMMARIZATION produces concise episode summaries that enrich future tickets:
  "User U-1003 had a VPN issue last week resolved by restarting the client"
- Raw SEMANTIC search would return full conversation turns — too verbose for
  prompt injection into new incidents
- SUMMARIZATION compresses multi-turn agent reasoning into a single retrievable
  fact, keeping prompt size manageable

**Trade-off**: Loses fine-grained detail (exact tool call sequence from prior
incidents). Acceptable because the agent re-diagnoses each ticket fresh; it just
needs *context* from prior incidents, not a replay.

---

## ADR-5: DynamoDB for Ticket + Asset Store (not RDS/Aurora)

**Decision**: Use DynamoDB tables for users, processes, tickets, and change requests.

**Why**:
- **Serverless**: No connection pool management, no VPC required for basic setup
- **Pay-per-request**: Zero cost when no tickets are being processed
- **Partition key access**: All tool lookups are single-item gets by ID — DynamoDB's
  sweet spot
- **Idempotency**: `attribute_not_exists` conditional writes prevent duplicate
  processing naturally
- **CDK simplicity**: `dynamodb.Table` is a single construct vs Aurora's cluster +
  subnet groups + security groups

**Trade-off**: No complex queries (joins, aggregations). Acceptable because the
agent doesn't query across tickets — it resolves one ticket at a time using known
IDs.

---

## ADR-6: Dual Auth Mode (AWS_IAM default, CUSTOM_JWT toggle)

**Decision**: Support both `AWS_IAM` (default, zero-config) and `CUSTOM_JWT`
(Auth0/OIDC) via an environment variable toggle.

**Why**:
- **Easy start**: `AWS_IAM` requires zero external setup — just deploy and it works
- **Production path**: Real deployments need OIDC/JWT for user-facing access
- **Demonstration**: Shows AgentCore Identity's credential management without
  forcing it on first-time users
- **Toggle, not branch**: Same codebase, same CDK stack — one env var switches mode

**Trade-off**: Two code paths in `mcp_client/client.py` (SigV4 vs JWT auth).
Managed via a clean `if/else` on `GATEWAY_AUTH_MODE`.

---

## ADR-7: Policy Engine in LOG_ONLY Mode

**Decision**: Deploy Cedar Policy Engine attached to the Gateway in `LOG_ONLY`
mode rather than `ENFORCE` mode.

**Why**:
- **Observability first**: See what *would* be denied before blocking anything
- **Safe for samples**: Users learning AgentCore won't be blocked by policy
  misconfiguration
- **Production upgrade path**: Flip to `ENFORCE` when policies are validated
- **Demonstrates the feature**: CloudWatch logs show policy evaluation results
  even in LOG_ONLY

**Trade-off**: No actual access control at the policy layer. Compensated by
per-tool IAM on the Lambda execution roles (defense in depth).

---

## ADR-8: Cost-Based Model Routing

**Decision**: Route LOW priority tickets to Haiku (fast, cheap) and MEDIUM+
to Sonnet (capable, expensive).

**Why**:
- LOW priority tickets (e.g., "reset my password") are formulaic — Haiku handles
  them well at 10x lower cost
- HIGH/CRITICAL tickets need Sonnet's reasoning for complex multi-tool diagnosis
- Demonstrates a production pattern for cost optimization at scale

**Trade-off**: Slightly more complex model loading logic. Contained in
`model/load.py` (~20 lines).

---

## ADR-9: Single CDK Stack (not multi-stack)

**Decision**: Deploy all resources (AgentCore + supporting infra) in a single
CloudFormation stack.

**Why**:
- **Atomic deployment**: Everything deploys or rolls back together — no partial states
- **Cross-reference simplicity**: Lambda ARNs → Gateway targets, Runtime ARN →
  Trigger Lambda env var — all resolved within one stack
- **Sample clarity**: One `cdk deploy` = everything. No orchestration needed.
- **Cleanup**: One `cdk destroy` removes everything

**Trade-off**: Large stack (~40 resources). At production scale, you'd split into
"shared infra" and "agent" stacks. The README's "Scale for Production" section
notes this.

---

## ADR-10: Bedrock Guardrail at the Agent Entry Point

**Decision**: Apply the Bedrock Guardrail on incoming ticket payloads at the agent
entry point, not as a model-level guardrail.

**Why**:
- **PII sanitization**: Ticket descriptions may contain email addresses, phone
  numbers, or account IDs that should be anonymized before the model sees them
- **Prompt attack filtering**: Malicious ticket descriptions could attempt prompt
  injection — catch it before the agent loop starts
- **Performance**: One guardrail call at entry vs applying on every model turn
- **Separation**: The guardrail is a security boundary, not a model behavior modifier

**Trade-off**: Guardrail only sees the initial payload, not tool responses. Tool
responses are trusted (they come from our own Lambdas behind IAM).

---

## Summary Table

| # | Decision | Key Driver |
|---|----------|-----------|
| 1 | Container build | Reproducibility + local dev parity |
| 2 | Lambda tools via Gateway | Isolation + policy + observability |
| 3 | SNS + Trigger Lambda | Decoupling + resilience |
| 4 | SUMMARIZATION memory | Concise context for prompt injection |
| 5 | DynamoDB | Serverless + pay-per-request + idempotency |
| 6 | Dual auth (IAM/JWT) | Easy start + production path |
| 7 | Policy Engine LOG_ONLY | Safe learning + upgrade path |
| 8 | Cost-based model routing | 10x cost reduction on simple tickets |
| 9 | Single CDK stack | Atomic deploy + sample clarity |
| 10 | Guardrail at entry | Security boundary + performance |
