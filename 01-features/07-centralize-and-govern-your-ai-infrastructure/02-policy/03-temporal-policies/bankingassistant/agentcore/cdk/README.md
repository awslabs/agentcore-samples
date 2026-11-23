# Banking AgentCore CDK application

Uses the locked @aws/agentcore-cdk **1.2.0**. From bankingassistant/:

```bash
npm ci
npm ci --prefix agentcore/cdk
npm test --prefix agentcore/cdk
```

`bin/cdk.ts` reads v1 project config and resolves target stacks, including recorded
credential ARNs. `lib/cdk-stack.ts` creates the application and preserves the
legacy StackNameOutput. It also applies a permissions boundary limiting the MCP
Runtime to its own code artifact and name-ID-delimited log writes, with regional
X-Ray/CloudWatch telemetry and optional S3-mediated decryption for an existing
bootstrap KMS key. Log-group discovery is metadata-only and regional; metric
publishing is restricted to the bedrock-agentcore namespace. CDK uses the
explicit relative path `opentelemetry-instrument ./main.py`, and `main.py` has an
executable Python shebang (CodeConfiguration allows only two entrypoint items).
Instrumentation stays enabled and its dependencies are locked. The optional real packaging test
validates that actual launcher, distribution/module closure and ARM64 native tags,
not merely ZIP existence. This is custom CDK wiring: do not regenerate
it away. The gateway, policy engine, target and tutorial policies
remain managed by setup.py, not this stack.

Deploy through `npm run --silent agentcore -- deploy --target default --region "$REGION"`
only after following DEPLOYMENT.md's Cognito, Runtime and OAuth setup. The launcher
uses private CLI preferences for the tested CDK locks and skips automatic
account-level transaction-search setup without changing global settings. Do not run raw
CDK deploy instead: CLI packaging and credential provisioning are prerequisites.
This is a fresh-checkout v1 port, not a safe existing-stack upgrade. A populated
legacy-state migration/physical-ID comparison is NOT RUN. An existing deployment
requires separate native migration review; never delete state or
remove `migratedFrom` to force a deploy.
