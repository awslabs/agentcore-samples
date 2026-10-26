# Commands & Operations Reference

A deep dive on the CLI commands used to **scaffold**, **manage**, **operate**, and
**troubleshoot** this project.

- For the **architecture** (pattern, data flows, file map), see [ARCHITECTURE.md](ARCHITECTURE.md).
- For the **quickstart** (deploy + run the demo), see the [README](../README.md).

---

## How this project was scaffolded (CLI-first)

This project uses the **recommended** AgentCore development workflow: the CLI
(`@aws/agentcore`) manages all AgentCore-owned resources, and their configuration
is committed to `agentcore/agentcore.json`.

> **You do NOT need to run these.** The configuration is already committed. They are
> a reference for building your own project from scratch.

```bash
# Scaffold a new AgentCore project (Strands framework, container Runtime, short-term Memory)
agentcore create --name ITIncidentAgent --framework Strands --build Container --memory shortTerm

# Add long-term Memory with a SUMMARIZATION strategy (rolls each session into a
# per-requester summary that powers cross-incident recall). The memory name becomes
# the MEMORY_{NAME}_ID env var the L3 injects into the Runtime — here
# ITIncidentAgentMemory → MEMORY_ITINCIDENTAGENTMEMORY_ID.
agentcore add memory --name ITIncidentAgentMemory --strategies SUMMARIZATION --expiry 30

# Add a Gateway (MCP protocol) to expose tools to the agent, secured with IAM auth
agentcore add gateway --name ITIncidentGateway --authorizer-type AWS_IAM

# Register each Lambda tool as a Gateway target so the agent can call it via MCP
agentcore add gateway-target --name lookup-user --type lambda-function-arn --tool-schema-file ...
agentcore add gateway-target --name get-process-info --type lambda-function-arn --tool-schema-file ...
agentcore add gateway-target --name create-change-request --type lambda-function-arn --tool-schema-file ...
agentcore add gateway-target --name query-kb --type lambda-function-arn --tool-schema-file ...

# Configure continuous online evaluation (LLM-as-judge) tied to the Runtime's trace output
agentcore add online-eval --name ITIncidentAgentEval --runtime ITIncidentAgent \
  --evaluator Builtin.Correctness Builtin.Helpfulness Builtin.ToolSelectionAccuracy Builtin.GoalSuccessRate \
  --sampling-rate 100

# Add a Policy Engine for bounded autonomy (Cedar policies on tool access)
agentcore add policy-engine --name ITIncidentPolicyEngine \
  --description "Cedar policy engine for bounded autonomy" \
  --attach-to-gateways ITIncidentGateway --attach-mode LOG_ONLY

# Add Cedar policies to the engine
agentcore add policy --name LogAllToolCalls --engine ITIncidentPolicyEngine \
  --statement 'permit(principal, action, resource is AgentCore::Gateway);' \
  --validation-mode IGNORE_ALL_FINDINGS

agentcore add policy --name RequireReasonForChangeRequest --engine ITIncidentPolicyEngine \
  --statement 'forbid(principal, action, resource is AgentCore::Gateway) when { context has "toolName" && context.toolName == "create-change-request" && !(context has "reason") };' \
  --validation-mode IGNORE_ALL_FINDINGS
```

OTEL auto-instrumentation is provided by the Dockerfile `CMD`
(`["opentelemetry-instrument", "python", "-m", "main"]`); the OTEL/X-Ray env vars
are declared in `agentcore.json` → `runtimes[].envVars[]`.

