# Online Evaluation Configuration

Online evaluation is configured declaratively in `agentcore/agentcore.json` under `onlineEvalConfigs[]`:

```json
"onlineEvalConfigs": [{
  "name": "ITIncidentAgentEval",
  "agent": "ITIncidentAgent",
  "evaluators": [
    "Builtin.Correctness",
    "Builtin.Helpfulness",
    "Builtin.ToolSelectionAccuracy",
    "Builtin.GoalSuccessRate"
  ],
  "samplingRate": 100,
  "description": "Online evaluation for IT incident response agent (4 built-in evaluators)"
}]
```

The `AgentCoreApplication` L3 construct handles the full lifecycle:
- Creates an IAM execution role with least-privilege permissions
- Creates the `OnlineEvaluationConfig` CloudFormation resource
- Adds dependency ordering on the Runtime (ensuring the log group exists first)

## Prerequisite: CloudWatch Transaction Search (auto-enabled)

Online evaluation requires **CloudWatch Transaction Search** so OTEL spans are
ingested into the `aws/spans` log group. The stack **enables this automatically**:
when `onlineEvalConfigs` is non-empty, a custom resource
(`lambdas/infra/transaction_search.py`, wired via `enableTransactionSearch()` in
`cdk-stack.ts`) calls the X-Ray control plane to route trace segments to
CloudWatch Logs and set the span indexing percentage (100% by default, override
with `TXN_SEARCH_INDEXING_PERCENTAGE`).

On stack delete, Transaction Search is intentionally **left enabled** — it is an
account/region-level setting other agents may depend on.

The first deploy may take 10-15 minutes for the log group to provision. Verify:
```bash
aws logs describe-log-groups --log-group-name-prefix "/aws/spans" --region us-west-2
# Should return a log group; if empty, wait longer
```

> Manual enablement (`aws application-signals start-monitoring`) is not
> required; it remains available as a fallback if you deploy with
> `onlineEvalConfigs: []` and later enable eval out of band.

## Required Runtime Environment Variables

The agent runtime **must** emit `gen_ai` semantic spans (with `session.id`) for the
online evaluator to have data to score. These are enabled via env vars in
`agentcore/agentcore.json` → `runtimes[].envVars[]` (OTEL auto-instrumentation is
provided by the Dockerfile `CMD ["opentelemetry-instrument", "python", "-m", "main"]`):

| Variable | Purpose |
|----------|---------|
| `AGENT_OBSERVABILITY_ENABLED` | Enables the AgentCore observability pipeline that emits `gen_ai` spans tagged with `session.id` |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | Captures model input/output so evaluators can assess correctness/helpfulness |
| `OTEL_AWS_APPLICATION_SIGNALS_ENABLED` | Enables AWS Application Signals (session correlation) |
| `OTEL_TRACES_EXPORTER=otlp` | Exports traces via OTLP |
| `OTEL_PROPAGATORS=baggage,tracecontext,xray` | Propagates trace headers across the SNS → trigger Lambda → runtime hops |

> **Failure mode:** If these are missing, the runtime still emits low-level HTTP
> spans (IMDS/credential calls) but **no `gen_ai` spans with `session.id`**. The
> online eval config deploys fine and `aws/spans` fills up, but evaluations never
> trigger because there are no agent sessions to score. The four variables
> above are required for evaluations to run.

## Verifying evaluations are running

After invoking the agent (e.g., via `scripts/test-e2e.sh`), online eval results
take **~10-15 minutes** to appear (5-min session timeout + backend processing):

```bash
# 1. Confirm gen_ai spans with session.id are flowing
aws logs filter-log-events --log-group-name "aws/spans" \
  --filter-pattern "session.id" --limit 5 --region us-west-2

# 2. Check the eval results log group for scored sessions
aws logs filter-log-events \
  --log-group-name-prefix "/aws/bedrock-agentcore/evaluations/results/ITIncidentAgent_ITIncidentAgentEval" \
  --limit 20 --region us-west-2
```

## Disabling Online Evaluation

To deploy without online evaluation (e.g., if Transaction Search is not enabled):

1. Set `onlineEvalConfigs` to `[]` in `agentcore/agentcore.json`
2. Redeploy: `./scripts/deploy.sh`

## Evaluators

| Evaluator                | What it measures                         |
|--------------------------|------------------------------------------|
| `GoalSuccessRate`        | Did the agent achieve its stated goal?   |
| `Correctness`            | Was the information provided accurate?   |
| `Helpfulness`            | Was the response useful to the user?     |
| `ToolSelectionAccuracy`  | Did the agent pick the right tools?      |

## Cost

Typical workload (100 requests/day): **$5-15/month** for CloudWatch Transaction Search + evaluation.
