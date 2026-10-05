# AGENTS.md — Receipts IDP on AgentCore

Guidance for AI agents and contributors working in this sample.

## Orientation
- **How it works:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). **Why each choice:** [docs/decisions/](docs/decisions/) (ADRs). **Knobs:** [docs/CONFIGURATION.md](docs/CONFIGURATION.md). **Guided run:** [docs/tutorial.md](docs/tutorial.md).
- **Sibling reference:** `awslabs/agentcore-samples/.../event-driven-claims-agent` — this sample mirrors its layout and conventions. When in doubt, match it.
- **What it is:** receipt → Textract OCR → dual agent (extractor → independent validator) → Cedar-gated persist or human review, with a model degradation ladder (config-driven model, in-agent 503 step-down, account-level control loop, L4 SQS drain), an event-driven S3 front door, a multi-turn chat assistant on its own Runtime, and an evaluation suite (ADR-0017).

## Conventions (do not break)
- **The seam:** `app/receiptsagent/config.py` is the ONLY place env vars are read. The agent must depend on env/AppConfig, never on CLI/CDK specifics, so the deploy mechanism stays replaceable (ADR-0001).
- **Model id is never hardcoded.** It comes from config (the L0 default) and, in a deployed stack, from the active degradation rung. Pass `model_id` into `load_model()`.
- **Auth is agent-as-principal M2M Cognito**, not per-user JWT. Per-user data separation is the DynamoDB partition key + each tool only touching its given `userId`.
- **Tools are Gateway Lambdas** (`lambdas/<tool>/handler.py` + `lambdas/schemas/<tool>.json` + a `PLACEHOLDER_<TOOL>` target in `agentcore.json` patched by the CDK stack). Keep schema ↔ handler in sync.
- **Global inference profile ids** (incl. the `claude-opus-4-6-v1` `-v1` suffix) come from `aws bedrock list-inference-profiles` — never pattern-construct them.
- **Grounding:** verify AgentCore APIs against the SDK/docs before writing (Cedar, Gateway, AppConfig especially). Don't write from memory.

## Deploy / test
`./deploy.sh <region>` (the whole stack plus the chat live-evaluation config) · `python3 scripts/test_invoke.py` · `./destroy.sh <region>`.

## Evaluators (do not break)
- **Every evaluator judges a decision a model makes, against a right answer or a clear reference**, and passes a contrast test before it is trusted (ADR-0017). Do not add an evaluator that re-checks a control the code already enforces, or a second evaluator for a question one already answers.
- **The code-based evaluators are one module** (`evaluators/business_outcomes/`), deployed as one Lambda entry point per evaluator; the same code scores locally in `evals/` through `handler`, which routes on the evaluator name. Keep the evaluator names in `agentcore.json` in sync with the handler.
- **The code-based evaluators read span attributes, names and statuses, never message content**, so they keep working with content capture off: the `receipts.*` outcome on the invocation span, the validator's `approve_expense` / `send_to_review` call (routing grades that decision, not the final status), and a denied `save_expense` call. If you change what the agent stamps in `_tag_span_outcome`, or rename the decision tools, update the evaluators.
- **Judges scoring one pipeline model get only the trace up to that model** (`_through_agent` in `evals/score_saved.py`); later agents repeat earlier outputs.
