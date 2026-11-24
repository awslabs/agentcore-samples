# Custom data-analyst CDK backend

This is the authoritative infrastructure source. `../agentcore/cdk` points here;
there is no second generated stack. Start with the [sample README](../README.md)
and [migration contract](../MIGRATION.md), not the former standalone deploy recipe.

The entrypoint uses CLI v1.2.0's `readAgentCoreProject`, `transformAgentCoreJson`
and `resolveTargetStacks`. A deliberately narrow adapter consumes Runtime and
Memory configuration. The remaining resources need CDK references and remain
owned by `cdklib/cdk-data-analyst-assistant-agentcore-strands-stack.ts`:

| Resource group | Contract |
|---|---|
| Runtime + endpoint | ARM64 Python/Strands image, HTTP/IAM transport, DEFAULT invocation plus named endpoint |
| Memory | 90-day events, semantic `Facts`, `/facts/{actorId}` |
| Gateway + Lambda target | IAM MCP, `DatabaseToolsTarget`, schema and SELECT query tools |
| Policy Engine + four policies | `ENFORCE`, permit baseline, prompt attack/content filtering/PII suppression definitions |
| Two evaluators | Original SQL accuracy TRACE and response quality SESSION instructions and 1–5 scales |
| Data | Aurora PostgreSQL 17.4/Data API, import bucket/CSV, read-only user Provider, DynamoDB results |
| Network | VPC, two AZs, public/private subnets, NAT, S3/DynamoDB endpoints, security groups |
| Observability | Runtime logs/traces, Memory/Gateway logs, four deliveries, two-week retention |
| Outputs | Existing frontend/evaluator outputs plus v1 Runtime/Memory export-name contracts |

Use **npm** here (`npm ci`, `npm run build`); exact dependencies are locked in
`package-lock.json`. The frontend has a separate pnpm lock. No global CDK install
is needed. The old SDK resource deployer no longer exists: `../deploy.py` only
configures a fresh target and refuses old state.

The CDK Provider now owns all database initialization. Its handler returns the
Provider onEvent result and propagates failures; it must not PUT a second
CloudFormation response itself. Updates do not re-import nonempty tables and no
clock-based synth property forces a reload. The manual database scripts remain
historical utilities, not deployment steps.

Cognito remains Amplify-owned. Supply `COGNITO_USER_POOL_ID` and
`COGNITO_CLIENT_ID` from the first auth deployment before targeted synthesis.
Runtime verifies the ID token independently from its IAM transport auth.

Run tests from the sample root. `node tests/stack-invariants.cjs` synthesizes the
real stack with denied networking and synthetic account context. This is not a
Docker build, asset publication, policy-service validation or database execution.
The CloudFormation/Cedar guardrails-in-policy feature requires regional service
support; do not remove policies to force deployment through an unsupported region.
