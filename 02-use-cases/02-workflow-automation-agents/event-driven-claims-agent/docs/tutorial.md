# Tutorial: make it your own

Start with the [fresh-stack deployment procedure](deployment.md) and
[migration boundary](MIGRATION.md). This is a custom CLI 1.2.0/v1 app. Cloud
exercises require separate authorization; the test configuration must remain
notification-free. Use offline tests first, not a model invocation for every edit.

## Understand the three phases

1. The caller signs a Runtime request with IAM/SigV4. Cognito JWT is **not** the
   Runtime's inbound authentication method.
2. The processor uses Sonnet and the read-only policy-lookup tool, then
   `submit_decision`. Its Gateway connection obtains M2M tokens through Identity.
3. The Haiku validator uses only `submit_validation` to score and route.
4. Phase 3 makes direct qualified MCP tool calls without an LLM. It verifies the
   SDK result and confirmed claim ID before escalation/notification.

Missing structured output defaults safely: REJECT for missing decision and
HUMAN_REVIEW for missing validation. Cedar ENFORCE runs at the Gateway before
tool execution. A denied create is not an automatic human-review record.

## Experiment 1: change the confidence threshold

Edit `AUTO_APPROVE_THRESHOLD` in `agentcore/agentcore.json` →
`runtimes[0].envVars`, for example from `80` to `90`. Update the validator prompt
so its instructions match the hard threshold in `routing.py`.

```bash
app/claimsagent/.venv/bin/python tests/run_offline.py
# After separately authorized deployment review:
./deploy.sh --allow-cloud
```

Changing a shell variable does not override a declared deployed envVar. Model
changes also require updating the explicit model ARNs in `runtime-boundary.ts`.
Do not remove that boundary to make a new model work.

## Experiment 2: author and test Cedar

The current policy matches the **action** `create-claim___create_claim`, not the
hyphenated target name alone. Change its amount threshold in the project spec,
then extend `test_pipeline.py` with below/at/above-boundary inputs. Its offline
Gateway fixture evaluates the actual Cedar statements and correlates real Lambda
code with in-memory rows. This is not proof of the service context.

For an additional policy, CLI 1.2.0 accepts an explicit statement file:

```bash
agentcore add policy --engine ClaimsPolicyEngine --name AdditionalClaimRule \
  --statement file://your-reviewed-rule.cedar --validation-mode ignore-all-findings
```

Keep Gateway `ENFORCE`. `IGNORE_ALL_FINDINGS` is static validation mode; it does
not disable authorization. Do not treat a model-supplied field such as
`policy_status` as authoritative policy data without server-side verification.
After an authorized deployment, `scripts/test_cedar.py --allow-cloud` discovers
the wire tool name and checks denial against DynamoDB effects, not model prose.

## Experiment 3: add a read-only tool

To extend the original fraud-check example:

1. Add `lambdas/fraud_check/handler.py`, returning `json.dumps({...})` directly.
   Keep output deterministic in tests; a sample random score is not fraud evidence.
2. Add `lambdas/schemas/fraud_check.json`, an array of tool definitions. Match every
   required argument to the Lambda handler.
3. Add the Lambda in `infra-construct.ts`, using `projectRoot`/`lambdasPath` and
   `${prefix}-FraudCheck`, not a global name or cwd-dependent path. Grant only the
   exact data access required. Add its ARN to `lambdaArnMap`.
4. Add the target to the Gateway config using the v1 complete-target interface:
   `agentcore add gateway-target --gateway ClaimsGateway --target-configuration file://fraud-target.json`.
   Use the existing Lambda-target entries as the schema reference. Missing ARN
   mappings intentionally fail synth.
5. If the processor should use it, add its qualified name (for example
   `fraud-check___check_fraud_score`) to the MCP **read-only allowlist** as well as
   the processor prompt. Mutation tools should stay exclusive to Phase 3.
6. Add schema/result/IAM/negative tests. Rebuild TypeScript, synth and rerun offline
   tests before reviewing any deployment diff.

## Adapt to a different domain

The same phases can support a loan workflow: applicant/eligibility lookup,
structured decision, independent confidence review and deterministic execution.
Change the prompts, data model, handlers, schemas, policy action IDs and tests
together. Keep fail-safe routing, exact-ID ownership and independent inbound /
outbound authentication. Do not rename resources in an existing stack without a
separately reviewed data/resource migration plan.

Runtime model/routing/Memory settings live in `agentcore.json` envVars. Lambda
timeouts, inbox prefix and removal policies are CDK properties, **not** the
unimplemented `LAMBDA_TIMEOUT_SECONDS`, `S3_INBOX_PREFIX` or `DESTROY_ON_DELETE`
environment variables shown in older versions of this tutorial.

## Local development is cloud-connected

```bash
agentcore dev --agent claimsagent --mode headless
agentcore invoke --runtime claimsagent --local --payload '{"prompt":"..."}'
```

Only the Runtime process runs locally. Gateway, Lambda, DynamoDB, Cognito,
Identity, Memory and Bedrock remain cloud-backed. Use the exact provider binding
from the owned deployment, never a client-secret bypass or a guessed bare v0
provider. Local Identity permissions require review. For offline iteration use
`tests/run_offline.py`; for container startup use `/ping` with `--network none`.

See [configuration](CONFIGURATION.md), [architecture](ARCHITECTURE.md), and the
[decision records](decisions/README.md) for the maintained contracts.
