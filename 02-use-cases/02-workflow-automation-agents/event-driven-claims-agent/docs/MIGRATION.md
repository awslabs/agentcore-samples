# CLI v1 migration boundary

## Implemented approach

This sample uses CLI **1.2.0**, `@aws/agentcore-cdk` **1.2.0**, and JSON schema
version **2**. It is a **preserving custom port**, not a version-only edit or a
forced native scaffold replacement. The previous native migrator refused the
custom app and would remove `InfraConstruct`; that guard is not a reason to
remove the infrastructure.

- `readAgentCoreProject` / `resolveTargetStacks` / `transformAgentCoreJson` replace
  legacy ConfigIO. One v1 `AgentCoreApplication` replaces the old application/MCP
  split. Typed `runtime()` and `gateway()` accessors replace tree searches.
- The custom Infra keeps three tables and GSIs, six tool Lambdas, Trigger Lambda,
  S3/EventBridge ingress, SNS topic, DLQ and alarm. Targets get exact Lambda ARNs;
  missing mappings fail synthesis.
- Container/ARM64, Sonnet/Haiku dual agents, deterministic Phase 3, Memory's
  two original strategies/90-day expiry, custom evaluator and three online
  built-ins at 100% sampling remain.
- Runtime IAM ingress is independent from Gateway CUSTOM_JWT/M2M. Secrets stay
  in Identity. A runtime boundary limits the broader generated role while
  preserving model, Memory, Identity, image and telemetry access.
- Gateway policy stays ENFORCE. Cedar now matches the qualified action name.
  Actual SDK result/error handling and qualified MCP calls prevent a failed
  create from being reported/notified as persisted. Phase 1 gets lookup only.

## Fresh stacks only — no invented continuity

**This is not an in-place migration of deployed v0 stacks.** A unique project
name scopes all fresh resource names, including custom resources and OAuth.
Existing target/CLI/Cognito/`.env` state is refused, even if it appears empty.
Do not delete or hand-edit state to bypass this refusal. Existing stacks and the
bare potentially shared provider must remain untouched until an independently
reviewed resource-ID/template/refactor plan exists.

The new lifecycle transfers the dedicated Cognito secret directly to a tagged
Identity provider, writes only returned ARNs into new CLI state and journals all
steps. It never claims that a fresh provider is the same identity as an old one.
CLI 1.2.0 can otherwise reuse/update providers by name, so use the ownership-
checking `deploy.sh` wrapper, with no credential overrides. A naked CLI deploy
is not a substitute for this safety preflight.

## Lifecycle and notification safety

`configure_target.py` is pure local configuration. All cloud helpers require
`--allow-cloud`, verify the account and refuse unowned collisions. The generated
configuration is notification-free: real SES/SNS handlers remain, but sending
and send IAM grants are disabled. No automatic seeding, bootstrap, email identity
verification, subscriptions or account observability changes.

Cleanup waits on the exact stack ARN, retains physical IDs, checks tags before
external deletion and propagates failures. It never scans by `claims` substrings,
retains failed resources to fake success, or erases state. Uncertain creates,
retained resources, scheduled secrets/KMS deletion and surviving workload
identities require explicit exact-ID recovery. See [deployment/cleanup](deployment.md#cleanup).

Runtime/Lambda/build logs and Gateway/Memory logs/traces are now CFN-owned.
The optional **identity-only** deliveries are preserved through
`enable_observability.py --allow-cloud`: it reads the owned Runtime/Gateway IDs,
preflights every delivery/log name, tags and journals exact returned IDs, and
never changes account Transaction Search. `disable_observability.py` and stack
cleanup delete only those receipts, checking ownership and absence. Uncertain
creates or partial deletes retain their journal and fail closed.

## Validation boundaries

Offline checks use exact locked npm/Python graphs, not an incompatible global
Strands installation. The tests exercise real v1 schema/construct synthesis,
actual source assets, real Cedar evaluation, actual SDK MCP result mapping and
Memory construction, real Lambda handlers and correlated in-memory effects.
Accounts/provider ARNs, models, JWT issuer, remote clients and persistence in
these tests are **fixtures**. No fixture ZIP/image substitutes for packaging.

The ARM64 Dockerfile is built from the real source with the frozen dependency
lock and digest-pinned base. A `/ping` smoke check runs under network isolation;
it does not invoke a model. These checks do not prove AWS authentication,
Cedar service context, asynchronous Runtime continuation, Memory service behavior,
evaluation ingestion or successful cloud cleanup. **Cloud is NOT RUN.**

Before integration/cloud testing, independently review the custom stack,
permissions boundary, lifecycle failure branches, service SDK contracts and
synthesized template. Then follow the bounded, notification-free procedure in
[deployment.md](deployment.md). Keep first failures and exact-ID cleanup evidence.
