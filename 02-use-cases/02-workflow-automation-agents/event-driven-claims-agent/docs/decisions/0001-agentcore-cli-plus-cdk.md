# ADR-0001: AgentCore CLI for AgentCore Resources + CDK for Supplementary Infra

**Status:** Accepted; implemented as a custom CLI 1.2.0 v1 port for fresh stacks only (see [MIGRATION.md](../MIGRATION.md)). Cloud verification is NOT RUN.
**Date:** 2025-06-17

## Context

This sample needs two categories of infrastructure:
1. **AgentCore resources** — Runtime, Gateway, Memory, PolicyEngine, OnlineEval. These are managed by the AgentCore CLI and declared in `agentcore.json`.
2. **Supplementary infrastructure** — DynamoDB tables, S3 bucket, SNS topics, Cognito user pool, EventBridge rules, Lambda functions. The AgentCore CLI does not manage these.

The question: how do we deploy both categories together as a single, reproducible unit?

## Decision

Use the AgentCore CLI as the primary interface for AgentCore resources (declared in `agentcore/agentcore.json`). Use a TypeScript CDK app (`agentcore/cdk/`) for supplementary infrastructure. The CDK app lives inside the AgentCore project structure so that `agentcore deploy --target dev` synthesizes and deploys everything together as the single CloudFormation stack `AgentCore-<project>-dev` (the v1 generator chooses a unique `Claims<suffix>` project).

**How the pieces fit:**
- `agentcore/agentcore.json` — declares AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval)
- `agentcore/cdk/lib/infra-construct.ts` — creates supplementary AWS resources (DynamoDB, Lambda, S3, SNS, EventBridge); Cognito is script-managed outside CDK
- `agentcore/cdk/lib/cdk-stack.ts` — wires Lambda ARNs into Gateway targets, configures the JWT authorizer and injects Gateway URL/provider name (never client secrets)
- `agentcore deploy --target dev` — one command that runs CDK synth + deploy for the combined stack

## Reasoning

The custom app now uses `readAgentCoreProject`, `resolveTargetStacks`, `transformAgentCoreJson` and typed v1 accessors. The ownership-checking `deploy.sh` invokes `agentcore deploy --target dev --skip-bootstrap` for the combined, uniquely named stack. Do not replace this app with the generated scaffold.

But a real agent needs surrounding infrastructure — data stores, event triggers, auth. CDK is the natural choice for AWS infrastructure-as-code, and the AgentCore CLI already uses CDK under the hood. Placing the CDK app inside `agentcore/cdk/` means `agentcore deploy` handles everything — developers don't need to run two separate deploy commands.

## Alternatives Considered

- **CDK only (no CLI):** Would create a functional stack but miss the educational value of demonstrating the AgentCore CLI workflow (`validate`, `dev`, `deploy`).
- **CLI only:** Not possible — the CLI doesn't manage DynamoDB, S3, Lambda tools, Cognito, or EventBridge.
- **Separate stacks:** Two CloudFormation stacks (one for AgentCore, one for infra) would complicate deployment, require cross-stack references, and force the developer to deploy in the right order.

## Consequences

Two configuration surfaces must stay in sync:
1. `agentcore.json` — AgentCore resource declarations (including PLACEHOLDER ARNs for Lambda targets)
2. `agentcore/cdk/lib/cdk-stack.ts` — patches real Lambda ARNs over the placeholders at synth time, and injects provider name + Gateway URL (not secrets) into the Runtime environment

If you add a new Lambda tool, you update both: add the target in `agentcore.json` (with a PLACEHOLDER) and add the ARN patching in `cdk-stack.ts`. See [tutorial](../tutorial.md#experiment-3-add-a-read-only-tool) for the step-by-step process.
