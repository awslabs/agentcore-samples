# Real-World Example — User → Frontend → Agent on Runtime → Gateway → Mock API (Okta)

A production-shaped deployment of Use Case 2 (Okta flavor): **two OBO exchanges in one chain**, both using Okta's RFC 8693 Token Exchange grant, with user identity preserved end-to-end.

```
👤 User
  ↓ sign-in (Auth Code + PKCE via authlib)
🖥️  FastAPI BFF
  ↓ Bearer T_user
🤖 Strands agent on AgentCore Runtime
  ↓ OBO #1 (Token Exchange) → T_gateway
🛡  AgentCore Gateway  (OpenAPI target → mock downstream)
  ↓ OBO #2 (Token Exchange) → T_downstream  (transparent — no agent code involved)
🎯 httpbin.org/anything (mock downstream API, echoes the request back)
```

## How to use this tutorial

1. **`README.md`** (this file) — the setup checklist. Follow Prerequisites and Quick Start to get the stack deployed and the frontend running.
2. **[`ARCHITECTURE.md`](./ARCHITECTURE.md)** — design rationale, the three Okta apps, the two credential providers, the request lifecycle, the three tokens decoded.
3. **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)** — six chapters you walk through *after* the stack is running, focused on observing the two-OBO mechanics in the logs and tokens.

## Prerequisites

### Tooling

