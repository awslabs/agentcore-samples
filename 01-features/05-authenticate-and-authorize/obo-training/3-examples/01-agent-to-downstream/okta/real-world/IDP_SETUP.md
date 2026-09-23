# Okta Setup — Real-World Example

Two Okta app integrations and an authorization server are needed for this real-world variant:

1. **Web App (frontend)** — the user-facing OIDC client the browser signs into.
2. **Service App (agent)** — the middle-tier confidential client that does OBO. Its credentials authenticate the exchange call.

You'll also configure an authorization server on the tenant (most tenants ship with a `default` one) with two access policies — one for the Web App's user-sign-in flow, one for the Service App's OBO exchange. This guide walks you through all of it.

## Concepts and terminology

If you're new to OAuth / OIDC / Okta, the steps below will ask you to pick between options whose names all sound similar ("OIDC vs OAuth", "Web App vs Native App vs API Services", "client secret vs PKCE"). This section defines the terms in order of first appearance so you know what you're choosing between.

- **OAuth 2.0** — a framework for *delegated authorization*. It lets an application act on behalf of a user without seeing the user's password. OAuth itself does not say anything about who the user is — only what the caller is allowed to do.
- **OIDC (OpenID Connect)** — a thin identity layer on top of OAuth 2.0. It adds a standard way to *identify* the signed-in user (via an ID token and the `/v1/userinfo` endpoint). When Okta asks whether your app uses "OIDC – OpenID Connect" vs "API Services", OIDC means "a user is going to sign in interactively"; API Services means "no user — a machine authenticates as itself".
- **Client** — any application that talks to an authorization server. Your frontend and your agent are both clients. A **confidential client** can safely store a client secret (it runs on a server you control). A **public client** cannot (mobile apps, SPAs running in a browser) — it uses PKCE instead of a secret.
- **Web App / Native App / Single-Page App** — Okta's categories for OIDC clients. Web App = server-side (your FastAPI BFF) — confidential, uses a client secret. Native App = mobile or desktop — public, uses PKCE only. SPA = in-browser JavaScript — public, uses PKCE only. For this example you want **Web App** because the BFF is a server-side process holding a secret.
- **API Services (app type)** — Okta's label for machine-to-machine clients. No user signs in. Your Service App is this type because the agent authenticates as itself, not as the user, when calling Okta's `/v1/token` for the exchange.
- **Authorization server** — the Okta component that mints and validates access tokens. A single Okta tenant can host multiple auth servers; the one you pick becomes the issuer of every token in this flow. It also holds the scopes, access policies, and audience values.
- **Audience (`aud`)** — who the token is *for*. In Okta's model, `aud` is the auth server's audience (typically `api://default`), not a specific app. An API validating a token checks `aud` to confirm "yes, this token was intended for me to consume".
- **Client ID (`cid`)** — who the token was *issued to*. Every token Okta mints records which client asked for it. On the inbound user token, `cid` is your Web App. On the OBO'd token, `cid` rotates to the Service App — that's how Okta records the actor.
- **Subject (`sub`)** — who the token is *about*. The end user's login (e.g. `alice@example.com`). This is the claim that stays the same across the OBO exchange; preserving it is the whole point of OBO.
- **Scope (`scp`)** — what the caller is allowed to do. For the user sign-in, scopes like `openid profile email` grant the agent access to the corresponding profile fields. Scopes can narrow across the exchange, but they can never exceed what the user originally consented to.
- **Authorization Code grant** — the OAuth flow for interactive user sign-in. The user signs into Okta, Okta redirects back to your app with a short-lived code, and your app exchanges that code for an access token. This is what the Web App uses in Step 1.
- **PKCE (Proof Key for Code Exchange)** — an add-on to the Authorization Code flow that prevents the code from being usable if intercepted. Required for public clients; recommended (and we enable it) for confidential clients too.
- **Client secret** — the confidential client's password. Stored server-side; Okta uses it to authenticate the client when the client exchanges the auth code for a token, and again when the Service App does the OBO exchange. Never leaves the server.
- **Token Exchange grant (RFC 8693)** — the OAuth flow Okta uses for OBO. The middle-tier client (Service App) presents the user's token as a "subject token" and asks Okta to mint a new token whose `cid` is the Service App but whose `sub` is still the user.
- **DPoP (Demonstrating Proof of Possession)** — a mechanism that cryptographically binds a token to a specific client-held key, so a stolen token can't be replayed. Newer Okta tenants default to requiring DPoP on API Services apps. AgentCore Identity does not sign DPoP proofs, so **DPoP must be disabled** on the Service App for this example to work. This is not a security regression in isolation — OBO tokens are audience-bound and short-lived — but you'd want to evaluate this for production.
- **Access policy** — Okta's rule system on an authorization server. An access policy applies to a set of clients and says what grants and scopes they're allowed to use. For this example you need two policies on the auth server:
  - One allowing the **Web App** to use the Authorization Code grant with `openid profile email`.
  - One allowing the **Service App** to use the Token Exchange grant (OBO).
