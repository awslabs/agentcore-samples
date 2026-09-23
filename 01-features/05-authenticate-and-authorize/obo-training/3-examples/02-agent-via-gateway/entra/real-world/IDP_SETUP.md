# Entra ID Setup — Real-World Use Case 2

Three Entra app registrations are needed:

1. **Frontend app** — the user-facing OIDC client the browser signs into.
2. **Agent app** — audience for the inbound user token; OAuth client for OBO #1 (agent → Gateway).
3. **Gateway app** — audience for the OBO'd token sent to the Gateway; OAuth client for OBO #2 (Gateway → Microsoft Graph).

This is a fresh set of registrations — do **not** reuse any apps from Use Case 1. Each OBO hop owns its own client identity, so each gets a separate app and its own client secret.

The setup also wires up `knownClientApplications` between the apps so the user sees a single combined consent prompt at sign-in covering all four scopes the chain needs.

> **Two ways to do this setup:**
> - **Automated path** (recommended): run `python deploy/00_create_entra_apps.py`. ~30 seconds end-to-end. See [Quick path](#quick-path-automated).
> - **Manual path**: click through the Entra admin console. ~15 minutes. See [Manual path](#manual-path) starting at Step 0.
>
> The automated path uses the Azure CLI and produces the same result as the manual path. The manual walkthrough is preserved below for reference and for environments where the Azure CLI isn't available.

## Architecture recap

```
👤 User ──signin──▶ FrontendApp
                       │ token T_user, aud = AgentApp
                       ▼
                    AgentApp  (Runtime A audience; client for OBO #1)
                       │ token T_gateway, aud = GatewayApp
                       ▼
                    GatewayApp  (Gateway audience; client for OBO #2)
                       │ token T_graph, aud = https://graph.microsoft.com
                       ▼
                    Microsoft Graph /me
```

Permissions and consent flow:

```
FrontendApp ──delegated── api://AgentApp/access_as_user      (consented at user sign-in)
AgentApp     ──delegated── api://GatewayApp/access_as_user   (combined consent via knownClientApplications)
GatewayApp   ──delegated── Microsoft Graph User.Read         (combined consent via knownClientApplications)
```

`knownClientApplications` chain: AgentApp lists FrontendApp, GatewayApp lists AgentApp. Without these links, the user would have to consent at each OBO step at runtime — which is impossible because there's no UI in the OBO path.

## Prerequisites

- An Entra ID tenant where you can register apps and grant admin consent.
- A test user in the tenant (work or school account — personal MS accounts don't work for Graph profile in cross-tenant scenarios).
- For the automated path: the [Azure CLI (`az`)](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli), version 2.50 or newer.

---

## Quick path (automated)

If you have the Azure CLI and rights to create apps + grant admin consent in your tenant, this is the fastest way.

### Step Q1 — Bootstrap `.env`

```bash
cd obo-training/examples/02-agent-via-gateway/entra/real-world
cp config.example.env .env
```

Leave the placeholders alone — the script will fill them in.

### Step Q2 — Sign in to Azure

```bash
az login
```

A browser window opens. Sign in with an account that has at least the **Application Administrator** Entra role (or **Global Administrator** if you want admin consent to also be granted automatically — see the note in Step Q3).

Verify you landed in the right tenant:

```bash
az account show --query "{tenantId: tenantId, user: user.name}"
```

If the wrong tenant comes back, switch with `az account set --subscription <id>` or re-run `az login --tenant <tenant-id>`.

### Step Q3 — Run the automation

```bash
python deploy/00_create_entra_apps.py
```

This single command performs every action documented in the manual path below:

1. Creates three app registrations (`agentcore-obo-uc2-frontend`, `-agent`, `-gateway`).
2. Sets the FrontendApp redirect URI to `http://localhost:8000/auth/callback`.
3. Sets `api://<app-id>` and a single `access_as_user` scope on AgentApp and GatewayApp.
4. Adds the three delegated API permissions (FrontendApp → Agent, AgentApp → Gateway, GatewayApp → Microsoft Graph User.Read).
5. Sets `knownClientApplications` so consent is combined across the chain.
6. Grants admin consent on all three apps.
7. Mints a client secret for each app.
8. Writes every value to `.env`: `TENANT_ID`, `FRONTEND_CLIENT_ID/SECRET`, `AGENT_CLIENT_ID/SECRET`, `GATEWAY_CLIENT_ID/SECRET`, `AGENT_SCOPE`, `GATEWAY_SCOPE`.

The script is **idempotent** — re-running picks up existing apps by display name and only mints new secrets when the corresponding `.env` value is missing or set to `replace-me`. Pass `--rotate-secrets` to force fresh secrets.

### Step Q4 — Verify

```bash
grep -E '^(TENANT_ID|FRONTEND_CLIENT_ID|AGENT_CLIENT_ID|GATEWAY_CLIENT_ID|AGENT_SCOPE|GATEWAY_SCOPE)=' .env
```

You should see five non-empty values plus a non-empty `TENANT_ID`. The three `_SECRET` values are also written but masked here for safety.

You're done with IdP setup. Skip to the next step in the [README's Quick Start](./README.md#quick-start) (deploy/01_create_providers.py).

> **If `admin-consent` failed**, the script tells you which apps need a Global Admin to consent manually. Either ask them to run `az ad app permission admin-consent --id <appId>` for each affected app, or open the Entra admin console at App registrations → the app → API permissions → "Grant admin consent for <tenant>". Verify after by running `az ad app permission list-grants --id <appId> --filter "consentType eq 'AllPrincipals'"` — you want a tenant-wide grant entry per app.

### Re-running and tearing down

| Goal | Command |
|---|---|
| Pick up where you left off (re-run is safe) | `python deploy/00_create_entra_apps.py` |
| Force-rotate all three client secrets | `python deploy/00_create_entra_apps.py --rotate-secrets` |
| Delete the three app registrations entirely | `python deploy/00_delete_entra_apps.py --yes` |

`00_delete_entra_apps.py` removes the apps and clears the related fields from `.env`. Don't run it unless you're sure — Entra app deletions are not undoable from the script.

---

## Manual path

The walkthrough below produces the same result as the automated path, click by click. Use it if you can't (or don't want to) install the Azure CLI, or if you're learning what each step does.

## Step 0 — Bootstrap your local `.env`

```bash
cd obo-training/examples/02-agent-via-gateway/entra/real-world
cp config.example.env .env
```

Open `.env` in your editor and keep it alongside the Entra admin console as you work through the steps below. Whenever a step says "Copy X → `SOME_VAR`," paste the value into `.env` immediately so you don't have to backtrack later.

## Step 1 — Register the Gateway app (resource at the bottom)

We register apps in **bottom-up order** because each one needs to know the audience of the one below it. The Gateway app sits at the bottom of the OBO chain (it's the audience for OBO #2's input and the OAuth client for OBO #2 against Graph).

### 1a. Register

1. **Entra admin center → App registrations → New registration**.
2. Name: `agentcore-obo-uc2-gateway`.
3. Supported account types: **Accounts in this organizational directory only**.
4. Redirect URI: leave blank.
5. **Register**.
6. Copy from the **Overview** page:
   - Application (client) ID → `GATEWAY_CLIENT_ID` in `.env`.
   - Directory (tenant) ID → `TENANT_ID` in `.env`.

### 1b. Create a client secret

1. **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-uc2-gateway`. Expires: 6 months.
3. Copy the **Value** → `GATEWAY_CLIENT_SECRET`.

### 1c. Expose an API

1. **Expose an API → Set** Application ID URI (accept default `api://<GATEWAY_CLIENT_ID>`).
2. **Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: `Admins and users`
   - Admin consent display name: "Access the gateway as the signed-in user"
   - Admin consent description: "Allows the agent to invoke gateway tools on the user's behalf"
   - State: **Enabled**
3. The full scope string is `api://<GATEWAY_CLIENT_ID>/access_as_user` → `GATEWAY_SCOPE` in `.env`.

### 1d. Add Microsoft Graph permission

This is where the Graph permission lives in UC2 — on the GatewayApp, because the GatewayApp is the OAuth client for OBO #2 against Graph.

1. **API permissions**.
2. If `User.Read` is not already listed, click **Add a permission → Microsoft Graph → Delegated permissions**, select `User.Read`, **Add permissions**.
3. **Grant admin consent for <tenant>** — confirm Status shows ✓ Granted.

> Missing admin consent here means OBO #2 will fail at runtime with `AADSTS65001`. Do not skip.

## Step 2 — Register the Agent app (middle layer)

### 2a. Register

1. **App registrations → New registration**.
2. Name: `agentcore-obo-uc2-agent`.
3. Supported account types: **Accounts in this organizational directory only**.
4. Redirect URI: leave blank.
5. **Register**.
6. Copy Application (client) ID → `AGENT_CLIENT_ID` in `.env`.

### 2b. Create a client secret

1. **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-uc2-agent`. Expires: 6 months.
3. Copy the Value → `AGENT_CLIENT_SECRET` in `.env`.

### 2c. Expose an API

1. **Expose an API → Set** Application ID URI (accept default `api://<AGENT_CLIENT_ID>`).
2. **Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: `Admins and users`
   - Admin consent display name: "Access the agent as the signed-in user"
   - Admin consent description: "Allows the frontend to invoke the agent on the user's behalf"
   - State: **Enabled**
3. The full scope string is `api://<AGENT_CLIENT_ID>/access_as_user` → `AGENT_SCOPE` in `.env`.

### 2d. Add permission to call the Gateway app

The agent app needs delegated permission to call `api://GatewayApp/access_as_user` so OBO #1 can request that scope.

1. **API permissions → Add a permission → APIs my organization uses**.
2. Search for and select the Gateway app you registered in Step 1 (`agentcore-obo-uc2-gateway`).
3. Choose **Delegated permissions** → check `access_as_user`.
4. **Add permissions**.
5. **Grant admin consent for <tenant>** — confirm Status shows ✓ Granted.

### 2e. Authorize FrontendApp on AgentApp (set up later in Step 3d)

We can't do this yet — FrontendApp doesn't exist. We'll come back to AgentApp's Expose-an-API page in Step 3d to add FrontendApp as an authorized client.

## Step 3 — Register the Frontend app (top of the chain)

### 3a. Register

1. **App registrations → New registration**.
2. Name: `agentcore-obo-uc2-frontend`.
3. Supported account types: **Accounts in this organizational directory only**.
4. Redirect URI: **Web** → `http://localhost:8000/auth/callback`.
   > Entra requires `http://` redirect URIs to use the literal hostname `localhost` (not `127.0.0.1` or any other IP). Use `https://` for any other host.
5. **Register**.
6. Copy Application (client) ID → `FRONTEND_CLIENT_ID` in `.env`.

### 3b. Create a client secret

1. **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-uc2-frontend`. Expires: 6 months.
3. Copy Value → `FRONTEND_CLIENT_SECRET` in `.env`.

### 3c. Add permission to call the Agent app

1. **API permissions → Add a permission → APIs my organization uses**.
2. Search for and select the Agent app from Step 2 (`agentcore-obo-uc2-agent`).
3. **Delegated permissions** → check `access_as_user`.
4. **Add permissions**.
5. **Grant admin consent for <tenant>** — confirm Status shows ✓ Granted.

### 3d. Authorize FrontendApp on AgentApp

> **Important:** this step happens on the **Agent app**, not the Frontend app. Navigate back to AgentApp.

1. Open the Agent app (`agentcore-obo-uc2-agent`) → **Expose an API**.
2. Scroll to **Authorized client applications** → **Add a client application**.
3. **Client ID**: paste `FRONTEND_CLIENT_ID` (from Step 3a).
4. **Authorized scopes**: check the box next to `api://<AGENT_CLIENT_ID>/access_as_user`.
5. **Add application**.

This sets `knownClientApplications` on AgentApp so the user's sign-in into FrontendApp covers consent for AgentApp's `access_as_user` scope as well.

### 3e. Authorize AgentApp on GatewayApp

1. Open the Gateway app (`agentcore-obo-uc2-gateway`) → **Expose an API**.
2. Scroll to **Authorized client applications** → **Add a client application**.
3. **Client ID**: paste `AGENT_CLIENT_ID` (from Step 2a).
4. **Authorized scopes**: check the box next to `api://<GATEWAY_CLIENT_ID>/access_as_user`.
5. **Add application**.

Now the chain is complete: a single sign-in into FrontendApp triggers a combined consent prompt that lists all four scopes (`access_as_user` on AgentApp, `access_as_user` on GatewayApp, Microsoft Graph `User.Read`, and the basic OIDC scopes). Both downstream OBO exchanges then run without any further user interaction.

## Step 4 — Verify

From `.env` you should now have:

| Env var | Source |
|---|---|
| `TENANT_ID` | Directory (tenant) ID |
| `FRONTEND_CLIENT_ID` | Frontend app Application ID |
| `FRONTEND_CLIENT_SECRET` | Frontend app secret value |
| `AGENT_CLIENT_ID` | Agent app Application ID |
| `AGENT_CLIENT_SECRET` | Agent app secret value |
| `GATEWAY_CLIENT_ID` | Gateway app Application ID |
| `GATEWAY_CLIENT_SECRET` | Gateway app secret value |
| `AGENT_SCOPE` | `api://<AGENT_CLIENT_ID>/access_as_user` |
| `GATEWAY_SCOPE` | `api://<GATEWAY_CLIENT_ID>/access_as_user` |
| `GRAPH_SCOPE` | `https://graph.microsoft.com/.default` |
| `FRONTEND_REDIRECT_URI` | `http://localhost:8000/auth/callback` |

### Sanity checks

Walk through each app one at a time. Don't switch between apps mid-check — it's how mistakes happen.

**Gateway app** (`agentcore-obo-uc2-gateway`):

- **API permissions**: Microsoft Graph `User.Read` shows ✓ Granted.
- **Expose an API**: Application ID URI is `api://<GATEWAY_CLIENT_ID>` and `access_as_user` is Enabled.
- **Expose an API → Authorized client applications**: AgentApp's client ID is listed with `access_as_user` checked.
- **Certificates & secrets**: client secret value is in `.env` as `GATEWAY_CLIENT_SECRET`.

**Agent app** (`agentcore-obo-uc2-agent`):

- **API permissions**: GatewayApp's `access_as_user` shows ✓ Granted.
- **Expose an API**: Application ID URI is `api://<AGENT_CLIENT_ID>` and `access_as_user` is Enabled.
- **Expose an API → Authorized client applications**: FrontendApp's client ID is listed with `access_as_user` checked.
- **Certificates & secrets**: client secret value is in `.env` as `AGENT_CLIENT_SECRET`.

**Frontend app** (`agentcore-obo-uc2-frontend`):

- **Authentication**: Web platform redirect URI `http://localhost:8000/auth/callback` is registered.
- **API permissions**: AgentApp's `access_as_user` shows ✓ Granted.
- **Certificates & secrets**: client secret value is in `.env` as `FRONTEND_CLIENT_SECRET`.

If any of these show ⚠ Not granted, fix admin consent before proceeding.

## Troubleshooting

### `AADSTS65001: The user or administrator has not consented`

You skipped admin consent on one of the apps, or the `knownClientApplications` chain is incomplete. Check in order:

1. All three apps' API permissions show ✓ Granted.
2. AgentApp lists FrontendApp under Expose-an-API → Authorized client applications.
3. GatewayApp lists AgentApp under Expose-an-API → Authorized client applications.

### `AADSTS500113: No reply address is registered`

FrontendApp's redirect URI doesn't match. In FrontendApp → Authentication, ensure `http://localhost:8000/auth/callback` is registered under Web platform.

### OBO #1 fails with `400 Token exchange failed` (agent → Gateway)

The exchange is failing on the agent's call. Check:

1. AgentApp's `access_as_user` is consented (Step 2c).
2. AgentApp has been granted the GatewayApp's `access_as_user` permission and admin-consented (Step 2d).
3. AgentApp's client secret in `.env` matches the one stored on the credential provider (run `python deploy/01_create_providers.py` again if you rotated).
4. The `T_user` JWT being sent has `aud = AGENT_CLIENT_ID`. Decode it at jwt.io if unsure.

### Agent returns `OBO #1 failed: ... Token exchange failed with HTTP status 400`

Entra rejected the exchange. Fastest diagnosis is to reproduce it against Entra directly with curl:

```bash
source .env
T_USER="<paste a valid T_user from the BFF>"

curl -sS -X POST "https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/token" \
  -d "grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer" \
  -d "assertion=$T_USER" \
  -d "client_id=$AGENT_CLIENT_ID" \
  -d "client_secret=$AGENT_CLIENT_SECRET" \
  -d "scope=$GATEWAY_SCOPE" \
  -d "requested_token_use=on_behalf_of" | python3 -m json.tool
```

**If curl returns an `access_token`** — Entra is happy; something's stale on the AgentCore side. The credential provider still holds the old `client_secret`. Fix:

```bash
python deploy/01_create_providers.py    # now update-in-place; refreshes secret
```

The updated `01_create_providers.py` calls `update_oauth2_credential_provider` when a provider already exists, so re-running always syncs the current `.env` values into AgentCore Identity.

**If curl returns an AADSTS error code**, use it to narrow the fix:

| AADSTS code | Meaning | Fix |
|---|---|---|
| `AADSTS7000215` | AgentApp client secret invalid | Rotate: `python deploy/00_create_entra_apps.py --rotate-secrets` then `python deploy/01_create_providers.py` |
| `AADSTS65001` | Consent not granted | Re-run `python deploy/00_create_entra_apps.py` (re-grants admin consent) |
| `AADSTS500131` | Delegated permission missing | AgentApp doesn't have `api://GatewayApp/access_as_user` — re-run `00_create_entra_apps.py` |
| `AADSTS50013` | Assertion invalid/expired | Sign out and sign back in; try again with the fresh token |
| `AADSTS9002313` | Malformed request | Show the full error; likely a scope typo |
| `AADSTS70008` | Assertion signature/issuer mismatch | v1/v2 token/discovery URL misalignment (see previous section) |

**A less-common cause:** the agent code is missing `customParameters={"requested_token_use": "on_behalf_of"}` on the `get_resource_oauth2_token` call. Verify with:

```bash
grep -A1 customParameters agent/agent.py
```

The `agent/agent.py` in this example includes it; verify your copy still does after any edits.

### OBO #2 fails with `400` (Gateway → Graph)

The exchange is failing inside the Gateway. Check:

1. GatewayApp's Microsoft Graph `User.Read` is consented (Step 1d).
2. GatewayApp has been authorized as a known client app of nothing — but AgentApp has authorized FrontendApp, and GatewayApp has authorized AgentApp (Steps 3d and 3e).
3. The `T_gateway` JWT received by the Gateway has `aud = GATEWAY_CLIENT_ID`. Check the Gateway's CloudWatch logs.
4. GatewayApp's client secret in `.env` matches the one on the gateway-actor credential provider.
5. The Gateway target has `customParameters: {"requested_token_use": "on_behalf_of"}` — without it Entra silently rejects the exchange.

### Agent returns `401: Claim 'iss' value mismatch with configuration.`

The Gateway is rejecting `T_gateway` because its `iss` claim doesn't match the discovery URL. This happens when there's a v1/v2 mismatch anywhere in the chain — either the app manifests don't force v2 tokens, or the Gateway's discovery URL is on the wrong side.

**The setup normalizes on v2 tokens end-to-end:**

- **Discovery URLs (Runtime + Gateway):** `https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration` — the v2.0 variant. Declares `iss = https://login.microsoftonline.com/<tenant>/v2.0`.
- **App manifests (Agent + Gateway):** `api.requestedAccessTokenVersion = 2`. This forces Entra to issue v2 access tokens regardless of which endpoint they were requested from. Without it, Entra defaults to v1 tokens (`iss = https://sts.windows.net/<tenant>/`) and the v2 Gateway rejects them.

**How to check & fix if you're already deployed:**

```bash
# Should print 2. If it prints null or 1, run the update below.
az ad app show --id "$AGENT_CLIENT_ID"   --query "api.requestedAccessTokenVersion"
az ad app show --id "$GATEWAY_CLIENT_ID" --query "api.requestedAccessTokenVersion"

# Fix: force v2 tokens on both apps
az ad app update --id "$AGENT_CLIENT_ID"   --set 'api.requestedAccessTokenVersion=2'
az ad app update --id "$GATEWAY_CLIENT_ID" --set 'api.requestedAccessTokenVersion=2'
```

**After fixing the app manifests, sign out and back in.** The user's cached token in the BFF session is still v1; only a fresh sign-in produces a v2 token.

The `00_create_entra_apps.py` automation now sets `requestedAccessTokenVersion=2` when it creates apps, so this only bites if the apps existed before that fix landed or if apps were created manually.

### `AADSTS50105: The signed in user is not assigned to a role for the application`

You set Supported account types to "Accounts in this organizational directory only" but the test user is from a different directory or is a guest. Either use a user from the same tenant, or tighten the test user's tenant assignment.
