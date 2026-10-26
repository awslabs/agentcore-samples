# Receipts IDP on Amazon Bedrock AgentCore

An **agentic** Intelligent Document Processing sample: a dual-agent pipeline on
AgentCore that turns a **receipt** into a validated, persisted expense record, and
self-protects with a **model degradation ladder** when a model tier is
capacity-constrained.

It is a sibling of the `event-driven-claims-agent` sample and follows its
conventions. The distinct contribution here is the degradation ladder (config-driven
model selection on AWS AppConfig, stepping on a Bedrock `503`).

> [!IMPORTANT]
> This sample is for experimental and educational purposes only. It demonstrates
> concepts and techniques but is not intended for direct use in production.

| | |
|---|---|
| ⏱️ **Time to deploy** | ~20-30 minutes (first time, prerequisites met) |
| 💰 **Running cost** | a few $/day (Bedrock + Textract on demand, DynamoDB on-demand, Lambda, AgentCore Runtime). Tear down when not testing. |
| 🏗️ **Resources created** | one CloudFormation stack (Runtime, Gateway + 5 tool Lambdas, Cedar policy, Evaluator, DynamoDB, S3, Cognito, SQS, AppConfig, EventBridge, KMS, CloudWatch) |

🎥 **Demo:** a recorded run of the full flow is here — [demo.mp4](demo.mp4).

## What it does (target)

A receipt lands in S3 → an extractor agent OCRs it (Textract) and produces a
structured expense → an independent validator agent checks it reconciles and
decides auto-persist vs review → the expense is written per-user through governed
Gateway tools, with Cedar gating writes on amount/category, Memory carrying the
user's history, and the whole run traced and evaluated in CloudWatch.

## The six AgentCore services

Runtime (dual-agent host), Memory (per-user facts, `receipts/{actorId}/...`),
Gateway (five MCP Lambda tools), Observability (OTel → CloudWatch), Policy (Cedar
on tool input), Evaluations (LLM-as-judge + online metrics).

## Architecture

![Receipts IDP architecture](docs/diagrams/architecture.png)

The event-driven front door (upload → S3 → EventBridge → Trigger → Runtime):

![Event-driven front door](docs/diagrams/front-door.png)

The agent authenticates as itself (agent-as-principal, M2M Cognito) — the front
door is an S3 event, so there is no logged-in user at run time. Per-user data
separation lives at the data layer (Expenses partitioned by `userId`). Full
walkthrough + the Mermaid source in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Deploy

```bash
./deploy.sh us-west-2     # CDK infra + AgentCore Runtime, one command
python3 scripts/test_invoke.py --region us-west-2
./destroy.sh us-west-2    # leaves nothing billable
```