- **Issuer (`iss`)** — the URL of the authorization server that minted the token. Downstream APIs use this to fetch the JWKS and verify the token's signature. This claim stays the same across the OBO exchange because both tokens come from the same auth server.
- **Discovery URL** — the standard OIDC endpoint that lists everything an API needs to validate tokens (signing keys, issuer, audience, supported grants). URL pattern: `https://<domain>/oauth2/<auth-server-id>/.well-known/openid-configuration`. AgentCore Runtime points at this URL to validate inbound JWTs; authlib uses it in the frontend to drive the Authorization Code flow.

## Architecture at a glance

```
👤 User signs into Web App (browser)
       ↓
🖥️  Web App → mint Web App client secret + Auth Code grant with PKCE
       ↓ user access token (aud=api://default, cid=<Web App>)
🤖 Agent on AgentCore Runtime
       ↓ uses Service App's credentials to do RFC 8693 exchange
       ↓ AgentCore Identity POSTs to Okta /v1/token
🏛  Okta Authorization Server (issues downstream token)
       ↓ downstream token (aud=api://default, cid=<Service App>, sub=same user)
🎯 Okta /v1/userinfo (the downstream API)
```

### Which Okta objects you'll create

| Object | Purpose |
|---|---|
| Web App (frontend) | Frontend OIDC client the browser signs into |
| Service App (agent) | Confidential client the agent uses to do OBO |
| Authorization server | Issues + validates all tokens; holds scopes and access policies |
| Access policy — upstream | Allows the Web App to mint user tokens via Authorization Code |
| Access policy — OBO | Allows the Service App to swap user tokens via Token Exchange |

This example calls Okta's `/v1/userinfo` endpoint as the downstream API. Userinfo accepts any token with `openid` in its scopes, so we don't need to define a custom downstream scope — `openid profile email` covers everything.

## Prerequisites

- An Okta org (free Integrator plan works).
- A test user in your tenant.

## Step 0 — Bootstrap your local `.env`

Before touching the Okta console, create the `.env` file you'll fill in as you go.

```bash
cd obo-training/examples/01-agent-to-downstream/okta/real-world
cp config.example.env .env
```

Open `.env` in your editor and keep it alongside the Okta admin console as you work through the steps below. Every time a step says "Copy X → `SOME_VAR`", paste the value into `SOME_VAR=` in `.env` right away.

> **Tip:** `.env` is gitignored (see `.gitignore` in this folder) so your secrets won't accidentally land in version control.

## Step 0.5 — Find your Okta domain and authorization server ID

Two values in `.env` — `OKTA_DOMAIN` and `OKTA_AUTH_SERVER_ID` — describe *which Okta tenant* and *which authorization server* everything else will use. You need to set these before creating any apps, because the apps you create in the next steps will live in this tenant and issue tokens from this authorization server.

### `OKTA_DOMAIN`

The short hostname of your Okta tenant — the part **before** `/admin/` in the URL of your Okta admin console.

1. Open the Okta admin console in a browser.
2. Look at the URL bar. It will be one of:
   - `https://<something>-admin.okta.com/admin/…` (classic Okta URL)
   - `https://<something>.okta.com/admin/…`
3. Take the host, remove `-admin` if present, and keep everything before the first `/`. Examples:
   - Admin URL `https://integrator-1234567-admin.okta.com/admin/dashboard` → `OKTA_DOMAIN=integrator-1234567.okta.com`
   - Admin URL `https://dev-987654.okta.com/admin/dashboard` → `OKTA_DOMAIN=dev-987654.okta.com`
   - Custom domain `https://login.acme.com/admin/…` → `OKTA_DOMAIN=login.acme.com`

