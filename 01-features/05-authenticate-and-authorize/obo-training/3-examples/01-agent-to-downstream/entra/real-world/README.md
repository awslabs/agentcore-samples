# Real-World Example — User → Frontend → Agent on Runtime → Graph

A production-shaped deployment of Use Case 1. Everything runs where it would in real life: the frontend on your laptop or a container, the agent on AgentCore Runtime, and Microsoft Graph as the downstream target.

Uses the [new Node.js-based AgentCore CLI (`@aws/agentcore`)](https://github.com/aws/agentcore-cli) — the replacement for the deprecated `bedrock-agentcore-starter-toolkit`. The workflow here is patterned after [the AgentCore CLI workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/c770f35f-90a9-4e02-8985-4ef912bddb77/en-US).

## How to use this tutorial

This real-world example has two files you'll read in sequence:

1. **This `README.md`** — the setup checklist. Follow the Prerequisites and Quick Start sections to get the stack deployed and the frontend running.
2. **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** — a 6-chapter hands-on tour you walk through *after* the stack is running. Each chapter has an objective, an action (usually "click this / watch that log"), the expected result, and a teaching observation. This is where the actual OBO learning happens.

Think of it as: README gets it running, LEARNING_GUIDE teaches you what's running. The companion `local/` example has the same arc but condensed into a single interactive Python script — start there if you want to see the mechanics end-to-end without any AWS deploys.

## Architecture

```
┌────────────────┐     1. browser sign-in via Entra
│   Browser      │ <─────────────────────────┐
│   (HTML+JS)    │                            │
└────────┬───────┘                            │
         │ session cookie                    ┌┴────────────────┐
         ↓                                    │ Microsoft       │
┌────────────────┐    2. POST /ask            │ Entra ID        │
│  FastAPI BFF   │ ──────────────┐            └┬────────────────┘
│  (frontend)    │               │              ↑
└────────┬───────┘               │              │ 5. OBO exchange
         │ Bearer: user JWT      │              │ grant_type=jwt-bearer
         │ (aud=agent-app)       │              │ assertion=user-JWT
         ↓                       │              │
┌────────────────┐    3. invoke  │              │
│ AgentCore      │ ──────────────┘              │
│ Runtime        │    4. inbound JWT validated  │
│ (Strands agent)│       (customJWTAuthorizer)  │
└────────┬───────┘                              │
         │ 6. Bearer: Graph token (aud=graph)   │
         ↓                                       │
┌────────────────┐                               │
│ Microsoft      │                               │
│ Graph /me      │ ──────────────────────────────┘
└────────────────┘
```

Four moving parts:

| Component | What it is | Where it runs |
|---|---|---|
| **Frontend** | FastAPI BFF with a minimal HTML UI | Your laptop / any container |
| **Agent** | Strands agent with an HTTP handler | AgentCore Runtime |
| **Entra ID** | 2 app registrations (frontend + agent) | Microsoft cloud |
| **AgentCore Identity** | Workload + 1 OBO-enabled credential provider | AWS account |

## What you'll deploy

Two Entra app registrations (see [`IDP_SETUP.md`](./IDP_SETUP.md)):
1. **Frontend app** — user-facing OIDC client with redirect URI pointing to the FastAPI BFF.
2. **Agent app** — the resource that exposes `access_as_user`. Has Graph `User.Read` delegated permission.

One AgentCore workload with credential providers (see [`deploy/01_create_providers.py`](./deploy/01_create_providers.py)):
- Workload identity for the agent.
- One `MicrosoftOauth2` credential provider for the OBO exchange.

> **Note:** This variant creates **new** AgentCore resources (`obo-usecase1-entra-realworld`, `obo-uc1-entra-realworld-actor`) that are separate from the `local/` variant. Running the two variants in the same AWS account is safe — they don't share resources. If you'd rather reuse the same workload identity and credential provider across both, point `WORKLOAD_NAME` and `ACTOR_PROVIDER_NAME` in both `.env` files at the same values.

The Strands agent is deployed via the Node.js AgentCore CLI (`agentcore create`, `agentcore deploy`). The workflow is spelled out in the Quick start below.

## Prerequisites

Before you run the quick-start, make sure you have all of this in place. Each item is a known gotcha we've hit during setup.

### Tooling

| Tool | Why | How to install |
|---|---|---|
| **Python 3.10+** | Frontend, helper scripts, agent | Your package manager |
| **Node.js 20+** | AgentCore CLI (`@aws/agentcore`) is a Node CLI | `brew install node` / nvm |
| **AWS CLI v2** | Credentials, bootstrap verification | [docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| **AWS CDK CLI** | AgentCore CLI deploys via CDK under the hood | `npm install -g aws-cdk` |
| **AgentCore CLI** | Scaffolds + deploys the Runtime | `npm install -g @aws/agentcore@0.30.0` |

> Uninstall the deprecated starter toolkit first if it's on your system — it shadows the new CLI: `pip uninstall bedrock-agentcore-starter-toolkit` (or `pipx uninstall` / `uv tool uninstall` depending on how you installed it).

### AWS permissions required on YOUR caller (the one running `agentcore deploy`)

The identity you use to deploy this example must be able to do all of the following. For a personal dev account, the `AdministratorAccess` managed policy covers everything. For a shared account, the narrower list is:

- **CloudFormation** — `cloudformation:*` on stacks `CDKToolkit` and `AgentCore-<AGENT_RUNTIME_NAME>-default`.
- **IAM** — create roles (`iam:CreateRole`, `iam:PutRolePolicy`, `iam:AttachRolePolicy`, `iam:PassRole`, etc.) for the agent's execution role and the CDK bootstrap roles.
- **S3 + ECR** — CDK staging bucket and image repo.
- **Bedrock** — `bedrock:InvokeModel*` on the Claude model you're using (default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`). *Note: IAM permission alone is not enough — see "Bedrock model access" below.*
- **AgentCore control plane** — `bedrock-agentcore-control:*` on your workload + credential provider.
- **STS** — `sts:GetCallerIdentity`.

If you're on a restricted identity without these, the deploy will fail with `AccessDenied` in the CloudFormation logs, and you'll need to either escalate your permissions or ask someone with AdministratorAccess to run the deploy once.

### AWS account / region bootstrap state

- **CDK bootstrap.** CDK needs a one-time `cdk bootstrap` per account+region. The AgentCore CLI's `agentcore deploy` will fail with `SSM parameter /cdk-bootstrap/hnb659fds/version not found` if this is missing. Fix: `cdk bootstrap aws://<account>/<region>`.
- **Bedrock model access.** In the **Bedrock console** in your region → **Model access** → request access to the Claude model you're using (default: Claude Sonnet 4.5 via the `us.anthropic.claude-sonnet-4-5-...` inference profile). **This is a console-level toggle that is separate from IAM permissions** — your account needs it approved even if your IAM role has `bedrock:InvokeModel`. Without it, your agent deploys successfully but invocations fail with `AccessDeniedException` from Bedrock. Cross-region inference profiles (the `us.`/`eu.`/`global.` prefixes) require model access in every region they can route to.
- **Single credential source.** Don't have both `AWS_PROFILE` and `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` set at the same time — the JS SDK warns loudly and CDK sometimes picks the wrong one. Pick one:
  ```bash
  # If you use profiles:
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  export AWS_PROFILE=your-profile

  # Or if you use static keys:
  unset AWS_PROFILE
  export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=...
  ```

### Entra ID access

- A Microsoft Entra ID tenant where you can register apps and grant admin consent.
- A test user in that tenant to sign in as when demoing the frontend.
- Details in [`IDP_SETUP.md`](./IDP_SETUP.md).

## Quick start

This example follows the workflow from the [AgentCore CLI workshop](https://catalog.us-east-1.prod.workshops.aws/workshops/c770f35f-90a9-4e02-8985-4ef912bddb77/en-US): scaffold with the CLI, patch the config for Entra auth, deploy, invoke.

### 1. Complete IdP setup
Follow [`IDP_SETUP.md`](./IDP_SETUP.md) to register two Entra apps (frontend + agent) and grant admin consent.

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

# Node.js AgentCore CLI
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

#### Uninstall the old starter-toolkit CLI (important)

Both CLIs use the same `agentcore` command name. If you have the deprecated Python starter toolkit installed, it shadows the new Node.js CLI and commands like `agentcore validate` will fail with "No such command".

Check which CLI is active:

```bash
which agentcore
# /usr/local/bin/agentcore     ← could be either
agentcore --version
# If it's the old one, you'll see a deprecation banner telling you to migrate.
```

Confirm by running a command that only exists in the new CLI:

```bash
agentcore validate --help
# New CLI: shows validate help.
# Old CLI: "No such command 'validate'".
```

If you have the old one, uninstall it with whichever tool you used:

```bash
pip uninstall bedrock-agentcore-starter-toolkit    # if installed via pip
pipx uninstall bedrock-agentcore-starter-toolkit   # if installed via pipx
uv tool uninstall bedrock-agentcore-starter-toolkit  # if installed via uv
```

Then reinstall the new one and verify:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
hash -r          # flush your shell's path cache
agentcore --help | head -5     # should show "Build and deploy Agentic AI applications on AgentCore"
```

### 4. Create AgentCore Identity resources
```bash
python deploy/01_create_providers.py
```
This creates the workload identity and the `MicrosoftOauth2` credential provider that the agent uses for the OBO exchange.

### 5. Scaffold the agent project (AgentCore CLI — no wizard)

Run this from inside `real-world/`. The one-liner below first sanity-checks your location, then sources your `.env` and passes every required flag so the CLI runs fully non-interactively — no prompts for framework, model provider, memory, or language.

```bash
# Confirm you're in the right folder — bail out if not
[ -f ./config.example.env ] && [ -d ./deploy ] && [ -d ./frontend ] && [ -d ./agent ] \
  || { echo "✗ Not in real-world/. cd into obo-training/examples/01-agent-to-downstream/entra/real-world/ first."; return 1 2>/dev/null || exit 1; }

set -a && source .env && set +a && \
agentcore create \
  --name "$AGENT_RUNTIME_NAME" \
  --framework Strands \
  --model-provider Bedrock \
  --memory none \
  --build CodeZip \
  --defaults
```

What the sanity check does:

- Confirms `config.example.env` exists (the hallmark of the `real-world/` folder).
- Confirms `deploy/`, `frontend/`, and `agent/` subfolders exist.
- If any of those are missing, prints a clear message and aborts (without running `agentcore create` in the wrong place).

What each flag does:

| Flag | Value | Purpose |
|---|---|---|
| `--name` | `$AGENT_RUNTIME_NAME` from `.env` | Project + agent name (≤36 chars, starts with a letter, alphanumeric + underscores) |
| `--framework` | `Strands` | Uses Strands Agents; matches our `agent/agent.py` |
| `--model-provider` | `Bedrock` | Claude Sonnet via Bedrock; no API key needed |
| `--memory` | `none` | No AgentCore Memory resource (not needed for this use case) |
| `--build` | `CodeZip` | Zip-based deploy — no Docker required locally |
| `--defaults` | — | Skip any remaining prompts and use defaults for everything else |

> **Why no `--authorizer` flag?** The new CLI does not expose inbound auth as a `create` flag. Auth is configured in `agentcore/agentcore.json` and applied by `agentcore deploy`. Step 7 patches the JSON with the Entra CUSTOM_JWT config.

> **About the name:** the new CLI requires the project name to be **≤ 23 characters**, **start with a letter**, and **contain only alphanumeric characters** — no underscores, no hyphens. Use camelCase. The `.env` ships with a conforming name (`oboUc1EntraAgent`, 16 chars).

### 6. Copy the agent code into the scaffolded project

The `agentcore create` command creates a subdirectory named `$AGENT_RUNTIME_NAME/` inside `real-world/`. The scaffold lives there, and the CLI expects you to run further `agentcore ...` commands from inside it. Copy our OBO-aware agent code over the template the CLI generated, then `cd` into the project:

```bash
# Still in real-world/
cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
cp agent/requirements.txt "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/requirements.txt 2>/dev/null || true
cd "$AGENT_RUNTIME_NAME"
```

`agent/agent.py` in this repo is the canonical OBO-aware Strands agent. The scaffold created a template `main.py` that we overwrite with our OBO logic.

> For the rest of the README, all `agentcore …` commands should be run from inside the `$AGENT_RUNTIME_NAME/` folder. Helper Python scripts (`python deploy/...`) still live one level up in `real-world/` — the path is handled in each script.

### 7. Patch `agentcore/agentcore.json` for Entra JWT inbound auth

Run this from inside the project folder (`real-world/$AGENT_RUNTIME_NAME/`). The script locates `agentcore/agentcore.json` in the current directory, patches it, and writes it back:

```bash
# From inside $AGENT_RUNTIME_NAME/
python ../deploy/02_patch_agentcore_json.py
```
This adds:
- `requestHeaderAllowlist`: `["Authorization"]` (so the JWT reaches the agent handler).
- `authorizerType`: `CUSTOM_JWT`.
- `authorizerConfiguration.customJwtAuthorizer`: Entra's OIDC discovery URL and your `AGENT_CLIENT_ID` as `allowedAudience`.
- `envVars`: `WORKLOAD_NAME`, `ACTOR_PROVIDER_NAME`, `GRAPH_SCOPE`, `AWS_REGION` as an array of `{name, value}` objects (the schema at `https://schema.agentcore.aws.dev/v1/agentcore.json` expects this exact shape — an object map under `environmentVariables` looks right but is silently ignored at deploy time).

#### Before / after — what the patch changes

<details>
<summary><b>Before</b> — what <code>agentcore create</code> generated (click to expand)</summary>

```json
{
  "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
  "name": "oboUc1EntraAgent",
  "version": 1,
  "managedBy": "CDK",
  "tags": {
    "agentcore:created-by": "agentcore-cli",
    "agentcore:project-name": "oboUc1EntraAgent"
  },
  "runtimes": [
    {
      "name": "oboUc1EntraAgent",
      "build": "CodeZip",
      "entrypoint": "main.py",
      "codeLocation": "app/oboUc1EntraAgent/",
      "runtimeVersion": "PYTHON_3_14",
      "networkMode": "PUBLIC",
      "protocol": "HTTP"
    }
  ],
  "memories": [],
  "credentials": [],
  "evaluators": [],
  "onlineEvalConfigs": [],
  "agentCoreGateways": [],
  "policyEngines": []
}
```

Notice: the runtime has no auth, no request-header allowlist, and no environment variables. If you deployed like this, the Runtime would default to IAM SigV4 authentication, the handler would have no way to read the user's JWT, and the agent would have no way to find your AgentCore Identity workload or credential provider.

</details>

<details>
<summary><b>After</b> — what the patch script produces (click to expand)</summary>

```json
{
  "$schema": "https://schema.agentcore.aws.dev/v1/agentcore.json",
  "name": "oboUc1EntraAgent",
  "version": 1,
  "managedBy": "CDK",
  "tags": {
    "agentcore:created-by": "agentcore-cli",
    "agentcore:project-name": "oboUc1EntraAgent"
  },
  "runtimes": [
    {
      "name": "oboUc1EntraAgent",
      "build": "CodeZip",
      "entrypoint": "main.py",
      "codeLocation": "app/oboUc1EntraAgent/",
      "runtimeVersion": "PYTHON_3_14",
      "networkMode": "PUBLIC",
      "protocol": "HTTP",
      "requestHeaderAllowlist": [
        "Authorization"
      ],
      "authorizerType": "CUSTOM_JWT",
      "authorizerConfiguration": {
        "customJwtAuthorizer": {
          "discoveryUrl": "https://login.microsoftonline.com/<TENANT_ID>/.well-known/openid-configuration",
          "allowedAudience": [
            "<AGENT_CLIENT_ID>",
            "api://<AGENT_CLIENT_ID>"
          ]
        }
      },
      "envVars": [
        {"name": "WORKLOAD_NAME", "value": "obo-usecase1-entra-realworld"},
        {"name": "ACTOR_PROVIDER_NAME", "value": "obo-uc1-entra-realworld-actor"},
        {"name": "GRAPH_SCOPE", "value": "https://graph.microsoft.com/User.Read"},
        {"name": "AWS_REGION", "value": "us-west-2"}
      ]
    }
  ],
  "memories": [],
  "credentials": [],
  "evaluators": [],
  "onlineEvalConfigs": [],
  "agentCoreGateways": [],
  "policyEngines": []
}
```

The four new/changed fields on the runtime:

| Field | Purpose |
|---|---|
| `requestHeaderAllowlist: ["Authorization"]` | Lets the user's Bearer JWT pass through to the agent handler. Without this, the handler can't read the token. |
| `authorizerType: "CUSTOM_JWT"` | Switches the Runtime from IAM SigV4 to JWT-based inbound auth. |
| `authorizerConfiguration.customJwtAuthorizer` | Points at Entra's OIDC discovery doc and restricts accepted tokens to those audienced at your agent app. |
| `envVars` | Config the agent handler reads at runtime to know which workload identity and credential provider to use for the OBO exchange. Array of `{name, value}` objects — not an object map. |

`<TENANT_ID>` and `<AGENT_CLIENT_ID>` are substituted from your `.env`. Everything else stays identical — the patch is surgical.

</details>

**Optional — see the diff on your machine.** Stash a copy before running the patch, then diff the two:

```bash
# from inside $AGENT_RUNTIME_NAME/
cp agentcore/agentcore.json /tmp/agentcore-before.json
python ../deploy/02_patch_agentcore_json.py
diff /tmp/agentcore-before.json agentcore/agentcore.json
```

### 8. Fill in your AWS deployment target

`agentcore create --defaults` scaffolds an empty `agentcore/aws-targets.json`. Before deploying, add your AWS account and region. Still from inside `oboUc1EntraAgent/`:

```bash
# Get your account ID from STS. Honor AWS_PROFILE if set.
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

Verify:

```bash
cat agentcore/aws-targets.json
```

You should see a single target named `"default"` with your 12-digit AWS account and one of the AgentCore-supported regions.

> **AWS credentials warning**
>
> If you see `Multiple credential sources detected: Both AWS_PROFILE and the pair AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY static credentials are set`, the CLI is warning you it's falling back to `AWS_PROFILE`. This is non-fatal — but to silence it, unset whichever set you're not using:
> ```bash
> # to use AWS_PROFILE:
> unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
>
> # or to use static credentials:
> unset AWS_PROFILE
> ```

### 9. Validate and deploy

Still from inside `oboUc1EntraAgent/`:

```bash
agentcore validate
agentcore deploy -y -v
```

This uses the AWS CDK under the hood.

> **Prerequisite: CDK bootstrap**
>
> If this is the first time any CDK stack has been deployed to your account and region, you'll see:
> ```
> SSM parameter /cdk-bootstrap/hnb659fds/version not found. Has the environment been bootstrapped?
> ```
> Fix: run `cdk bootstrap` once per account/region:
> ```bash
> npx cdk bootstrap aws://<account-id>/<region>
> # or if you don't have npx:
> npm install -g aws-cdk
> cdk bootstrap aws://<account-id>/<region>
> ```
> This takes ~2 minutes and creates CDK's staging S3 bucket, ECR repo, and IAM roles. After it completes, re-run `agentcore deploy -y -v`.

### 10. Grant the agent's execution role OBO permissions

The AgentCore CLI auto-creates an IAM role for the agent but does NOT grant it the permissions required to call AgentCore Identity APIs or read the credential-provider secret. This script finds the auto-created role and attaches an inline policy with the required permissions:

```bash
# Still from inside oboUc1EntraAgent/
python ../deploy/03_grant_agent_iam_permissions.py
```

The script prints the role it found and the permissions it granted. IAM changes take effect within seconds — no need to redeploy.

> **Why is this needed?** The role the CLI creates has baseline Runtime permissions only — enough to run your container and write logs. Doing OBO from inside the handler requires three additional permissions, each gated by a separate IAM action. The CLI may add these automatically in a future release; for now, run this script once per deploy.
>
> #### The three actions and why they're needed
>
> | IAM action | When it's called | What it does | What fails without it |
> |---|---|---|---|
> | `bedrock-agentcore:GetWorkloadAccessTokenForJWT` | First, when the tool receives the user's JWT | Wraps the inbound user JWT into an AgentCore-internal "workload access token" that AgentCore Identity can later unwrap. This is the AWS-side of binding the IdP-issued JWT to your agent's workload identity. | `AccessDeniedException: is not authorized to perform: bedrock-agentcore:GetWorkloadAccessTokenForJWT`. The OBO flow halts before even reaching the IdP. |
> | `bedrock-agentcore:GetResourceOauth2Token` | Second, with `oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE` | Tells AgentCore Identity to perform the actual token exchange: unwrap the user JWT, look up the OAuth credential provider, POST the exchange request to Entra's token endpoint (`grant_type=jwt-bearer`), and return the downstream Graph-scoped token. | `AccessDeniedException: is not authorized to perform: bedrock-agentcore:GetResourceOauth2Token`. No token exchange happens. |
> | `secretsmanager:GetSecretValue` | During the exchange, transparently | When AgentCore Identity makes the POST to Entra, it needs your Entra client secret to authenticate. It stores that secret in AWS Secrets Manager under a managed ARN (`bedrock-agentcore-identity!default/oauth2/*`) and reads it via `GetSecretValue` at exchange time — **NOT your agent code**. The agent's role needs read access so AgentCore Identity, running with the role's identity, can fetch the secret. | `AccessDeniedException: is not authorized to perform: secretsmanager:GetSecretValue`. The exchange call gets to AgentCore Identity, which then fails before hitting the IdP. |
>
> The script scopes each action to the minimum resources needed. Open [`deploy/03_grant_agent_iam_permissions.py`](./deploy/03_grant_agent_iam_permissions.py) to read the actual policy — nothing is wildcarded to `*`.
>
> #### Resource scoping details (what the policy restricts each action to)
>
> - **`GetWorkloadAccessTokenForJWT`**, `GetWorkloadAccessToken`, `GetWorkloadAccessTokenForUserId` — scoped to:
>   - `workload-identity-directory/default` (the default directory)
>   - `workload-identity-directory/default/workload-identity/<WORKLOAD_NAME>` (your specific workload — nobody else's)
>
>   So even if your agent is compromised, it cannot mint workload tokens for other workloads in the same account.
>
> - **`GetResourceOauth2Token`** — scoped to your workload identity AND the token vault:
>   - `workload-identity-directory/default/workload-identity/<WORKLOAD_NAME>`
>   - `token-vault/default`
>   - `token-vault/default/oauth2credentialprovider/*` (any credential provider in your account's default token vault)
>
> - **`secretsmanager:GetSecretValue`** — scoped to AgentCore-managed OAuth secrets only:
>   - `secret:bedrock-agentcore-identity!default/oauth2/*`
>
>   That prefix is the naming convention AgentCore Identity uses. Your agent's role cannot read arbitrary secrets in your account — only the ones AgentCore Identity itself provisions for credential providers.
>
> #### How this ties back to the OBO flow
>
> ```
>   Agent handler receives user JWT
>   │
>   ├─► GetWorkloadAccessTokenForJWT          ← IAM action #1
>   │     wraps user JWT into workload token
>   │
>   ├─► GetResourceOauth2Token (OBO flow)     ← IAM action #2
>   │     │
>   │     ├─► reads Entra client secret from  ← IAM action #3
>   │     │   Secrets Manager
>   │     │
>   │     ├─► POSTs grant_type=jwt-bearer to
>   │     │   login.microsoftonline.com
>   │     │
>   │     └─► returns Graph-scoped access token
>   │
>   └─► calls Microsoft Graph /me with the new token
> ```
>
> Remove any one of the three and the chain breaks at a predictable step. That's what the permission boundary is supposed to do.

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

- `agentcore status` prints ARN `arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/oboUc1EntraAgent-abc123`.
- URL-encode it: `arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1EntraAgent-abc123`.
- Full invoke URL:
  ```
  https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1EntraAgent-abc123/invocations?qualifier=DEFAULT
  ```
- Paste into `.env`:
  ```
  AGENT_RUNTIME_INVOKE_URL=https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime%2FoboUc1EntraAgent-abc123/invocations?qualifier=DEFAULT
  ```

Quick URL-encoder:

```bash
python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" \
  "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/oboUc1EntraAgent-abc123"
```

Sanity check: the URL should contain `%3A` (encoded `:`) and `%2F` (encoded `/`), and end with `/invocations?qualifier=DEFAULT`. If either is missing, re-check.

### 12. Run the frontend

Back in `real-world/`:

```bash
cd ..
python frontend/app.py
```

Open `http://localhost:8000`, click "Sign in with Microsoft", sign in as a user from your tenant, then click "Ask agent about me". You should see the user's real display name, email, or job title rendered cleanly (the frontend parses the agent's streaming SSE response into plain text).

### 13. Walk the learning guide

Now that everything is running, switch to **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** and walk through the six chapters. Each chapter tells you what to observe in the logs / browser / CloudWatch to understand what's actually happening under the hood. That's where the OBO concepts land.

Keep the stack running while you walk through it.

---

### Teardown
```bash
# From inside oboUc1EntraAgent/
agentcore remove agent --name "$AGENT_RUNTIME_NAME" -y   # mark for removal
agentcore deploy -y -v                                     # tears down the CloudFormation stack

# From real-world/
cd ..
python deploy/teardown.py                                  # deletes AgentCore Identity resources

# Optional: delete the scaffolded CLI project (to start fresh)
rm -rf oboUc1EntraAgent/

# Optional: clear the invoke URL so step 11 has something to fill next time
sed -i.bak 's|^AGENT_RUNTIME_INVOKE_URL=.*|AGENT_RUNTIME_INVOKE_URL=|' .env && rm .env.bak
```

Entra app registrations are not touched — they're just OAuth client configs that stay valid across tests. Only remove them if you're done for good.

## Folder structure

```
real-world/
├── README.md              ← this file (setup checklist)
├── LEARNING_GUIDE.md      ← 6-chapter hands-on tour (read after setup)
├── ARCHITECTURE.md        ← deeper dive on the design
├── IDP_SETUP.md           ← 2-app Entra registration steps
├── requirements.txt       ← Python deps (frontend + deploy helpers)
├── config.example.env     ← env var template
│
├── frontend/              ← FastAPI BFF + HTML UI
│   ├── app.py
│   ├── templates/
│   │   ├── home.html
│   │   └── result.html
│   └── README.md
│
├── agent/                 ← canonical OBO Strands agent
│   ├── agent.py           ← copied into app/<name>/main.py by you in step 6
│   ├── requirements.txt
│   └── README.md
│
├── deploy/                ← helper scripts that complement the AgentCore CLI
│   ├── 01_create_providers.py           ← creates AgentCore Identity workload + provider
│   ├── 02_patch_agentcore_json.py       ← adds Entra JWT auth + env vars to agentcore.json
│   ├── 03_grant_agent_iam_permissions.py ← grants the auto-created role OBO permissions
│   └── teardown.py                      ← deletes the Identity resources
│
├── agentcore/             ← (generated by `agentcore create`) CLI project config
│   ├── agentcore.json
│   ├── aws-targets.json
│   └── cdk/
│
└── app/                   ← (generated by `agentcore create`)
    └── <AGENT_RUNTIME_NAME>/
        ├── main.py        ← you replace this with a copy of agent/agent.py
        └── pyproject.toml
```

`agentcore/` and `app/` are created by the AgentCore CLI (`agentcore create`). You don't hand-write them — follow the Quick start steps.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for why we chose BFF over SPA-with-token, why `customJWTAuthorizer` is used on Runtime, and how requests flow through each layer.

## How this differs from `local/`

| Aspect | `local/` | `real-world/` |
|---|---|---|
| Agent runs... | Inline in the script | On AgentCore Runtime |
| User sign-in... | `USER_FEDERATION` against a credential provider | Real Entra auth code flow via browser redirect |
| Frontend... | Doesn't exist — the script simulates it | FastAPI BFF with HTML templates |
| Token storage... | In-memory, one session | HTTP session cookies |
| Where OBO happens | Top-level script | Inside the agent handler |
| Entra app registrations | 1 (combined client + resource) | 2 (frontend + agent) |
| When to use | Learning, debugging, CI | Demo, reference, production-shape |
