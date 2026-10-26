# Tutorial

A guided run, then five experiments that exercise the parts that make this sample interesting: the event-driven front door, the Cedar guardrail, the degradation ladder (the distinct contribution), and adding a tool. Assumes you've deployed — see [deployment.md](deployment.md).

Prerequisites: the stack is deployed (`./deploy.sh us-west-2`), and the four ladder global inference profiles are enabled in your account (`aws bedrock list-inference-profiles`).

## The guided run

**1. Confirm the agent responds.** Upload the sample receipt and invoke directly:

```bash
python3 scripts/upload_sample_receipt.py --region us-west-2      # prints the s3:// URI
python3 scripts/test_invoke.py --region us-west-2 \
    --s3-uri s3://receipts-inbox-<account>-us-west-2/receipts/sample-receipt.png \
    --user-id user-001
```

The response is a structured result: `{status, rung, needs_review, model, extractor_confidence, validator, expense, ...}`. On a healthy account `rung` is `L0` and `status` is `processed` or `needs_review`.

**2. See it in DynamoDB.** The expense row landed under the user:

```bash
aws dynamodb query --table-name ReceiptsAgent-Expenses \
    --key-condition-expression "userId = :u" \
    --expression-attribute-values '{":u":{"S":"user-001"}}' \
    --region us-west-2
```

**3. See the trace.** CloudWatch → GenAI Observability → your runtime. Each run is one session; the span carries `receipts.ladder.rung`/`.model`/`.degraded`.

## Experiment 1 — the event-driven front door

No direct invoke. Just drop a file and watch the pipeline run itself ([ADR-0006](decisions/0006-s3-eventbridge-over-direct-invoke.md)):

```bash
aws s3 cp tests/fixtures/sample-receipt.png \
    s3://receipts-inbox-<account>-us-west-2/receipts/alice/lunch.png --region us-west-2
```

The key `receipts/alice/lunch.png` makes the trigger derive `user_id=alice`. After ~90s, query the Expenses table for `userId = alice` — the row is there, written by a run nobody invoked by hand. S3 → EventBridge → trigger Lambda → Runtime.

## Experiment 2 — the Cedar guardrail

`save_expense` is gated at the Gateway: an expense ≥ $2,000 cannot auto-persist, it routes to review — deterministically, independent of the agents ([ADR-0012](decisions/0012-cedar-on-tool-input.md)). The `tests/test_e2e_cedar_live.py` test drives this directly through the Gateway with the agent's M2M token: a small total is allowed, a $2,000+ total is denied and the agent falls back to `human_review`. Run it against the live stack:

```bash
AWS_REGION=us-west-2 python3 -m pytest tests/test_e2e_cedar_live.py -v
```

## Experiment 3 — flip the degradation rung (no redeploy)

The ladder's core promise: change the model for every run by editing AppConfig, with no stack redeploy ([ADR-0007](decisions/0007-degradation-ladder-on-503.md), [ADR-0008](decisions/0008-appconfig-over-hand-rolled-flags.md)).

```bash
# Find the AppConfig ids, then deploy a config with activeRung flipped to L3:
AWS_REGION=us-west-2 python3 -m pytest tests/test_e2e_ladder_live.py -v
```

That test flips `activeRung` L0 → L3 via AppConfig (control plane only), invokes the agent, and confirms the model swapped to Sonnet 4.6 with **no redeploy** — then restores L0. To do it by hand: edit the hosted config profile's `activeRung`, `create-hosted-configuration-version`, `start-deployment`; the agent picks it up within the cache TTL.

## Experiment 4 — the account-level control loop

A sustained `503` should step the whole fleet down, then recover ([ADR-0010](decisions/0010-two-rung-setting-paths.md)). You can't summon a real Bedrock `503` on demand — but `cloudwatch set-alarm-state` fires the *real* EventBridge event, so the alarm → controller → AppConfig path runs for real:

```bash
AWS_REGION=us-west-2 python3 -m pytest tests/test_e2e_controller_live.py -v
```

This forces the ladder alarm to `ALARM`, asserts the controller stepped `activeRung` L0 → L1, then proves the cooldown blocks an immediate recovery and the rung steps back up after it elapses. The L4 SQS drain is covered by `tests/test_e2e_drain_live.py` ([ADR-0011](decisions/0011-l4-sqs-jittered-drain.md)).

## Experiment 5 — add a new tool

Two config surfaces stay in sync ([ADR-0001](decisions/0001-agentcore-cli-plus-cdk.md), [ADR-0003](decisions/0003-gateway-lambda-targets-over-co-located-tools.md)):

1. **The Lambda:** `lambdas/<new_tool>/handler.py` + `lambdas/schemas/<new_tool>.json` (keep the schema in sync with the handler's parameters, or the agent won't see a field).
2. **`agentcore.json`:** add a Gateway target with a `PLACEHOLDER_<NEW_TOOL>` ARN + the schema file.
3. **`cdk-stack.ts` / `infra-construct.ts`:** create the Lambda (with least-privilege grants) and patch its real ARN into the `lambdaArnMap` so the placeholder is replaced at synth.
4. **The agent:** reference the tool by name from the Gateway tool list in `main.py`.

`make synth` validates the wiring before you deploy.

## Evaluations

The agent's runs are scored two ways ([ARCHITECTURE.md](ARCHITECTURE.md)): a SESSION LLM-as-judge evaluator (`ReceiptsExtractionQualityEvaluator`) and an online eval with built-in metrics. Run the judge on demand over recent sessions:

```bash
agentcore run eval -r receiptsagent -e ReceiptsExtractionQualityEvaluator --days 1
```

Scores land in CloudWatch (they lag span ingestion by a few minutes).
