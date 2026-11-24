# Data-analyst frontend

Next.js 16.1.7, React 19.2.3 and Amplify Gen 2 provide Cognito sign-in, streamed
conversations, query-result tables, Bedrock-generated charts and semantic Memory
facts. Follow the ordered workflow in the [sample README](../README.md).

## Setup and ownership

Use pnpm **10.11.0** with `pnpm install --frozen-lockfile`. The lock includes the
manifest's security overrides; do not mix it with the backend's npm lock.

Deploy the approved Amplify sandbox **first without AGENT_RUNTIME_ARN** to create
Cognito. Its `amplify_outputs.json` contains the user pool, client and identity
pool identifiers. Supply the pool/client to the custom backend. After backend
deployment, `../setup-frontend.sh --stack-name NAME --region REGION` writes
`.env.local`. Export the exact Runtime ARN in the shell running `pnpm ampx sandbox`
to attach the narrowly scoped invoke policy; `.env.local` is for Next.js and is
not a substitute for those Amplify synth-time environment variables.

```bash
pnpm ampx sandbox   # cloud operation; requires separate approval
pnpm dev
# production compilation, after real Amplify outputs exist:
pnpm build
```

No global install is necessary. For an offline compiler test, a **synthetic**
`amplify_outputs.json` may be supplied and clearly labeled as a fixture. It must
never be deployed or mistaken for verified Cognito configuration. Build artifacts
from such a test are not cloud-ready. System fonts keep builds independent of
Google Fonts requests.

## Security and data contract

- IAM signs Runtime transport; every payload also carries the signed-in user's
  Cognito ID token. Runtime verifies its issuer, audience, signature and expiry.
- The backend derives the actor from the verified subject. A client-supplied
  `user_id` is only a consistency assertion, never an authorization decision.
- Browser roles have **no Memory or DynamoDB access**. `query_results` and
  `memory_facts` Runtime operations return only that actor's data. Caller-selected
  table names, namespaces and actor IDs are not forwarded to those reads.
- `/api/agent/query-results` now returns 410 rather than proxying caller-chosen
  DynamoDB tables. The UI uses the Runtime instead.
- SSE parsing buffers partial lines and split UTF-8 bytes instead of dropping
  partial JSON. Server rejection and truncated streams are errors, not success.
- Chart generation retains direct, scoped Bedrock model invocation. Model access
  and cross-region inference must be approved for the selected account/region.

## Hosting and cleanup

For Amplify Hosting, use the same project root and environment identifiers as the
sandbox test, following your approved hosting workflow. This migration does not
provision GitHub integrations or third-party hosting. Do not deploy an offline
fixture. Verify auth, two-user isolation, streaming, tables, charts and facts
before publishing the app.

Delete only the test sandbox (`pnpm ampx sandbox delete`) and any separately
created test hosting app. Keep its resource ledger until deletion is verified.
Backend stack and evaluation cleanup are separate; see [MIGRATION.md](../MIGRATION.md).