> **Important:** use the non-admin host. OIDC discovery is only served from the app-facing host, not the `-admin` one. Using the admin host causes `Invalid Discovery URL` errors.

Paste your value into `.env`:

```
OKTA_DOMAIN=<your-tenant>.okta.com
```

### `OKTA_AUTH_SERVER_ID`

An Okta tenant can host multiple custom authorization servers. Each one is a distinct issuer with its own signing keys, its own audience, its own scopes, and its own access policies. For this example you'll configure access policies on one specific server; `OKTA_AUTH_SERVER_ID` is that server's short identifier.

1. In the Okta admin console, go to **Security → API → Authorization Servers**.
2. You'll see a table of servers. Each has a **Name**, **Audience**, **Issuer URI**, and other columns.
3. Look at the **Issuer URI** column and find the last path segment of the issuer URI. That segment is the auth server ID:
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/default` → `OKTA_AUTH_SERVER_ID=default`
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/ausXXXXXXXXXXXX` → `OKTA_AUTH_SERVER_ID=ausXXXXXXXXXXXX`

> **The table is empty?** Newer Okta Integrator tenants ship without a `default` authorization server. Two options:
>
> - **Create one named `default`.** Click **Add Authorization Server**. Name: `default`. Audience: `api://default`. Description: whatever. **Save.** Then use `OKTA_AUTH_SERVER_ID=default`. The auth server ID is the last path segment of the **Issuer URI** (for servers named `default`, that string will also be `default`).
> - **Use an existing custom server.** Pick any one from the table, copy the last segment of its Issuer URI (`ausXXXXXXXXXXXX`), and use that as `OKTA_AUTH_SERVER_ID`. Its audience becomes your `OKTA_AUDIENCE` in Step 3.

Paste your value into `.env`:

```
OKTA_AUTH_SERVER_ID=default
```

### Verify both values before continuing

Open this URL in your browser, substituting your values:

```
https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration
```

You should see a JSON document with fields like `issuer`, `jwks_uri`, `authorization_endpoint`, `token_endpoint`. If you instead get:

- A sign-in page → you're using the admin host; drop the `-admin`.
- A 404 → the auth server ID is wrong, or the server doesn't exist yet.
- `ERR_NAME_NOT_RESOLVED` / DNS error → typo in `OKTA_DOMAIN`.

Don't move on until this URL returns a JSON document.

## Step 1 — Register the Web App (frontend)

This is the OIDC client the browser signs into. Because our frontend is a server-side FastAPI process (a BFF, not a browser-only SPA), we pick **Web Application** — a confidential OIDC client that can hold a secret.

1. Okta admin → **Applications → Applications → Create App Integration**.
2. Select **OIDC – OpenID Connect**, then **Web Application**.
3. Name: `AgentCore OBO UC1 Web (frontend)`.
4. **Grant type**: enable **Authorization Code** (default) and **Refresh Token**.
5. **Sign-in redirect URIs**: `http://localhost:8000/auth/callback`.
   > Okta requires `http://` redirect URIs to use the literal hostname `localhost` (not `127.0.0.1`). Use `https://` for any other host.
6. **Sign-out redirect URIs**: leave blank or set to `http://localhost:8000/`.
7. **Controlled access**: pick `Allow everyone in your organization to access` for the demo.
   > **Production tip:** prefer `Limit access to selected groups` and scope the app to a specific Okta group (e.g. `agentcore-obo-users`). Only people in that group can sign in, and you can use the same group to drive downstream authorization via group claims.
8. **Save**.
9. Configure client authentication and PKCE. On the **General** tab:

   1. Find the **Client Credentials** section and click **Edit**.
   2. **Client Authentication**: select **Client secret**.
   3. **PKCE**: check **Require PKCE as additional verification**.
   4. Click **Save**.

   > Both settings matter. Client secret lets the BFF authenticate to Okta when exchanging the auth code; PKCE protects the exchange from code-interception attacks. Missing either will cause the auth-code flow to fail.

