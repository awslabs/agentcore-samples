# A customer-support agent on AgentCore Runtime

Run a small Strands agent locally, then deploy the same Python code with **AgentCore CLI v1**.
It answers questions about five fictional products and three return policies. Its two tools
only read dictionaries: they cannot access customer records, change orders, or issue refunds.
This is a learning example, **not a production customer-support system**.

```text
Your terminal → AgentCore Runtime (IAM-authenticated) → Python agent → Amazon Bedrock
                                                        ↓
                                            product and policy lookups
```

Each request is independent: no conversation history is retained, even with a reused Runtime
session ID. The entrypoint constructs a fresh Agent, validates input and filters model output.

## Prerequisites

- A **sandbox AWS account** and an approved short-lived AWS profile; AWS CLI v2.
- Node.js **20.12+** (supported LTS recommended), npm and Git.
- Python **3.13**, managed by [uv](https://docs.astral.sh/uv/getting-started/installation/).
- A Region with AgentCore Runtime CodeZip support and on-demand **Amazon Nova Lite v1** access
  (`amazon.nova-lite-v1:0`); start with `us-west-2`.

Local and cloud model invocations incur Bedrock charges; deployments create billable resources.
Read [cleanup](#cleanup) before deploying. Use fictional data only.

## 1. Install and test

```bash
git clone https://github.com/awslabs/agentcore-samples.git
cd agentcore-samples/00-getting-started
npm install --global @aws/agentcore@1.0.3
agentcore --version  # expect 1.0.3
uv python install 3.13
uv sync --project app/agent --frozen
npm ci --prefix agentcore/cdk --ignore-scripts
uv run --project app/agent --frozen pytest
uv run --project app/agent --frozen ruff check .
uv run --project app/agent --frozen ruff format --check .
```

To avoid replacing another CLI installation, use
`npx --yes --package=@aws/agentcore@1.0.3 agentcore` instead of `agentcore` below.
Do **not** install the repository-root `requirements.txt`: other samples use the legacy Starter
Toolkit's conflicting executable. The `bedrock-agentcore` Python runtime SDK is still required.

Tests exercise the actual Strands tool loop and AgentCore HTTP adapter with scripted responses,
not live inference. Infra/ZIP checks skip without a build; native context tests are opt-in.
See [repeatable checks](DEPLOYMENT.md#repeatable-checks); a source-only pass is not a full build pass.

| File | Purpose |
|---|---|
| [`app/agent/main.py`](app/agent/main.py) | Fictional catalog/policies, two tools, model and request handler |
| [`app/agent/pyproject.toml`](app/agent/pyproject.toml), `uv.lock` | Python requirements and locked graph |
| [`agentcore/agentcore.json`](agentcore/agentcore.json) | One HTTP Runtime, `support`, using Python 3.13 CodeZip |
| [`agentcore/cdk/lib/cdk-stack.ts`](agentcore/cdk/lib/cdk-stack.ts) | CDK application and execution-role boundary |
| [`agentcore/cdk/cdk.json`](agentcore/cdk/cdk.json) | Persistent asset KMS context; review before deployment |
| [`tests/`](tests/) | Application, real SDK retry-path and infrastructure regressions |

## 2. Run locally

In **each** terminal that calls AWS, select and verify your sandbox identity:

```bash
export AWS_PROFILE=your-sandbox-profile
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION="$AWS_REGION"
aws sso login --profile "$AWS_PROFILE"  # or your organization's login mechanism
aws sts get-caller-identity
```

Never put access keys in source, JSON or `.env` files. Local inference uses your profile's
Bedrock permissions; the deployed agent uses its own execution role.

In terminal A, from the sample directory:

```bash
agentcore dev --agent support --mode headless --no-traces
```

Leave it running on port 8080. The local endpoint is **unauthenticated**: keep it on loopback,
never behind a tunnel or on a shared host. Browser `Origin` headers and non-JSON POSTs are
rejected to stop websites/forms abusing local credentials; this is not end-user authentication.

In terminal B, from the same directory:

```bash
agentcore invoke --runtime support --local \
  --payload '{"prompt":"What is the price of PROD-002 in USD? Use the product tool."}'
agentcore invoke --runtime support --local \
  --payload '{"prompt":"For PROD-002, what is the return window and does it require original packaging? Look up both product and policy."}'
```

Check facts, not exact phrasing: **Smart Watch, $249.99**, category **electronics**, **30 days**,
original packaging, unused or defective, full refund to the original payment method.
The second question should use both lookups; model wording and tool choices can vary.

```bash
curl --fail-with-body http://127.0.0.1:8080/invocations \
  -H 'Content-Type: application/json' -d '{"prompt":""}'
```

Expect HTTP **400**, an explanation and a nonzero curl exit, without a model call.
Stop terminal A with **Ctrl-C** when finished.

## 3. Build, deploy and invoke

**Mandatory before deployment:** read [DEPLOYMENT.md](DEPLOYMENT.md) for account/bootstrap and
Transaction Search approval, persistent KMS configuration, saved targets and IAM boundaries.
Do not bypass these checks or grant AdministratorAccess to follow the tutorial.

```bash
agentcore build --json
uv run --project app/agent --frozen pytest
```

The build packages Linux ARM64 dependencies and synthesizes CloudFormation; it does not deploy.
Review `agentcore/cdk/cdk.out/*.template.json`: expect one Runtime, role/policy and permissions
boundary, possibly CDK metadata—not a Gateway, Memory, CodeBuild project, NAT or customer store.
The post-build tests verify the current source bytes and native architecture inside the ZIP.

Confirm your account with `aws sts get-caller-identity` again. Never deploy over a project you
do not own. If `CustomerSupport` is taken, change only the project `name` in
`agentcore/agentcore.json`: alphanumeric, starts with a letter, at most **23 characters**.
Keep Runtime name `support`; the reviewed boundary binds to it.

```bash
agentcore deploy --region "$AWS_REGION"
agentcore status --json
agentcore invoke --runtime support \
  --payload '{"prompt":"What is the price of PROD-002 in USD? Use the product tool."}'
agentcore invoke --runtime support \
  --payload '{"prompt":"For PROD-002, what is the return window and does it require original packaging? Look up both product and policy."}'
agentcore logs runtime --name support --since 10m --limit 20
```

Wait for Runtime readiness before invoking. Check the same product/policy facts and fixed tool
completion logs. Cloud invocation is **IAM-authenticated**; `PUBLIC` means public **outbound**
networking, not anonymous inbound access. No public API or JWT authorizer is created.

The sample bounds prompts to 2,000 characters, four logical model steps and a 60-second deadline.
Botocore allows one retry per step (at most **eight transport attempts**); Strands retries stay
disabled. Output filtering and safe logs are intentional safeguards, not production guarantees.
See [limits and permissions](DEPLOYMENT.md#security-boundary-and-limits) before customization.

## Cleanup

**Only inside this dedicated sample project**, using its original target/account:

```bash
agentcore remove all --yes
agentcore deploy --yes
agentcore status --json
```

`remove all` edits local configuration; `deploy` applies removal and deletes the empty stack.
The boundary is omitted when `support` is removed. Confirm stack deletion and Runtime absence.
Shared `CDKToolkit` resources remain, and uploaded ZIPs/service-created logs may remain too.
Follow [artifact cleanup](DEPLOYMENT.md#cleanup-and-retention); never empty a shared bucket or
delete shared bootstrap resources. Use a fresh checkout to rerun after removal.

## Troubleshooting and next steps

- Wrong flags? Confirm CLI **1.0.3**, not the Toolkit executable earlier in `PATH`.
- HTTP 400? Send only `{"prompt":"non-empty text"}`, at most 2,000 characters.
- Model failure? Check profile/Region/model access locally, role and boundary in AWS.
- HTTP 504? Check service health before retrying; retries cost money.
- Port busy? Use `agentcore dev --agent support --mode headless --port 8081 --no-traces`;
  add `--port 8081` to local invokes.
- Deployment failure? Inspect CloudFormation events; do not bypass IAM controls.
- Audits or model changes? See [DEPLOYMENT.md](DEPLOYMENT.md), not a blanket `Resource: "*"` grant.

Continue with [AgentCore CLI](https://github.com/aws/agentcore-cli),
[Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
and [Strands](https://strandsagents.com/). Persistence or real business actions need separate
authorization, threat assessment and tests—not silent expansion of this example.
