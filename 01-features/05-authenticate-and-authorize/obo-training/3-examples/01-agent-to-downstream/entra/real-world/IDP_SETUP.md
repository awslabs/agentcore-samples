# Entra ID Setup — Real-World Example

Two Entra app registrations are needed:
1. **Frontend app** — the user-facing OIDC client the browser signs into.
2. **Agent app** — the resource that exposes `access_as_user` and does the OBO exchange.

This separation is the production-correct pattern. The local example combined them into one app for simplicity; here we separate them.

## Prerequisites

- A Microsoft Entra ID tenant where you have rights to register apps and grant admin consent.
- A test user in the tenant.

---

## Step 1 — Create the Agent app (resource app)

This is the app that "owns" the `access_as_user` scope and that the user consents to on the way in.

### 1a. Register

1. **Entra admin center → App registrations → New registration**.
2. Name: `agentcore-obo-uc1-rw-agent`.
3. Supported account types: **Accounts in this organizational directory only**.
4. Redirect URI: leave blank.
5. **Register**.
6. Copy from Overview:
   - Application (client) ID → `AGENT_CLIENT_ID`.
   - Directory (tenant) ID → `TENANT_ID`.

### 1b. Create a client secret

1. **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-uc1-rw`. Expires: 6 months.
3. Copy the **Value** → `AGENT_CLIENT_SECRET`.

### 1c. Expose an API

1. **Expose an API → Set** the Application ID URI (accept default `api://<AGENT_CLIENT_ID>`).
2. **Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: `Admins and users`
   - Admin consent display name: "Access the agent as the signed-in user"
   - Admin consent description: "Allows the frontend to invoke the agent on the user's behalf"
   - State: **Enabled**
3. Full scope string is `api://<AGENT_CLIENT_ID>/access_as_user` → `AGENT_SCOPE`.

### 1d. Add Microsoft Graph permission

1. **API permissions**.
2. If `User.Read` is NOT listed, click **Add a permission → Microsoft Graph → Delegated permissions**, select `User.Read`, **Add permissions**. (Some app templates auto-add this — in that case just skip ahead.)
3. **Grant admin consent for <tenant>** — confirm the Status column shows ✓ Granted.

> Missing admin consent is the #1 cause of `400 Token exchange failed`. Do not skip this step.

---

## Step 2 — Create the Frontend app (client app)

This is the app the browser actually signs into.

### 2a. Register

1. **Entra admin center → App registrations → New registration**.
2. Name: `agentcore-obo-uc1-rw-frontend`.
3. Supported account types: **Accounts in this organizational directory only**.
4. Redirect URI: **Web** → `http://localhost:8000/auth/callback`.
   > Entra requires `http://` redirect URIs to use the literal hostname `localhost` (not `127.0.0.1` or any other IP). Use `https://` if you want a different host.
5. **Register**.
6. Copy Application (client) ID → `FRONTEND_CLIENT_ID`.

### 2b. Create a client secret

1. **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-uc1-rw-frontend`.
3. Copy the Value → `FRONTEND_CLIENT_SECRET`.

### 2c. Grant the frontend permission to call the agent

1. **API permissions → Add a permission → My APIs** (or "APIs my organization uses").
2. Select the Agent app you created in Step 1.
3. Choose **Delegated permissions** → `access_as_user`.
4. **Add permissions**.
5. **Grant admin consent for <tenant>** — confirm the Status is ✓ Granted.

### 2d. Add the frontend as a known client application on the Agent app

> **Important:** this configuration is on the **Agent app** (the one you created in Step 1, named `agentcore-obo-uc1-rw-agent`) — NOT on the Frontend app you just registered. Navigate back to the Agent app to do this.

This lets the frontend's consent flow cover both apps in a single prompt (combined consent). Without it, signing into the frontend only consents to the frontend app, and the OBO exchange will later fail because the user never consented to the agent app.

1. Open the **Agent app** (`agentcore-obo-uc1-rw-agent`) in Entra.
2. Go to **Expose an API**.
3. Scroll down to **Authorized client applications** and click **Add a client application**.
4. **Client ID**: paste your `FRONTEND_CLIENT_ID` (the client ID of the app you just registered in Step 2a).
5. **Authorized scopes**: check the box next to the scope `api://<AGENT_CLIENT_ID>/access_as_user` — the UI lists the full scope URI, not just the short `access_as_user` name.
6. Click **Add application**.

