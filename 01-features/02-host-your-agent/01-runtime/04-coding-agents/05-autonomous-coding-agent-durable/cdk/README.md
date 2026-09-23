# CDK deployment — autonomous AgentCore coding agent

This `cdk/` directory stands up the **entire** system on a fresh AWS account with
`cdk deploy --all`. It is the CDK equivalent of the `deploy/*.sh` scripts, kept in
lock-step with them (notably the `poc/dh-gaps-swift-durable` branch: 4 runtimes,
AgentCore Memory, SSM runtime-ARN params, and the Lambda **Durable Function**
orchestrator). The 4th runtime, `cagent_evaluator`, is a **standalone review/evaluator
agent** — its OWN container image and its OWN least-privilege, read-only IAM role
(separate logs / cost / IAM from the coder), running Opus 4.8. It is no longer the
coding-agent image repurposed via a `REVIEW_MODE` flag. There are now **4 images**
(coding-agent, sandbox, sandbox-swift, evaluator), not 3. The only step CDK cannot do
inline is **building the ARM64 container images** — a one-time out-of-band step (see
"Build the images" below).

## Stacks (dependency order)

| Stack | Resources | Mirrors |
|---|---|---|
| `cagent-network` | VPC (2 AgentCore-supported AZs), 2 public + 2 private subnets, NAT, SG (self-ref NFS 2049 + HTTPS 443), 5 interface VPC endpoints (bedrock-agentcore, bedrock-runtime, ecr.api, ecr.dkr, logs) + S3 gateway endpoint | `deploy/10_vpc.sh` |
| `cagent-storage` | Versioned S3 bucket, S3 Files sync role, **native** `AWS::S3Files::FileSystem` + 2 `MountTarget`s + broad `AccessPoint` (rootDir `/work`, uid/gid 1000), demo ticket seed | `deploy/05_s3files.sh` |
| `cagent-build` | 4 ECR repos (coding-agent, sandbox, sandbox-swift, evaluator) + 4 CodeBuild projects (native ARM64) | `deploy/00_bootstrap.sh` (repos) + `deploy/20_build_push.sh` |
| `cagent-memory` | `AWS::BedrockAgentCore::Memory` (semantic strategy, namespace `lessons/{actorId}`) | `deploy/06_memory.sh` |
| `cagent-runtime` | Shared coder/sandbox exec role + **evaluator's own least-privilege read-only role** + **4** `AWS::BedrockAgentCore::Runtime` (coding_agent, sandbox, sandbox_swift, **evaluator**) + 4 SSM params `/<project>/runtime/<key>` | `deploy/00_bootstrap.sh` + `deploy/30_create_base_runtimes.sh` + `deploy/31_create_poc_runtimes.sh` |
| `cagent-orchestrator` | Lambda **Durable Function** (python3.13, `DurableConfig` at creation) + published version + EventBridge rule → the version + SNS topic + role (durable managed policy + app inline) | `deploy/41_durable_orchestrator.sh` |
| `cagent-monitoring` | CloudWatch alarms (errors, throttles) + dashboard | `deploy/redeploy_instrumented.sh` instrumentation |

`gateway_policy_stack.py` (AgentCore Gateway + Cedar) is **intentionally not wired**
into `app.py` — it was never part of the live shell-script deployment and is out of
PoC scope. Left in the tree for reference only.

## CFN-support findings (all native — no custom resources needed)

Every piece the old shell scripts drove via preview/boto3 shims is now a **native
CloudFormation resource type** (verified against the current CFN Template Reference,
June 2026):

- **S3 Files**: `AWS::S3Files::FileSystem | MountTarget | AccessPoint | FileSystemPolicy`
  were added to CloudFormation **2026-04-14** — *after* the live shell deploy, which is
  why `deploy/05_s3files.sh` had to use the `deploy/s3files_boto.py` boto3 shim (the API
  was not in the installed CLI). CDK uses raw `CfnResource` against the verified PascalCase
  schemas. No custom resource required.
- **AgentCore Runtime**: `AWS::BedrockAgentCore::Runtime` (native; the repo already learned
  this). `FilesystemConfigurations` accepts `S3FilesAccessPoint{AccessPointArn,MountPath}`
  and `SessionStorage{MountPath}`; `NetworkConfiguration{NetworkMode,NetworkModeConfig{Subnets,SecurityGroups}}`.
- **AgentCore Memory**: `AWS::BedrockAgentCore::Memory` (native), `EventExpiryDuration` is an
  Integer, `MemoryStrategies[].SemanticMemoryStrategy{Name,Namespaces}`.
- **Lambda Durable Functions**: native CDK L2 support — `aws_lambda.Function(durable_config=
  DurableConfig(execution_timeout=..., retention_period=...))` (synthesizes the `DurableConfig`
  property; requires aws-cdk-lib ≥ 2.258). The role attaches the AWS managed policy
  `service-role/AWSLambdaBasicDurableExecutionRolePolicy`. EventBridge targets the **published
  version** (durable functions must be invoked via a qualified ARN), not `$LATEST`.

