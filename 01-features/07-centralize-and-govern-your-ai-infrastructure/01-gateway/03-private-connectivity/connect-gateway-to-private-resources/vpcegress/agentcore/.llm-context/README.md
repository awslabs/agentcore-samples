# CLI v1 configuration reference

These labs use CLI/CDK **1.0.3**, with `agentcore.json` schema **version 2**.
The installed `@aws/agentcore-cdk` package in `../cdk/node_modules/` is the
source of truth for types and validation. The old copied v0 type definitions
were removed because they described unsupported fields and commands.

- `AgentCoreProjectSpecSchema` validates project JSON.
- `AwsDeploymentTargetsSchema` validates `aws-targets.json`.
- `readAgentCoreProject`, `resolveTargetStacks` and `transformAgentCoreJson`
  load and translate configuration in the CDK app.
- `npm test` from `agentcore/cdk/` validates and synthesizes offline fixtures.

Use `agentcore add runtime --help`, `agentcore add gateway --help` and
`agentcore add gateway-target --help` before editing commands. Inbound JWT
configuration does not create an outbound OAuth provider. Declare only the
outbound credentials a tutorial actually needs. Do not put secrets in JSON.

`migratedFrom: "v1"` means the **old schema version**, not CLI v1. Keep it:
it preserves v0 resource naming, including the long runtime names in these labs.
Do not clear real deployment state or remove this marker on deployed copies.
Do not drop SDK-managed features to make a config pass the CLI schema.
