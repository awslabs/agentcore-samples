# Real-World Example — User → Frontend → Agent on Runtime → Gateway → Graph (Entra)

A production-shaped deployment of Use Case 2: **two OBO exchanges in one chain**, with user identity preserved end-to-end.

```
👤 User
  ↓ sign-in
🖥️  FastAPI BFF
  ↓ Bearer T_user
🤖 Strands agent on AgentCore Runtime
  ↓ OBO #1 → T_gateway
🛡  AgentCore Gateway  (OpenAPI target → Microsoft Graph)
  ↓ OBO #2 → T_graph  (transparent — no agent code involved)
🎯 Microsoft Graph /me
```

## How to use this tutorial

1. **`README.md`** (this file) — the setup checklist. Follow Prerequisites and Quick Start to get the stack deployed and the frontend running.
2. **[`ARCHITECTURE.md`](./ARCHITECTURE.md)** — design rationale, the three Entra apps, the two credential providers, the request lifecycle, the three tokens decoded.
3. **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** — six chapters you walk through *after* the stack is running, focused on observing the two-OBO mechanics in the logs and tokens.

## Prerequisites

### Tooling

| Tool | Minimum version | Install |
|---|---|---|
| **Python** | 3.10+ | Your package manager |
| **Node.js** | 20+ | `brew install node` / nvm / mise |
| **AWS CLI v2** | 2.x | [docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| **AWS CDK CLI** | 2.1129.0 (see note) | `npm install -g aws-cdk@2.1129.0` |
| **AgentCore CLI** (`@aws/agentcore`) | 0.21.1+ | `npm install -g @aws/agentcore@0.30.0` |
| **Azure CLI** (only if using automated IdP setup) | 2.50+ | `brew install azure-cli` |

> If you have the deprecated `bedrock-agentcore-starter-toolkit` Python CLI installed, uninstall it first (`pip uninstall bedrock-agentcore-starter-toolkit`) — it shadows the new Node CLI and `agentcore validate` will fail with "No such command".

> Version pins learned the hard way: **`aws-cdk@3` is deprecated** (accidentally published). Stay on `aws-cdk@2.x` — 2.1129.0 or later. AgentCore CLI **0.11 and older mishandle newer cdk-lib schemas** — use 0.21.1+.

#### Pre-flight version check

Run this before starting; every line should pass:

```bash
python3 --version                       # 3.10+
node --version                          # v20+
aws --version                           # 2.x
cdk --version                           # 2.1129.0+
agentcore --version                     # 0.21.1+
az version --query '"azure-cli"'        # 2.50+, only if using automated IdP setup
```

If `cdk --version` prints an older version after `npm install -g`, your shell has cached the old binary location. Run `hash -r` (bash) or `rehash` (zsh), or open a fresh terminal. Also run `which -a cdk` to confirm there aren't multiple `cdk` binaries on PATH — mise + Homebrew + Volta can each ship one and the first-on-PATH wins.

### AWS permissions on YOUR caller

Same baseline as Use Case 1, plus Gateway-related permissions:

- **CloudFormation** — `cloudformation:*` on `CDKToolkit` and `AgentCore-<AGENT_RUNTIME_NAME>-default`.
- **IAM** — `iam:CreateRole`, `iam:GetRole`, `iam:PutRolePolicy` (script auto-creates the Gateway service role in step 5), plus the standard role management the AgentCore CLI needs for the agent's execution role.
- **S3 + ECR** — CDK staging.
- **Bedrock** — `bedrock:InvokeModel*` on Claude Sonnet 4.5 (default model).
- **AgentCore control plane** — `bedrock-agentcore-control:*` on workloads, credential providers, gateways, and gateway targets.
- **STS** — `sts:GetCallerIdentity`.

For a personal dev account, `AdministratorAccess` covers everything.

### Bedrock model access

In the **Bedrock console** in your region → **Model access** → request access to Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-...`). This is a console toggle separate from IAM — without it, the agent deploys but invocations fail with `AccessDeniedException` from Bedrock.

### CDK bootstrap

`cdk bootstrap aws://<account>/<region>` once per account/region if you haven't before.

### AWS credentials — one source, verified

`agentcore deploy` runs `cdk deploy` as a subprocess. The subprocess starts a fresh AWS credential chain — if your shell has both `AWS_PROFILE` and `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY` set, cdk may pick one, fail, and refuse to fall back with:

```
CDK deploy failed: Need to perform AWS calls for account <id>, but no credentials have been configured
```

Pick exactly one source, then verify:

```bash
# Option A: profile (recommended for SSO / IAM Identity Center)
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE=your-profile

# Option B: static/temporary keys (make sure to include AWS_SESSION_TOKEN
# if you're using assume-role or SSO-issued temp creds)
unset AWS_PROFILE
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=...

# Verify — must succeed in the same shell you'll run agentcore from
aws sts get-caller-identity
```

If `aws sts get-caller-identity` works but `agentcore deploy` still errors on credentials, one of the env vars from the *other* option is still set. Re-check with `env | grep '^AWS_'`.

If you're on SSO and temp creds have expired: `aws sso login --profile "$AWS_PROFILE"`.

### Gateway service role

The Gateway needs an IAM role it can assume to call AgentCore Identity for OBO #2 and to read the credential provider's secret from Secrets Manager. **`deploy/02_create_gateway.py` creates this role for you** if one doesn't already exist under the conventional name `AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>`. The role's inline policy grants exactly:

- `bedrock-agentcore:GetWorkloadAccessTokenForJWT` / `GetWorkloadAccessToken` / `GetResourceOauth2Token` (for OBO #2)
- `secretsmanager:GetSecretValue` scoped to `bedrock-agentcore-identity!default/oauth2/*`
- CloudWatch Logs permissions scoped to `/aws/bedrock-agentcore/gateway*`

Trust policy allows `bedrock-agentcore.amazonaws.com` with account-scoped `SourceAccount` and `SourceArn` conditions (confused-deputy guard).

Two ways to override:

- **Bring your own role:** set `GATEWAY_SERVICE_ROLE_ARN=<arn>` in `.env` or your shell before running step 5. The script honors that and skips role creation.
- **Bring the conventional name:** if a role named `AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>` already exists, the script reuses it and re-applies the inline policy (idempotent).

Your caller needs `iam:CreateRole`, `iam:GetRole`, `iam:PutRolePolicy` for this to work. If you don't want the script creating IAM roles for you, create the role yourself with the trust and permission policies above and set `GATEWAY_SERVICE_ROLE_ARN` in `.env`.

## Quick start

### 1. Complete IdP setup

Follow [`IDP_SETUP.md`](./IDP_SETUP.md). Two paths, you pick:

- **Automated** (~30 seconds): `python deploy/00_create_entra_apps.py` — drives the Azure CLI to register all three apps, set scopes, add API permissions, link them via `knownClientApplications`, grant admin consent, mint secrets, and write `.env`. See `IDP_SETUP.md` → "Quick path (automated)".
- **Manual** (~15 minutes): click through the Entra admin console. See `IDP_SETUP.md` → "Manual path".

Either way, by the end you have a populated `.env` with three app IDs, three secrets, and the two scope URIs.

### 2. Configure `.env`

If you used the automated path in step 1, your `.env` is already populated — skip ahead.

If you took the manual path:

```bash
cd obo-training/examples/02-agent-via-gateway/entra/real-world
cp config.example.env .env
# Fill in every placeholder
```

### 3. Install Python tooling and pre-flight the environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node.js CLIs — pin the versions we verified against
npm install -g aws-cdk@2.1129.0 @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

Then pre-flight — every line should pass before you move on:

```bash
# 1. Tool versions
python -c "import boto3; assert tuple(int(x) for x in boto3.__version__.split('.')[:3]) >= (1,43,2), boto3.__version__; print('boto3', boto3.__version__)"
cdk --version         # 2.1129.0+
agentcore --version   # 0.21.1+

# 2. AWS credentials — see the "AWS credentials" section under Prerequisites.
env | grep '^AWS_'   # confirm only ONE of AWS_PROFILE or the AWS_ACCESS_KEY set
aws sts get-caller-identity
```

If any of those fail, fix it now — every later step assumes they all pass.

### 4. Create AgentCore Identity resources (workload + 2 credential providers)

```bash
python deploy/01_create_providers.py
```

Creates the agent's workload identity, the agent-actor credential provider (auths as AgentApp; used by agent code for OBO #1), and the gateway-actor credential provider (auths as GatewayApp; used by Gateway for OBO #2).

### 5. Create the Gateway and its OpenAPI target

```bash
python deploy/02_create_gateway.py
```

Does three things:

1. **Auto-creates the Gateway service role** (`AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>`) with the trust and OBO permissions the Gateway needs. Skipped if `GATEWAY_SERVICE_ROLE_ARN` is set in `.env` or your shell, or if a role with that conventional name already exists.
2. Creates the Gateway with inbound `customJWTAuthorizer` against Entra (audience = GatewayApp).
3. Creates one OpenAPI target backed by `gateway/graph_openapi.json` with outbound OBO using the gateway-actor credential provider.

Writes `GATEWAY_MCP_URL` and `GATEWAY_SERVICE_ROLE_ARN` back into `.env`.

The script waits 10 seconds after creating the role so IAM propagation completes before the Gateway API tries to assume it. On re-runs this wait is skipped.

### 6. Scaffold the agent project (AgentCore CLI — non-interactive)

Run from inside `real-world/`. Sanity-check the working directory first, then source `.env` and run with all the right flags so the CLI doesn't prompt:

```bash
[ -f ./config.example.env ] && [ -d ./deploy ] && [ -d ./frontend ] && [ -d ./agent ] && [ -d ./gateway ] \
  || { echo "✗ Not in real-world/. cd into obo-training/examples/02-agent-via-gateway/entra/real-world/ first."; return 1 2>/dev/null || exit 1; }

set -a && source .env && set +a && \
agentcore create \
  --name "$AGENT_RUNTIME_NAME" \
  --framework Strands \
  --model-provider Bedrock \
  --memory none \
  --build CodeZip \
  --defaults
```

This creates `$AGENT_RUNTIME_NAME/` next to `gateway/`, with a placeholder `app/$AGENT_RUNTIME_NAME/main.py`.

### 7. Copy the OBO-aware agent code into the scaffolded project

```bash
# Still in real-world/
cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
cp agent/requirements.txt "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/requirements.txt 2>/dev/null || true
cd "$AGENT_RUNTIME_NAME"
```

All further `agentcore …` commands run from inside `$AGENT_RUNTIME_NAME/`. Helper Python scripts (`python ../deploy/...`) live one level up.

### 8. Patch `agentcore/agentcore.json` for inbound JWT + env vars

```bash
# From inside $AGENT_RUNTIME_NAME/
python ../deploy/03_patch_agentcore_json.py
```

This adds:

- `requestHeaderAllowlist: ["Authorization"]` — the JWT reaches the agent handler.
- `authorizerType: "CUSTOM_JWT"` with Entra's OIDC discovery URL and `allowedAudience = [AGENT_CLIENT_ID, api://AGENT_CLIENT_ID]`.
- `envVars` array — `AGENT_WORKLOAD_NAME`, `AGENT_OBO_PROVIDER_NAME`, `GATEWAY_SCOPE`, `GATEWAY_MCP_URL`, `AWS_REGION`.

### 9. Fill in your AWS deployment target

```bash
# From inside $AGENT_RUNTIME_NAME/
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-west-2}

cat > agentcore/aws-targets.json <<EOF
[{ "name": "default", "account": "$ACCOUNT_ID", "region": "$REGION" }]
EOF
```

### 10. Validate and deploy the agent

```bash
agentcore validate
agentcore deploy -y -v
```

First time in a new account/region you'll need `cdk bootstrap aws://<account>/<region>` first.

If deploy fails, in order of likelihood:

- **"CDK synth failed: Cloud assembly schema version mismatch"** — your `cdk` or `agentcore` CLI is behind. Re-check step 3's pre-flight; the required versions are pinned there.
- **"CDK deploy failed: no credentials have been configured"** — you have both `AWS_PROFILE` and `AWS_ACCESS_KEY_ID` set. Fix per the "AWS credentials" section in Prerequisites, then rerun.
- **"Bootstrap stack CDKToolkit is not up to date"** — `cdk bootstrap aws://<account>/<region>` and retry.

### 11. Grant the agent's execution role IAM permissions for OBO #1

```bash
# Still from inside $AGENT_RUNTIME_NAME/
python ../deploy/04_grant_agent_iam_permissions.py
```

Attaches the inline OBO policy (`GetWorkloadAccessTokenForJWT`, `GetResourceOauth2Token`, `secretsmanager:GetSecretValue` on the AgentCore-managed OAuth secrets prefix only) to the auto-created execution role.

**How it finds the role** (in order): explicit `AGENT_EXECUTION_ROLE_NAME` env var → CloudFormation stack `AgentCore-<AGENT_RUNTIME_NAME>-default` (most reliable — the AgentCore CLI names it deterministically) → IAM name scan by truncated prefix. If it can't find a role, it prints exactly what to look for.

If you already know the role name (e.g., from the CloudFormation console), skip the auto-lookup:

```bash
AGENT_EXECUTION_ROLE_NAME=AgentCore-oboUc2EntraAgen-ApplicationAgentOboUc2Ent-xxxxxx \
  python ../deploy/04_grant_agent_iam_permissions.py
```

### 11b. Set log retention and confirm observability config

Optional but recommended. AgentCore emits logs to CloudWatch automatically — this step just caps retention (default is never-expire) and prints handy debug commands:

```bash
# From real-world/ (one level up from $AGENT_RUNTIME_NAME/)
cd ..
python deploy/05_enable_observability.py            # 30-day retention
# Or: python deploy/05_enable_observability.py --retention 7
```

Prints a quick-reference for `agentcore logs` and `agentcore traces`, and links to the one-time CloudWatch console toggles for **Application Signals** (needed for `agentcore traces` to work) and **Transaction Search** (needed for `agentcore logs --query` to search log content).

### 12. Set the invoke URL

Run this snippet from inside `$AGENT_RUNTIME_NAME/` — it parses `agentcore status`, appends `?qualifier=DEFAULT`, and writes `AGENT_RUNTIME_INVOKE_URL` into `../.env`. This avoids the hand-copy trap where long URLs get truncated (a very common cause of `404 UnknownOperationException`).

```bash
python3 <<'EOF'
import pathlib, re, subprocess, sys
out = subprocess.check_output(["agentcore", "status"], text=True)
m = re.search(r"https://bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com/runtimes/\S+/invocations", out)
if not m:
    print("ERROR: could not find invoke URL in `agentcore status` output.", file=sys.stderr)
    print(out, file=sys.stderr)
    sys.exit(1)
url = m.group(0) + "?qualifier=DEFAULT"
p = pathlib.Path("../.env")
lines = p.read_text().splitlines()
for i, line in enumerate(lines):
    if line.startswith("AGENT_RUNTIME_INVOKE_URL="):
        lines[i] = f"AGENT_RUNTIME_INVOKE_URL={url}"
        break
else:
    lines.append(f"AGENT_RUNTIME_INVOKE_URL={url}")
p.write_text("\n".join(lines) + "\n")
print(f"✓ Wrote AGENT_RUNTIME_INVOKE_URL to ../.env")
print(f"  {url}")
EOF
```

Sanity check the URL is right before starting the frontend:

```bash
set -a && source ../.env && set +a
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "Authorization: Bearer not-a-real-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hi"}' \
  "$AGENT_RUNTIME_INVOKE_URL"
# Expect: 401 (auth failed = URL is correct)
#         404 = URL is wrong; see notes below
```

The URL format:
```
https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-arn>/invocations?qualifier=DEFAULT
```

> **⚠ Do NOT forget `?qualifier=DEFAULT`.** `agentcore status` prints the URL WITHOUT the qualifier — you must append it (the snippet above does this for you). Without it, the Runtime returns **`404 UnknownOperationException`** (older versions returned `400 missing qualifier`). The frontend surfaces this as "Agent returned 404: <UnknownOperationException/>".

If you're constructing the URL from a raw ARN instead:

```bash
python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" \
  "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/oboUc2EntraAgent-abc123"
```

### 13. Run the frontend

```bash
cd ..   # back to real-world/
python frontend/app.py
```

Open `http://localhost:8000`, click **Sign in with Microsoft**, sign in as a user from your tenant, then click **Ask agent**. You should see your real display name / job title rendered in plain text.

### 14. Walk the LEARNING_GUIDE

Switch to **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)**. Six chapters that walk you through observing each part of the chain in the logs / claims / network calls. Keep the stack running.

---

## Teardown

Order matters — the agent runtime must go first (CDK stack references the credential providers), then the AWS AgentCore resources, then optionally the local scaffolding and Entra apps.

```bash
# 1. Remove the agent runtime (from inside $AGENT_RUNTIME_NAME/)
cd "$AGENT_RUNTIME_NAME"
agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y
agentcore deploy -y -v

# 2. Remove AWS AgentCore resources + verify + clear deploy-populated .env values
cd ..
python deploy/teardown.py --clean-env
```

`python deploy/teardown.py` deletes (in reverse-dependency order):

- **Every** gateway target (not just the one this example creates — orphaned targets from earlier runs are cleaned up too), then waits for async deletion to complete.
- The Gateway.
- Both credential providers (agent-actor + gateway-actor).
- The workload identity.
- The Gateway service IAM role (only if named `AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>` or referenced by `GATEWAY_SERVICE_ROLE_ARN` in `.env`).

Then **verifies** every resource is actually gone and prints any survivors. If anything is still present after 30 seconds, re-run — AgentCore deletes are async and sometimes need a few seconds to finalize.

`--clean-env` clears deploy-populated values (`GATEWAY_MCP_URL`, `GATEWAY_SERVICE_ROLE_ARN`, `AGENT_RUNTIME_INVOKE_URL`) from `.env` so the next fresh run starts clean without stale references.

### Verify only (no deletes)

Useful if you want to sanity-check state without removing anything:

```bash
python deploy/teardown.py --verify-only
```

### Optional next steps

```bash
# Delete the scaffolded CLI project folder
rm -rf "$AGENT_RUNTIME_NAME"/

# Delete the Entra app registrations (they're free to keep, but this deletes them cleanly)
python deploy/00_delete_entra_apps.py --yes
```

The Entra app registrations are not touched by `teardown.py` — they're reusable OAuth client configs that cost nothing to keep. Use `00_delete_entra_apps.py` only if you want to burn them down between runs.

## Observability & debugging

AgentCore emits logs and traces to CloudWatch automatically — no per-resource opt-in needed. This section catalogs where things live and how to look at them.

### Log group naming

| Resource | Log group prefix | What's in it |
|---|---|---|
| Runtime (agent) | `/aws/bedrock-agentcore/runtimes/<runtime-id>-*` | Agent handler stdout/stderr, OBO calls, MCP calls |
| Gateway | `/aws/bedrock-agentcore/gateways/<gateway-id>*` | Inbound MCP requests, JWT validation results, outbound OBO exchanges, downstream calls |

The exact suffixes depend on the AgentCore CLI version. To list every UC2 log group in one shot:

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /aws/bedrock-agentcore \
  --region us-west-2 \
  --query 'logGroups[?contains(logGroupName, `oboUc2Entra`) || contains(logGroupName, `obo-uc2-entra`)].logGroupName' \
  --output table
```

### Reading logs

Use the AgentCore CLI when you can — it knows the naming convention:

```bash
# From inside $AGENT_RUNTIME_NAME/
agentcore logs --since 10m                                # tail last 10 minutes
agentcore logs --since 10m --level warn                   # warn+ only
agentcore logs --since 30m --query "OBO"                  # substring search (needs Transaction Search)
```

Fallback to raw CloudWatch when a specific log group or query is needed:

```bash
aws logs tail /aws/bedrock-agentcore/runtimes/<runtime-id>* --since 10m --follow --region us-west-2
```

### Traces

```bash
agentcore traces list --runtime oboUc2EntraAgent          # last N invocations
agentcore traces get <trace-id>                           # download one trace to JSON
```

Each trace shows the request lifecycle: JWT validation → agent handler entry → OBO #1 call → MCP session open → tool call → response. Traces need **CloudWatch Application Signals** enabled once per account/region.

### Metrics

- CloudWatch namespace: `AWS/BedrockAgentCore` (runtimes, gateways).
- Common metrics: `Invocations`, `Errors`, `Latency`, `WorkloadTokenExchanges`.
- Access from the CloudWatch → Metrics console.

### Two one-time CloudWatch toggles that unlock features

These are account-wide, not per-resource. Do them once per account/region:

1. **Application Signals** — enables the `agentcore traces` command to actually find traces. Console: CloudWatch → Application Signals → "Enable". Or:

    ```bash
    open 'https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#application-signals-services'
    ```

2. **Transaction Search** — enables full-text log content search (required by `agentcore logs --query "..."`). Console: CloudWatch → Logs Insights → Transaction Search → "Enable". Or:

    ```bash
    open 'https://us-west-2.console.aws.amazon.com/cloudwatch/home?region=us-west-2#logsV2:logs-transaction-search'
    ```

`deploy/05_enable_observability.py` prints these links plus a copy-pasteable reference for the current runtime + gateway.

### The three most-useful debug queries

**Did OBO #1 succeed?** Look at the agent's runtime log for a successful `GetResourceOauth2Token` call.

```bash
agentcore logs --since 5m --query "OBO"
# Or: search for the error string surfaced by agent.py
agentcore logs --since 5m --query "OBO #1 failed"
```

**Did Gateway validate T_gateway?** Gateway logs the JWT claim it received and the discovery-URL issuer it compared against.

```bash
aws logs tail "/aws/bedrock-agentcore/gateways/<gateway-id>*" --since 5m --region us-west-2 | grep -i "iss\|jwt\|401"
```

**Which credentials did AgentCore Identity use for OBO #2?** CloudTrail shows the `GetSecretValue` from the Gateway service role.

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --region us-west-2 --max-results 10 \
  --query 'Events[?contains(Resources[0].ResourceName, `bedrock-agentcore-identity`)].[EventTime,Username,Resources[0].ResourceName]' \
  --output table
```

## Folder structure

```
real-world/
├── README.md              ← this file
├── ARCHITECTURE.md        ← design + request lifecycle + 3 tokens
├── LEARNING_GUIDE.md      ← 6-chapter hands-on tour
├── IDP_SETUP.md           ← 3-app Entra registration steps
├── requirements.txt
├── config.example.env
│
├── frontend/              ← FastAPI BFF + HTML UI
│   ├── app.py
│   ├── templates/{home.html, result.html}
│   └── README.md
│
├── agent/                 ← canonical OBO-aware Strands agent
│   ├── agent.py           ← copied into app/<name>/main.py in step 7
│   ├── requirements.txt
│   └── README.md
│
├── gateway/               ← Gateway target spec
│   ├── graph_openapi.json
│   └── README.md
│
└── deploy/                ← helper scripts that complement the AgentCore CLI
    ├── 00_create_entra_apps.py         ← automates 3-app Entra setup
    ├── 00_delete_entra_apps.py         ← removes Entra apps (teardown helper)
    ├── 01_create_providers.py          ← workload + 2 OBO credential providers
    ├── 02_create_gateway.py            ← Gateway + OpenAPI target + service role
    ├── 03_patch_agentcore_json.py      ← inbound JWT + env vars for the agent
    ├── 04_grant_agent_iam_permissions.py  ← attaches OBO policy to agent role
    ├── 05_enable_observability.py      ← log retention + CloudWatch quick-refs
    ├── compare_obo_claims.py           ← decode + diff T_user, T_gateway, T_graph
    └── teardown.py                     ← removes AWS resources
```

`agentcore/` and `app/` get added by the AgentCore CLI when you run `agentcore create` in step 6.

## How this differs from Use Case 1

| Aspect | UC1 | UC2 |
|---|---|---|
| OBO hops | 1 | 2 |
| Agent calls Graph directly? | Yes | No (Gateway does) |
| Entra app registrations | 2 | 3 |
| Credential providers | 1 | 2 |
| `knownClientApplications` links | 1 | 2 |
| Where Graph permission lives | AgentApp | GatewayApp |
| Token chain length | T_user → T_graph | T_user → T_gateway → T_graph |

The new artifact UC2 lets you observe is the **`oid` claim staying constant across three different audiences while `azp` rotates frontend → agent → gateway**. That's the proof of identity propagation. The LEARNING_GUIDE walks through it.