There is **no residual script-only step** for infrastructure. The only out-of-band step is
image builds (which `cdk deploy` cannot run inline regardless).

## AZ constraint (important)

AgentCore Runtime VPC mode rejects subnets in unsupported Availability **Zone-IDs** (the
constraint is by zone-id, not zone-name, and the name→id mapping differs per account). On
the target account **123456789012** the live deployment runs in `us-east-1a` (use1-az2) +
`us-east-1b` (use1-az4) — both supported here (verified via the live subnets in
`deploy/config.env` and `aws ec2 describe-subnets … AvailabilityZoneId`). `network_stack.py`
pins those two AZ names. On a different account, override:

```bash
cdk deploy cagent-network -c agentcore_azs="us-east-1a,us-east-1d"
```

Pick AZ names that resolve to AgentCore-supported zone-ids on *your* account.

## Prerequisites

- Python 3.13 venv with `aws-cdk-lib>=2.258`, `boto3>=1.43`, `jsii`, and the
  `aws-durable-execution-sdk-python` package available to pip (the orchestrator Lambda is
  bundled locally — no Docker needed at synth time; falls back to the PYTHON_3_13 bundling
  image if local pip is unavailable).
- CDK CLI **≥ 2.1126** (the cloud-assembly schema for aws-cdk-lib 2.258 is v54).
- AWS credentials for the target account/region (us-east-1).

```bash
cd cdk
pip install -r requirements.txt
```

## Deploy sequence

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 1) Bootstrap the CDK environment (once per account/region)
cdk bootstrap aws://$AWS_ACCOUNT_ID/us-east-1 -c account=$AWS_ACCOUNT_ID

# 2) Stand up network + storage + build (ECR repos must exist before images push) + memory
cdk deploy cagent-network cagent-storage cagent-build cagent-memory \
  -c account=$AWS_ACCOUNT_ID --require-approval broadening

# 3) Build + push the 4 ARM64 images (the ONLY out-of-band step) — see below

# 4) Deploy the runtimes (need images in ECR), orchestrator, monitoring
cdk deploy cagent-runtime cagent-orchestrator cagent-monitoring \
  -c account=$AWS_ACCOUNT_ID --require-approval broadening
```

`cdk deploy --all -c account=$AWS_ACCOUNT_ID` works in one shot too, **provided the four
images are already in ECR** when the runtime stack creates the runtimes (the
`AWS::BedrockAgentCore::Runtime` resource pins the image at creation). On a truly fresh
account, deploy `cagent-build` first, push images, then `--all`.

### Build the images (out-of-band, ARM64 only)

The `cagent-build` stack creates a CodeBuild project per image. After it exists, upload the
build contexts and start the builds (e.g. via `scripts/build_images.sh`, which zips the
`coding-agent/`, `sandbox/`, and `evaluator-agent/` contexts to
`s3://<bucket>/build-artifacts/*.zip` and runs `aws codebuild start-build`). The swift
sandbox reuses the `sandbox/` context but builds `Dockerfile.swift`. Alternatively
build/push locally with `deploy/20_build_push.sh all`.

The evaluator runtime is a **standalone agent with its own image** (`evaluator-agent/`,
its own `Dockerfile`) running Opus 4.8 under a least-privilege read-only role — it no
longer reuses the coding-agent image and there is no `REVIEW_MODE` flag. **4 images total**
(coding-agent, sandbox, sandbox-swift, evaluator).

To point a runtime at a specific image URI/digest instead of `:latest`:

```bash
cdk deploy cagent-runtime -c account=$AWS_ACCOUNT_ID \
  -c coding_agent_image=<uri> -c sandbox_image=<uri> \
  -c sandbox_swift_image=<uri> -c evaluator_image=<uri>
```

### Seed the Swift demo repo (optional)

The storage stack seeds the demo ticket JSONs (`tickets-source/TICKET-1.json`,
`RAINBOW-1.json`). The sample source repo is **not** vendored or pre-seeded — each ticket
carries a `repo_url`, and the hydrate step `git clone`s it into the work dir inside the
sandbox on demand (real public repo). Nothing third-party lives in this repo or in S3.

## Fire a ticket

```bash
bash scripts/fire_ticket.sh RAINBOW-1     # EventBridge cagent.tickets / TicketCreated -> durable orchestrator
```

## Context / config

- `-c account=<id>` (or `CDK_DEFAULT_ACCOUNT`) — required.
- `-c region=<region>` (default `us-east-1`), `-c project=<prefix>` (default `cagent`).
- `-c agentcore_azs="az1,az2"` — override the two VPC AZs.
- `-c notification_email=<addr>` — subscribe an email to the SNS results topic.
- `-c coding_agent_image=… -c sandbox_image=… -c sandbox_swift_image=… -c evaluator_image=…` — pin image URIs.

## Verify it synthesizes

```bash
cdk synth -c account=$AWS_ACCOUNT_ID        # synthesizes all 7 stacks
```

(`cdk synth` does an `availability-zones` context lookup the first time — it needs read-only
AWS creds, or a pre-populated `cdk.context.json`.)
