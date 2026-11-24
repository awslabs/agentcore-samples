# CLI v1 custom-port contract

## Ownership, not a scaffold replacement

The v0 JSON was an invalid abbreviated inventory, not a deployable owner definition.
The authoritative source was always
`cdk-data-analyst-assistant-agentcore-strands/cdklib/cdk-data-analyst-assistant-agentcore-strands-stack.ts`.
The native migrator's refusal does not mean this source is absent. This port keeps
that stack and uses public **@aws/agentcore-cdk 1.2.0** config/target APIs.

- `agentcore/agentcore.json` is schema **2**, with complete Runtime/Memory specs.
  The shorter project identifier is required by v1's 23-character alphanumeric
  rule. It intentionally gives **new stacks**, not adoption of the legacy stack.
- `agentcore/aws-targets.json` is the required **array**, initially empty, not
  `{ "targets": [{ "accountId": "..." }] }`. No account/region is guessed.
- `agentcore/cdk` aliases the existing backend directory. One CDK app owns all
  resources. The Runtime code directory is resolved from the v1 project root.
- Runtime/Memory config is consumed by the custom adapter. Gateway, inline Lambda
  target, Policy Engine, four Cedar policies, both evaluator definitions, Aurora,
  S3/data loader, DynamoDB, network and all log deliveries stay **custom-CDK-owned**.
  Their empty manifest arrays do not mean they were removed from infrastructure;
  they must not be duplicated by `AgentCoreApplication` or generic CLI `add`.
- The custom app emits v1 Runtime ID/ARN and Memory ARN export names for CLI
  discovery, while preserving the existing frontend and evaluator output keys.
  The template preserves the original 84-resource inventory and adds one separate
  exact-resource IAM policy (**85 resources** total). Custom resources outside the manifest
  are inspected via CloudFormation outputs/service-level CLI commands.
- Generic `add/remove` mutations that this adapter cannot preserve are refused.
  In particular `remove all` is not the cleanup path. Destroy the exact custom
  stack only after reviewing its data-removal policies and resource ledger.

### Config/deploy writers

`deploy.py` is now a local, fresh-target-only configurator; its former SDK owner
created different/incomplete resources and is not retained as a deployment path.
It writes the project name, a v1 target and `fresh-port.json`. It refuses existing
state/targets before writing. CLI `build` also normalizes the Dockerfile field;
that field is explicit and accepted by the adapter. CLI `deploy` writes empty
credential state **before synthesis** and later a stack ARN. The guard explicitly
accepts that v1 lifecycle for a configured fresh marker, but rejects legacy
resource maps and different-stack ARNs. `setup-frontend.sh` only reads exact stack
outputs and writes frontend `.env.local`; it does not deploy or install packages.
Amplify owns Cognito and its output file, not the data stack. The data-loader
Provider owns database setup; the old manual database scripts are not setup steps.

## Fresh stacks only

Do not run this against an existing stack, copy legacy state into the project,
or delete state to make guards pass. The marker and environment acknowledgement
are operator intent, **not proof that a remote name is unused**. Before deployment,
the coordinator must independently inventory stack names/templates/physical IDs
and choose a unique test name. An in-place migration would require a separately
reviewed identity/template mapping; none is claimed here.

## Preserved scenario and necessary repairs

The active path remains Strands -> MCP Gateway -> Lambda -> RDS Data API, with
Cognito UI, STM/LTM, SQL results/charts and LLM evaluations. There is no active
Code Interpreter resource in this revision; none has been added or removed.

- Gateway uses `AWS_IAM`; each MCP POST/GET/DELETE gets a fresh SigV4 signature.
- IAM Runtime transport is augmented with independent Cognito ID-token verification.
  Verified subject owns Memory and results. Memory sessions hash actor + conversation
  UUID; request tracing retains the original session ID for evaluator correlation.
- Results are persisted only **after** Gateway returns a successful SQL tool result,
  using server-bound `actor#prompt_uuid`. Result size is bounded below DynamoDB's
  400 KiB item limit. SQL guards reject multiple/mutating statements, while the
  DB's SELECT-only grants and default read-only transactions remain authoritative.
- Frontend reads results/facts through authenticated Runtime operations, not broad
  database/Memory grants. Missing Runtime ARN no longer grants all runtimes.
- Runtime loses unused DB/admin-secret and ECR write permissions. ECR reads use
  the actual image asset repository, not a guessed bootstrap qualifier. Some
  inherited model/observability/Gateway-policy permissions remain broad and need
  deployment-role review; no AdministratorAccess or new blanket grant is added.