Supplementary infrastructure (DynamoDB, S3, SNS, Lambda tools, EventBridge,
Guardrail, KB, alarms) is **not** AgentCore-managed — it lives in the same CDK stack
via `InfraConstruct` and deploys together with one `agentcore deploy`. See
[ARCHITECTURE.md → Declarative vs Imperative](ARCHITECTURE.md#declarative-vs-imperative-whats-managed-where).

---

## Operating the deployed agent

```bash
agentcore status                          # deployed resources + key outputs
agentcore logs --since 5m                  # runtime logs
agentcore traces list                      # recent traces
agentcore traces get <trace_id>            # a specific trace

./scripts/publish_ticket.sh                # publish the bundled sample ticket to SNS
./scripts/publish_ticket.sh /tmp/x.json    # publish a custom ticket
./scripts/show_ticket.sh INC-20260604-001  # read a ticket's resolution from DDB
python scripts/evaluate.py                 # online-eval scores (last hour; --hours N, --raw)
./scripts/destroy.sh                       # tear down all resources
```

Inspect what the agent wrote:

```bash
aws dynamodb scan --table-name <ChangeRequestsTable> --region $AWS_REGION   # change requests
aws bedrock-agentcore list-events --memory-id <MemoryId> \
  --actor-id U-1003 --region $AWS_REGION                                    # memory episodes
```

---

## Managing AgentCore resources

### Memory

Memory is declared in `agentcore/agentcore.json` under `memories[]`:

```json
"memories": [{
  "name": "ITIncidentAgentMemory",
  "eventExpiryDuration": 30,
  "strategies": [
    { "type": "SUMMARIZATION", "name": "summary_strategy", "namespaces": ["incidents/{actorId}/{sessionId}"] }
  ]
}]
```

To (re)create it via the CLI instead of editing JSON by hand:

```bash
agentcore add memory --name ITIncidentAgentMemory --strategies SUMMARIZATION --expiry 30
agentcore validate
agentcore deploy -y --target dev
```

| Flag | Value / meaning |
| ---- | --------------- |
| `--name` | `ITIncidentAgentMemory` — becomes the `MEMORY_{NAME}_ID` env var the L3 injects into the Runtime (`MEMORY_ITINCIDENTAGENTMEMORY_ID`), which `config.py` reads as `MEMORY_ID`. |
| `--strategies` | `SUMMARIZATION` — rolls each session into a per-requester summary. Comma-separate to add more (e.g. `SEMANTIC,SUMMARIZATION`). |
| `--expiry` | Event expiry in days (default 30, min 7, max 365). |

> **SUMMARIZATION requires `{sessionId}`:** a `SUMMARIZATION` strategy's `namespaces`
> **must** include the `{sessionId}` placeholder, or `CreateMemory` fails validation.
> This project uses `incidents/{actorId}/{sessionId}`.

> **Namespace alignment:** `memory/enrichment.py` retrieves with the prefix
> `incidents/{requester_id}` (i.e. `incidents/{actorId}`). Because `retrieve_memories`
> does **prefix** matching, the session-scoped namespace
> `incidents/{actorId}/{sessionId}` is fully matched by that prefix — so retrieval
> returns all of a requester's session summaries. If you change the strategy's
> `namespaces`, keep the `incidents/{actorId}/...` prefix (or update
> `retrieve_past_incidents()` to match), or retrieval returns nothing.

> **Disable / no-op:** if `memories[]` is empty (and no `MEMORY_ID` is set), the
> Memory code degrades gracefully to a no-op — tickets are still resolved, just
> without cross-incident recall. Remove it with
> `agentcore remove memory --name ITIncidentAgentMemory`.

**Local dev:** Memory is not available during `agentcore dev`. To test against the
deployed Memory resource, set `MEMORY_ID=<deployed-id>` in `agentcore/.env.local`
(get the ID from `agentcore status` or the `MemoryId` stack output).

### Knowledge Base

The KB is **auto-created** by default using S3 Vectors (fully managed, zero
prerequisites). The deploy creates a Bedrock Knowledge Base with
`amazon.titan-embed-text-v2:0` embeddings, an S3 data source pointing at `kb-docs/`,
and the `query-kb` Gateway tool. **Ingestion runs automatically on deploy** (the
seeder custom resource calls `start_ingestion_job` once the KB + data source exist).

Re-index manually (e.g. after editing `kb-docs/` without a redeploy):

```bash
agentcore status   # get KB_ID + DataSourceId from outputs
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <KB_ID> --data-source-id <DataSourceId> --region $AWS_REGION
```

- **Use a pre-existing KB:** set `KB_ID=<your-kb-id>` in `.env` before deploying.
  Auto-ingestion only applies to the stack-created KB; manage ingestion yourself for
  a reused KB.
- **Disable the KB tool entirely:** set `SKIP_KB=true` in `.env`.

### Add a new tool

1. **Create the Lambda:** add `lambdas/tools/my_tool.py`.
2. **Create the schema:** add `tool-schemas/my-tool.json`.
3. **Register via CLI:**
   ```bash
   agentcore add gateway-target --name my-tool --type lambda-function-arn \
     --tool-schema-file tool-schemas/my-tool.json --gateway ITIncidentGateway --lambda-arn PLACEHOLDER
   ```
4. **Wire in CDK:** add the Lambda to `infra-construct.ts` and update `lambdaArnMap`.
5. **Deploy:** `agentcore deploy -y --target dev`.

### Online evaluation

Declared in `agentcore.json` → `onlineEvalConfigs[]`. To recreate via CLI:

```bash
agentcore add online-eval --name ITIncidentAgentEval --runtime ITIncidentAgent \
  --evaluator Builtin.Correctness Builtin.Helpfulness Builtin.ToolSelectionAccuracy Builtin.GoalSuccessRate \
  --sampling-rate 100
```

Set `onlineEvalConfigs` to `[]` to disable. Full setup, required OTEL env vars, and
verification commands are in [online-evaluation.md](online-evaluation.md).

### Enterprise auth (CUSTOM_JWT)

```bash
./scripts/enable-custom-jwt.sh             # interactive Auth0/OIDC setup
```

Or store the credential manually (the agent never sees the secret):

```bash
agentcore add credential --name auth0-m2m --type oauth \
  --client-id <CLIENT_ID> --client-secret <CLIENT_SECRET> \
  --discovery-url https://<TENANT>.auth0.com/.well-known/openid-configuration
```

Then set `GATEWAY_AUTH_MODE=CUSTOM_JWT`, `GATEWAY_OAUTH_PROVIDER_NAME=auth0-m2m`, and
`GATEWAY_OAUTH_AUDIENCE=...` in `.env` and redeploy. See
[custom-jwt-auth-upgrade.md](custom-jwt-auth-upgrade.md) for Google/Okta/Microsoft.

---

## Cleanup

```bash
./scripts/destroy.sh
```

Or manually:

```bash
agentcore remove all -y
agentcore deploy -y --target dev   # deploys empty state, tears down CloudFormation
```

---

## Troubleshooting

| Issue | Solution |
| ----- | -------- |
| `agentcore validate` says "Required file not found: aws-targets.json" | Fresh clone — create it from the template: `cp agentcore/aws-targets.json.template agentcore/aws-targets.json` and fill in your account ID + region. |
| `agentcore validate` says "Deployed state contains target names not present in aws-targets" | The `name` in `aws-targets.json` must match the deployed target in `agentcore/.cli/deployed-state.json` (default: `dev`). If you tore down the stack, reset `.cli/deployed-state.json` to `{"targets": {}}`. |
| `cdk synth`/`deploy` fails with an esbuild error | esbuild's platform-specific binary did not finish installing. Run `npm rebuild esbuild` (or `rm -rf node_modules && npm install`) in `agentcore/cdk/`. |
| Empty `package-lock.json` appears at the project root after `npm install` | Harmless npm quirk (parent dir has no `package.json`). Safe to delete. |
| `agentcore deploy` fails with "S3VectorsConfiguration: required key [IndexArn] not found" | The CDK must explicitly create `AWS::S3Vectors::VectorBucket` and `AWS::S3Vectors::Index` and pass their ARNs/name into the KB's `s3VectorsConfiguration` — CloudFormation does NOT auto-create S3 Vectors resources. Delete the `ROLLBACK_COMPLETE` stack (`aws cloudformation delete-stack --stack-name <stack>`) and redeploy. |
| Agent returns "AccessDeniedException: GetResourceOauth2Token on auth0-m2m" | `.env` has `GATEWAY_AUTH_MODE=CUSTOM_JWT` but the `auth0-m2m` credential was removed. Change to `GATEWAY_AUTH_MODE=AWS_IAM` and redeploy (the deploy script bakes `GATEWAY_AUTH_MODE` into the Runtime env vars). |
| `agentcore deploy` fails on container build | Ensure Docker is running. Check CodeBuild logs in the AWS console. |
| `agentcore dev` says "No agentcore project found" | Run from the project root (not the parent dir). The CLI looks for `agentcore/agentcore.json` in CWD. Verify `runtimes` is not empty in `agentcore.json`. |
| `agentcore dev` Web UI shows "Workload access token has not been set" | The Web UI doesn't send the `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header that `@requires_access_token` needs under CUSTOM_JWT. Set `GATEWAY_AUTH_MODE=AWS_IAM` in `agentcore/.env.local` and restart, or test via `curl` on port 8082 with the header. |
| Gateway returns 403 | Runtime IAM role needs `bedrock-agentcore:InvokeGateway` (already configured). Check the role trust policy includes `bedrock-agentcore.amazonaws.com`. |
| `publish_ticket.sh` says "Could not find TicketsTopicArn" | Stack not deployed yet, or region mismatch. The stack is in `us-west-2` — if `AWS_REGION` differs, set `DEPLOY_REGION=us-west-2`. Run `agentcore status` to verify. |
| Trigger Lambda says "Invalid length for runtimeSessionId" | Session ID must be ≥33 chars; the trigger Lambda generates a compliant ID. If you see this after a manual Lambda code update, ensure you deployed the latest `lambdas/` directory. |
| Trigger Lambda says "AGENT_RUNTIME_ARN: PENDING" | The CDK wires the Runtime ARN post-creation. Run a fresh `agentcore deploy -y --target dev` or set the env var via `aws lambda update-function-configuration`. |
| "The provided model identifier is invalid" during `agentcore dev` | (1) Verify `AWS_REGION=us-west-2` is set in `agentcore/.env.local`. (2) Verify `AGENT_MODEL_ID` includes the full version (e.g. `us.anthropic.claude-sonnet-4-6`). (3) Check availability: `aws bedrock list-foundation-models --region us-west-2 --query 'modelSummaries[?contains(modelId, \`claude\`)].modelId'`. |
| Agent returns empty resolution | Check `agentcore logs --since 10m` for errors. Common cause: model access not enabled in the Bedrock console. |
| KB tool returns no results | KB requires a data source + completed ingestion job. Check the KB status in the Bedrock console. |
| Build times out | ARM64 CodeBuild can be slow. The CLI handles retries automatically. |
| Memory events not persisting | Verify `MEMORY_ITINCIDENTAGENTMEMORY_ID` env var is set in the runtime (check `agentcore status`). |
| Online eval deploy fails: "Access denied when accessing index policy for aws/spans" | The stack auto-enables Transaction Search via `transaction_search.py`, but the `/aws/spans` log group can take 10–15 minutes to provision on first deploy. Wait, then redeploy. Fallback: `aws application-signals start-monitoring --region us-west-2`. To skip eval, set `onlineEvalConfigs: []`. |
| Online eval shows no results | Enable **CloudWatch Transaction Search** in the region; eval requires traces to exist first. |
| Deploy hangs on a custom resource | If a custom-resource Lambda fails to import a module, CloudFormation waits 1 hour. This project uses the CDK Provider framework to prevent that. If it happens, `aws cloudformation cancel-update-stack` then fix the Lambda code. |
| CDK synth "Cannot find asset" | Path resolution issue. The project uses `process.cwd()` instead of `__dirname` for reliable paths in compiled TypeScript. Maintain this pattern if you modify CDK code. |
| CUSTOM_JWT auth fails with "credential not found" | Run `agentcore add credential --name <GATEWAY_OAUTH_PROVIDER_NAME> ...` first. The name must exactly match `GATEWAY_OAUTH_PROVIDER_NAME` in `.env`. |
| Jira MCP returns 401 / "invalid_grant" | The 3LO callback URL on the Atlassian app must exactly match `JiraOauthCallbackUrl` from stack outputs. If consent was never granted, check runtime logs for the consent URL. |
| Jira tools not appearing in agent | Ensure `JIRA_OAUTH_CLIENT_ID` is set in `.env` AND the deploy completed after setting it. Check `agentcore logs` for "Jira integration not configured". |
| "Atlassian consent required" in logs | One-time setup: open the logged URL, authenticate as the Jira user, approve scopes. AgentCore caches the refresh token for future invocations. |
| Agent resolves ticket but Jira issue unchanged | Verify the agent is in Jira mode (`"mode": "jira"` in the response). Check that Jira scopes include `write:jira-work`. |

---

## Known configuration notes

| Item | Detail |
| ---- | ------ |
| **`agentcore.json` `credentials.discoveryUrl`** | Points to `accounts.google.com` — correct when using Google as the OIDC provider. For Auth0, replace with `https://TENANT.auth0.com/.well-known/openid-configuration`. |
| **`agentcore.json` `memories[]`** | A Memory resource (`ITIncidentAgentMemory`, SUMMARIZATION, namespace `incidents/{actorId}/{sessionId}`) is provisioned by the L3 `AgentCoreApplication` construct. The L3 injects `MEMORY_ITINCIDENTAGENTMEMORY_ID`, which `config.py` reads as `MEMORY_ID`. If `memories[]` is emptied, the code degrades to a no-op. SUMMARIZATION namespaces must include `{sessionId}`. |
| **Model ID alignment** | `model/load.py` defaults to `claude-sonnet-4-6`. The deployed runtime gets `AGENT_MODEL_ID` from `agentcore.json` → `runtimes[].envVars[]` (rendered by the L3); the Python default is the local-dev fallback only. Editing `.env` does not change the deployed model — edit `agentcore.json`. |
| **`FAST_MODEL_ID`** | Declared in `agentcore.json` → `runtimes[].envVars[]` (`us.anthropic.claude-haiku-4-5-20251001-v1:0`, fastest/cheapest for LOW priority) and rendered into the runtime by the L3. |
| **Gateway `authorizerType`** | `agentcore.json` declares `AWS_IAM`, but CDK can override to `CUSTOM_JWT` via env var at deploy time. Agent code handles both modes dynamically. |
| **`query-kb` target** | Always present in `agentcore.json` but gracefully removed by CDK `patchMcpSpecArns()` when no KB is available (`SKIP_KB=true` or no `KB_ID`). |
| **Jira MCP transport** | Uses SSE (`sse_client`) while the Gateway uses streamable HTTP (`streamablehttp_client`) — intentional, since Atlassian's server uses the SSE protocol. |