This corresponds to `knownClientApplications` in the manifest. With it in place, signing into the frontend once triggers consent for both apps.

### Sanity check after 2d

Back on the **Agent app** → **Expose an API** page, you should see an entry under "Authorized client applications" listing your frontend's Client ID with `api://<AGENT_CLIENT_ID>/access_as_user` checked. If the scope URI looks wrong or isn't checked, redo step 5.

---

## Step 3 — Verify

From `.env` you should now have:

| Env var | Value |
|---|---|
| `TENANT_ID` | Directory (tenant) ID |
| `FRONTEND_CLIENT_ID` | Frontend app Application ID |
| `FRONTEND_CLIENT_SECRET` | Frontend app secret value |
| `AGENT_CLIENT_ID` | Agent app Application ID |
| `AGENT_CLIENT_SECRET` | Agent app secret value |
| `AGENT_SCOPE` | `api://<AGENT_CLIENT_ID>/access_as_user` |
| `GRAPH_SCOPE` | `https://graph.microsoft.com/User.Read` |
| `FRONTEND_REDIRECT_URI` | `http://localhost:8000/auth/callback` |

### Sanity check

Verify each app's settings before moving on. Stay inside one app at a time to avoid context switching.

**Agent app** (`agentcore-obo-uc1-rw-agent`):

- **API permissions**: Microsoft Graph `User.Read` shows ✓ Granted for <tenant>.
- **Expose an API**: Application ID URI is `api://<AGENT_CLIENT_ID>` and the `access_as_user` scope is listed and Enabled.
- **Expose an API → Authorized client applications**: your `FRONTEND_CLIENT_ID` is listed with `api://<AGENT_CLIENT_ID>/access_as_user` checked.
- **Certificates & secrets**: client secret value is in your `.env` as `AGENT_CLIENT_SECRET`.

**Frontend app** (`agentcore-obo-uc1-rw-frontend`):

- **Authentication**: a Web platform redirect URI `http://localhost:8000/auth/callback` is registered.
- **API permissions**: `<agent-app>/access_as_user` shows ✓ Granted for <tenant>.
- **Certificates & secrets**: client secret value is in your `.env` as `FRONTEND_CLIENT_SECRET`.

If any of these show ⚠ Not granted, fix admin consent before proceeding.

---

## Troubleshooting

### `AADSTS65001: The user or administrator has not consented`

You skipped admin consent on one of the apps. Go back and click **Grant admin consent for <tenant>** on both apps' API permissions pages.

### `AADSTS500113: No reply address is registered`

The frontend's redirect URI doesn't match. In the Frontend app → Authentication, add `http://localhost:8000/auth/callback` under Web platform. Note: Entra requires `http://` redirect URIs to use the literal hostname `localhost` (not `127.0.0.1`).

### `AADSTS700016: Application not found in directory`

You're signing in with a personal Microsoft account (outlook.com / hotmail.com) against a single-tenant app. Either use a work account, or change the Frontend app's "Supported account types" to allow personal accounts (adds complexity — prefer using a work account).

### `ValidationException: Token exchange failed with HTTP status 400`

This comes from Entra rejecting the OBO exchange. Check in order:

1. **Admin consent missing** (most common). In the Entra admin center → App registrations → your agent app → API permissions, every permission should show "Granted for <tenant>" in the Status column. If any show "Not granted", click **Grant admin consent for <tenant>**. Same check on the frontend app — if either app hasn't been consented, OBO fails.
2. **Inbound token `aud` does not match `AGENT_CLIENT_ID`.** The token the frontend forwards must be audienced at the agent app. A mismatch means the frontend requested the wrong scope or the frontend is pointed at the wrong agent app in `.env`.
3. **Client secret expired or rotated.** Entra secrets expire. If `.env` has a stale secret for the agent app, the OBO exchange can't authenticate the middle-tier client. Regenerate the secret in the agent app's **Certificates & secrets** and update `.env`, then re-run `python deploy/01_create_providers.py` to push the new secret to the credential provider.
4. **Agent app missing the expected Graph permission.** API permissions → confirm `User.Read` is listed and Granted.
5. **Token version mismatch.** Entra issues v1.0 tokens by default. If you've manually overridden the discovery URL to `/v2.0/` but your app still issues v1.0, OBO can fail. Rare with the built-in `MicrosoftOauth2` provider; worth checking if you've customized the credential provider config.