10. Get the client secret (reuse the existing one if Okta auto-generated it; otherwise create one). Still on the **General** tab, scroll to the **Client Credentials** section.

    1. Click **Edit** on the **Client Credentials** section (or on **CLIENT SECRETS** if it's shown as a separate subsection).
    2. Look at the **CLIENT SECRETS** table:
       - **If a row with Status = Active already exists** (common on newer Okta Integrator tenants — Okta auto-generates a secret at app creation time), click the reveal button on that row and copy the value. You're done.
       - **If the table is empty** and shows the placeholder *"A new client secret is generated after you click Save"* (the **Generate new secret** button is disabled on fresh apps), click **Save** at the bottom of the panel. Okta generates the secret on save. Reveal the value and copy it.
    3. Treat the value like any credential — store it once in `.env` and don't leave it pinned in your clipboard.

    > **When is "Generate new secret" enabled?** Once you already have at least one active secret on the app. That's Okta's zero-downtime rotation pattern — you'd use it to mint a second secret, roll traffic over, then delete the old one. For this demo one secret is enough.

11. Copy:
    - **Client ID** → `FRONTEND_CLIENT_ID` (paste into `.env` now).
    - **Client secret** value → `FRONTEND_CLIENT_SECRET` (paste into `.env` now).

### Assign the app to your test user

Open the Web App → **Assignments** tab. The experience depends on your tenant:

#### Option A — Traditional assignment (most dev and customer orgs)

You'll see an **Assign → Assign to People** button.

1. Click **Assign → Assign to People**.
2. Pick your test user and click **Assign**.
3. **Save**.

#### Option B — Implicit assignment (newer Okta Integrator orgs with Federation Broker Mode)

You'll see a message "**This app is implicitly assigned to users — Immediate access is currently enabled with Federation Broker Mode for this app. As a result, user access is determined by app sign-on policies.**"

In this mode every user in your org can sign into the app by default, gated by the app's sign-on policy. For this demo the default (allow all, no extra MFA) is fine — no action needed. To tighten:

1. Click **Configure Sign On Policy** (or go to the **Sign On** tab → **Sign On Policy**).
2. Add a rule that requires the user to be in a specific group, or requires MFA, etc.
3. **Save**.

> **Which mode am I in?** If the Assignments tab shows an **Assign** button, you're in Option A. If it shows the "implicitly assigned" message with a **Configure Sign On Policy** button, you're in Option B. Both work for this demo.

### Sanity check

Open the Web App → **General** tab. You should see:

- **Grant types**: Authorization Code and Refresh Token checked.
- **Sign-in redirect URIs**: `http://localhost:8000/auth/callback`.
- **Client authentication**: Client secret.
- **PKCE**: required.
- **Client Credentials**: one Active client secret listed.

## Step 2 — Register the Service App (agent)

This is the confidential client that authenticates the OBO exchange call. No user signs into this app — it's machine-to-machine. The agent running on AgentCore Runtime uses this app's credentials (stored on the AgentCore credential provider) to present itself to Okta when swapping the user's token for a downstream-scoped one.

1. Okta admin → **Applications → Applications → Create App Integration**.
2. Select **API Services**. Click **Next**.
3. Name: `AgentCore OBO UC1 Service (agent)`.
4. Click **Save**.
5. Configure grant type and proof-of-possession. On the **General** tab, click **Edit** at the top of **General Settings**, then:

   1. Find **Grant types** and click **Show advanced settings** (usually a small link under the default checkboxes).
   2. Check **Token Exchange**. Leave other defaults alone.
   3. Find **Proof of possession** (sometimes labeled **Require Demonstrating Proof of Possession (DPoP) header in token requests**). Set it to **Not required** / unchecked.
   4. Click **Save**.

   > **Why DPoP has to be off.** DPoP requires the caller to include a freshly-signed proof JWT on every token request. AgentCore Identity authenticates to Okta with `client_secret_basic` and does not mint DPoP proofs. Leaving DPoP enabled causes Okta to reject the exchange with `invalid_dpop_proof: The DPoP proof JWT header is missing.` Newer Okta Integrator tenants default DPoP **on** for API Services apps, so explicitly turning it off is not optional.
   >
   > **Why Token Exchange has to be on.** Without it Okta refuses `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` from this client and you get `unauthorized_client`.

6. Get the client secret (reuse the existing one if Okta auto-generated it; otherwise create one). Still on the **General** tab:

   1. Scroll to the **Client Credentials** section. Click **Edit** if it isn't already open.
   2. Look at the **CLIENT SECRETS** table:
      - **If a row with Status = Active already exists** (common on newer Okta Integrator tenants — Okta auto-generates a secret at app creation time for API Services apps), reveal it and copy the value. You're done.
      - **If the table is empty** with the placeholder *"A new client secret is generated after you click Save"*, click **Save** at the bottom of the panel. The **Generate new secret** button is disabled on fresh apps; for your first secret just **Save**. Reveal and copy the value.
   3. Store it once in `.env` — don't leave the value pinned in your clipboard.

7. Copy:
   - **Client ID** → `AGENT_CLIENT_ID` (paste into `.env` now).
   - **Client secret** value → `AGENT_CLIENT_SECRET` (paste into `.env` now).

### Sanity check

Open the Service App → **General** tab. You should see:

- **App type**: API Services.
- **Grant types**: Token Exchange checked (other defaults left alone).
- **Proof of possession**: Not required.
- **Client Credentials**: one Active client secret listed.

## Step 3 — Configure the authorization server, custom scope, and access policies

You've already picked the auth server in Step 0.5 (`OKTA_AUTH_SERVER_ID`) and verified its discovery URL works. Now define a custom downstream scope for OBO, then add two access policies.

1. Okta admin → **Security → API → Authorization Servers**.
2. Click the name of the server you chose in Step 0.5.
3. Confirm the **Audience** field matches what you expect (typically `api://default`). Copy it → `OKTA_AUDIENCE` in `.env`.

### 3a. Custom scope for the downstream API

The agent's OBO exchange asks for a **custom scope** (not an OIDC scope). Okta refuses two mutually exclusive things on Token Exchange:

- **`openid`** is not allowed on Token Exchange requests (`outcome.reason=openid_not_allowed_token_exchange`).
- Other **OIDC scopes** (`profile`, `email`, `address`, `phone`) may only appear **alongside `openid`** (`outcome.reason=openid_scope_required`).

A custom scope sidesteps both rules and is the realistic production pattern anyway — the downstream token is meant for your own resource server, not for Okta's userinfo.

1. On the auth server, go to the **Scopes** tab. Click **Add Scope**.
2. **Name**: `agent.downstream`.
3. **Display phrase**: `Downstream API access on behalf of the user`.
4. **Description**: `Allows the agent to call downstream APIs on the user's behalf`.
5. Check **Include in public metadata**. Check **Set as a default scope**.
6. Click **Create**.

### 3b. Access policy for the Web App (upstream — user sign-in)

This policy tells Okta: "The Web App is allowed to mint user tokens via the Authorization Code grant."

1. **Access Policies** tab. Click **Add New Access Policy**.
2. Name: `Access agent (upstream)`. Description: `Policy allowing the Web App to get user tokens for the agent`.
3. **Assign to**: select **The following clients** and pick the **Web App** you created in Step 1 (named `AgentCore OBO UC1 Web (frontend)`).
4. Click **Create Policy**.
5. On the new policy, click **Add Rule**:
   - Name: `Web app to user tokens`.
   - **Grant type**: check **Authorization Code**.
   - **User is**: `Any user assigned the app` (or tighten per your tenant's needs).
   - **Scopes requested**: select `The following scopes` → check `openid`, `profile`, `email`. Alternatively pick `Any scopes` for this demo.
   - **Access token lifetime**: leave the default (1 hour).
6. Click **Create Rule**.
7. **Activate the policy.** On the policy card's top-right corner there's a status dropdown that Okta creates in the **Inactive** state. Click it and switch to **Active**. An inactive policy is silently skipped during evaluation — you'll see "Policy evaluation failed" at sign-in time and chase a rule bug that isn't there.

### 3c. Access policy for the Service App (OBO)

This policy tells Okta: "The Service App is allowed to exchange user tokens via Token Exchange and receive the custom downstream scope."

1. Same **Access Policies** tab. Click **Add New Access Policy**.
2. Name: `Access agent (OBO)`. Description: `Policy allowing the Service App to perform OBO token exchange for the downstream scope`.
3. **Assign to**: select **The following clients** and pick the **Service App** from Step 2.
4. Click **Create Policy**.
5. Click **Add Rule**:
   - Name: `Agent OBO exchange`.
   - **Grant type**: check **Token Exchange**.
   - **Scopes requested**: select `The following scopes` → check `agent.downstream` (the custom scope from Step 3a). Do NOT add `openid`/`profile`/`email` to this rule — mixing OIDC and custom scopes here is unnecessary and makes the UI harder to navigate.
6. Click **Create Rule**.
7. **Activate the policy.** Same as 3b.7 — the status dropdown on the policy card starts at **Inactive**. Switch it to **Active**.

### Sanity check

On the auth server:

- **Scopes** tab: `agent.downstream` is listed, with **Include in public metadata** and **Set as a default scope** both checked.
- **Access Policies** tab: two policies, both **Active**:
  - `Access agent (upstream)` assigned to your Web App with an Authorization Code rule for `openid profile email`.
  - `Access agent (OBO)` assigned to your Service App with a Token Exchange rule for `agent.downstream`.

## Values you should have in `.env`

| Env var | Value |
|---|---|
| `OKTA_DOMAIN` | app-facing host (no `-admin`) |
| `OKTA_AUTH_SERVER_ID` | last path segment of the Issuer URI |
| `OKTA_AUDIENCE` | `api://default` or the configured audience |
| `FRONTEND_CLIENT_ID` | Web App client ID |
| `FRONTEND_CLIENT_SECRET` | Web App client secret |
| `AGENT_CLIENT_ID` | Service App client ID |
| `AGENT_CLIENT_SECRET` | Service App client secret |
| `UPSTREAM_SCOPE` | `openid profile email` |
| `DOWNSTREAM_SCOPE` | `openid profile email` |
| `FRONTEND_REDIRECT_URI` | `http://localhost:8000/auth/callback` |

## Troubleshooting

### `AADSTS`-prefixed errors

You're looking at Entra errors, not Okta. Confirm the credential provider's `discoveryUrl` is Okta's, not Microsoft's.

### `error_description: The 'audience' claim is required`

The SDK parameter name is `audiences` (plural, list). If you're hand-writing a call, make sure you're using the list form, not a bare string.

### `invalid_dpop_proof: The DPoP proof JWT header is missing`

DPoP is still enabled on the Service App. Okta admin → your Service App → **General** tab → **General Settings → Edit** → set **Proof of possession** to **Not required**. Save. This is step 2.5.3 above — newer Okta tenants default DPoP **on** for API Services apps, so if you missed flipping it, you'll hit this at runtime.

### `unauthorized_client` on the exchange

Either the Service App doesn't have the Token Exchange grant (fix: Step 2.5), or the OBO access policy doesn't apply to it (fix: Step 3b — confirm the policy is assigned to the Service App and its rule has Token Exchange checked).

### `outcome.reason = openid_not_allowed_token_exchange`

Seen in Okta's System Log on a `app.oauth2.as.token.grant` event. Okta does not allow the `openid` scope on Token Exchange *requests* — it's reserved for the initial user sign-in.

### `outcome.reason = openid_scope_required`

Seen on the same event type. Okta rejects OIDC scopes (`profile`, `email`, `address`, `phone`) unless `openid` is also present. Combined with the rule above, this means **OIDC scopes as a group are not usable on Token Exchange** — you can't have `openid` with them, and you can't have them without `openid`.

**Fix: use a custom non-OIDC scope on the OBO exchange.** `DOWNSTREAM_SCOPE` in `.env` should point at a custom scope you defined on the auth server (Step 3a). The example ships with `agent.downstream`. `UPSTREAM_SCOPE` stays as `openid profile email` — that's the user sign-in leg, not a token exchange, and OIDC rules don't apply.

If you need the downstream token to carry user identity info (for calling Okta's `/v1/userinfo` or similar), use the **inbound** user token for that — it already has `openid` from sign-in. The agent in this example demonstrates both: OBO mints a custom-scope downstream token (proof the exchange worked), and the inbound user token is used against `/v1/userinfo` for the profile fields.

### `401 Unauthorized` when the agent is invoked

The Runtime rejected the inbound user JWT. Common causes:

1. The Web App is signing the user in against a **different** auth server than the one configured on the Runtime (`OKTA_AUTH_SERVER_ID` in `agentcore.json`). authlib's discovery URL in `frontend/app.py` and the Runtime's `customJwtAuthorizer.discoveryUrl` must point at the same server.
2. The user token's `aud` doesn't match the `allowedAudience` configured on the Runtime. For Okta that's typically `api://default`; the Runtime rejects if the values differ.
3. The token is expired (default 1 hour).

### Agent reports "OKTA_DOMAIN not set" / LLM narrates an Okta config problem after sign-in

The agent's `get_my_profile` tool reads `OKTA_DOMAIN` and `OKTA_AUTH_SERVER_ID` from its environment at startup to build the `/v1/userinfo` URL. If either is missing inside the deployed container, the tool returns an error and the LLM paraphrases it as something like "Okta integration is not properly configured in this environment."

This means the deployed container is running with an `agentcore.json` that doesn't have those env vars yet — typically because the agent was deployed before the patch script added them, or `.env` was updated and `02_patch_agentcore_json.py` wasn't re-run.

**Fix.** From inside `real-world/$AGENT_RUNTIME_NAME/`:

```bash
python ../deploy/02_patch_agentcore_json.py
grep -A 10 environmentVariables agentcore/agentcore.json
# you should see OKTA_DOMAIN and OKTA_AUTH_SERVER_ID listed
agentcore deploy -y -v
```

No IAM re-grant is needed after this (env var changes don't affect the execution role).

### `400 Bad Request: PKCE code challenge is required by the application`

Shows on Okta's error page right after clicking **Sign in with Okta**. Means the Web App is configured with **Require PKCE** (Step 1.9), but the frontend isn't sending a `code_challenge` on the authorize request.

**Fix:** the bundled `frontend/app.py` sends `code_challenge_method=S256` through authlib's `client_kwargs`. If you're running a modified frontend that dropped this kwarg, add it back:

```python
oauth.register(
    name="okta",
    ...,
    client_kwargs={
        "scope": UPSTREAM_SCOPE,
        "code_challenge_method": "S256",
    },
)
```

Alternatively (not recommended for production): turn off **Require PKCE** on the Web App in Okta. PKCE adds defense in depth against auth-code interception and costs nothing to keep on.

### `Error: Policy evaluation failed for this request, please check the policy configurations.`

Shows on Okta's error page after the user signs in. Okta found the request but the upstream access policy (Step 3a) denied it. Five common reasons, in order of likelihood:

1. **Policy status is Inactive.** On the policy card (Auth Server → Access Policies → `Access agent (upstream)`), the top-right dropdown should read **Active**. An Inactive policy is silently ignored during evaluation — Okta behaves as if it doesn't exist, so even a perfectly-configured rule has no effect. Toggle the dropdown to **Active**. Do the same check on `Access agent (OBO)`.
2. **Policy isn't assigned to the Web App.** On the policy card, **Assigned to clients** should show the Web App's client ID (`$FRONTEND_CLIENT_ID`). If it shows the Service App or nothing, the policy doesn't apply to this request.
3. **Rule scopes don't match what authlib requested.** With `UPSTREAM_SCOPE=openid profile email` in `.env`, the rule must allow all three. If the rule was created with only `openid`, Okta rejects — `profile` and `email` were requested but not granted. Fix: edit the rule → **Scopes requested** → check `openid`, `profile`, `email` (or select "Any scopes" for a demo).
4. **Rule's `User is` condition excludes you.** For a traditional-assignment tenant (Option A in Step 1) use `Any user assigned the app`. For a Federation Broker Mode tenant (Option B) use `Any user`. A narrower "Specific users" or "Specific groups" selection that doesn't include your test user blocks the request.
5. **Rule's Grant type doesn't include Authorization Code.** Step 3a.5 — the rule needs **Authorization Code** checked.

**Fast way to pin down the exact cause.** Open Okta admin → **Reports → System Log**, filter by event type starting with `policy`, and find the event from the time you clicked Sign In. Expand → look at `debugContext.debugData` for `policyName`, `ruleName`, `policyOutcome`, `requestedScopes`, and `reason`. That tells you exactly which policy/rule fired and why it denied.
