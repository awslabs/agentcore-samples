# ADR-0001: AgentCore CLI for AgentCore Resources + CDK for Supplementary Infra

**Status:** Accepted
**Date:** 2026-06-24

## Context

This sample needs two categories of infrastructure:
1. **AgentCore resources** — Runtime, Gateway, PolicyEngine, Evaluator, OnlineEvaluationConfig. These are declared in `agentcore/agentcore.json` and managed by the AgentCore CLI.
2. **Supplementary infrastructure** — DynamoDB tables, an S3 inbox bucket, a Cognito user pool, SQS queues, AppConfig, CloudWatch alarms, EventBridge rules, and Lambda functions (the Gateway tools + the trigger, controller, and drain). The AgentCore CLI does not manage these.

The question: how do we deploy both categories together as a single, reproducible unit?

## Decision

Use the AgentCore CLI as the primary interface for AgentCore resources (declared in `agentcore/agentcore.json`). Use a TypeScript CDK app (`agentcore/cdk/`) for supplementary infrastructure. The CDK app lives inside the AgentCore project structure so that `agentcore deploy` synthesizes and deploys everything together as the single CloudFormation stack `AgentCore-ReceiptsAgent-dev`.

**How the pieces fit:**
- `agentcore/agentcore.json` — declares AgentCore resources (Runtime, Gateway + targets, PolicyEngine + policies, Evaluator, OnlineEvaluationConfig).
- `agentcore/cdk/lib/infra-construct.ts` — creates the supplementary AWS resources.
- `agentcore/cdk/lib/cdk-stack.ts` — the "glue": patches real Lambda ARNs over the `PLACEHOLDER_<TOOL>` Gateway targets, configures the Cognito JWT authorizer, and injects env (Cognito creds, Gateway URL, AppConfig ids, the resolved Runtime ARN) into the Lambdas and the Runtime.
- `agentcore deploy` — one command that runs CDK synth + deploy for the combined stack.

## Reasoning

The point of this sample is to show how to build a workflow-automation agent with the AgentCore CLI — the canonical developer flow: scaffold → configure → validate → dev → deploy. But a real agent needs surrounding infrastructure: data stores, an event trigger, auth, and (here) the AppConfig + alarm + controller machinery of the degradation ladder. CDK is the natural choice for that, and the AgentCore CLI already uses CDK under the hood. Placing the CDK app inside `agentcore/cdk/` means one `agentcore deploy` handles everything.

## Alternatives Considered

- **CDK only (no CLI):** functional, but loses the educational value of the AgentCore CLI workflow (`validate`, `dev`, `deploy`).
- **CLI only:** not possible — the CLI doesn't manage DynamoDB, S3, the Lambdas, Cognito, SQS, AppConfig, alarms, or EventBridge.
- **Separate stacks:** two CloudFormation stacks would complicate deployment, need cross-stack references, and force a deploy order.

## Consequences

Two configuration surfaces must stay in sync: `agentcore.json` (with `PLACEHOLDER_<TOOL>` ARNs) and `cdk-stack.ts` (which patches the real ARNs at synth time). Adding a tool means touching both. See [tutorial.md](../tutorial.md) for the step-by-step.

> **Hard-won note.** The CLI version used here (`0.19.0-preview`) has no `destroy` command — tear down with `aws cloudformation delete-stack`. And `cdk synth` / `cdk destroy` can silently no-op in some sandboxes, so the build runs the CDK app directly (`CDK_OUTDIR=cdk.out node dist/bin/cdk.js`), which is what `make synth` does.
