# Custom CLI 1.2.0 CDK application

Do not replace this scaffold with a generated one. It preserves the incident
workflow's infrastructure, bindings, tools, optional Jira and observability.

- `bin/cdk.ts`: reads the native v2 project, requires one target and checks the
  fresh reservation/owner binding (or explicit offline-preview mode).
- `lib/cdk-stack.ts`: patches Lambda ARNs before v1 `AgentCoreApplication`, adds
  parent Memory read, custom runtime grants, Jira lifecycle and bounded readiness.
- `lib/infra-construct.ts`: scenario tables, buckets, KB, Guardrail, tools,
  trigger, DLQ, event bus, seeder and alarms.
- `test/cdk.test.ts`: schemas, optional configurations and synthesized contracts.

```bash
npm ci --ignore-scripts
npm run build
# From the sample root, prepare scripts/package_sdk.py first (see main README).
npm test -- --runInBand
IT_SAMPLE_SYNTH_ONLY=true npm run cdk -- synth --no-lookups --telemetry=false --notices=false
```

Preview is not a deploy authorization. Use `scripts/deploy.sh` from the sample
root with explicit project/target/account/region; it performs CLI pre-deploy
identity setup only after reserving and verifying a new StackId. No `.env` is
automatically sourced. [Commands](../../docs/commands-reference.md) and
[fresh-only lifecycle](../../docs/cli-v1-migration.md).

The locks pin CLI/CDK 1.2.0 and the complete npm graph. Audit findings remain
review work; tests and cdk-nag suppressions are not acceptance.
