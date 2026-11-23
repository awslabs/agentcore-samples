# Banking temporal-policy sample (CLI v1)

Read README.md and DEPLOYMENT.md before changing this sample. Use the pinned CLI
through `npm run --silent agentcore -- --help` and command help for its syntax.
The launcher confines CLI preferences to this project; do not bypass it with a
global CLI or reuse commands from an old v0 checkout.

## Ownership and invariants

- Scope is a fresh-checkout v1 port, not an existing-stack upgrade. Populated v0
  migration and physical/logical-ID continuity are NOT RUN; preserve real state.
- `agentcore/agentcore.json` is schema version 2. `migratedFrom: "v1"` means the
  old schema version, not the current CLI: preserve it and all resource names.
- This is intentionally an empty checked-in scaffold. DEPLOYMENT.md Step 2 registers
  the existing Python MCP code with `agentcore add runtime` and separately adds
  OAuth credentials using `agentcore add credentials oauth`. First reserve a
  unique tagged provider with `oauth_provider.py`; verify `--reuse-owned` before
  every credential-bearing deploy. Keep its durable ledger. Unowned/legacy
  provider reuse and rotation are unsupported; CLI upsert by name is not an
  ownership check. Do not replace
  `app/banking_assistant_tools/main.py` with a generated agent template.
- CDK uses `readAgentCoreProject`, `resolveTargetStacks`, and
  `transformAgentCoreJson` from the locked @aws/agentcore-cdk 1.2.0. Custom CDK is owned source;
  inspect it before regenerating. Preserve StackNameOutput and the MCP Runtime
  permissions boundary (one code artifact, name-ID-delimited log writes,
  regional telemetry; no model/identity grants). Keep the explicit instrumented
  entrypoint `opentelemetry-instrument ./main.py`, executable Python shebang and
  locked ADOT patch extra.
  The optional existing bootstrap KMS key is constrained by S3 service/context.
- setup.py owns a separate gateway, IAM role, policy engine, MCP target and ten
  base permits. Do not duplicate those resources in CDK. Keep target
  `banking-assistant-tools`, both MCP protocol versions, JWT authentication,
  OAuth scope `api/mcp`, streaming and temporal sessions.
- Never base-permit transfer_funds, load_portfolio, rebalance_portfolio or
  execute_trade. Their temporal permits are the authorization path.
- Gateway ENFORCE blocks denied requests; LOG_ONLY does not. Policy/engine
  lifecycle status ACTIVE is readiness, not per-policy enforcementMode ACTIVE.
  Keep both enforcement checks. Use requirements-control.txt and policy_contract.py
  before provisioning: exact published SDK models, no HOME/AWS_DATA_PATH shadowing.
  Check actual configured/default API selection, then bind control clients to the
  same verified botocore session and explicit version; a fixed-directory check alone
  is insufficient. Direct helpers also reject divergent real-client models.
  Never inject models, bypass validation or alter global models to force a pass.
  Use definition.cedar for portable plain statements; accept the published policy
  wrapper on reads only when its exact statement matches. Older AWS CLI parsers
  can drop enforcementMode: use the checked SDK get-policy read in DEPLOYMENT.md.
  Tests must serialize requests and parse genuine wire responses, not echo input
  dictionaries as service output. Do not claim a ready engine, logged denial,
  mock response or local test proves enforcement.
- The client prompt deliberately does not add prerequisite tool calls. Preserve
  that behavior and the signed trade cost test input; do not redesign tool
  workflows under the guise of migration. Human approval is simulated, not
  authenticated. Tool errors are distinct from policy DENY.
- Run one setup/cleanup process at a time. Never discard durable create intents
  or regenerate their tokens on retry. IAM recovery requires the original intent
  and unique ownership tag. Cleanup must finish read-only identity/ownership and
  relationship checks before deleting anything, including in partial-create cases.
  Unresolved parent AND target/base-policy intents block cleanup until recovered.
- Setup's separate Gateway role uses exact engine/Gateway/provider/secret and
  returned workload-identity ARNs, with only the pinned OAuth directory/vault root
  exceptions. Include CheckAuthorizePermissions from the pinned v1 contract.
  Reapply exact remote trust after immutable ownership validation on every retry;
  trust_scoped is historical state, not remote proof.
- Base policy IDs must be recorded, awaited and checked for exact definition and
  ACTIVE enforcement on retry/conflict; READY targets must match endpoint/auth.
- Keep CLI deployment state, setup_config.json and secrets out of source control.
  Never clear state to bypass a migrator guard or hide failed cleanup. Existing
  deployments need native migration/physical-ID review, not a fresh-state deploy.

## Local checks

```bash
npm ci --prefix agentcore/cdk
npm test --prefix agentcore/cdk
# First follow DEPLOYMENT.md's hash-locked control environment and model preflight.
banking_control -m unittest discover -s tests -v
```

Tests stub SDK calls and use fake credentials; never run setup.py or cleanup.py
against AWS merely to test imports. `agentcore build` packages local Runtime
artifacts but is not a substitute for schema, synthesis and negative tests.
`agentcore status --target default --json` reports `resources` with resourceType,
deploymentState and arn; use runtime_access.py rather than old invocationUrl or
deployedState JSON paths.

Deploy/remove/invoke require explicit cloud authorization. Follow DEPLOYMENT.md's
separate setup-resource, CLI-resource, retained-provider and Cognito cleanup.
Migrated OAuth providers may be shared and are not implicitly deleted by CLI
resource removal. Never broaden IAM or reset shared bootstrap/observability to
make a deployment pass.
