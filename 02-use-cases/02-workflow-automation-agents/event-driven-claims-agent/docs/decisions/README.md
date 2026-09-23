# Decision Records

Each ADR captures a non-trivial architectural choice, the reasoning behind it, the alternatives that were considered and rejected, and the consequences of the decision. Read these to understand *why* the system is built this way — not just *what* it does.

| # | Title | Why it matters |
|---|-------|---------------|
| [0001](0001-agentcore-cli-plus-cdk.md) | AgentCore CLI + CDK for Supplementary Infra | Shows how to combine CLI-managed AgentCore resources with CDK-managed AWS infra in one stack |
| [0002](0002-dual-agent-over-single-agent.md) | Dual-Agent Over Single-Agent | Eliminates confirmation bias — a single agent rarely overrides its own first decision |
| [0003](0003-gateway-lambda-targets-over-co-located-tools.md) | Gateway Lambda Targets Over Co-Located Tools | Unlocks Cedar enforcement and independent tool scaling via the MCP Gateway |
| [0004](0004-cognito-m2m-over-iam-auth.md) | Hybrid Auth: SigV4 Inbound + Cognito M2M Outbound | Demonstrates OAuth2 patterns for external integrations (not just AWS-internal SigV4) |
| [0005](0005-container-build-over-codezip.md) | Container Build Over CodeZip | Picks the Runtime build mode best suited to a longer, multi-step pipeline |
| [0006](0006-s3-eventbridge-over-ses-lambda.md) | S3 + EventBridge Over Direct SES Lambda | S3 provides audit trail + handles large payloads; EventBridge enables fan-out |
| [0007](0007-explicit-cognito-pool-for-gateway-auth.md) | One Shared Cognito Pool for Runtime + Gateway | One M2M client and one token across both hops for a clean, unified identity model |
| [0008](0008-semantic-summarization-memory.md) | SEMANTIC + SUMMARIZATION Memory | Combines fact recall across sessions with context-window compression for long conversations |
| [0009](0009-ignore-all-findings-policy-validation.md) | IGNORE_ALL_FINDINGS Policy Validation | Matches the Cedar validation mode to runtime-resolved policies; STRICT for production |
| [0010](0010-identity-token-vault-for-secrets.md) | Cognito Secret via AgentCore Identity Token Vault | Uses Identity's token vault instead of CDK env var injection for secure credential management |
| [0011](0011-externalized-configuration.md) | Externalized Configuration via Environment Variables | Makes all operational knobs (model, threshold, timeouts, memory tuning) configurable without code changes |
| [0012](0012-gsi-over-scan-for-status-queries.md) | GSI Over Scan for Status Queries | Eliminates full table scans — uses DynamoDB GSIs for O(results) instead of O(table) |
| [0013](0013-cost-routing-fast-model-for-validator.md) | Cost-Based Model Routing — Fast Model for Validator | Uses Haiku for the classification-only Validation Agent (Phase 2), saving 3–8s and ~80% cost per invocation |
| [0014](0014-deterministic-phase3-no-llm.md) | Deterministic Phase 3 — Direct MCP Tool Calls | Eliminates the 3rd LLM call; executes tool calls directly via MCPClient with known parameters from structured output |
