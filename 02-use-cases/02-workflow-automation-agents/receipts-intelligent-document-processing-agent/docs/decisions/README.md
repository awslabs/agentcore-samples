# Decision Records

Each ADR captures a non-trivial architectural choice, the reasoning behind it, the alternatives that were considered and rejected, and the consequences of the decision. Read these to understand *why* the system is built this way — not just *what* it does.

This sample mirrors the structure of the sibling [`event-driven-claims-agent`](https://github.com/awslabs/agentcore-samples/tree/main/02-use-cases/02-workflow-automation-agents/event-driven-claims-agent) sample. The first records track decisions shared with that sample (often reaching the same conclusion for the same reason). ADRs 0007 onward cover this sample's distinct contribution: a **model degradation ladder** that keeps the pipeline serving through a Bedrock capacity event.

| # | Title | Why it matters |
|---|-------|---------------|
| [0001](0001-agentcore-cli-plus-cdk.md) | AgentCore CLI + CDK for Supplementary Infra | Combines CLI-managed AgentCore resources with CDK-managed AWS infra in one stack |
| [0002](0002-dual-agent-over-single-agent.md) | Dual-Agent Over Single-Agent | Eliminates confirmation bias — a single agent rarely overrides its own first decision |
| [0003](0003-gateway-lambda-targets-over-co-located-tools.md) | Gateway Lambda Targets Over Co-Located Tools | Unlocks Cedar enforcement and per-tool least-privilege via the MCP Gateway |
| [0004](0004-agent-as-principal-m2m-over-per-user-jwt.md) | Agent-as-Principal M2M Over Per-User JWT | An event-driven front door has no logged-in user; the agent authenticates as itself |
| [0005](0005-container-build-over-codezip.md) | Container Build Over CodeZip | Picks the Runtime build mode best suited to a longer, multi-step pipeline |
| [0006](0006-s3-eventbridge-over-direct-invoke.md) | S3 + EventBridge Over Direct Invoke | S3 gives a durable audit trail + no payload limit; EventBridge enables fan-out |
| [0007](0007-degradation-ladder-on-503.md) | A Model Degradation Ladder, Stepped on 503 | The distinct contribution: stay available through a Bedrock capacity event |
| [0008](0008-appconfig-over-hand-rolled-flags.md) | AWS AppConfig Over a Hand-Rolled Flag Store | Safe, deploy-free behavior change with validation + alarm-backed rollback |
| [0009](0009-appconfigdata-not-lambda-extension.md) | Read AppConfig via appconfigdata, Not the Lambda Extension | The Runtime is a container, not a Lambda — the extension does not apply |
| [0010](0010-two-rung-setting-paths.md) | Two Rung-Setting Paths + a Custom Step-Down Metric | Reactive in-agent step-down and proactive account-level control loop, distinct signals |
| [0011](0011-l4-sqs-jittered-drain.md) | L4 SQS Buffer with a Jittered Drain | Never drop a receipt during an outage; never stampede the recovered tier |
| [0012](0012-cedar-on-tool-input.md) | Cedar Gates on Tool Input, Not Caller Identity | A deterministic guardrail on the amount, independent of the agents |
| [0013](0013-ignore-all-findings-policy-validation.md) | IGNORE_ALL_FINDINGS Policy Validation | Matches the Cedar validation mode to runtime-resolved policies; STRICT for production |
| [0014](0014-cognito-secret-via-cdk-injection.md) | Cognito Client Secret via CDK Injection | Keeps the sample focused on AgentCore; production reads the secret from Secrets Manager |
| [0015](0015-processing-runs-ledger.md) | Per-Receipt ProcessingRuns Ledger (async) | One durable fate row per receipt + push error alerts; "what happened to X?" is one lookup (post-M3) |
| [0016](0016-conversational-identity-no-idor.md) | Conversational Query Mode — Signed Identity, No IDOR | Chat about your expenses; user_id from a KMS-signed token + server-side tool pinning, not the request body (post-M3) |
