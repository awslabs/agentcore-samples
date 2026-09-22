# Real-World Example — User → Frontend → Agent on Runtime → OBO exchange + Okta /v1/userinfo

A production-shaped deployment of Use Case 1 (Okta flavor). Everything runs where it would in real life: the frontend on your laptop or a container, the agent on AgentCore Runtime, and a two-call downstream pattern — the agent does an OBO exchange for a custom-scope token (to prove the exchange semantics), and uses the inbound user token to read `/v1/userinfo` for profile fields.

Uses the [Node.js-based AgentCore CLI (`@aws/agentcore`)](https://github.com/aws/agentcore-cli) — same CLI as the Entra real-world variant. The workflow here is the Okta adaptation of [the AgentCore CLI workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/c770f35f-90a9-4e02-8985-4ef912bddb77/en-US).

## How to use this tutorial

1. **This `README.md`** — the setup checklist. Follow the Prerequisites and Quick Start sections to get the stack deployed and the frontend running.
2. **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** — a 6-chapter hands-on tour you walk through *after* the stack is running. Each chapter has an objective, an action (usually "click this / watch that log"), the expected result, and a teaching observation.
3. **[`ARCHITECTURE.md`](./ARCHITECTURE.md)** — design decisions and the request lifecycle.

The `local/` variant next door is a single-script condensed version of the OBO mechanics — run that first if you want to understand OBO without deploying anything.

## Architecture

```
┌────────────────┐     1. browser sign-in via Okta (authlib)
│   Browser      │ <─────────────────────────┐
│   (HTML+JS)    │                            │
└────────┬───────┘                            │
         │ session cookie                    ┌┴────────────────┐
         ↓                                    │ Okta            │
┌────────────────┐    2. POST /ask            │ Authorization   │
│  FastAPI BFF   │ ──────────────┐            │ Server          │
│  (frontend)    │               │            └┬────────────────┘
└────────┬───────┘               │              ↑
         │ Bearer: user JWT      │              │ 5a. Token Exchange (RFC 8693)
         │ (aud=api://default,   │              │     grant_type=token-exchange
         │  cid=<Web App>,       │              │     subject_token=<user JWT>
         │  scp=openid profile   │              │     scope=agent.downstream
         │       email)          │              │     → downstream token
         ↓                       │              │       (cid=<Service App>,
┌────────────────┐    3. invoke  │              │        sub=<same user>,
│ AgentCore      │ ──────────────┘              │        scp=agent.downstream)
│ Runtime        │    4. inbound JWT validated  │
│ (Strands agent)│       (customJWTAuthorizer)  │
└────────┬───────┘                              │
         │                                       │
         │ 6a. (in production) call your own API │
         │     with the downstream token          │
         │                                       │
         │ 6b. (in this demo) call /v1/userinfo  │
         │     with the INBOUND user token       │
         │     — userinfo requires openid, which │
         │     Okta won't issue via exchange     │
         ↓                                       │
┌────────────────┐                               │
│ Okta           │                               │
│ /v1/userinfo   │ ──────────────────────────────┘
└────────────────┘
```

> **Why two separate downstream calls?** Okta's Token Exchange grant refuses `openid` (reserved for sign-in), and Okta's OIDC spec refuses `profile`/`email` without `openid`. So an OBO-minted token cannot include any OIDC scope. That's actually the realistic production pattern — OBO tokens are for your own resource servers with custom scopes, not for the IdP's identity endpoints. The example does both: the OBO exchange mints a custom-scope token (so you can see the exchange semantics), and the inbound user token is used for the userinfo call (so the UI has something to display).

Four moving parts:

| Component | What it is | Where it runs |
|---|---|---|
| **Frontend** | FastAPI BFF with a minimal HTML UI | Your laptop / any container |
| **Agent** | Strands agent with an HTTP handler | AgentCore Runtime |
| **Okta** | 2 app registrations (Web App + Service App), 1 custom auth server | Okta cloud |
| **AgentCore Identity** | Workload identity + 1 OBO-enabled credential provider | AWS account |

## What you'll deploy

Two Okta app registrations (see [`IDP_SETUP.md`](./IDP_SETUP.md)):

1. **Web App (frontend)** — user-facing OIDC client with redirect URI pointing to the FastAPI BFF.
2. **Service App (agent)** — the middle-tier confidential client that does OBO. Has **Token Exchange** enabled and **Proof of possession** (DPoP) disabled.

One AgentCore workload with credential provider (see [`deploy/01_create_providers.py`](./deploy/01_create_providers.py)):
- Workload identity for the agent.
- One `CustomOauth2` credential provider configured with `TOKEN_EXCHANGE` grant for the OBO flow.

> **Note:** This variant creates **new** AgentCore resources (`obo-usecase1-okta-realworld`, `obo-uc1-okta-realworld-actor`) that are separate from the `local/` variant. Running the two variants in the same AWS account is safe — they don't share resources. If you'd rather reuse the same workload identity and credential provider across both, point `WORKLOAD_NAME` and `ACTOR_PROVIDER_NAME` in both `.env` files at the same values.

The Strands agent is deployed via the Node.js AgentCore CLI (`agentcore create`, `agentcore deploy`). The workflow is spelled out in the Quick start below.

## Prerequisites

### Tooling

| Tool | Why | How to install |
|---|---|---|
| **Python 3.10+** | Frontend, helper scripts, agent | Your package manager |
| **Node.js 20+** | AgentCore CLI (`@aws/agentcore`) is a Node CLI | `brew install node` / nvm |
| **AWS CLI v2** | Credentials, bootstrap verification | [docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| **AWS CDK CLI** | AgentCore CLI deploys via CDK under the hood | `npm install -g aws-cdk` |
| **AgentCore CLI** | Scaffolds + deploys the Runtime | `npm install -g @aws/agentcore@0.30.0` |

> Uninstall the deprecated `bedrock-agentcore-starter-toolkit` Python CLI first if it's on your system — it shadows the new Node CLI: `pip uninstall bedrock-agentcore-starter-toolkit` (or `pipx uninstall` / `uv tool uninstall` depending on how you installed it).

### AWS permissions required on YOUR caller

Same list as the Entra real-world variant:

- **CloudFormation** — `cloudformation:*` on stacks `CDKToolkit` and `AgentCore-<AGENT_RUNTIME_NAME>-default`.
- **IAM** — create roles (`iam:CreateRole`, `iam:PutRolePolicy`, `iam:AttachRolePolicy`, `iam:PassRole`, etc.) for the agent's execution role.
- **S3 + ECR** — CDK staging bucket and image repo.
- **Bedrock** — `bedrock:InvokeModel*` on the Claude model you're using (default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`). *IAM permission alone is not enough — see "Bedrock model access" below.*
- **AgentCore control plane** — `bedrock-agentcore-control:*` on your workload + credential provider.
- **STS** — `sts:GetCallerIdentity`.

For a personal dev account, `AdministratorAccess` covers everything.

### AWS account / region bootstrap state

- **CDK bootstrap.** `cdk bootstrap aws://<account>/<region>` once per account+region if you haven't before.
- **Bedrock model access.** In the Bedrock console → Model access → request access to the Claude model. Console-level toggle, separate from IAM.
- **Single credential source.** Don't have both `AWS_PROFILE` and `AWS_ACCESS_KEY_ID` set at the same time. Unset whichever one you're not using.

### Okta access

- An Okta tenant where you can register apps and edit authorization server access policies.
- A test user to sign in as when demoing the frontend.
- Details in [`IDP_SETUP.md`](./IDP_SETUP.md).

## Quick start

This example follows the same flow as the Entra real-world variant: scaffold with the CLI, patch the config for Okta auth, deploy, invoke.

### 1. Complete IdP setup

Follow [`IDP_SETUP.md`](./IDP_SETUP.md). It walks you through registering two Okta apps (Web App for the frontend, Service App for the agent), configuring an authorization server, and wiring up two access policies.

### 2. Configure `.env`

```bash
cp config.example.env .env
# Fill in every placeholder
```

### 3. Install tooling

```bash
# Python (for frontend + helper scripts)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node.js AgentCore CLI (if not already installed from the Entra variant)
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

### 4. Create AgentCore Identity resources

```bash
python deploy/01_create_providers.py
```

Creates the workload identity and the `CustomOauth2` credential provider (with Okta's `TOKEN_EXCHANGE` grant) the agent uses for the OBO exchange. Preflights the discovery URL so obvious config mistakes fail fast.

### 5. Scaffold the agent project (AgentCore CLI)

Run this from inside `real-world/`. The one-liner sanity-checks your location, then sources `.env` and passes every flag so the CLI runs fully non-interactively.

```bash
[ -f ./config.example.env ] && [ -d ./deploy ] && [ -d ./frontend ] && [ -d ./agent ] \
  || { echo "✗ Not in real-world/. cd into obo-training/examples/01-agent-to-downstream/okta/real-world/ first."; return 1 2>/dev/null || exit 1; }

set -a && source .env && set +a && \
agentcore create \
  --name "$AGENT_RUNTIME_NAME" \
  --framework Strands \
  --model-provider Bedrock \
  --memory none \
  --build CodeZip \
  --defaults
```

Flag meanings (same as Entra):

| Flag | Value |
|---|---|
| `--name` | `$AGENT_RUNTIME_NAME` from `.env` (≤23 chars, alphanumeric, starts with letter) |
| `--framework` | `Strands` |
| `--model-provider` | `Bedrock` |
| `--memory` | `none` |
| `--build` | `CodeZip` |
| `--defaults` | Skip remaining prompts |

### 6. Copy the agent code into the scaffolded project

```bash
# Still in real-world/
cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
cp agent/requirements.txt "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/requirements.txt 2>/dev/null || true
cd "$AGENT_RUNTIME_NAME"
```

All further `agentcore …` commands run from inside `$AGENT_RUNTIME_NAME/`. Helper Python scripts (`python ../deploy/...`) still live one level up.

### 7. Patch `agentcore/agentcore.json` for Okta JWT inbound auth

```bash
# From inside $AGENT_RUNTIME_NAME/
python ../deploy/02_patch_agentcore_json.py
```

This adds:
- `requestHeaderAllowlist`: `["Authorization"]` so the JWT reaches the handler.
- `authorizerType`: `CUSTOM_JWT`.
- `authorizerConfiguration.customJwtAuthorizer`: Okta's OIDC discovery URL and your `OKTA_AUDIENCE` as `allowedAudience`.
- `envVars`: array of `{name, value}` objects covering workload name, credential provider name, downstream scope, Okta coordinates, region. The schema requires the array-of-objects shape — `environmentVariables` as an object map is silently dropped at deploy time.

> **What's different from Entra?** The `allowedAudience` is your Okta auth server's audience (typically `api://default`), not a client ID. Okta tokens are audienced at the auth server, and every token minted by that server has the same `aud` regardless of client — actor identity is in `cid`, not `aud`. The Runtime validates signature + issuer + audience + expiry; `cid` is not validated at this layer (it would be at your API layer if you had one).

### 8. Fill in your AWS deployment target

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=${AWS_REGION:-us-west-2}

cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "default",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
```

### 9. Validate and deploy

```bash
agentcore validate
agentcore deploy -y -v
```

First time in a new account/region: `cdk bootstrap aws://<account-id>/<region>` first.

### 10. Grant the agent's execution role OBO permissions

```bash
# Still from inside $AGENT_RUNTIME_NAME/
python ../deploy/03_grant_agent_iam_permissions.py
```

Attaches an inline policy with `GetWorkloadAccessToken{,ForJWT,ForUserId}`, `GetResourceOauth2Token`, and `secretsmanager:GetSecretValue` on AgentCore-managed OAuth secrets — all scoped to this workload. See the script for the full policy.

### 11. Get the invoke URL

```bash
agentcore status
```

Look for `Runtime: READY (arn:aws:bedrock-agentcore:...)`. Note the full ARN, then construct the invoke URL and paste it into `real-world/.env` (one level up) as `AGENT_RUNTIME_INVOKE_URL`.

Format:

```
https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-arn>/invocations?qualifier=DEFAULT
```

> **⚠ Do NOT forget the `?qualifier=DEFAULT` query parameter at the end.** Without it, the Runtime returns `400 Bad Request: missing qualifier` on every invocation and the frontend will render an opaque error. The Runtime uses the qualifier to pick which version of your agent to route traffic to; `DEFAULT` is the alias that points at whichever version was last deployed.

Worked example, end to end:

- `agentcore status` prints ARN `arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/oboUc1OktaAgent-abc123`.
- URL-encode it: `arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1OktaAgent-abc123`.
- Full invoke URL:
  ```
  https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1OktaAgent-abc123/invocations?qualifier=DEFAULT
  ```
- Paste into `.env`:
  ```
  AGENT_RUNTIME_INVOKE_URL=https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1OktaAgent-abc123/invocations?qualifier=DEFAULT
  ```

Quick URL-encoder:

```bash
python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" \
  "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/oboUc1OktaAgent-abc123"
```

Sanity check: the URL should contain `%3A` (encoded `:`) and `%2F` (encoded `/`), and end with `/invocations?qualifier=DEFAULT`. If either is missing, re-check.

### 12. Run the frontend

```bash
cd ..
python frontend/app.py
```

Open `http://localhost:8000`, click **Sign in with Okta**, sign in, then click **Ask agent**. You should see your name / email / preferred_username rendered in plain text.

### 13. Walk the learning guide

Now switch to **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** and walk through the six chapters. Keep the stack running while you do.

---

### Teardown

```bash
# From inside $AGENT_RUNTIME_NAME/
agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y
agentcore deploy -y -v

# From real-world/
cd ..
python deploy/teardown.py

# Optional: delete the scaffolded CLI project
rm -rf "$AGENT_RUNTIME_NAME"/

# Optional: clear the invoke URL from .env
sed -i.bak 's|^AGENT_RUNTIME_INVOKE_URL=.*|AGENT_RUNTIME_INVOKE_URL=|' .env && rm .env.bak
```

Okta apps are left in place — they're reusable configurations that cost nothing to keep.

## Folder structure

```
real-world/
├── README.md              ← this file
├── LEARNING_GUIDE.md      ← 6-chapter hands-on tour
├── ARCHITECTURE.md        ← design decisions + request lifecycle
├── IDP_SETUP.md           ← Okta setup for this variant
├── requirements.txt       ← frontend + deploy helper deps
├── config.example.env     ← env var template
│
├── frontend/              ← FastAPI BFF + HTML UI
│   ├── app.py             ← authlib-based Okta auth
│   ├── templates/{home.html, result.html}
│   └── README.md
│
├── agent/                 ← canonical OBO Strands agent
│   ├── agent.py           ← copied into app/<name>/main.py by you in step 6
│   ├── requirements.txt
│   └── README.md
│
├── deploy/                ← helper scripts around the AgentCore CLI
│   ├── 01_create_providers.py
│   ├── 02_patch_agentcore_json.py
│   ├── 03_grant_agent_iam_permissions.py
│   └── teardown.py
│
├── agentcore/             ← (generated by `agentcore create`)
│   ├── agentcore.json
│   ├── aws-targets.json
│   └── cdk/
│
└── app/                   ← (generated by `agentcore create`)
    └── <AGENT_RUNTIME_NAME>/
        ├── main.py        ← you replace this with a copy of agent/agent.py
        └── pyproject.toml
```

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for design rationale and the full request lifecycle.

## How this differs from the Entra real-world example

The overall shape is identical — two Python files in `agent/` and `frontend/`, four in `deploy/`, three docs. Differences are all about the IdP:

| Aspect | Entra | Okta |
|---|---|---|
| OBO protocol | RFC 7523 JWT Bearer | RFC 8693 Token Exchange |
| Credential provider vendor | `MicrosoftOauth2` (built-in OBO) | `CustomOauth2` with `TOKEN_EXCHANGE` grant |
| Exchange-time parameters | None — config does it | `customParameters={subject_token_type: ...}` + `audiences=[...]` on every call |
| User identity claim | `oid` (stable object ID) | `sub` (login) |
| Actor claim | `appid` / `azp` | `cid` |
| Inbound JWT `aud` | Agent app client ID | Okta auth server audience (`api://default`) |
| Downstream API | Microsoft Graph `/me` | Okta `/v1/userinfo` |
| Downstream scope | `User.Read` | `agent.downstream` (custom) — Okta refuses OIDC scopes on Token Exchange |
| Frontend OAuth lib | MSAL | authlib |
| DPoP consideration | N/A | Must be disabled on Service App |
| App registrations | 2 (frontend, agent) + combined consent via `knownClientApplications` | 2 (Web App, Service App) + access policies on auth server |
