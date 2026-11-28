# Deployment guide — CLI 1.2.0 custom v1 app

**Cloud is NOT RUN in migration validation.** This is the coordinator/reviewer
workflow, not permission to create resources. Existing v0 stacks are unsupported:
keep their state and use a fresh checkout. Never run the automatic scaffold
replacement over this app.

## Prerequisites

- Exact CLI 1.2.0 (`AGENTCORE_CLI=/absolute/path/to/agentcore`), Node 20+, Python
  3.12 + uv 0.9.28, local locked CDK dependencies, ARM64 Docker/Finch.
- Approved commercial-partition account/region; existing CDK bootstrap left unchanged. The helper uses
  `--skip-bootstrap`. Bedrock access for Sonnet 4.6 and Haiku 4.5 and their
  cross-region inference profiles must already be approved.
- Existing account Transaction Search setup suitable for online evaluation.
  This sample never enables/disables it. Resource-scoped log/trace deliveries
  are declared in CDK; account observability is not sample-owned.
- Reviewed deployer permissions for CloudFormation, asset publication, bounded
  PassRole and the services declared in the synthesized template, plus dedicated
  Cognito/Identity creation/tagging/deletion. No AdministratorAccess workaround.
- Inspect the template and IAM boundary before deploying. Runtime permissions
  are not deployer permissions; see [configuration](CONFIGURATION.md#iam).

## Offline validation

```bash
npm --prefix agentcore/cdk ci --ignore-scripts
npm --prefix agentcore/cdk run build
(cd app/claimsagent && uv sync --frozen --no-dev --no-install-project --python 3.12)
app/claimsagent/.venv/bin/python tests/run_offline.py
node --require ./tests/offline.cjs tests/test_cdk_wiring.cjs
# Real image build (registry/PyPI downloads; no AWS provisioning):
docker build --platform linux/arm64 -t claims-local app/claimsagent
# Finch can be substituted for docker.
```

Python tests use real locked SDKs, real Cedar and Lambda code with stubbed remote
boundaries. The template test uses fake ARNs but real source assets and constructs,
not a mocked CDK or fixture ZIP. Container `/ping` can be checked with fake AWS
credentials, `OTEL_SDK_DISABLED=true`, and `--network none` using an exec inside
the container. Never submit a claim just to check container startup.

## Fresh configuration and auth

Use a unique lowercase alphanumeric suffix of 8–12 characters:

```bash
python3 scripts/configure_target.py --account 123456789012 --region us-west-2 --suffix test20261118
```

The account above is illustrative, not an inferred/default target. The generator
refuses any prior target, CLI, `.env`, Cognito or ownership state. It reserves the
new project identity before writing other config, so partial local setup also
fails closed. Review `.claims-v1.json` and `agentcore/aws-targets.json`.

Use the locked Python environment for every helper (activate it or select its
interpreter). Only after authorization:

```bash
python3 scripts/lifecycle.py setup --allow-cloud
```

Setup refuses a pool/domain/provider collision (including a later list page),
creates a tagged dedicated Cognito M2M pool/client and Identity OAuth provider,
and records returned IDs immediately in `waiting-provider` before readiness
checks. Only explicit `READY` from GetOauth2CredentialProvider advances setup to
`auth-ready`; the modeled status is optional, so omission is not proof of readiness.
Polling retries only `CREATING`, for at most 24 reads with a 120-second polling
deadline and 5-second intervals; an in-flight control request has 5-second connect /
10-second read timeouts and no automatic retries. Failure, unknown status or timeout
retains IDs for exact-ID recovery, without publishing ready CLI state. Every
deployment preflight independently requires `READY` and matching ownership/config.
The secret is transferred **only in memory** to Identity. `agentcore/.cli/deployed-state.json` receives the exact
credential ARN/secret ARN, not a secret. This is new v1 state, not invented old
resource continuity. A timeout/partial setup requires exact-ID recovery from the
journal; rerunning setup cannot adopt a potentially unowned resource.

## Deploy without account-level changes

CLI 1.2.0 reads global settings from `HOME/.agentcore/config.json` (the
`AGENTCORE_CONFIG_DIR` variable does **not** redirect that file). Create a
**dedicated** CLI home, not a replacement for your normal one:

```bash
export CLAIMS_CLI_HOME=/absolute/path/to/private-claims-cli-home
mkdir -p "$CLAIMS_CLI_HOME/.agentcore"
printf '%s\n' '{"disableDependencyManagement":true,"transactionSearch":false,"telemetry":{"enabled":false}}' \
  > "$CLAIMS_CLI_HOME/.agentcore/config.json"
export AGENTCORE_CLI=/absolute/path/to/pinned-1.2.0/agentcore
./deploy.sh --allow-cloud
```

The dedicated HOME needs the coordinator's separately approved credential
mechanism (for example short-lived environment credentials); do not copy a
credential store into it. This sample does not acquire credentials. No secrets
should be in `CREDENTIAL_*` overrides or `agentcore/.env.local`: the helper rejects
them to avoid unintended CLI credential updates. It verifies provider tags,
ARN and discovery/client config before invoking CLI deploy, and checks unowned
stack and named-resource collisions. On success it records the actual stack ARN.
All CloudFormation failures propagate and leave `phase: deploying` for review.
No automatic retry/adoption, bootstrap, seeding, or notifications.

## Bounded cloud verification

The generated test configuration must stay `notifications: false` throughout.
SES/SNS send permissions are omitted and both handlers suppress sends, including
human review. Do not verify email identities, add subscriptions or recipients.

```bash
python3 scripts/test_auth.py --allow-cloud   # no model calls
python3 scripts/test_cedar.py --allow-cloud  # two direct tool calls, one expected write
python3 scripts/test_e2e.py --allow-cloud    # three direct Runtime requests + one S3 event
```

- Auth checks distinguish IAM Runtime from JWT Gateway. Invalid scope is checked
  at Cognito's token endpoint, not inferred from an unrelated Runtime 403.
- Cedar checks discover the actual qualified tool name, require a direct Gateway
  error and no correlated row at $100,000, and one confirmed row at $99,999.
  These checks do not use LLM prose as a policy oracle.
- E2E seeds unique policy IDs, reads complete bounded Runtime streams and checks
  claim/review rows. The S3 event polls up to 180 seconds; an initial Trigger HTTP
  200 or old row is not success. Model routing is nondeterministic: a failed
  expected review is a failed check, not a reason to weaken the assertion.
- Separately inspect Memory strategies and recorded events for one actor/session,
  verify cross-session retrieval, run the custom evaluator with reference inputs,
  and observe the three built-in evaluation metrics. Bound evaluations/invocations
  and record their IDs. These service integrations remain cloud-unverified.
- Capture the template, outputs, resource list, test JSON results and stack events.
  Keep the unique policies, test rows and S3 keys in the cleanup ledger.

An individual IAM model invocation is available via
`python3 scripts/test_invoke.py --allow-cloud --prompt '...'`, or
`agentcore invoke --runtime claimsagent --target dev --payload '{"prompt":"..."}'`.
Both are cloud-connected and can incur model/tool effects. The CLI does not
perform the sample helper's ownership check; use only with the reviewed target.

## Cleanup

```bash
./destroy.sh --allow-cloud
```

1. Verify account, recorded stack ARN and ownership tags. Save stack physical IDs
   before deletion. Delete the stack and wait at most ten minutes.
2. Stop on DELETE_FAILED, retained resources, timeout or access denied. Do not
   force-retain resources to obtain a misleading DELETE_COMPLETE. No broad
   `claims`/Gateway/Memory/log-name sweeps are permitted.
3. After stack deletion, delete only the tagged recorded provider and pool/domain;
   verify provider/pool absence. Auth is not removed ahead of a live stack.
4. An Identity secret may remain or be scheduled for deletion. The helper reports
   this as incomplete and preserves state; do not claim absence until verified.
   Likewise record KMS scheduled deletion and any service-created log/identity
   residues during the coordinator's final exact-ID audit. CloudFormation deletion
   alone does not prove all asynchronous/service-managed residue is gone.
5. Keep `.claims-v1.json`, CLI state and logs even after successful cleanup. These
   are evidence, not trash to erase in order to retry a fresh deployment.

A partially created pool/provider/stack may exist even if a response was lost.
Use the reserved unique name, exact returned IDs and ownership tags for a separately
reviewed recovery; do not mark a colliding pre-existing object as owned. The helper
intentionally refuses these ambiguous states rather than guessing.

## Observability and local dev limitations

CDK owns Runtime/Lambda/build log groups and Gateway/Memory application-log and
trace deliveries, with one-week retention. Optional identity-only deliveries use
the Runtime/Gateway service-generated workload IDs after deployment:

```bash
python3 scripts/enable_observability.py --allow-cloud
# Optional separate teardown of just those journaled identity deliveries:
python3 scripts/disable_observability.py --allow-cloud
```

The helper preflights names, tags/journals exact resources, and refuses collisions
or partial prior setup. Stack cleanup removes these receipts before deleting the
stack. No regional discovery/deletion or account Transaction Search mutation.
These optional service calls, like all cloud checks, remain NOT RUN.

After a reviewed deployment, `agentcore dev --agent claimsagent --mode headless`
and `agentcore invoke --runtime claimsagent --local --payload '{"prompt":"..."}'`
are cloud-connected development interfaces. Local Identity access may need a
separately reviewed workload identity; there is no client-secret bypass. Use the
offline tests for deterministic development.
