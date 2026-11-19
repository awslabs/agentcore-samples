# A customer-support agent on AgentCore Runtime

Run a small Strands agent locally, then deploy the same Python code with **AgentCore CLI v1**.
The agent answers questions about five fictional products and three return policies. Its two
tools only read dictionaries: they cannot access customer records, change orders, or issue refunds.

This is a learning example, **not a production customer-support system**. See
[VALIDATION.md](VALIDATION.md) for completed live checks and remaining toolchain findings.
Each request is an independent question. There is no conversation history, even when you reuse
a Runtime session ID.

```text
Your terminal → AgentCore Runtime (IAM-authenticated) → Python agent → Amazon Bedrock
                                                        ↓
                                            product and policy lookups
```

## Before you start

Use a **sandbox AWS account**. Deployments create billable resources, and both local and cloud
agent invocations incur Bedrock inference charges. Deployment time varies; the first deployment
also needs a CDK bootstrap environment. See [cleanup](#cleanup) before deploying.

You need:

- Node.js **20.12+** (a supported LTS release is recommended), npm, and Git.
- Python **3.13**, managed by [uv](https://docs.astral.sh/uv/getting-started/installation/).
- AWS CLI v2 and a short-lived authenticated AWS profile, for example
  [IAM Identity Center](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).
- An account/Region with AgentCore Runtime CodeZip support and on-demand access to
  **Amazon Nova Lite v1**, model ID `amazon.nova-lite-v1:0`. Start with `us-west-2`.

The code, model, SDKs, and dependencies are explicit; this tutorial does not depend on whichever
template or default model the CLI happens to generate today.

### Get the sample and CLI

```bash
git clone https://github.com/awslabs/agentcore-samples.git
cd agentcore-samples/00-getting-started
npm install --global @aws/agentcore@1.0.3
agentcore --version
```

Expect `1.0.3`. If you already use another CLI version and want to leave it unchanged, replace
`agentcore` in the commands below with `npx --yes --package=@aws/agentcore@1.0.3 agentcore`.

Do **not** install the repository-root `requirements.txt` for this sample. It serves other
examples and can install the legacy Starter Toolkit's conflicting `agentcore` executable.
The `bedrock-agentcore` Python runtime SDK used here is still required; it is not the deprecated
`bedrock-agentcore-starter-toolkit` deployment package.

### Select your AWS identity

In each terminal that will call AWS:

```bash
export AWS_PROFILE=your-sandbox-profile
export AWS_REGION=us-west-2
export AWS_DEFAULT_REGION="$AWS_REGION"
aws sso login --profile "$AWS_PROFILE"  # for IAM Identity Center profiles
aws sts get-caller-identity
```

Check the returned account and role before proceeding. Use your organization's login mechanism
if it is not IAM Identity Center. Do not put access keys in source, JSON configuration, or `.env`
files. The SDK and CLI use the normal AWS credential chain.

For local model calls, the profile needs `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on the selected model. Cloud deployment additionally needs
permission to manage this project's CloudFormation stack, AgentCore Runtime, IAM execution role
and permissions boundary, and to publish its S3 asset. A separate platform administrator may need
to bootstrap the account. **Do not attach AdministratorAccess just to follow the tutorial.** Use
an approved deployment role and your organization's CDK bootstrap policy.

**Do not let the CLI change shared account settings without approval.** CLI 1.0.3 attempts to
enable CloudWatch Transaction Search during deployment, even for this non-instrumented sample.
Check `aws xray get-trace-segment-destination --region "$AWS_REGION"` with your platform owner.
If the result is not already `CloudWatchLogs` / `ACTIVE`, obtain approval for that account-level
change before running `deploy`. The application setting `OTEL_SDK_DISABLED` does not disable CLI
account setup. The end-to-end validation account already had Transaction Search enabled.

**KMS-encrypted bootstrap buckets:** ask your platform owner whether the CDK asset bucket uses a
customer-managed key (verify with `aws s3api get-bucket-encryption --bucket BUCKET_NAME`). If it
does, export that exact key ARN in the terminal used for build/deploy:

```bash
export AGENTCORE_SAMPLE_ASSET_KMS_KEY_ARN='arn:aws:kms:REGION:ACCOUNT_ID:key/KEY_ID'
```

Replace the placeholders with the actual bucket key; do not copy the example literally. The
sample adds `kms:Decrypt` on that key to the execution role and its boundary, restricted to S3
and the deployment asset/bucket encryption context. It does not modify the key/bucket policy or
grant direct KMS decryption. Keep this setting for subsequent deployments; leave it unset for
an S3-managed-encryption bucket. Your key policy must also permit the execution role's access.

The deployed agent uses its own execution role, not your local profile. See
[the security boundary](#security-boundary).

## 1. Install the locked dependencies and run the tests

From `00-getting-started`:

```bash
uv python install 3.13
uv sync --project app/agent --frozen
npm ci --prefix agentcore/cdk --ignore-scripts
uv run --project app/agent --frozen pytest
```

These tests make **no AWS calls**. They exercise the real Strands tool loop with a scripted model
and the real AgentCore HTTP adapter, including invalid input, concurrent requests, timeouts, and
reasoning limits. Passing them is not evidence that your AWS permissions or model access work.

### Read the code

| File | Purpose |
|---|---|
| [`app/agent/main.py`](app/agent/main.py) | Fictional data, two tools, model configuration, and the request handler |
| [`app/agent/pyproject.toml`](app/agent/pyproject.toml), `uv.lock` | Python requirements and exact resolved dependencies |
| [`agentcore/agentcore.json`](agentcore/agentcore.json) | CLI v1 project: one HTTP Runtime named `support`, deployed as Python 3.13 CodeZip |
| [`agentcore/cdk/lib/cdk-stack.ts`](agentcore/cdk/lib/cdk-stack.ts) | CDK application and the sample's execution-role permissions boundary |
| [`tests/test_agent.py`](tests/test_agent.py) | Deterministic application and HTTP regression tests |

The catalog and policies live in one source file; there is no second copy to paste from this
README. `@tool` exposes the two lookup functions to Strands. The entrypoint validates the prompt,
constructs a **fresh Agent for each request**, and returns `{"response": "..."}`. This intentionally
avoids a process-global conversation leaking between requests during local development.

## 2. Run locally

In terminal A, from the sample directory:

```bash
agentcore dev --agent support --mode headless --no-traces
```

Leave it running. This sample uses the headless development server, not an interactive chat.
Its default port is 8080. The local endpoint is unauthenticated: keep it on loopback, and do not
publish it through a tunnel or a shared development host.

In terminal B, select the same AWS profile/Region and run from the same directory:

```bash
agentcore invoke --runtime support --local \
  --payload '{"prompt":"What is the price of PROD-002 in USD? Use the product tool."}'

agentcore invoke --runtime support --local \
  --payload '{"prompt":"For PROD-002, what is the return window and does it require original packaging? Look up both product and policy."}'
```

Check facts, not exact wording:

- The product is the **Smart Watch**, priced **$249.99**.
- Its category is **electronics**: **30 days**, original packaging, unused or defective,
  with a full refund to the original payment method.

The second question should require both lookups. The model's phrasing and tool choices can vary;
this is why the deterministic tests separately verify the tool-execution path.

Test input validation without a model call:

```bash
curl --fail-with-body http://127.0.0.1:8080/invocations \
  -H 'Content-Type: application/json' -d '{"prompt":""}'
```

Expect HTTP 400 and an error explaining that the prompt must be a non-empty string. Curl exits
nonzero for this intentional failure. Stop terminal A with **Ctrl-C** when finished.

## 3. Build and inspect before deploying

```bash
agentcore build --json
uv run --project app/agent --frozen pytest
```

After the build, the test run also checks the synthesized IAM policy and deployment ZIP. These
four checks explicitly skip in a source-only checkout.

The build packages Linux ARM64 dependencies and synthesizes CloudFormation locally; it does not deploy
the stack. Review `agentcore/cdk/cdk.out/AgentCore-CustomerSupport.template.json` before proceeding.
It should contain one Runtime, its execution role/policy, and the sample permissions boundary.
CDK may also include metadata. There should be no Gateway, Memory, CodeBuild project, NAT gateway,
or customer-data storage created by this example.

The project is already in v1 format (`"version": 2` is the **project schema version**, not CLI v2).
You do not need `agentcore create`, `agentcore import`, or a temporary v0 installation to run this
fresh sample.

## 4. Deploy and invoke

Check `aws sts get-caller-identity` again. The project name is `CustomerSupport`; if your sandbox
already contains a project/stack with that name, change **only the project `name`** in
`agentcore/agentcore.json` first. Keep the Runtime named `support` because the reviewed boundary
binds to it. CLI project names must be alphanumeric, start with a letter, and have at most **23 characters**.
Never deploy over an existing project you do not own.

```bash
agentcore deploy --region "$AWS_REGION"
agentcore status --json
```

The CLI creates the default deployment target from the selected AWS account/Region on first
deploy. Afterwards, inspect `agentcore/aws-targets.json` as well: changing your shell profile or
Region does not necessarily change a saved target. If your platform team already bootstrapped the
account and forbids bootstrap changes, use `agentcore deploy --skip-bootstrap`.

Wait for the Runtime to be ready, then invoke it:

```bash
agentcore invoke --runtime support \
  --payload '{"prompt":"What is the price of PROD-002 in USD? Use the product tool."}'

agentcore invoke --runtime support \
  --payload '{"prompt":"For PROD-002, what is the return window and does it require original packaging? Look up both product and policy."}'

agentcore logs runtime --name support --since 10m --limit 20
```

Check the same facts as the local run. The cloud request is IAM-authenticated; `PUBLIC` networking
means public **outbound** network access, not anonymous permission to invoke the Runtime.
No custom JWT authorizer or public API is created.

## Security boundary

- **Only two read-only tools.** No shell, browser, file reader, arbitrary URL fetcher, customer
  database, or refund action is exposed. Adding any of those requires a new threat assessment.
- **Terminal-only HTTP interface.** Requests carrying a browser `Origin` are rejected, as are
  non-JSON POSTs, to reduce the risk of websites/forms abusing the unauthenticated local server.
  This is not end-user authentication; do not expose the server outside loopback. Browser
  Inspector clients are deliberately outside this tutorial's scope.
- **No conversation persistence.** Each question is independent; session IDs do not establish
  end-user identity or enable memory in this example.
- **Bounded inference.** A prompt is at most 2,000 characters. A request permits at most four
  model calls, each with at most 512 output tokens, and has a 60-second application timeout.
  The loop returns a fixed explanation if it hits the call limit. These limits reduce cost; they
  are not account-wide rate limits, and cancellation cannot guarantee a provider stops billing
  work already in flight.
- **Restricted execution-role permissions.** CLI v1.0.3's default role is broader than needed.
  The checked-in CDK boundary limits effective access to the selected model **in the deployment
  Region**, the exact deployment ZIP, and this Runtime's logs/log-delivery setup. It does not permit configuration-
  bundle changes, other model invocations, S3 writes, or reading other logs. A boundary does not
  grant permissions by itself; it intersects with the generated role's policies. Keep it intact.
  The optional encrypted-asset setting permits only S3-mediated decryption with the selected key;
  it is not a general-purpose KMS grant. Keep the boundary intact when changing the sample.
- **Prompt minimization in logs.** The application disables Strands' printing callback and
  disables OTel export for this tutorial. It does not log prompts, answers, or raw exception text.
  It records only fixed tool names on completion (not arguments/results), which lets you confirm
  that both tools actually ran. Nova's tagged reasoning preambles are removed from the response;
  malformed or answerless output is rejected rather than shown to the user.
  Account-level Bedrock model invocation logging is separate; check that setting before sending
  sensitive data. Use only fictional data here.
- **Prompts are not authorization.** A model can still produce a wrong or manipulated answer.
  The system prompt is guidance, not an isolation boundary. Production requires authentication
  of end users, authorization, quotas, data-handling controls, evaluations, and operational review.

To change models, update `MODEL_ID` **and** the model ARN in the permissions boundary, check the
model's regional availability/terms, and rerun the tests and cloud validation. Do not replace the
boundary with `Resource: "*"` to work around an access error.

## Troubleshooting

| Symptom | Check |
|---|---|
| Old flags are rejected | `agentcore --version` must show 1.0.3; a Toolkit executable or another npm installation may be earlier in `PATH`. |
| HTTP 400 | Send only `{"prompt":"non-empty text"}`, within 2,000 characters. Do not send model/tool/system-prompt overrides. |
| Model request fails locally | Verify profile login, Region, model availability and Bedrock inference permission. Local credentials differ from the deployed execution role. |
| Model request fails only in AWS | Check the execution-role boundary and model ID, not just the permissions on your local profile. |
| HTTP 504 | The application reached its timeout; inspect service/model health before retrying. Repeated retries cost money. |
| Port 8080 is busy | Use `agentcore dev --agent support --port 8081 --no-traces` and add `--port 8081` to local invokes. |
| Deployment fails | Inspect CloudFormation events. Do not bypass IAM controls or retry with an administrator role without understanding the failure. |
| Package audits report CDK findings | See [VALIDATION.md](VALIDATION.md); build-tool dependencies and runtime dependencies have separate risk/exposure. Do not hide unresolved advisories. |

## Cleanup

**Run only inside this dedicated sample project**, with its original deployment target/account.
`remove all` would remove every declared resource in whichever project you run it in.

```bash
agentcore remove all --yes
agentcore deploy --yes
agentcore status --json
```

The first command changes local configuration; the second applies the removal and deletes the
empty project's stack. Confirm deletion in CloudFormation and check that the Runtime is absent.
The boundary is omitted when the Runtime is removed, so it should not keep the stack alive.

**Do not assume this removes every artifact.** The shared `CDKToolkit` bootstrap stack/bucket
remains. Uploaded assets and service-created CloudWatch logs may remain too. Identify the exact
sample ZIP and log group from the deployment record and your account's retention policy; remove
only sample-owned leftovers if permitted. Never empty a shared bootstrap bucket or delete a
shared bootstrap stack. Keep a record of any intentionally retained resources.

Removing resources also empties `agentcore/agentcore.json`. To rerun, use a fresh checkout/copy or
restore that file deliberately after cleanup; do not overwrite unrelated work.

## Next steps

- [AgentCore CLI](https://github.com/aws/agentcore-cli) for current resource commands.
- [AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
  for the hosting and invocation contract.
- [Strands](https://strandsagents.com/) for agent development.

Add persistence, Gateway integration, or real business actions as separate exercises with their
own authorization and tests—not by silently expanding this example's permissions or scope.
