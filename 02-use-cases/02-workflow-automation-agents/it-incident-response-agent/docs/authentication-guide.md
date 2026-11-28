# Authentication boundaries

There are three independent boundaries. They are not interchangeable.

| Boundary | Default / optional authentication |
| --- | --- |
| Trigger → Runtime | IAM; the trigger can invoke only this Runtime and endpoints |
| Runtime → internal Gateway | IAM SigV4 by default; optional JWT M2M |
| Runtime → Atlassian MCP | Optional Identity **USER_FEDERATION** (OAuth 3LO) |

Gateway JWT does not turn Runtime inbound auth into JWT. Jira is a direct SSE
MCP connection, not a replacement for the internal Gateway's tools.

## Gateway M2M

See [the fresh JWT setup](custom-jwt-auth-upgrade.md). The local helper configures
both `authorizerConfiguration.customJwtAuthorizer` and runtime non-secret
settings. `GATEWAY_OAUTH_SCOPES` must match Gateway `allowedScopes`; the actual
`requires_access_token` request sends those scopes with `auth_flow="M2M"`.
Audience and client restrictions remain enabled. There is no fallback from a
failed JWT request to IAM or to a tool-free successful resolution.

The runtime role's token-vault grants are bounded to the selected account/region,
configured providers and default workload-identity directory. Identity-managed
secret reads are limited to the recorded secret ARNs, not every Identity secret.
The default directory still contains a workload-identity wildcard; review the
effective role before live use rather than treating the sample as least-privilege
certification.

## Jira USER_FEDERATION

Use a disposable Jira project, a dedicated consenting synthetic test user and
an authorized Atlassian OAuth 3LO application. Do not use production issues or
let arbitrary SNS publishers choose another user's federation identity.

Before the fresh deployment, export:

```bash
export JIRA_OAUTH_CLIENT_ID='<registered client ID>'
export JIRA_OAUTH_SECRET_ARN='arn:aws:secretsmanager:REGION:ACCOUNT:secret:external-jira-SUFFIX'
export JIRA_OAUTH_SECRET_VERSION_ID='<immutable Secrets Manager VersionId>'
export JIRA_SITE_URL='https://your-disposable-site.atlassian.net'
export JIRA_CLOUD_ID='<approved site cloud UUID>'
export JIRA_PROJECT_KEY='INC'
export JIRA_RUNTIME_USER_ID='U-1001'
export JIRA_ALLOWED_TOOLS='<reviewed actual jira_-prefixed MCP tool names>'
```

An authorized owner must create/manage the external same-account/region secret,
with JSON key `client_secret`. **Never pass `JIRA_OAUTH_CLIENT_SECRET` to synth**;
it is rejected. Synthesis does not read the value. The provider Lambda reads only
that ARN and the explicit VersionId at deployment time, sends the value to
Identity, and never logs it. For a customer-managed KMS key, supply the reviewed
`JIRA_OAUTH_SECRET_KMS_KEY_ARN`; decrypt is conditioned on that secret's encryption
context and Secrets Manager service.

After deployment, register the `JiraOauthCallbackUrl` output with Atlassian and
complete consent through an approved, user-facing AgentCore Identity integration.
The trigger passes the **configured** `runtimeUserId` and requires the Jira
payload's requester to match it. SNS is not an interactive consent UI. Missing
consent fails processing; the runtime does not print consent URLs in logs.
Do not paste callback/consent URLs into public logs, tickets or commits.

The Strands MCP prefix is `jira_`. Review the real server's schemas before setting
`JIRA_ALLOWED_TOOLS` to exactly the three issue read/comment/transition names.
Jira completion requires results from all three. The hook requires an explicit matching `issueIdOrKey`,
`issue_key` or `issueKey`, and the configured `cloudId`; global/ambiguous
ID-only or different-site operations are denied. Only
issues under `JIRA_PROJECT_KEY` are accepted. Jira result content and outgoing
arguments pass through the Guardrail. External schema changes can require a
reviewed adapter change; do not bypass this check to get a successful demo.

### Verify the Jira mode

When Jira is configured, the full E2E suite requires `--jira-events path.json`;
it does not report a successful DDB-only run as Jira verification. Supply three
existing, approved synthetic issues for the same configured federation user,
with LOW/HIGH/CRITICAL priorities. Each issue should describe a runbook-supported
incident that exercises the preserved internal tools as well as Jira.

```json
[
  {"issue_key":"INC-1042","requester_id":"U-1001","priority":"LOW","synthetic":true},
  {"issue_key":"INC-1043","requester_id":"U-1001","priority":"HIGH","synthetic":true},
  {"issue_key":"INC-1044","requester_id":"U-1001","priority":"CRITICAL","synthetic":true}
]
```

```bash
./scripts/test-e2e.sh "${BIND[@]}" --jira-events path.json
```

These commands do not create the external Jira fixture or obtain consent for you.
Inspect actual comments/transitions and evaluator outcomes in the authorized
fixture; locally passing SDK stubs do not establish those live effects.

### Secret-only rotation

Create a new version in the existing external secret using your approved secret
management process. Then, with the same explicit binding from the README:

```bash
python scripts/lifecycle.py rotate-jira "${BIND[@]}" \
  --confirm-stack-id '<recorded full StackId>' \
  --secret-version-id '<new immutable VersionId>'
```

This updates only the `JiraSecretVersionId` parameter on the **existing template**.
It does not synthesize a new template or change the provider/client identity.
CloudFormation delivers an Update with the new version to the owned provider.
The handler verifies ARN and creation-time ownership before updating. Errors
propagate; keep the old version available for an explicitly authorized rollback.
Validate actual token refresh/consent after rotation before revoking the old
credential. A successful CloudFormation update alone does not prove Jira access.

### Collision and cleanup behavior

A name conflict is never permission to adopt/update a provider. The retained
DynamoDB ownership record stores proof from a completed Create API/lookup. A
verified Create retry reads it without updating another provider. An interrupted
Create without that proof requires investigation. Only not-found is successful
absent cleanup; denial/timeouts retain failure evidence. The external secret and
ownership table remain after stack deletion.

## Required live negatives

On an independently approved fixture, check missing/expired JWTs, wrong audience,
wrong client and missing scope against the Gateway; none should execute tools.
For Jira, check missing consent, an unapproved tool, a different issue/project,
secret-only rotation and denied cleanup. The repository's SDK/shell stubs do not
establish these service outcomes. Cedar remains LOG_ONLY throughout.
