# Observability and online evaluation

The preserved evaluation configuration samples 100% of eligible Runtime traces
with `Builtin.Correctness`, `Builtin.Helpfulness`, `Builtin.ToolSelectionAccuracy`
and `Builtin.GoalSuccessRate`. These are online outcomes, not an on-demand test
runner and not a guarantee of correctness.

## Account-owner prerequisite

Transaction Search is shared **account/region state**. The account owner must
prepare the Logs delivery/resource policy and route X-Ray trace segments to
CloudWatch Logs. `aws/spans` has no leading slash. Sampling must be configured and
actually applied at the requested percentage (100 by default).

By default the custom resource is read-only: it checks destination **ACTIVE**,
desired indexing and **actual** indexing, with bounded polling. A pending setting,
missing actual percentage, API denial or timeout fails deployment. Required
indexing errors are not warnings and never produce a claimed 100% result.
Evaluators depend on that readiness resource.

CLI 1.2.0 has no deploy flag for Transaction Search. The wrapper uses the real
`transactionSearch: false` global preference in a **private sample CLI HOME** and
verifies the CLI reads it, so its separate best-effort setup does not bypass this
contract. It does not change the operator's global CLI preferences. No permission to change
real account settings is implied by this sample or its tests.

Only if the account owner separately authorizes mutation may the operator set
both `TXN_SEARCH_ALLOW_MUTATION=true` and
`TXN_SEARCH_ACCOUNT_OWNER_APPROVAL=ACCOUNT/REGION`. Only that mode receives X-Ray
write grants. The Logs delivery policy remains the account owner's prerequisite.
Delete deliberately retains all shared settings; it never disables or resets
them on behalf of this stack.

## Content and privacy

Message-content capture is off in the committed runtime defaults. Deploying the
preserved online-evaluation mode requires the explicit
`IT_SAMPLE_EVALUATION_CONTENT=synthetic-only` acknowledgment, which enables the
content that evaluators need. Use synthetic profiles, incidents, runbooks and
Jira issues only. Instrumentation can capture input before application redaction;
the Guardrail is **not** a blanket PII guarantee for traces or stores.

Requester IDs must be opaque, not email addresses. The app no longer writes a
requester span attribute or logs Jira consent URLs. Tool results, recalled
summaries, incident text and final resolution are guarded; unavailable/blocked
guardrails fail the workflow. Access and retention policies for logs, Memory,
DynamoDB and evaluation results still need operator review.

## Retrieve and verify

Use the binding defined in the README:

```bash
python scripts/evaluate.py "${BIND[@]}" --hours 1 --summary
./scripts/test-e2e.sh "${BIND[@]}"
```

The results helper resolves the owned evaluation resource's actual output log
group, not a latest-name match. Logs Insights uses epoch seconds, bounded polls
and propagated query errors. The display is exploratory; it is not full E2E proof.

A Jira-enabled stack additionally requires the explicit three-issue fixture
(`--jira-events`); DDB-only results cannot certify that optional mode.
The E2E suite requires unique current-run sessions and trace IDs, completed tool
results, EventBridge acceptance, a resolution, cross-session **Memory** record
IDs/namespaces and every evaluator outcome for each session/trace. A DynamoDB
recurrence count is not Memory proof. Old, empty, nonnumeric, error or incomplete
results cannot pass. It waits for configured session inactivity plus bounded
processing time, retaining `.e2e/` evidence on failure.

All four scores need human interpretation. A zero score is a complete but poor
outcome, not a hidden pass on quality. LOG_ONLY policies remain non-enforcing,
regardless of an evaluator score. Optional features skipped by an operator must
be labeled NOT RUN, not removed to obtain a successful full-suite status.
