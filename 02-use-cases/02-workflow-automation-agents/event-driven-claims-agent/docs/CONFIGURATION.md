# Configuration reference

The custom CLI 1.2.0/v1 project uses two deliberate configuration surfaces:
`agentcore/agentcore.json` for AgentCore resources, and the custom CDK app for
supplementary infrastructure/wiring. External Cognito/Identity IDs and ownership
are journaled in `.claims-v1.json`. See [deployment](deployment.md) before cloud use.

## Names and ownership

`configure_target.py --account ... --region ... --suffix ...` reserves a fresh
project `Claims<suffix>` and target `dev`. It refuses existing state; it is not an
in-place v0 migration or a retargeting tool.

| Resource | Fresh name |
|---|---|
| Stack | `AgentCore-Claims<suffix>-dev` |
| Tables | `Claims<suffix>-dev-{Policies,Claims,Reviews}` |
| Tool/Trigger Lambdas | `Claims<suffix>-dev-<purpose>` |
| SNS / SQS / alarm / rule | `Claims<suffix>-dev-<purpose>` |
| Inbox | `claims<suffix>-dev-inbox-<account>-<region>` |
| Runtime | `Claims<suffix>_dev_claimsagent` (service adds its ID suffix) |
| Gateway | `Claims<suffix>-dev-ClaimsGateway` |
| Memory/evaluator/policy engine | v1 project + target + spec name |
| OAuth provider | `Claims<suffix>_dev_cognito-gateway-m2m` |
| Cognito pool/domain | names in `.claims-v1.json`, tagged with `claims:owner` |

These are **new identities**. The old bare `cognito-gateway-m2m`, old pool and old
stack remain untouched. Never fabricate v1 state for existing resources.

## Runtime environment

Set model/routing/Memory overrides in `runtimes[0].envVars` in `agentcore.json`.
Exporting a shell variable does not override a declared runtime envVar at deploy.

| Variable | Source/default |
|---|---|
| `AGENTCORE_GATEWAY_URL` | typed v1 Gateway accessor |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | actual provider name from recorded ARN |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | `agentcore/invoke` |
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` |
| `FAST_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `AUTO_APPROVE_THRESHOLD` | `80` |
| `MEMORY_CLAIMSAGENTMEMORY_ID` | v1 Memory binding |
| `MEMORY_RETRIEVAL_TOP_K` | `5` (summary uses `max(top_k - 2, 1)`) |
| `MEMORY_RETRIEVAL_RELEVANCE` | `0.5` |
| `AGENT_OBSERVABILITY_ENABLED` | `true` |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true`; use synthetic data |

The container gets **no client secret**. Identity owns that secret and the
`requires_access_token` M2M decorator obtains tokens. Runtime ingress remains IAM
(SigV4), not JWT. Local development still needs cloud services and separately
reviewed Identity permissions; `.env.example` contains nonsecret examples only.

## Lambda settings and notifications

Tables/topic/Runtime ARN are CDK references, never guessed defaults at deploy.
The notification and human-review Lambdas receive `NOTIFICATIONS_ENABLED`.
The fresh test configuration sets it to `false`, suppresses both SES sends and
SNS publication, and omits those actions from IAM. It does not remove the tools,
topic or review persistence.

For the original notification-enabled demonstration, a separately authorized
operator may change `.claims-v1.json` `notifications` to `true` and supply a
verified `SENDER_EMAIL` at synth time. Review recipient policy, IAM and ownership
before doing this. The bounded cloud checks **refuse** notification-enabled
configurations. Never verify an address or create a subscription as part of
migration validation. An SES error is returned as `ses_error`, not delivery proof.

## MCP and policy

Schema files in `lambdas/schemas/` remain the source of tool arguments. CDK patches
placeholder Lambda ARNs and throws for an unknown mapping, rather than removing
a target. Wire names are `<target>___<schema tool>`, for example
`create-claim___create_claim`. Only `policy-lookup___lookup_policy` is offered to
the Phase 1 model. Phase 3 calls the mutating tools directly and checks the actual
Strands `MCPToolResult` dictionary `status`/`isError`/`content` contract.

```cedar
permit(principal, action, resource is AgentCore::Gateway);

