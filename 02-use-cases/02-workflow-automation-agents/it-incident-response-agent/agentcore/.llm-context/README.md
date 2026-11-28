# LLM Context Files

These `.ts` files are **historical v0 snapshots**, not the current schema. Do not
copy their types into config-generating scripts. CLI 1.2.0 uses project version 2;
the authoritative installed schema is `@aws/agentcore-cdk` 1.2.0 under
`agentcore/cdk/node_modules/@aws/agentcore-cdk/dist/schema/`. Run
`npm test -- --runInBand` in `agentcore/cdk` to check this sample's real config
and full custom stack. See `../../docs/cli-v1-migration.md` before upgrades.

## Files

| File             | JSON Config        | Purpose                                   |
| ---------------- | ------------------ | ----------------------------------------- |
| `agentcore.ts`   | `agentcore.json`   | Project, agent, memory, credential config |
| `aws-targets.ts` | `aws-targets.json` | Deployment targets (account + region)     |

## Usage

For current commands, use `agentcore <command> --help` with CLI 1.2.0 and
`../../docs/commands-reference.md`. Do not regenerate or replace the custom CDK
stack with a stock CLI scaffold; it owns the sample's additional resources.