- The Provider returns its onEvent result and propagates failures. Data loading
  uses a stable version property, quotes generated SQL password literals, and
  grants only the sample table, not all future tables.
- Evaluation creation/list/batch/session use SDK 1.27.0 PREDEFINED scenarios with
  list-string assertions/trajectories and qualified Gateway tool names. The SDK,
  not the dataset documents, performs API reference-object conversion. Runtime
  accepts bounded 36–100 character frontend/runner session IDs without replacing
  their trace identity; Memory still hashes the verified actor plus that identity.
- Both judges and built-ins require terminal success and full eight-scenario/four-
  evaluator coverage, with zero invocation/server failures and no missing counters.
  Returned dataset IDs are durably journaled before waits/publication. Exact-ID
  recovery verifies ownership and modeled absence; failed/denied/timed-out cleanup
  is nonzero and cannot mask the original failure. `--keep-dataset` is explicit.
  The Runtime still rejects invented `user_id=evaluator` identities; the evaluator
  SDK and Strands extras remain pinned.
- Frontend streaming survives arbitrary split lines and UTF-8 bytes. Runtime
  errors are propagated without exposing tokens/internal SQL/service exceptions.

## Local validation and remaining gates

The source supports the commands in the README. Tests use fake credentials,
network denial, stubbed service clients and real local SDK/schema packages.
`tests/stack-invariants.cjs` checks the actual custom stack; `project-contract.cjs`
checks v1 shape/state/selection; `streaming.cjs` exercises every byte split;
Python tests check JWT negatives, SigV4, result ownership, SQL negatives, Provider
failure behavior, Runtime early rejection and evaluator argument wiring.

The frontend can compile with explicitly synthetic Amplify identifiers, but this
is **not** an auth integration test. An actual ARM64 image build requires a running
container daemon. No placeholder ZIP or incompatible installed SDK graph is a
substitute for a package/container build.

CLI 1.2.0 supports `transactionSearch:false` in a dedicated
`HOME/.agentcore/config.json`. Disable telemetry with `AGENTCORE_TELEMETRY_DISABLED=1`,
verify `agentcore config transactionSearch` prints `false`, and preserve that HOME
for deployment with `--skip-bootstrap`. Isolate AWS config/credential files and
IMDS; never copy credential stores into the dedicated HOME. See the README's
public configuration commands. No invented deploy flag or account-change approval
is needed; the CDK-only alternative remains valid.
The custom `when guardrails` Cedar definitions require regional service support;
local synthesis does not validate service grammar, schema or enforcement. The
full user prompt is not a Gateway tool argument: do not advertise these policies
as proof of filtering every user message. The preserved policies, actual input/
output shape and TRACE/SESSION evaluator behavior require bounded cloud checks.

## Coordinator cloud test and cleanup plan — NOT RUN

1. Approve a unique target, required deploy/runtime permissions, supported region,
   Haiku model access and existing bootstrap; verify public `transactionSearch:false`
   configuration so deployment does not enable account-level observability.
   Confirm the chosen stack name is unused. Do not alter shared bootstrap/IAM.
2. Deploy auth, build the real ARM64 image, synthesize/review the full template and
   deploy the exact fresh stack. Record every physical resource and asset. Confirm
   CSV row count, SELECT-only user, expected outputs and log deliveries. Finish
   the scoped Amplify Runtime binding.
3. Use two test users. Run one schema question, one limited aggregate and one chart.
   Verify rows in the UI and DynamoDB ownership; verify STM then second-session
   `/facts/{actorId}` retrieval after bounded extraction wait. Test split streaming.
4. Reject missing/expired/wrong-pool/wrong-client tokens, forged payload actors,
   cross-user prompt UUID/result reads and Memory reads, unauthenticated Gateway,
   direct browser Memory/DynamoDB requests, SQL writes/multiple statements/PII and
   cost fields. Stop on any unexpected access; do not widen grants to pass tests.
5. Check all four Gateway policies with their actual tool input/output schema in
   a supported region. Stop if syntax/schema/enforcement differs; do not remove
   a policy to label the migration successful. Score one trace/session with **both**
   custom judges before the full eight-scenario dataset batch.
6. Delete managed datasets and evaluation artifacts, only the test Amplify
   sandbox/hosting app, then the exact custom CDK stack. Verify Gateway targets,
   policies/engine, evaluators, endpoint/Runtime, Memory, deliveries/log groups,
   Lambda, Aurora/secrets, import S3 objects/bucket, DynamoDB and VPC/NAT deletion.
   Check test ECR asset retention separately without deleting shared bootstrap
   repositories. Remove token files. Reconcile the ledger and retain deletion
   evidence; shared account settings must remain unchanged unless separately approved.
