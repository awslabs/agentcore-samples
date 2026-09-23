# Configuration

Everything the agent reads comes through one seam — `app/receiptsagent/config.py`. The agent depends only on environment variables and AppConfig, never on CLI/CDK specifics, so the deploy mechanism stays replaceable ([ADR-0001](decisions/0001-agentcore-cli-plus-cdk.md)). This document covers the env vars, the AppConfig ladder config, the Cedar policy, and the tuning knobs.

## Environment variables (the seam)

All set by the CDK stack at deploy time; `.env.example` mirrors them for local `agentcore dev`.

| Variable | Purpose | Set by |
|----------|---------|--------|
| `AGENT_MODEL_ID` | The L0 default model + the local-dev fallback when AppConfig is unreachable. **Not** the live model in a deployed stack — that comes from the active rung. | `agentcore.json` envVars |
| `APPCONFIG_APPLICATION` / `_ENVIRONMENT` / `_PROFILE` | AppConfig coordinates for the degradation ladder. Unset (local dev) ⇒ run on `AGENT_MODEL_ID`, all features on. | CDK (parent stack) |
| `AGENTCORE_GATEWAY_URL` | The MCP Gateway endpoint. | CDK (from the Gateway resource) |
| `AGENTCORE_GATEWAY_TOKEN_ENDPOINT` / `_CLIENT_ID` / `_CLIENT_SECRET` / `_OAUTH_SCOPES` | Cognito M2M `client_credentials` for agent-as-principal auth ([ADR-0004](decisions/0004-agent-as-principal-m2m-over-per-user-jwt.md), [ADR-0014](decisions/0014-cognito-secret-via-cdk-injection.md)). | CDK (from Cognito) |
| `MEMORY_ID` | AgentCore Memory id. Optional — the agent runs without it. | CDK |
| `DEFER_QUEUE_URL` | The L4 SQS defer queue ([ADR-0011](decisions/0011-l4-sqs-jittered-drain.md)). | CDK |
| `RUN_EVENT_BUS` | The run-ledger EventBridge bus ([ADR-0015](decisions/0015-processing-runs-ledger.md)). Unset (local dev) ⇒ no ledger emit, agent runs normally. | CDK |
| `IDENTITY_KEY_ID` | KMS HMAC key for conversational-query identity ([ADR-0016](decisions/0016-conversational-identity-no-idor.md)). The agent verifies the signed token to derive `user_id` (never from the request body). | CDK |
| `ALLOW_FAULT_INJECTION` | Gates the `simulate_503` test hook. `true` in the sample; a production deploy would NOT set it. | CDK |

Lambda-side env (not the agent seam): the trigger reads `AGENTCORE_RUNTIME_ARN` + `DEFAULT_USER_ID`; the controller reads `APPCONFIG_*` + `LADDER_ALARM` + `LADDER_COOLDOWN_SECONDS`; the drain reads `RUNTIME_ARN` + `DRAIN_MIN/MAX_SECONDS`.

## The degradation ladder config (AppConfig)

A freeform JSON profile in AppConfig, deployed by the CDK and editable at runtime with no stack redeploy. Shape:

```json
{
  "activeRung": "L0",
  "rungs": {
    "L0": { "model": "global.anthropic.claude-opus-4-8",
            "features": { "validator": true, "memoryRead": true, "memoryWrite": true,
                          "merchantLookup": true, "categoryInference": true, "dedup": true,
                          "forceReview": false } },
    "L1": { "model": "global.anthropic.claude-opus-4-7",
            "features": { "validator": true, "memoryWrite": false, "merchantLookup": false, "...": "..." } },
    "L2": { "model": "global.anthropic.claude-opus-4-6-v1",
            "features": { "validator": false, "forceReview": true, "...": "..." } },
    "L3": { "model": "global.anthropic.claude-sonnet-4-6",
            "features": { "validator": false, "forceReview": true, "...": "..." } },
    "L4": { "features": { "forceReview": true, "...": "..." } }
  }
}
```

- **`activeRung`** — which rung every new run starts on. Change this (control-plane) to degrade or recover the whole fleet; the agent picks it up within the cache TTL, no redeploy.
- **Per-rung `model`** — a `global.` inference profile id. Read the exact ids from `aws bedrock list-inference-profiles` in your account; the suffix convention is **not** uniform (`opus-4-6` is `...-opus-4-6-v1`). A rung with no `model` (L4) is a defer rung.
- **Per-rung `features`** — sheddable capabilities. Missing flags inherit the L0 defaults. `forceReview: true` makes every receipt route to `human_review` (degrade-safe).

To change a rung's model: edit the profile, create a new hosted config version, start a deployment. To flip the active rung manually (a drill or planned swap): set `activeRung` and deploy. See [tutorial.md](tutorial.md).

## Cedar policy

Two policies on the Gateway's policy engine (`agentcore.json` → `policyEngines`), both `IGNORE_ALL_FINDINGS` ([ADR-0013](decisions/0013-ignore-all-findings-policy-validation.md)):

- **`AllowAllTools`** — `permit(principal, action, resource is AgentCore::Gateway)`. Allow-all baseline.
- **`BlockExcessiveExpense`** — forbids a `save_expense` with `total >= 2000`, routing it to review instead ([ADR-0012](decisions/0012-cedar-on-tool-input.md)). To change the threshold, edit the `>= 2000` in the policy statement. To add a category block, add another `forbid` keyed on `context.input.category` (guard `context has input` first).

## Tuning knobs

The *shapes* are settled; these *values* are tuned against your account's real Bedrock quotas (spec §12).

| Knob | Where | Default | Notes |
|------|-------|---------|-------|
| Ladder cooldown | `infra-construct.ts` → `LADDER_COOLDOWN_SECONDS` | `60` | Anti-flap window between rung changes. Production tunes higher. |
| Step-down alarm threshold | `infra-construct.ts` → `LadderStepDownAlarm` | `3` ModelStepDowns / 1 min | How many step-downs before the control loop reacts. |
| Drain pacing | `infra-construct.ts` → `DRAIN_MIN/MAX_SECONDS` | `1`–`3` s | Jittered sleep per replayed receipt; concurrency=1 + batch=1 bound the rate. |
| Drain timeout / queue visibility | `infra-construct.ts` | `4` min / `6` min | Visibility must exceed the drain timeout so an in-flight replay holds its message. |
| AppConfig deployment strategy | `infra-construct.ts` → `LadderStrategy` | all-at-once, no bake | A production deploy adds a bake window + an alarm rollback. |
| Cedar threshold | `agentcore.json` → `BlockExcessiveExpense` | `2000` | The auto-persist ceiling. |
