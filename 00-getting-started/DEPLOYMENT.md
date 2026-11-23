# Deployment and maintenance notes

The [customer walkthrough](README.md) has the required deployment steps. These maintainer
notes explain permissions, validation and retention. Use a sandbox and fictional data. A local test/build is not evidence of live model access or deployability in your account.

## Account, bootstrap and saved targets

Use an approved short-lived profile and verify `aws sts get-caller-identity` in every terminal.
Local inference needs `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on the
regional model. Deployment additionally needs approved CloudFormation, AgentCore Runtime, IAM
role/boundary and S3 asset publishing permissions. Do not attach AdministratorAccess to follow
this tutorial; a platform administrator may need to bootstrap using your organization's policy.

CLI 1.0.3 creates a default deployment target on first deploy. Afterwards inspect the local
`agentcore/aws-targets.json`: changing your shell profile/Region does not necessarily change a
saved target. Keep the original target for cleanup. The project is already CLI v1 format;
`"version": 2` is the project schema, not CLI v2. No `create`, `import` or Toolkit install is needed.
If already bootstrapped and bootstrap changes are prohibited, use `agentcore deploy --skip-bootstrap`.

The walkthrough sets `agentcore config transactionSearch false` so deploy skips account-level
Transaction Search setup. If explicitly enabled with platform-owner approval, CLI 1.0.3 attempts
setup but continues deployment if setup fails. `OTEL_SDK_DISABLED` controls the application,
not this CLI behavior.

## Persistent asset KMS setting

Synthesis does **not** look up or detect the bootstrap bucket's encryption. Ask its owner which
bucket/key this deployment uses; an authorized operator can check:

```bash
aws s3api get-bucket-encryption --bucket BUCKET_NAME
```

For a customer-managed KMS key, edit **`agentcore/cdk/cdk.json`**, replacing the `null` value of
`context.agentcoreSampleAssetKmsKeyArn` with the exact key ARN:

```json
"agentcoreSampleAssetKmsKeyArn": "arn:aws:kms:us-west-2:111122223333:key/00000000-0000-0000-0000-000000000000"
```

The ARN above is fictional; substitute the approved bucket key, not an alias. Keep this
sample-owned setting for subsequent builds/deployments, including new terminals. It is not a
secret, but review account identifiers before publishing local configuration. Use `null` for
no customer-managed key. Missing/null settings emit an actionable synth warning and grant no
KMS permission; they do **not** mean encryption was detected. Empty strings, wrong types and
malformed ARNs fail. A single setting assumes the same key for all configured targets; do not
reuse this project across differently encrypted targets without reviewing its configuration.

Use native `agentcore build` or `npm run cdk -- synth` from `agentcore/cdk`; plain
`node dist/bin/cdk.js` does not load `cdk.json` context and is not a substitute for those commands.

The adapter adds `kms:Decrypt` to **both** the generated role policy and its boundary. It permits
only the exact key through `kms:ViaService = s3.<deployment-region>.<dns-suffix>`, with S3 encryption
context matching the exact ZIP or its bucket (for S3 Bucket Keys). It grants no direct decryption
and changes neither key nor bucket policy; the key policy must also permit the role's access.
After building, inspect both grants and conditions. Keep them together when customizing.

## Security boundary and limits

- **Two read-only tools:** product and policy lookups only. No shell, file reader, arbitrary URL
  fetcher, customer database or refund action is exposed. Tool results are copies of fictional data.
- **HTTP protection:** reject browser `Origin` and non-JSON POSTs before inference. This reduces
  website/form abuse of the unauthenticated local server, not a replacement for authentication.
  Keep local development on loopback; browser Inspector clients are outside this tutorial.
- **Request-local state:** each question constructs a new Agent. Runtime session IDs do not
  identify end users or create memory. Concurrent users must not share conversation state.
- **Bounded inference:** at most 2,000 prompt characters, four logical model steps, 512 output
  tokens per step and a 60-second application deadline. Botocore standard mode allows **two
  transport attempts per step** (initial plus one retry), so this tool loop allows at most
  **4 × 2 = 8 transport attempts**, not four network attempts. Strands retries are disabled to
  avoid stacking retries. Retries cover retryable model transport errors, not arbitrary business
  side effects or replaying a partially consumed stream. Exhaustion returns a fixed/redacted error.
  Cancellation cannot guarantee a provider stops work/billing already in flight. These are not
  account-wide quotas, ingress body limits, cost budgets or abuse controls.
- **Effective IAM is an intersection:** CLI 1.0.3's generated role is broader than necessary.
  The boundary limits it to the regional Nova Lite model, the exact deployment ZIP, optional
  S3-mediated KMS decryption and log write operations. It excludes configuration-bundle
  changes, other models/assets and S3 writes. A boundary alone does not grant permissions.
- **Logs use a Runtime NAME PREFIX**, `/aws/bedrock-agentcore/runtimes/<runtime-name>*`, including
  streams—not the exact generated Runtime ID. Resources sharing that prefix also match. The
  generated ID cannot be used here without a Runtime → role → Runtime dependency cycle.
- **Safe logs/output:** disable the Strands printing callback and OTel export. Suppress the
  streaming parser's malformed-tool-input warning with a logger level that survives Uvicorn
  startup. The application logs fixed tool names or failure categories, not arguments, results,
  prompts, answers or raw exception text. Strip
  complete Nova `<thinking>` preambles and reject malformed/empty answers. Account-level Bedrock
  model invocation logging is separate; check its data-handling implications before use.
- **Prompts are not authorization:** a model can still be wrong or manipulated. Production needs
  end-user authentication/authorization, evaluations, data controls and operational review.

The `node.defaultChild` / `agentRuntimeArtifact` extraction in `cdk-stack.ts` is an adapter coupled
to **pinned `@aws/agentcore-cdk` 1.0.3 internals**, not a stable public artifact-address API. It
fails closed if the expected CfnRuntime/CodeZip shape is absent. Retest native CLI context,
packaging, role/boundary grants and cleanup on upgrades; do not remove the guard to make synth pass.
When `support` is removed, the adapter returns without creating a boundary, even with stale KMS
settings, so cleanup can synthesize an empty project.

## Model customization

The default uses a **regional foundation model**, not a cross-region inference profile. For
another regional model, change `MODEL_ID` and the model ARN in the boundary, verify that the
role also grants the needed invocation actions, check regional availability/terms and revalidate.
Cross-region inference requires the **inference profile ARN and destination-region model ARNs**
in the applicable role/boundary permissions, plus appropriate SCP/region access and revalidation.
It is not just a `MODEL_ID` replacement. Never substitute `Resource: "*"` to bypass access errors.
Changes to thinking, structured output or tool behavior also require retesting the request and
attempt bounds against the pinned provider adapter.

## Repeatable checks

From the sample directory, install with the README's frozen commands, then run:

```bash
uv run --project app/agent --frozen pytest
uv run --project app/agent --frozen ruff check --config ruff.toml .
uv run --project app/agent --frozen ruff format --check --config ruff.toml .
npm run build --prefix agentcore/cdk
agentcore build --json
uv run --project app/agent --frozen pytest
RUN_CDK_TESTS=1 uv run --project app/agent --frozen pytest tests/test_cdk_context.py
```

Source-only pytest explicitly skips infra/ZIP checks until `agentcore build` produces artifacts.
Native context tests are separately opt-in: they require CLI **1.0.3**, installed npm packages,
uv and cached Linux ARM64 wheels from the build above. Use the README's local `.tools`
installation and `PATH` export: a shell function alone is not visible to subprocesses.
Keep `uv`, Node/npm and Python 3.13 available as well. Before isolating HOME,
the fixture asks `uv cache dir` and `uv python dir` for the caller's actual cache and managed
Python locations, then passes those paths explicitly. No `UV_CACHE_DIR` or
`UV_PYTHON_INSTALL_DIR` export is required when using uv's normal defaults.

Run these tests under denied external networking with loopback available, private HOME/config/
TMP/cache and no real credentials or saved target/state files. The fixture sets CLI telemetry
with `agentcore config telemetry.enabled false` in its private `HOME/.agentcore/config.json`;
it does not rely on an invented configuration-routing or notices switch. Empty AWS credentials
and the OS network boundary are the safety controls, not telemetry settings.
They build a disposable project, persist a fictional KMS ARN across fresh processes, check both
grants, reject malformed settings, and exercise native `remove all` plus empty synthesis.
They do **not** deploy or prove cloud deletion. Dependency acquisition needs registry access
before running offline. No prebuilt/cache-bypass flag or patched package is required.

Native CLI invokes `npm run cdk -- synth`; the CDK wrapper normally attempts STS account discovery
before starting the app. With no credentials and metadata disabled it can synthesize this
**target-less, environment-agnostic** project without discovery. A fake credential chain can
instead produce a failed STS probe; neither failure is cloud validation. No saved target is
required for this local build. Deployment/account checks still require approved AWS access.

The application suite uses real frameworks with scripted model events. Retry tests replace only
HTTP transport responses, retaining Bedrock serialization, event-stream parsing and botocore's
standard retry handlers. They cover throttle/recovery, exhaustion and the eight-attempt bound;
Stubber alone would not test that path. The post-build suite checks IAM, current source bytes
and ELF AArch64 native libraries in the ZIP. Run both default and configured KMS modes after
changes, restoring your intended persistent setting afterwards.

CI discovers the sample-root `ruff.toml` when passed changed Python paths from the repository
root; repeat both `ruff check` and `ruff format --check` with those paths. No application-wide
assertion ignore or repository-wide rule relaxation is needed. Lockfiles need not change for lint.

Review dependency/security findings separately from tests: run `npm audit` in `agentcore/cdk`,
audit the CLI's own npm graph and the locked production Python graph, and inspect raw IaC scanner
results. Build dependencies are not in the Runtime ZIP, but are not risk-free. Track upstream
advisories such as [CDK's bundled minimatch](https://github.com/advisories/GHSA-g3hv-75wg-m249);
do not use forced downgrades or blanket suppressions to manufacture a clean result. Keep raw
CDK-Nag wildcard rows visible alongside the boundary's effective-permission analysis. Historical
PR evidence remains in Git/PR history; rerun approved bounded cloud checks for behavioral changes.

## Cleanup and retention

Use the README's `remove all` → `deploy` → `status` sequence only in this dedicated project and
its original account/target. Confirm deletion of the stack, Runtime, execution role and boundary.
`remove all` empties local declarations; restore `agentcore/agentcore.json` before reusing this sample.

Service-created CloudWatch logs/policies and uploaded assets may survive stack deletion. Identify
the exact sample log group and ZIP/template versions from the deployment record; remove only
sample-owned leftovers if permitted by retention policy. Record any intentionally retained
resources. **Never** empty the shared bootstrap bucket, delete its KMS key or remove `CDKToolkit`.
