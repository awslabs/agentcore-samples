# Entra ID Setup for Use Case 1

One Entra app registration is needed for this example. It plays the role of the **agent**.

## Prerequisites

- A Microsoft Entra ID tenant where you have rights to register apps.
- A Microsoft 365 user account for testing (personal `outlook.com` accounts work for `/me` but not Calendar/Mail).

## Steps

### 1. Register the agent app

1. Open the [Entra admin center](https://entra.microsoft.com).
2. Navigate to **Applications → App registrations → New registration**.
3. Name: `agentcore-obo-usecase1-agent` (or anything — just remember it).
4. Supported account types: `Accounts in this organizational directory only` (single tenant) for simplicity.
5. Redirect URI: leave blank for now — AgentCore will provide one in a later step.
6. Click **Register**.
7. From the **Overview** page, copy:
   - **Application (client) ID** → this is `AGENT_CLIENT_ID` in `.env`.
   - **Directory (tenant) ID** → this is `TENANT_ID` in `.env`.

### 2. Create a client secret

1. In the app, go to **Certificates & secrets → New client secret**.
2. Description: `agentcore-obo-usecase1`. Expires: 6 months (or per your policy).
3. Click **Add**. **Copy the secret Value immediately** (not the ID). This is `AGENT_CLIENT_SECRET` in `.env`.

### 3. Expose an API

1. Go to **Expose an API** in the app.
2. If Application ID URI is empty, click **Set** and accept the default `api://<client-id>`.
3. Click **Add a scope**:
   - Scope name: `access_as_user`
   - Who can consent: `Admins and users`
   - Admin/user consent display names & descriptions: "Access the app as the signed-in user" (be brief and clear).
   - State: `Enabled`
   - Click **Add scope**.

The scope identifier will be `api://<client-id>/access_as_user`. This is `AGENT_SCOPE` in `.env`.

### 4. Add Microsoft Graph API permission

1. Go to **API permissions**.
2. If `User.Read` is NOT listed (some Entra app types auto-add it, others don't):
   - Click **Add a permission** → **Microsoft Graph** → **Delegated permissions**.
   - Search for and select `User.Read`.
   - Click **Add permissions**.
3. If `User.Read` IS already listed, skip to the admin consent step.

#### 4a. Grant admin consent (critical — OBO will fail 400 without this)

On the **API permissions** page, look at the **Status** column:

- If `User.Read` shows a green ✓ "Granted for <tenant>" — you're done.
- If it shows a gray ⚠ "Not granted" — click **Grant admin consent for <tenant>** at the top and confirm.

You must be a tenant admin (Global Admin or Cloud Application Admin) to grant admin consent. If you are NOT an admin:

1. Ask your tenant admin to click the button for you, OR
2. Sign in as your test user and consent interactively — Entra will show a consent screen the first time you sign in IF the app is configured to allow user consent.

**Symptoms if admin consent is missing:** `GetWorkloadAccessTokenForJWT` succeeds, but `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE` returns:
```
ValidationException: Token exchange failed with HTTP status 400
```
This is Entra rejecting the OBO because no active consent exists for the agent → Graph delegation.

### 5. Add a redirect URI

The credential provider you'll create in step 01 will print a callback URL. Come back here after running `01_create_providers.py` and add the callback URL to the app:

1. Go to **Authentication → Add a platform → Web**.
2. Paste the callback URL from the provider creation output.
3. Enable **Access tokens** and **ID tokens** under Implicit grant (not strictly required for auth code flow, but helps some debugging).
4. Click **Configure**.

> **Note:** AgentCore Identity runs the 3LO callback on a managed URL. You do not need a redirect for `localhost` — that's only needed if you run a raw 3LO outside of AgentCore.

## Values you should now have for `.env`

| Env var | Value |
|---|---|
| `TENANT_ID` | Directory (tenant) ID |
| `AGENT_CLIENT_ID` | Application (client) ID |
| `AGENT_CLIENT_SECRET` | Secret value (not ID) |
| `AGENT_SCOPE` | `api://<agent-client-id>/access_as_user` |
| `GRAPH_SCOPE` | `https://graph.microsoft.com/User.Read` |
| `AWS_REGION` | Your AgentCore region, e.g. `us-west-2` |
| `USER_ALIAS` | A short identifier for the test user, e.g. `demo-user` |
| `WORKLOAD_NAME` | A name for the workload identity, e.g. `obo-usecase1-entra` |
| `CLIENT_PROVIDER_NAME` | Credential provider name for the frontend/3LO, e.g. `obo-uc1-entra-client` |
| `ACTOR_PROVIDER_NAME` | Credential provider name for the agent/OBO, e.g. `obo-uc1-entra-actor` |


## Troubleshooting

### `ValidationException: Token exchange failed with HTTP status 400`

This comes from Entra rejecting the OBO exchange. Check in order:

1. **Admin consent** (most common cause — see step 4a above). In the Entra admin center → App registrations → your agent app → API permissions, every permission in the list should show "Granted for <tenant>" in the Status column. If anything shows "Not granted", click **Grant admin consent for <tenant>**.

2. **`AGENT_SCOPE` matches the `aud` of the inbound token.** The inbound user JWT must have `aud == AGENT_CLIENT_ID`. Run:
   ```bash
   python -c "
   import json, base64
   t = json.load(open('.user-jwt-cache.json'))['token']
   p = t.split('.')[1]
   print(json.loads(base64.urlsafe_b64decode(p + '='*(-len(p)%4))))
   "
   ```
   Verify `aud` equals your `AGENT_CLIENT_ID`. If not, the user JWT was minted for a different app.

3. **Token version mismatch.** Entra issues v1.0 tokens by default (`iss` contains `sts.windows.net`). If you're manually overriding the discovery URL to the v2.0 endpoint and your tokens are v1.0, OBO can fail. With the built-in `MicrosoftOauth2` provider this should be handled automatically, so this is rare.

4. **Client secret expired or rotated.** Entra secrets expire. If your `.env` has a stale secret, the middle-tier client auth fails before OBO can run. Regenerate the secret in Certificates & secrets and update `.env`. Then re-run `01_create_providers.py` to push the new secret to the credential provider.

5. **Wrong scope format.** `GRAPH_SCOPE` should be the full resource-qualified scope: `https://graph.microsoft.com/User.Read`. A bare `User.Read` will fail.

### Running the diagnostic helper

For quick diagnosis, use the helper:
```bash
python diagnose_obo.py
```
It will decode your cached user JWT, check whether `aud` matches, and attempt an OBO exchange with detailed error output.