forbid(principal, action == AgentCore::Action::"create-claim___create_claim",
       resource is AgentCore::Gateway)
when {
  context has "input" && context.input has "estimated_amount"
  && context.input.estimated_amount >= 100000
};
```

`estimated_amount` is required and integer-valued in the tool schema. A forbid
overrides a permit. `IGNORE_ALL_FINDINGS` is policy validation mode, **not** an
authorization bypass. The integration refuses any Gateway mode other than
`ENFORCE`. A denial does not create a review automatically: the executor reports
a nonfatal failed write and does not escalate or notify with a nonexistent claim
ID. Offline Cedar tests evaluate the actual statements; direct service name and
denial/effect checks remain required before claiming service enforcement.

## Memory and evaluation

Memory keeps the original 90-day expiry and strategies. The actor key is
`claimant-` plus SHA-256 of the exact UTF-8 claimant identity (73 characters).
Each invocation gets a separate `claim-<UUID hex>` session (38 characters), never
raw email. Repeat identities share the actor key; different identities do not.
The email remains separately available for claim metadata and notifications.
This deterministic key is not encryption or a guarantee of anonymization.

The namespace templates are unchanged:
- SEMANTIC: `claims/{actorId}/facts`
- SUMMARIZATION: `claims/{actorId}/{sessionId}`

The real SDK session manager attaches to the processor; unconfigured/unavailable
Memory degrades without crashing the workflow. Do not remove Memory to fix a
migration/config error. The validator has only its local structured-output tool.

`ClaimsEvaluation` retains 100% sampling of `Builtin.Helpfulness`,
`Builtin.Correctness` and `Builtin.ToolSelectionAccuracy`. The separate
`ClaimsQualityEvaluator` keeps its SESSION LLM-as-judge prompt/rating scale and
Sonnet model for on-demand reference-based evaluations. Online eval depends on
the Runtime log group. Service-level data delivery/evaluation is cloud-unverified.

## IAM

`runtime-boundary.ts` intersects the generated v1 Runtime role with:
- exactly the two model IDs and their named inference profiles;
- its image repository/key and Runtime log prefix;
- its Memory ARN (original namespace conditions still apply);
- the exact dedicated OAuth provider/Identity secret;
- workload-token access in the current account/region default identity directory;
- X-Ray, metrics and ECR token APIs (wildcards only where the API cannot scope a
  resource; metrics restricted to namespace `bedrock-agentcore`).

The service-generated workload identity ID cannot be referenced from the role
without a dependency cycle; token access uses the current default directory and
its workload-identity suffix wildcard. The boundary excludes the library's broad
configuration-bundle mutations and API-key access. Model changes require a
corresponding boundary change. Cross-region model-region wildcards apply only to
the two named models, not arbitrary foundation models/profiles.

Trigger IAM remains separate: S3 inbox reads, runtime/endpoint invoke, logs and
DLQ. Gateway role invokes the six exact Lambda ARNs and evaluates the policy
engine. Lambda data grants target their own tables/topic/SES account identity.
Deployer/CodeBuild roles are not Runtime roles: review their separate template
policies and existing bootstrap permissions; never use AdministratorAccess as a
sample fix.

## Observability and lifecycle

Runtime OTEL stays enabled. CDK owns Runtime/helper/tool/build log groups and
Gateway/Memory TRACES + APPLICATION_LOGS delivery chains with one-week retention.
No sample code changes account Transaction Search. Optional identity-only
deliveries are created after deployment by `enable_observability.py --allow-cloud`
using actual workload IDs and an ownership-tagged receipt journal. Their exact-ID
teardown runs before stack cleanup (or via `disable_observability.py`). Partial
creates/deletes refuse retry/adoption without journal review. Legacy provider
repair by name remains disabled.

All lifecycle paths use `scripts/lifecycle.py`; they require `--allow-cloud`,
verify ownership and propagate failures. Cleanup keeps state on partial deletion,
including scheduled Identity secret/KMS deletion. See the
[cleanup procedure](deployment.md#cleanup) and [migration limits](MIGRATION.md).
