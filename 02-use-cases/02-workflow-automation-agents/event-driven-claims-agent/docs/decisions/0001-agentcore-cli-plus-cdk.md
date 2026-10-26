# ADR-0001: AgentCore CLI for AgentCore Resources + CDK for Supplementary Infra

**Status:** Accepted  
**Date:** 2025-06-17

## Context

This sample needs two categories of infrastructure:
1. **AgentCore resources** — Runtime, Gateway, Memory, PolicyEngine, OnlineEval. These are managed by the AgentCore CLI and declared in `agentcore.json`.
2. **Supplementary infrastructure** — DynamoDB tables, S3 bucket, SNS topics, Cognito user pool, EventBridge rules, Lambda functions. The AgentCore CLI does not manage these.

The question: how do we deploy both categories together as a single, reproducible unit?

## Decision

Use the AgentCore CLI as the primary interface for AgentCore resources (declared in `agentcore/agentcore.json`). Use a TypeScript CDK app (`agentcore/cdk/`) for supplementary infrastructure. The CDK app lives inside the AgentCore project structure so that `agentcore deploy --target dev` synthesizes and deploys everything together as the single CloudFormation stack `AgentCore-ClaimsAgent-dev`.

**How the pieces fit:**
- `agentcore/agentcore.json` — declares AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval)
- `agentcore/cdk/lib/infra-construct.ts` — creates supplementary AWS resources (DynamoDB, Lambda, S3, SNS, Cognito, EventBridge)
- `agentcore/cdk/lib/cdk-stack.ts` — the "glue" that wires them together (patches Lambda ARNs into Gateway targets, configures the JWT authorizer, injects Cognito credentials into Runtime env vars)
- `agentcore deploy --target dev` — one command that runs CDK synth + deploy for the combined stack

## Reasoning

The purpose of this sample is to demonstrate how to build workflow automation agents with the AgentCore CLI. The CLI is the canonical developer workflow: scaffold → configure → validate → dev → deploy. Developers learning AgentCore should see this flow as the primary interface.

But a real agent needs surrounding infrastructure — data stores, event triggers, auth. CDK is the natural choice for AWS infrastructure-as-code, and the AgentCore CLI already uses CDK under the hood. Placing the CDK app inside `agentcore/cdk/` means `agentcore deploy` handles everything — developers don't need to run two separate deploy commands.

## Alternatives Considered

- **CDK only (no CLI):** Would create a functional stack but miss the educational value of demonstrating the AgentCore CLI workflow (`validate`, `dev`, `deploy`).
- **CLI only:** Not possible — the CLI doesn't manage DynamoDB, S3, Lambda tools, Cognito, or EventBridge.
- **Separate stacks:** Two CloudFormation stacks (one for AgentCore, one for infra) would complicate deployment, require cross-stack references, and force the developer to deploy in the right order.

## Consequences

Two configuration surfaces must stay in sync:
1. `agentcore.json` — AgentCore resource declarations (including PLACEHOLDER ARNs for Lambda targets)
2. `agentcore/cdk/lib/cdk-stack.ts` — patches real Lambda ARNs over the placeholders at synth time, and injects Cognito credentials + Gateway URL into the Runtime environment

If you add a new Lambda tool, you update both: add the target in `agentcore.json` (with a PLACEHOLDER) and add the ARN patching in `cdk-stack.ts`. See [docs/tutorial.md](../docs/tutorial.md#experiment-4-add-a-new-tool) for the step-by-step process.
