# Command reference

Use the [README](../README.md) first. All AWS-facing helpers use the same binding:

```bash
PROJECT=YourUniqueIT  # must equal agentcore/agentcore.json name
TARGET=demo
ACCOUNT=111111111111
REGION=us-west-2
BIND=(--project "$PROJECT" --target "$TARGET" --account "$ACCOUNT" --region "$REGION")
```

No helper infers a default deployed stack or accepts an unrelated `STACK_NAME`.
They do not source `.env` automatically. The native CLI uses a private
`.sample-cli-home` with Transaction Search auto-setup and telemetry disabled.
No credential/state/cache is copied there. Explicit AWS config/credential file
references and environment credentials are preserved. Home-relative SSO caches
are not imported: use an approved credential mechanism configured for this
private context, and keep identity checks enabled.

`AWS_REGION`, `AWS_DEFAULT_REGION`,
`CDK_DEFAULT_ACCOUNT` and other ambient target settings must agree, if set.

## Local preparation and preview

```bash
python scripts/configure_targets.py --target "$TARGET" --account "$ACCOUNT" --region "$REGION"
(cd agentcore/cdk && npm ci --ignore-scripts && npm run build)
(cd app/ITIncidentAgent && uv sync --python 3.12 --frozen --no-dev)
app/ITIncidentAgent/.venv/bin/python scripts/package_sdk.py
(cd agentcore/cdk && IT_SAMPLE_SYNTH_ONLY=true npm run cdk -- synth --no-lookups --telemetry=false --notices=false)
```

`package_sdk.py` builds a hash-locked pure-Python boto3/botocore layer for the
infrastructure handlers; their API shapes do not depend on the managed Lambda
runtime's bundled SDK. Keep/review an old `.lambda-sdk` artifact before rebuilding.

Preview does not authorize direct deployment of that assembly. Use the fresh
entrypoint, which creates and verifies its reservation first. For optional JWT,
configure credentials before the first deployment; credential-dependent preview
may need the recorded credential references that CLI pre-deploy supplies.

## Authorized live operations

```bash
IT_SAMPLE_EVALUATION_CONTENT=synthetic-only ./scripts/deploy.sh "${BIND[@]}"
./scripts/publish_ticket.sh "${BIND[@]}" --ticket seed-data/sample_ticket.json
./scripts/show_ticket.sh "${BIND[@]}" --ticket-id INC-20260604-001
./scripts/test-e2e.sh "${BIND[@]}"
python scripts/evaluate.py "${BIND[@]}" --hours 1 --summary
python scripts/lifecycle.py inspect "${BIND[@]}"
./scripts/destroy.sh "${BIND[@]}" --confirm-stack-id '<recorded full StackId>'
```

`evaluate.py` reads online results from the owned evaluation configuration's
actual output log group. It does not perform on-demand scoring and takes no
positional trace ID. Only the full E2E suite checks current-run correlation and
completeness. `scripts/e2e_test.py` is a compatibility entrypoint to that suite,
not the former best-effort/latest-log demo.

After verifying the binding with `lifecycle.py inspect`, native read-only CLI
commands can help inspect a fixture (they are not stronger ownership checks):

```bash
"$AGENTCORE_CLI" status --target "$TARGET"
"$AGENTCORE_CLI" logs runtime --name ITIncidentAgent --target "$TARGET"
"$AGENTCORE_CLI" traces runtime list --name ITIncidentAgent --target "$TARGET"
```

## Deploy-time options

Export only options you have reviewed; see `.env.example` for names.

| Option | Behavior |
| --- | --- |
| `KB_ID` | Use an approved existing Knowledge Base; its lifecycle is external |
| `SKIP_KB=true` | Omit only query-kb/created KB; full E2E cannot pass this omission |
| `DESTROY_ON_DELETE=false` | Retain sample data; review the resource ledger separately |
| `IT_SAMPLE_EVALUATION_CONTENT=synthetic-only` | Explicitly enable content capture for synthetic evaluation |
| `TXN_SEARCH_ALLOW_MUTATION=true` | Exceptional account-owner-approved mode; not the default |
| `TXN_SEARCH_ACCOUNT_OWNER_APPROVAL=ACCOUNT/REGION` | Required exact acknowledgment for that mutation mode |
| `TXN_SEARCH_INDEXING_PERCENTAGE` | Desired and actual percentage must both be observed; default 100 |
| `JIRA_OAUTH_CLIENT_ID`, `JIRA_OAUTH_SECRET_ARN`, `JIRA_OAUTH_SECRET_VERSION_ID` | Optional external Jira credential configuration |
| `JIRA_ALLOWED_TOOLS` | Reviewed, space-separated actual Jira MCP names with issue-key schemas |

Model choices live in `agentcore.json` runtime envVars. Configure Gateway JWT with
the [local generator](custom-jwt-auth-upgrade.md), not just one environment toggle.
A conflicting runtime scope/allowedScopes configuration fails synthesis.

## Offline tests

Use the locked application Python environment; these tests use real SDK stubs,
not real credentials or inference. Run in an isolated network namespace with
private HOME/config and dummy AWS environment when validating untrusted inputs.

```bash
(cd agentcore/cdk && npm run build && npm test -- --runInBand)
app/ITIncidentAgent/.venv/bin/python -m unittest discover -s scripts -p 'test_*.py' -v
```

Package/image builds and tests do not establish cloud readiness. Native ARM64
image build/startup, live auth negatives and independently reviewed cloud
verification remain separate gates.
