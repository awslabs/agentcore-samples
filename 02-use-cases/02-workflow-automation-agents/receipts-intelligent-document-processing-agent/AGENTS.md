# AGENTS.md — Receipts IDP on AgentCore

Guidance for AI agents and contributors working in this sample.

## Orientation
- **How it works:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). **Why each choice:** [docs/decisions/](docs/decisions/) (ADRs). **Knobs:** [docs/CONFIGURATION.md](docs/CONFIGURATION.md). **Guided run:** [docs/tutorial.md](docs/tutorial.md).
- **Sibling reference:** `awslabs/agentcore-samples/.../event-driven-claims-agent` — this sample mirrors its layout and conventions. When in doubt, match it.
- **Build status:** complete and live-verified end to end — receipt → Textract OCR → dual agent (extractor → independent validator) → Cedar-gated persist or human review, with a model degradation ladder (config-driven model, in-agent 503 step-down, account-level control loop, L4 SQS drain), an event-driven S3 front door, Observability, and Evaluations. All six AgentCore services exercised.

## Conventions (do not break)
- **The seam:** `app/receiptsagent/config.py` is the ONLY place env vars are read. The agent must depend on env/AppConfig, never on CLI/CDK specifics, so the deploy mechanism stays replaceable (spec §13).
- **Model id is never hardcoded.** It comes from config (the L0 default) and, in a deployed stack, from the active degradation rung. Pass `model_id` into `load_model()`.
- **Auth is agent-as-principal M2M Cognito**, not per-user JWT. Per-user data separation is the DynamoDB partition key + each tool only touching its given `userId`.
- **Tools are Gateway Lambdas** (`lambdas/<tool>/handler.py` + `lambdas/schemas/<tool>.json` + a `PLACEHOLDER_<TOOL>` target in `agentcore.json` patched by the CDK stack). Keep schema ↔ handler in sync.
- **Global inference profile ids** (incl. the `claude-opus-4-6-v1` `-v1` suffix) come from `aws bedrock list-inference-profiles` — never pattern-construct them.
- **Grounding:** verify AgentCore APIs against the SDK/docs before writing (Cedar, Gateway, AppConfig especially). Don't write from memory.

## Deploy / test
`./deploy.sh <region>` (CDK + Runtime, one command) · `python3 scripts/test_invoke.py` · `./destroy.sh <region>`.
