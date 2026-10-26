# Deployment

One command deploys everything — the AgentCore resources (Runtime, Gateway, Policy, Evaluations) and the supplementary AWS infra (DynamoDB, S3, Cognito, SQS, AppConfig, alarms, EventBridge, the Lambdas) — as a single CloudFormation stack ([ADR-0001](decisions/0001-agentcore-cli-plus-cdk.md)).

## Prerequisites

- The **`@aws/agentcore` CLI**, Node + TypeScript, Python 3.12 + `uv`.
- A **container engine** — Docker or [Finch](https://runfinch.com). The Runtime is a `Container` build ([ADR-0005](decisions/0005-container-build-over-codezip.md)); the image builds in the cloud (CodeBuild + ECR), so the engine is only needed locally to assemble the build context. Using Finch? `finch vm start` once, and `deploy.sh` auto-selects it (`CDK_DOCKER=finch`).
- The **four ladder global inference profiles** enabled in the account: `aws bedrock list-inference-profiles` should list `global.anthropic.claude-opus-4-8`, `...-opus-4-7`, `...-opus-4-6-v1`, `...-sonnet-4-6`. Copy the ids verbatim — the suffix convention is not uniform.
- AWS credentials for the target account (a dev account; the sample provisions real resources).

## Deploy

```bash
./deploy.sh us-west-2
```

This runs `agentcore deploy`: CDK synth + deploy of the combined stack `AgentCore-ReceiptsAgent-dev`, then seeds a sample user. It also enables CloudWatch **Transaction Search** best-effort (needed once per account for span search; takes ~10 min to become active). The slowest stage is the container build (a few minutes).

Confirm:

```bash
python3 scripts/test_invoke.py --region us-west-2 \
    --s3-uri s3://receipts-inbox-<account>-us-west-2/receipts/sample-receipt.png
```

## Tear down

```bash
./destroy.sh us-west-2     # aws cloudformation delete-stack, with DELETE_FAILED recovery; leaves nothing billable
```

> The CLI (`0.19.0-preview`) has no `destroy` command, so `destroy.sh` uses `aws cloudformation delete-stack`. A failed policy **create** can leave a `ROLLBACK_COMPLETE` stack that blocks the next deploy — delete it manually first (`aws cloudformation delete-stack`).

### Teardown & DELETE_FAILED recovery

The AgentCore control-plane resources (Runtime, Gateway, GatewayTarget, PolicyEngine, Evaluator) occasionally fail to delete on the first pass because of control-plane resource ordering, leaving the stack in `DELETE_FAILED`. `destroy.sh` handles this for you:

1. **Retry once.** Most ordering orphans are transient — a second `delete-stack` clears them. The script polls for the terminal status and retries automatically.
2. **Retain the stuck ones.** If specific resources are still stuck, the script re-issues the delete with `--retain-resources <LogicalId ...>` (valid only for a stack already in `DELETE_FAILED`). CloudFormation then deletes the stack and everything else — so nothing billable is left running — and keeps just those few resources.
3. **Report for manual cleanup.** The script prints each retained resource as `ResourceType → PhysicalResourceId` so you can remove the handful by hand.

Delete a retained resource with the matching control-plane call, for example:

```bash
# The physical id printed by destroy.sh is the resource's ARN/id.
aws bedrock-agentcore-control delete-gateway        --gateway-identifier <id>       --region us-west-2
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gw> --target-id <id> --region us-west-2
aws bedrock-agentcore-control delete-agent-runtime  --agent-runtime-id <id>         --region us-west-2
```

If teardown still can't complete automatically, inspect the failure reasons and remove what's left:

```bash
aws cloudformation describe-stack-events --stack-name AgentCore-ReceiptsAgent-dev --region us-west-2 \
  --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]" --output table
```

## Local inner loop

No container, no deploy:

```bash
agentcore dev --no-browser     # runs the agent directly
```

With AppConfig/Gateway env unset, the agent runs on the L0 default model with all features on — the ladder is a deployed-stack concern. Copy `.env.example` → `.env` and fill from the stack outputs to point local dev at deployed Gateway/Memory/AppConfig.

## Automated end-to-end

`make e2e` (or `scripts/e2e.sh`) is a one-shot **real** deploy → assert-on-live → destroy that exits with the test result. It's how every phase of this sample was verified — no mocks. `make unit` runs the pure-function tests (no AWS); `make synth` builds + validates + runs the CDK app without creating resources.

## What gets created

The stack `AgentCore-ReceiptsAgent-dev` contains: an AgentCore Runtime + Gateway (5 Lambda targets) + PolicyEngine (2 Cedar policies) + Evaluator + OnlineEvaluationConfig; DynamoDB `ReceiptsAgent-Users`/`-Expenses`/`-Merchants`; an S3 inbox bucket (`receipts-inbox-<account>-<region>`, EventBridge-enabled); a Cognito M2M pool; SQS (`-L4Defer` + the trigger DLQ); AppConfig (app/env/profile + the ladder config); a `ModelStepDowns` alarm + EventBridge rules; and the trigger / controller / drain / tool Lambdas. All on-demand or serverless — `destroy.sh` removes it cleanly.