| Tool | Minimum version | Install |
|---|---|---|
| **Python** | 3.10+ | Your package manager |
| **Node.js** | 20+ | `brew install node` / nvm / mise |
| **AWS CLI v2** | 2.x | [docs](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| **AWS CDK CLI** | 2.1129.0+ | `npm install -g aws-cdk@2.1129.0` |
| **AgentCore CLI** (`@aws/agentcore`) | 0.21.1+ | `npm install -g @aws/agentcore@0.30.0` |

> If you have the deprecated `bedrock-agentcore-starter-toolkit` Python CLI installed, uninstall it first — it shadows the new Node CLI and `agentcore validate` will fail with "No such command".

#### Pre-flight version check

```bash
python3 --version                       # 3.10+
node --version                          # v20+
aws --version                           # 2.x
cdk --version                           # 2.1129.0+
agentcore --version                     # 0.21.1+
```

### AWS permissions on YOUR caller

- **CloudFormation** — `cloudformation:*` on `CDKToolkit` and `AgentCore-<AGENT_RUNTIME_NAME>-default`.
- **IAM** — `iam:CreateRole`, `iam:GetRole`, `iam:PutRolePolicy` (deploy/02_create_gateway.py auto-creates the Gateway service role), plus the standard role management the AgentCore CLI needs for the agent's execution role.
- **S3 + ECR** — CDK staging.
- **Bedrock** — `bedrock:InvokeModel*` on Claude Sonnet 4.5 (default model).
- **AgentCore control plane** — `bedrock-agentcore-control:*` on workloads, credential providers, gateways, gateway targets.
- **STS** — `sts:GetCallerIdentity`.

For a personal dev account, `AdministratorAccess` covers everything.

### Bedrock model access

In the **Bedrock console** in your region → **Model access** → request access to Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-...`). Console toggle separate from IAM.

### CDK bootstrap

`cdk bootstrap aws://<account>/<region>` once per account/region if you haven't before.

### AWS credentials — one source, verified

`agentcore deploy` runs `cdk deploy` as a subprocess and fails cryptically if both `AWS_PROFILE` and `AWS_ACCESS_KEY_ID` are set. Pick exactly one source, then verify:

```bash
# Option A: profile
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE=your-profile

# Option B: static/temporary keys
unset AWS_PROFILE
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=...

aws sts get-caller-identity   # must succeed in the same shell you'll run agentcore from
```

If you're on SSO and temp creds have expired: `aws sso login --profile "$AWS_PROFILE"`.

### Gateway service role

The Gateway needs an IAM role it can assume to call AgentCore Identity for OBO #2 and to read the credential provider's secret from Secrets Manager. **`deploy/02_create_gateway.py` creates this role for you** if one doesn't already exist under the conventional name `AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>`. Override via `GATEWAY_SERVICE_ROLE_ARN` in `.env` if you bring your own.

### Okta access

- An Okta tenant where you can register apps and edit authorization server access policies.
- A test user in your tenant.
- For the automated setup path: an **Okta API token** (Super Admin or Org Admin), created at Okta admin → Security → API → Tokens → Create Token. This is only needed by `00_create_okta_apps.py`; not at runtime.

Details in [`IDP_SETUP.md`](./IDP_SETUP.md).

## Quick start

### 1. Complete IdP setup

Follow [`IDP_SETUP.md`](./IDP_SETUP.md). Two paths, you pick:

- **Automated** (~30 seconds): set `OKTA_DOMAIN` and `OKTA_ADMIN_TOKEN` in `.env`, then run `python deploy/00_create_okta_apps.py`. Creates 3 app registrations (Frontend Web App, Agent API Services, Gateway API Services) + 3 custom scopes on the default auth server + 3 access policies + client secrets. Writes `.env`.
- **Manual** (~15 minutes): click through the Okta admin console. See `IDP_SETUP.md` → "Manual path".

Either way, by the end you have a populated `.env` with three client IDs, three secrets, and three scope values.

### 2. Configure `.env`

If you used the automated path, `.env` is already populated — skip ahead.

If you took the manual path:

```bash
cd obo-training/examples/02-agent-via-gateway/okta/real-world
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

Pre-flight — every line should pass before you move on:

```bash
python -c "import boto3; assert tuple(int(x) for x in boto3.__version__.split('.')[:3]) >= (1,43,2), boto3.__version__; print('boto3', boto3.__version__)"
cdk --version         # 2.1129.0+
agentcore --version   # 0.21.1+
env | grep '^AWS_'    # confirm only ONE of AWS_PROFILE or AWS_ACCESS_KEY set
aws sts get-caller-identity
```

### 4. Create AgentCore Identity resources (workload + 2 credential providers)

```bash
python deploy/01_create_providers.py
```

Preflights the Okta discovery URL, creates the agent's workload identity, and creates two CustomOauth2 credential providers configured for RFC 8693 Token Exchange (with `actorTokenContent: NONE` — no separate actor JWT, client credentials identify the actor).

### 5. Create the Gateway and its OpenAPI target

```bash
python deploy/02_create_gateway.py
```

Does three things:

1. **Auto-creates the Gateway service role** (`AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>`). Skipped if `GATEWAY_SERVICE_ROLE_ARN` is set or a role by that name already exists.
2. Creates the Gateway with inbound `customJWTAuthorizer` against Okta (allowedAudience = OKTA_AUDIENCE).
3. Creates one OpenAPI target backed by `gateway/downstream_openapi.json` with outbound OBO using the gateway-actor credential provider.

Writes `GATEWAY_MCP_URL` and `GATEWAY_SERVICE_ROLE_ARN` back into `.env`.

### 6. Scaffold the agent project (AgentCore CLI — non-interactive)

Run from inside `real-world/`:

```bash
[ -f ./config.example.env ] && [ -d ./deploy ] && [ -d ./frontend ] && [ -d ./agent ] && [ -d ./gateway ] \
  || { echo "✗ Not in real-world/. cd into obo-training/examples/02-agent-via-gateway/okta/real-world/ first."; return 1 2>/dev/null || exit 1; }

set -a && source .env && set +a && \
agentcore create \
  --name "$AGENT_RUNTIME_NAME" \
  --framework Strands \
  --model-provider Bedrock \
  --memory none \
  --build CodeZip \
  --defaults
```

### 7. Copy the OBO-aware agent code into the scaffolded project

```bash
# Still in real-world/
cp agent/agent.py "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/main.py
cp agent/requirements.txt "$AGENT_RUNTIME_NAME"/app/"$AGENT_RUNTIME_NAME"/requirements.txt 2>/dev/null || true
cd "$AGENT_RUNTIME_NAME"
```

### 8. Patch `agentcore/agentcore.json` for inbound JWT + env vars

```bash
# From inside $AGENT_RUNTIME_NAME/
python ../deploy/03_patch_agentcore_json.py
```

This adds:

- `requestHeaderAllowlist: ["Authorization"]` — the JWT reaches the agent handler.
- `authorizerType: "CUSTOM_JWT"` with Okta's OIDC discovery URL and `allowedAudience = [OKTA_AUDIENCE]`.
- `envVars` array — `AGENT_WORKLOAD_NAME`, `AGENT_OBO_PROVIDER_NAME`, `GATEWAY_SCOPE`, `GATEWAY_MCP_URL`, `OKTA_AUDIENCE`, `AWS_REGION`.

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

First time in a new account/region: `cdk bootstrap aws://<account>/<region>` first.

### 11. Grant the agent's execution role IAM permissions for OBO #1

```bash
# Still from inside $AGENT_RUNTIME_NAME/
python ../deploy/04_grant_agent_iam_permissions.py
```

Attaches the inline OBO policy (`GetWorkloadAccessTokenForJWT`, `GetResourceOauth2Token`, `secretsmanager:GetSecretValue`) to the auto-created execution role.

### 11b. Set log retention and confirm observability config

```bash
# From real-world/ (one level up)
cd ..
python deploy/05_enable_observability.py            # 30-day retention
```

Prints a quick-reference for `agentcore logs` and `agentcore traces`, and links to the one-time CloudWatch console toggles for **Application Signals** and **Transaction Search**.

### 12. Set the invoke URL

This step runs from inside `$AGENT_RUNTIME_NAME/`. If you're already inside it from step 11, skip the `cd`. Otherwise:

```bash
cd "$AGENT_RUNTIME_NAME"   # from real-world/
```

The snippet below parses the runtime ARN out of `agentcore status`, URL-encodes it, appends `?qualifier=DEFAULT`, and writes `AGENT_RUNTIME_INVOKE_URL` into `../.env`. We parse the ARN (not the URL) because `agentcore status` word-wraps the URL when the terminal is narrow — the ARN is always emitted on its own line.

```bash
python3 <<'EOF'
import pathlib, re, subprocess, sys, urllib.parse

proc = subprocess.run(["agentcore", "status"], capture_output=True, text=True)
out = (proc.stdout or "") + "\n" + (proc.stderr or "")

m = re.search(r"arn:aws:bedrock-agentcore:[a-z0-9-]+:\d+:runtime/[A-Za-z0-9_\-]+", out)
if not m:
    print("ERROR: could not find runtime ARN in `agentcore status` output.", file=sys.stderr)
    print(out, file=sys.stderr)
    sys.exit(1)

arn = m.group(0)
region = arn.split(":")[3]
encoded = urllib.parse.quote(arn, safe="")
url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded}/invocations?qualifier=DEFAULT"

p = pathlib.Path("../.env")
lines = p.read_text().splitlines()
for i, line in enumerate(lines):
    if line.startswith("AGENT_RUNTIME_INVOKE_URL="):
        lines[i] = f"AGENT_RUNTIME_INVOKE_URL={url}"
        break
else:
    lines.append(f"AGENT_RUNTIME_INVOKE_URL={url}")
p.write_text("\n".join(lines) + "\n")
print(f"✓ Wrote AGENT_RUNTIME_INVOKE_URL to ../.env:")
print(f"  {url}")
EOF
```

Sanity check:

```bash
# Pull just the invoke URL out of .env — avoids `source .env` picking up
# space-containing values like UPSTREAM_SCOPE.
AGENT_RUNTIME_INVOKE_URL=$(grep '^AGENT_RUNTIME_INVOKE_URL=' ../.env | cut -d= -f2-)

curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H "Authorization: Bearer not-a-real-token" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"hi"}' \
  "$AGENT_RUNTIME_INVOKE_URL"
# Expect: 401 or 403 — both mean the URL is correct and auth was rejected
#                     (401 = malformed/expired JWT; 403 = auth header the
#                      customJwtAuthorizer couldn't parse at all).
#         404       = URL is wrong; forgot ?qualifier=DEFAULT.
```

### 13. Run the frontend

```bash
cd ..   # back to real-world/
python frontend/app.py
```

Open `http://localhost:8000`, click **Sign in with Okta**, sign in, then click **Ask agent**. You should see a short answer confirming the mock downstream API responded.

### 14. Walk the LEARNING_GUIDE

Switch to **[`LEARNING_GUIDE.md`](./LEARNING_GUIDE.md)**. Six chapters that walk you through observing each part of the chain in the logs / claims / network calls. Keep the stack running.

---

## Teardown

Order matters — the agent runtime must go first (CDK stack references the credential providers), then the AWS AgentCore resources, then optionally the local scaffolding and Okta apps.

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

- **Every** gateway target on the gateway (not just this example's — orphans get cleaned up too), then waits for async deletion to complete.
- The Gateway.
- Both credential providers (agent-actor + gateway-actor).
- The workload identity.
- The Gateway service IAM role (only if named `AmazonBedrockAgentCoreGatewayRole-<GATEWAY_NAME>` or referenced by `GATEWAY_SERVICE_ROLE_ARN`).

Then **verifies** every resource is actually gone and prints any survivors.

### Verify only (no deletes)

```bash
python deploy/teardown.py --verify-only
```

### Optional next steps

```bash
# Delete the scaffolded CLI project folder
rm -rf "$AGENT_RUNTIME_NAME"/

# Delete the Okta app registrations + scopes + policies
python deploy/00_delete_okta_apps.py --yes
```

The Okta app registrations are not touched by `teardown.py`. Use `00_delete_okta_apps.py` when you want to burn them down.

## Observability & debugging

AgentCore emits logs and traces to CloudWatch automatically. See the section in UC2 Entra's `README.md` for the full catalog; the log group naming conventions are the same.

The most useful commands for UC2 Okta:

```bash
# See the OBO chain per-invocation with client-id annotations from .env
python deploy/show_obo_trace.py --since 10m

# Decode + diff the three tokens side by side
python deploy/compare_obo_claims.py --user-token "$T_USER"

# Set log retention + print CloudWatch quick-reference
python deploy/05_enable_observability.py
```

## Folder structure

```
real-world/
├── README.md                          ← this file
├── ARCHITECTURE.md                    ← design + request lifecycle + 3 tokens
├── LEARNING_GUIDE.md                  ← 6-chapter hands-on tour
├── IDP_SETUP.md                       ← Okta setup (automated + manual paths)
├── requirements.txt
├── config.example.env
│
├── frontend/                          ← FastAPI BFF + HTML UI (authlib)
│   ├── app.py
│   ├── templates/{home.html, result.html}
│   └── README.md
│
├── agent/                             ← canonical OBO-aware Strands agent
│   ├── agent.py                       ← copied into app/<name>/main.py in step 7
│   ├── requirements.txt
│   └── README.md
│
├── gateway/                           ← Gateway target spec
│   ├── downstream_openapi.json
│   └── README.md
│
└── deploy/                            ← helper scripts that complement the AgentCore CLI
    ├── 00_create_okta_apps.py         ← automates 3-app Okta setup via Admin API
    ├── 00_delete_okta_apps.py         ← removes Okta apps + scopes + policies
    ├── 01_create_providers.py         ← workload + 2 OBO credential providers
    ├── 02_create_gateway.py           ← Gateway + OpenAPI target + service role
    ├── 03_patch_agentcore_json.py     ← inbound JWT + env vars for the agent
    ├── 04_grant_agent_iam_permissions.py  ← attaches OBO policy to agent role
    ├── 05_enable_observability.py     ← log retention + CloudWatch quick-refs
    ├── compare_obo_claims.py          ← decode + diff T_user, T_gateway, T_downstream
    ├── show_obo_trace.py              ← OBOTRACE log helper for LEARNING_GUIDE
    └── teardown.py                    ← removes AWS resources
```

## How this differs from UC2 Entra

Same shape, different IdP protocol. Same file layout. The OBO exchanges are the concept in both — the wire format under them differs.

| Aspect | UC2 Entra | UC2 Okta |
|---|---|---|
| OBO protocol | RFC 7523 JWT Bearer | RFC 8693 Token Exchange |
| Credential provider grantType | `JWT_AUTHORIZATION_GRANT` | `TOKEN_EXCHANGE` |
| customParameters on OBO call | `{"requested_token_use": "on_behalf_of"}` | `{"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"}` |
| Additional exchange arg | none | `audiences=[OKTA_AUDIENCE]` |
| Number of IdP apps | 3 (Frontend + Agent + Gateway) | 3 (Frontend Web App + 2× API Services) |
| App-level combined consent | `knownClientApplications` chain | Access Policies on the auth server |
| DPoP consideration | N/A | Must be OFF on API Services apps |
| Audience across OBO hops | Rotates (AgentApp -> GatewayApp -> Graph) | Stays constant (`OKTA_AUDIENCE`) |
| Actor identity claim | `azp` / `appid` | `cid` |
| User identity claim | `oid` | `sub` (or `uid`) |
| Downstream API | Microsoft Graph `/me` | httpbin.org/anything (mock echo) |
| Frontend OAuth lib | MSAL | authlib |

The headline: **`sub` stays constant across `T_user`, `T_gateway`, and `T_downstream` while `cid` walks down the chain**. That's the proof of identity propagation in Okta's flavor of OBO. The LEARNING_GUIDE walks through it.