Prerequisites: the `@aws/agentcore` CLI, Node + TypeScript, Python 3.12 + `uv`, a
container engine (**Docker or [Finch](https://runfinch.com)** — the Runtime is a
`Container` build, ADR-0005), and the four ladder global inference profiles enabled
in the account (`aws bedrock list-inference-profiles`).

Using Finch? Start its VM once before deploying, and `deploy.sh` auto-selects it:

```bash
finch vm status || finch vm start     # ensure the Finch VM is running
./deploy.sh us-west-2                  # auto-detects Finch (or set CDK_DOCKER=finch)
```

The container engine is only needed for full `deploy` (it builds + pushes the
image). For the fast inner loop, `agentcore dev --no-browser` runs the agent
directly with no container build.

## Front door, Observability & Evaluations

**Event-driven front door.** Drop a receipt in the inbox bucket and the pipeline
runs itself — no direct invoke:

```bash
aws s3 cp receipt.png s3://receipts-inbox-<account>-<region>/receipts/user-001/receipt.png
```

S3 emits an `Object Created` event → an EventBridge rule (scoped to the `receipts/`
prefix) fires the trigger Lambda → it invokes the Runtime with `{s3_uri, user_id}`.
The `user_id` comes from the key (`receipts/<user_id>/<file>`), defaulting to
`user-001` for a flat `receipts/<file>` key. A DLQ + retries make a failed trigger
visible rather than dropping a receipt.

**Observability.** The Runtime is auto-instrumented (the full OTel env set is in
`agentcore.json`); traces, logs, and metrics land in CloudWatch **GenAI
Observability**, correlated by session id. Each run's span is tagged with the ladder
rung (`receipts.ladder.rung`/`.model`/`.degraded`) so a degraded run is visible, not
silent (spec §6.4). **One-time per account:** enable CloudWatch **Transaction
Search** (Application Signals → Transaction search → Enable) so spans are searchable;
`deploy.sh` / the `agentcore` CLI enables it best-effort on deploy, and it takes
~10 min to become active.

**Evaluations.** `agentcore.json` declares a SESSION-level LLM-as-judge evaluator
(`ReceiptsExtractionQualityEvaluator` — extraction accuracy, reconciliation, routing
correctness) plus an online-eval config with three built-in metrics
(`Builtin.Helpfulness`, `Builtin.Correctness`, `Builtin.ToolSelectionAccuracy`)
scored continuously from production spans. Run on-demand:

```bash
agentcore run eval -r receiptsagent -e ReceiptsExtractionQualityEvaluator --days 1
```

**Run ledger (operational audit).** Every run emits one EventBridge event; a writer
Lambda records a row in the **`ProcessingRuns`** table keyed by `receiptId =
hash(s3_uri)` — one durable fate per receipt (processed / needs_review / deferred /
**error**), including errors that never persisted. A `status=error` rule pushes to an
SNS topic (`ReceiptsAgent-RunErrors` — subscribe an email/Chatbot endpoint). "What
happened to receipt X?" is one lookup, not a log dig ([ADR-0015](docs/decisions/0015-processing-runs-ledger.md)):

```bash
python3 scripts/receipt_status.py --s3-uri s3://receipts-inbox-<acct>-us-west-2/receipts/u/r.jpg
python3 scripts/receipt_status.py --status error        # every error (GSI query)
python3 scripts/receipt_status.py --status needs_review # everything in review, with the validator's concern
```

**Talk to the agent (conversational query).** Beyond processing receipts, a user can
ask about their expenses in plain language — a read-only agent answers from live data
via the Gateway read tools:

```bash
python3 scripts/chat.py --user alex            # interactive REPL
# you> how much did I spend at Mr D.I.Y.?
# agent> MYR 68.01 across 2 expenses: ...
python3 scripts/ask.py --user alex "what are my most recent expenses?"   # one-shot
```

**Security ([ADR-0016](docs/decisions/0016-conversational-identity-no-idor.md)):** the
`user_id` is **not** trusted from the request body — it comes from a KMS-HMAC-signed
identity token the agent verifies, and the read tools are pinned server-side to that
verified user. So editing the request can't read another user's data (IDOR is designed
out), and a tampered token is rejected. The belt is read-only, so a query can't write.

## Layout

`agentcore/` (agentcore.json + CDK), `app/receiptsagent/` (the agent; `config.py`
is the single env-read seam; `identity.py` is the conversational-identity verifier),
`lambdas/` (Gateway tools + trigger + controller + drain + ledger_writer) +
`lambdas/schemas/`, `scripts/` (incl. `chat.py`/`ask.py`), `docs/`, `tests/`.

## Docs

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how it works (diagram, the three planes, the pipeline, the ladder).
- **[docs/decisions/](docs/decisions/)** — 16 ADRs: *why* it's built this way (the ladder, auth, Cedar, AppConfig, the drain, the run ledger, conversational identity).
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** — env vars, the AppConfig ladder config, the Cedar policy, tuning knobs.
- **[docs/tutorial.md](docs/tutorial.md)** — a guided run + five experiments (front door, Cedar, flip a rung, the control loop, add a tool).
- **[docs/deployment.md](docs/deployment.md)** — prerequisites, deploy/destroy, local dev, the one-shot `make e2e`.
