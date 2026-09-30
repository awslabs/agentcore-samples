# Deployment

One command deploys everything as a single CloudFormation stack ([ADR-0001](decisions/0001-agentcore-cli-plus-cdk.md)):
- the AgentCore resources: two Runtimes, the Gateway, the Cedar policy engine, the evaluators and a live evaluation config
- the supplementary AWS infrastructure: DynamoDB, S3, Cognito, SQS, AppConfig, alarms, EventBridge, KMS and the Lambdas

## Prerequisites

- The **`@aws/agentcore` CLI**, Node + TypeScript, Python 3.12 + `uv`.
- **No local container engine.** The Runtimes are `Container` builds ([ADR-0005](decisions/0005-container-build-over-codezip.md)), and the images are built in AWS CodeBuild from the uploaded source.
- The **four ladder global inference profiles** enabled in the account: `aws bedrock list-inference-profiles` should list `global.anthropic.claude-opus-4-8`, `...-opus-4-7`, `...-opus-4-6-v1`, `...-sonnet-4-6`. Copy the ids verbatim — the suffix convention is not uniform.
- AWS credentials for the target account (a dev account; the sample provisions real resources).

## Deploy

```bash
./deploy.sh us-west-2
```

This runs `agentcore deploy`: CDK synth + deploy of the combined stack `AgentCore-ReceiptsAgent-dev`, then applies the chat live-evaluation config with `scripts/chat_online_eval.py` (it uses managed third-party evaluators, which the CloudFormation schema does not accept yet), then seeds a sample user. It also enables CloudWatch **Transaction Search** (needed once per account for span search; online evaluation reads spans from the `aws/spans` log group it creates; takes ~10 min to become active). The slowest stage is the two Runtime image builds in CodeBuild.

Confirm:

```bash
python3 scripts/test_invoke.py --region us-west-2 \
    --s3-uri s3://receipts-inbox-<account>-us-west-2/receipts/sample-receipt.png
```

## Tear down

```bash
./destroy.sh us-west-2
```

It deletes the chat live-evaluation config first, then runs `aws cloudformation delete-stack` with DELETE_FAILED recovery, and leaves nothing billable.

### Teardown and DELETE_FAILED recovery

The AgentCore control-plane resources (Runtime, Gateway, GatewayTarget, PolicyEngine, Evaluator) occasionally fail to delete on the first pass, because of control-plane resource ordering, leaving the stack in `DELETE_FAILED`. `destroy.sh` handles this:

1. **Retry once.** Most ordering orphans are transient, and a second `delete-stack` clears them.
2. **Retain the stuck ones.** If specific resources are still stuck, it re-issues the delete with `--retain-resources <LogicalId ...>`. CloudFormation then deletes everything else, so nothing billable is left running.
3. **Report for manual cleanup.** It prints each retained resource as `ResourceType -> PhysicalResourceId`.

Delete a retained resource with the matching control-plane call, for example:

```bash
aws bedrock-agentcore-control delete-gateway        --gateway-identifier <id>       --region us-west-2
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier <gw> --target-id <id> --region us-west-2
aws bedrock-agentcore-control delete-agent-runtime  --agent-runtime-id <id>         --region us-west-2
```

If teardown still cannot complete, inspect the failure reasons:

```bash
aws cloudformation describe-stack-events --stack-name AgentCore-ReceiptsAgent-dev --region us-west-2 \
  --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]" --output table
```

## Local inner loop

No container, no deploy:

```bash
agentcore dev --no-browser     # runs the agent directly
```

With AppConfig/Gateway env unset, the agent runs on the L0 default model with all features on — the ladder is a deployed-stack concern. Copy `.env.example` → `.env` and fill from the stack outputs to point local dev at the deployed Gateway/AppConfig.

## Automated end-to-end

`make e2e` (or `scripts/e2e.sh`) is a one-shot **real** deploy, then assertions against the live stack, then destroy, exiting with the test result. `make unit` runs the tests that need no AWS. `make synth` builds and synthesizes the CDK app without creating resources.

## What gets created

The stack `AgentCore-ReceiptsAgent-dev` contains:
- **AgentCore:**
  - two Runtimes (pipeline and chat), with their CodeBuild image builders and ECR repositories
  - the Gateway with 5 Lambda targets
  - the PolicyEngine with 2 Cedar policies
  - three code-based evaluators (one Lambda each)
  - the `ReceiptsLive` online evaluation config, plus the execution role for the chat config
- **DynamoDB:** `ReceiptsAgent-Users`, `-Expenses`, `-Merchants` and `-ProcessingRuns`.
- **Storage and identity:**
  - an S3 inbox bucket, `receipts-inbox-<account>-<region>`, with EventBridge enabled
  - a Cognito M2M pool with a domain
  - a KMS HMAC identity key
- **Queues and events:**
  - SQS: `-L4Defer` and the trigger DLQ
  - EventBridge: a run-ledger event bus and rules, and the `ModelStepDowns` alarm
  - an SNS error topic
- **The ladder:** AppConfig (application, environment, profile, strategy) holding the ladder config.
- **Lambdas:** trigger, controller, drain, ledger writer and the five tools.

Everything is on-demand or serverless, and `destroy.sh` removes it.
