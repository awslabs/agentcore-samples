# Event-driven claims — coding context

Human docs: [README](README.md), [deployment](docs/deployment.md),
[architecture](docs/ARCHITECTURE.md), [migration boundaries](docs/MIGRATION.md).

## Migration contract

This is a **custom CLI 1.2.0 / @aws/agentcore-cdk 1.2.0 v1 app** (JSON version 2),
not a disposable generated scaffold. `bin/cdk.ts` uses `readAgentCoreProject`,
`resolveTargetStacks`, `transformAgentCoreJson`; `AgentCoreStack` preserves Infra
and uses typed runtime/gateway accessors. Do not run automatic scaffold migration
or overwrite `lib/`. Native v0 scaffold replacement would drop Infra.

Fresh stacks only: the local configuration generator reserves a unique project,
provider and resource prefix. Old targets, `.cli`, `.env` and Cognito state are
refused, not erased/adopted. Existing v0 resource continuity is NOT implemented.
External auth ownership lives in `.claims-v1.json`; CLI state stores exact returned
provider ARNs. Both files are retained after cleanup. Cloud is NOT RUN in offline
migration validation.

## Scenario and invariants

1. S3 (`claims-inbox/`) → EventBridge → Trigger Lambda → IAM/SigV4 Runtime. The
   initial HTTP 200 is acceptance, not proof of eventual persistence.
2. Sonnet Claims Processor can only access policy lookup + `submit_decision`.
   Haiku validator has only `submit_validation`. Missing structured decision
   rejects; missing validation/low confidence routes to review.
3. Phase 3 has no LLM call. It uses qualified `target___schema_tool` names and the
   actual Strands TypedDict `status`/`isError`/`content` result contract, with an
   explicit MCP session. Tool errors are nonfatal but never treated as a write.
   No escalation or approval/review notification after a failed claim create.
4. Lambda handlers return JSON text directly, not an HTTP envelope. Schemas live
   in `lambdas/schemas/`. Any new Lambda needs an explicit ARN mapping; unmapped
   targets fail synthesis instead of disappearing.
5. Runtime inbound IAM is distinct from Gateway CUSTOM_JWT. Runtime uses
   Identity-vault `requires_access_token(..., auth_flow='M2M')` for Cognito
   `agentcore/invoke`. No client secrets in env vars, state, argv or templates.
6. Gateway stays **ENFORCE**. Allow-all + targeted forbid at >=100000 uses
   `AgentCore::Action::"create-claim___create_claim"`; `IGNORE_ALL_FINDINGS`
   affects validation, not authorization. Verify service-level denial and no
   table effect directly, not from model prose.
7. Memory SEMANTIC `claims/{actorId}/facts` + SUMMARIZATION
   `claims/{actorId}/{sessionId}`, 90 days, SDK session manager and graceful
   degradation are retained. Custom evaluator and three 100%-sampled built-ins
   remain; do not remove them to fix schema errors.
8. Container/ARM64/Python3.12 and frozen `uv.lock`; never substitute CodeZip or
   older installed SDKs. Runtime model/Identity/Memory permissions are bounded by
   `runtime-boundary.ts`; the deployer and tool roles are separate.
9. Fresh test config suppresses SES/SNS and omits send grants, retaining tools and
   review persistence. Notification-enabled scenario is an explicit separately
   authorized opt-in, never part of migration tests.

## File responsibilities

- `agentcore/agentcore.json`: AgentCore declarations and runtime envVars.
- `agentcore/cdk/lib/infra-construct.ts`: three tables/GSIs, seven business
  Lambdas, inbox/EventBridge, SNS, DLQ/alarm. All names project/target-qualified.
- `cdk-stack.ts`: Lambda/JWT bindings, Runtime aliases, trigger invoke and outputs.
- `runtime-boundary.ts`: intersect generated Runtime IAM with sample needs.
- `observability.ts`: CFN-owned log groups/deliveries; no account Transaction
  Search changes. `scripts/identity_tracing.py` preserves optional workload-
  identity deliveries after deployment with tagged exact-ID receipts.
- `scripts/configure_target.py`: pure local fresh identity reservation.
- `scripts/lifecycle.py`: tagged dedicated Cognito/Identity, guarded CLI deploy,
  exact-ID cleanup. Partial/uncertain creates require journal recovery. Never
  sweep by names, swallow AccessDenied, force-retain to claim success, or erase
  state. Verify scheduled secrets/KMS and workload-identity residues too.
- `scripts/cloud_checks.py`: bounded direct auth/Cedar checks with actual DB
  correlation, gated by `--allow-cloud` and notification-free ownership.
- `scripts/test_e2e.py`: three IAM Runtime requests plus one S3 event, unique policy
  IDs, bounded stream reads/polls; no old-row/log/prose-only success oracle.

## Offline checks

```bash
npm --prefix agentcore/cdk ci --ignore-scripts
npm --prefix agentcore/cdk run build
(cd app/claimsagent && uv sync --frozen --no-dev --no-install-project --python 3.12)
app/claimsagent/.venv/bin/python tests/run_offline.py
node --require ./tests/offline.cjs tests/test_cdk_wiring.cjs
```

Tests block network and unstubbed AWS calls, use fake credentials and real locked
SDKs/Cedar/Lambda code with remote boundaries stubbed. Template synthesis is real,
with fake account/provider ARNs and actual source assets. Image `/ping` testing
must use `--network none`, fake AWS credentials and disabled OTEL exporters; never
invoke models just to test startup. Registry/PyPI downloads are not deployments.

CLI cloud commands and helpers are NOT offline. Use an explicit pinned binary,
a dedicated `CLAIMS_CLI_HOME` with `transactionSearch:false`, and `--skip-bootstrap`
through the lifecycle helper. See deployment.md. No Toolkit retirement, unrelated
sample changes, account configuration, email or cloud provisioning without
separate authorization.
