# Okta Setup for Use Case 1

This guide walks you through configuring Okta to support the OBO flow. It's long because Okta's model has several distinct objects — two app integrations, one authorization server, two scopes, two access policies. Each one plays a specific role. Before you start clicking, read the overview below so you know what you're building and why.

## Concepts and terminology

If you're new to OAuth / OIDC / Okta, this section defines the terms you'll see in the steps below. Skim it first so the clicks make sense.

- **OAuth 2.0** — a framework for *delegated authorization*. It lets an application act on behalf of a user without seeing the user's password. OAuth does not say anything about who the user is — only what the caller is allowed to do.
- **OIDC (OpenID Connect)** — a thin identity layer on top of OAuth 2.0. It adds a standard way to *identify* the signed-in user (via an ID token and the `/v1/userinfo` endpoint). When Okta asks whether your app uses "OIDC – OpenID Connect" vs "API Services", OIDC means "a user signs in interactively"; API Services means "no user — a machine authenticates as itself".
- **Client** — any application that talks to an authorization server. Your frontend and your agent are both clients. A **confidential client** can safely store a client secret (it runs on a server you control). A **public client** cannot (mobile apps, SPAs) — it uses PKCE instead of a secret.
- **Native App / Web App / Single-Page App** — Okta's categories for OIDC clients. Native App = mobile or desktop *or a local development tool like this script* — can optionally carry a client secret and MUST use PKCE. Web App = server-side — confidential, secret required. SPA = in-browser JavaScript — public, PKCE only. For `local/` we pick **Native App** because this Python script runs on a laptop, not a long-lived server.
- **API Services (app type)** — Okta's label for machine-to-machine clients. No user signs in. The Service App is this type because the agent authenticates as itself when calling Okta's `/v1/token` for the exchange.
- **Authorization server** — the Okta component that mints and validates access tokens. A single tenant can host multiple; the one you pick becomes the issuer of every token in this flow. It also owns the scopes, access policies, and audience values.
- **Audience (`aud`)** — who the token is *for*. In Okta's model, `aud` is the auth server's audience (typically `api://default`), not a specific app. A downstream API validates `aud` to confirm "this token was intended for me".
- **Client ID (`cid`)** — who the token was *issued to*. On the inbound user token, `cid` is the Native App. On the OBO'd token, `cid` rotates to the Service App — that's how Okta records the actor.
- **Subject (`sub`)** — who the token is *about*. The end user's login (e.g. `alice@example.com`). This claim stays the same across the OBO exchange; preserving it is the point of OBO.
- **Scope (`scp`)** — what the caller is allowed to do. `openid` is the standard OIDC baseline. Custom scopes (like `oboe2e.apiC.read` in this example) gate access to your own downstream APIs.
- **Authorization Code grant** — the OAuth flow for interactive user sign-in. The user signs into Okta, Okta redirects back with a short-lived code, and the client exchanges that code for an access token. Used by the Native App.
- **PKCE (Proof Key for Code Exchange)** — an add-on to the Authorization Code flow that prevents the code from being usable if intercepted. Required here.
- **Client secret** — the confidential client's password. Used by Okta to authenticate the client when exchanging the auth code for a token (Native App) and when doing the OBO exchange (Service App). Never leaves the server.
- **Token Exchange grant (RFC 8693)** — the OAuth flow Okta uses for OBO. The Service App presents the user's token as a "subject token" and asks Okta for a new token whose `cid` is the Service App but whose `sub` is still the user.
- **DPoP (Demonstrating Proof of Possession)** — a mechanism that cryptographically binds a token to a specific client-held key. Newer Okta tenants default to requiring DPoP on API Services apps. AgentCore Identity does not sign DPoP proofs, so **DPoP must be disabled** on the Service App for this example to work.
- **Access policy** — Okta's rule system on an authorization server. Two policies are needed here:
  - One allowing the **Native App** to use the Authorization Code grant with `openid`.
  - One allowing the **Service App** to use the Token Exchange grant for `oboe2e.apiC.read`.
- **Issuer (`iss`)** — URL of the authorization server that minted the token. Consumers use this to fetch JWKS and verify signatures. Stays the same across the OBO exchange.
- **Discovery URL** — OIDC's standard endpoint that lists everything an API needs to validate tokens (signing keys, issuer, audience, supported grants). Pattern: `https://<domain>/oauth2/<auth-server-id>/.well-known/openid-configuration`. You'll verify this URL returns JSON in Step 0.5.

## Architecture at a glance

The flow you'll run looks like this:

```
👤 User
  │
  │ 1. signs in via browser
  ↓
🖥️  Native App ──────────────────────────► 🏛  Okta Authorization Server
  (OIDC client that                          (mints + validates all tokens)
   the frontend uses)                         │
  │                                           │ audience: api://default
  │ 2. receives user access token             │ custom scope: oboe2e.apiC.read
  │    aud=api://default                      │ policy: "Native App may use
  │    cid=<Native App>                       │          Authorization Code
  │    scp=[openid]                           │          grant"
  │    sub=alice@example.com                  │
  ↓                                           │
🤖 Agent (middle tier) ◄────────────────────┤
  │                                           │
  │ 3. uses Service App credentials to do     │
  │    RFC 8693 token exchange                │
  │    subject_token = user token above       │ policy: "Service App may
  │                                           │          use Token Exchange
  │ 4. receives downstream token              │          for scope
  │    aud=api://default (same)              │          oboe2e.apiC.read"
  │    cid=<Service App>  (rotated!)          │
  │    scp=[oboe2e.apiC.read]  (rotated!)    │
  │    sub=alice@example.com  (preserved!)   │
  ↓
🎯 Downstream API (API2)
   (validates token and enforces
    the oboe2e.apiC.read scope)
```

### Which Okta objects you're about to create, and why

| Object | Okta console path | Role in the OBO flow | Why separate? |
|---|---|---|---|
| **Native App** (Step 1) | Applications → Applications | The OIDC client the frontend uses to sign the user in. Produces the *upstream* user access token. | OIDC requires a client with redirect URIs + PKCE. This is that client. |
| **Service App** (Step 2) | Applications → Applications | The confidential client the agent uses to perform the OBO exchange. Its credentials authenticate the exchange call. Never seen by the end user. | Token Exchange requires a confidential client with the Token Exchange grant — something the Native App cannot do. |
| **Authorization Server** (Step 3) | Security → API → Authorization Servers | The issuer / validator. Mints both tokens, hosts JWKS, defines your audience, holds the access policies. | The auth server is where Okta enforces "who can request what grant type for which scope". Centralizing this in one object is what makes the whole flow work. |
| **Custom scope `oboe2e.apiC.read`** (Step 3a) | Auth Server → Scopes | The permission the downstream token carries. API2 checks for this scope before serving a request. | OBO is only meaningful when the new token has a *different, narrower* scope than the inbound one. This is that scope. |
| **Access policy: "Access API1 (upstream)"** (Step 3b) | Auth Server → Access Policies | Says: "The Native App may request user tokens via Authorization Code." | Without this, Okta refuses to mint the upstream user token. |
| **Access policy: "Access API2 (OBO)"** (Step 3c) | Auth Server → Access Policies | Says: "The Service App may exchange user tokens for `oboe2e.apiC.read` via Token Exchange." | Without this, Okta refuses the OBO exchange call. |

Everything below is just filling in these six objects. If at any point you wonder *"why am I configuring this?"*, come back to this table.

## Prerequisites

- An Okta org (free Integrator plan works).
- At least one user account in your org for testing.

## Step 0 — Bootstrap your local `.env`

Before touching the Okta console, create the `.env` file you'll fill in as you go. This way you can paste each value into the right env var the moment you see it, instead of holding a pile of IDs and secrets in your head.

```bash
cd obo-training/examples/01-agent-to-downstream/okta/local
cp config.example.env .env
```

Open `.env` in your editor and keep it alongside the Okta admin console as you work through the steps below. Every time a step says "Copy X → `SOME_VAR`", paste the value into `SOME_VAR=` in `.env` right away.

> **Tip:** `.env` is gitignored (see `.gitignore` in this folder) so your secrets won't accidentally land in version control.

## Step 0.5 — Find your Okta domain and authorization server ID

Two values in `.env` — `OKTA_DOMAIN` and `OKTA_AUTH_SERVER_ID` — describe *which Okta tenant* and *which authorization server* everything else will use. You need to set these before creating any apps, because the apps you create in the next steps will live inside this tenant and issue tokens from this auth server.

### `OKTA_DOMAIN`

This is the short hostname of your Okta tenant — the part **before** `/admin/` in the URL of your Okta admin console.

1. Open the Okta admin console in a browser.
2. Look at the URL bar. It will be one of:
   - `https://<something>-admin.okta.com/admin/…` (classic Okta URL)
   - `https://<something>.okta.com/admin/…`
3. Take the host, remove `-admin` if present, and keep everything before the first `/`. Examples:
   - Admin console URL `https://integrator-1234567-admin.okta.com/admin/dashboard` → `OKTA_DOMAIN=integrator-1234567.okta.com`
   - Admin console URL `https://dev-987654.okta.com/admin/dashboard` → `OKTA_DOMAIN=dev-987654.okta.com`
   - Custom domain URL `https://login.acme.com/admin/…` → `OKTA_DOMAIN=login.acme.com`

> **Important:** use the non-admin host. OIDC discovery is only served from the app-facing host, not the `-admin` one. Using the admin host causes `Invalid Discovery URL` errors.

Paste your value into `.env`:

```
OKTA_DOMAIN=<your-tenant>.okta.com
```

### `OKTA_AUTH_SERVER_ID`

An Okta tenant can host multiple custom authorization servers. Each one is a distinct issuer — its own signing keys, its own audience, its own scopes, its own access policies. For this example you'll configure scopes and policies on one specific server; `OKTA_AUTH_SERVER_ID` is that server's short identifier.

1. In the Okta admin console, go to **Security → API → Authorization Servers**.
2. You'll see a table of servers. Each has a **Name**, **Audience**, **Issuer URI**, and other columns.
3. Look at the **Issuer URI** column and find the last path segment of the issuer URI. That segment is the auth server ID:
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/default` → `OKTA_AUTH_SERVER_ID=default`
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/ausXXXXXXXXXXXX` → `OKTA_AUTH_SERVER_ID=ausXXXXXXXXXXXX`

> **The table is empty?** Newer Okta Integrator tenants ship without a `default` authorization server. You have two options:
>
> - **Create a server named `default`.** Click **Add Authorization Server**. Name: `default`. Audience: `api://default`. Description: whatever. **Save.** Then use `OKTA_AUTH_SERVER_ID=default`. Do not confuse the *Name* column with the auth server ID — the ID is the last path segment of the **Issuer URI**, which for `default`-named servers will be the string `default`.
> - **Use any existing custom server.** Pick one from the table, copy the last segment of its Issuer URI (`ausXXXXXXXXXXXX`), and use that as `OKTA_AUTH_SERVER_ID`. Its audience becomes your `OKTA_AUDIENCE` in Step 3.

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

Don't move on to Step 1 until this URL returns a JSON document.

## Step 1 — Create the Native App (plays the user-facing OIDC client)

1. Go to **Applications → Applications → Create App Integration**.
2. Select **OIDC – OpenID Connect**, then **Native Application**.
3. Name: `AgentCore OBO UC1 Native`.
4. **Grant type**: enable **Authorization Code** (default) and **Refresh Token**.
5. Sign-in redirect URIs: you'll add one in a later step after creating the credential provider. For now leave the default or use a placeholder.
6. Controlled access: `Allow everyone in your organization to access`.
   > **Production tip:** this is the coarsest setting and is fine for a single-tenant demo. In production, prefer **"Limit access to selected groups"** and scope the app to a specific Okta group (e.g., `agentcore-obo-users`). That way only people in that group can sign in — you can also use the same group to drive downstream authorization via group claims.
7. **Save**.
8. Configure client authentication and PKCE. On the app's **General** tab:

   1. Find the **Client Credentials** section and click **Edit**.
   2. For **Client Authentication**, select **Client secret**.
   3. For **PKCE**, check **Require PKCE as additional verification**.
   4. Click **Save**.

   > Both settings are required. Client secret gives AgentCore something to authenticate with; PKCE protects the 3LO code exchange. Missing either will cause the credential provider's 3LO flow to fail later.
9. Generate a client secret. After saving Step 8, the **Client Credentials** section now shows a **CLIENT SECRETS** subsection. If you don't see it, click **Edit** on Client Credentials again.

   1. In **CLIENT SECRETS**, a placeholder row shows the message *"A new client secret is generated after you click Save"*. The **Generate new secret** button is disabled at this point — you don't need it for your first secret.
   2. Click **Save** at the bottom of the panel. Okta generates the secret on save.
   3. The secret now appears in the table with a Creation date, the Secret value (shown with a reveal button), and Status = **Active**.
   4. Reveal the value and copy it now — Okta will let you view it later, but treat it like any other credential: store it once in `.env` and don't leave it pinned in your clipboard.

   > **When is "Generate new secret" enabled?** Once you already have at least one active secret on the app. That's Okta's zero-downtime rotation pattern — generate a second secret, roll traffic over, then delete the old one. For this demo you only need the first one.
10. Copy:
    - **Client ID** → `NATIVE_APP_CLIENT_ID` (paste into `.env` now).
    - **Client secret** (the value, not ID) → `NATIVE_APP_CLIENT_SECRET` (paste into `.env` now).

## Step 2 — Create the Service App (plays the middle-tier / agent)

1. Go to **Applications → Applications → Create App Integration**.
2. Select **API Services**.
3. Name: `AgentCore OBO UC1 Service (API1)`.
4. **Save**.
5. On the General tab, click **Edit** at the top of **General Settings** and configure both grant type and proof-of-possession in this single edit:

   1. Find **Grant types** and click **Show advanced settings** (it's usually a small link/button below the default checkboxes).
   2. Check **Token Exchange**. Leave the other default grants as they are.
   3. Find **Proof of possession** (sometimes labeled **Require Demonstrating Proof of Possession (DPoP) header in token requests**). Set it to **Not required** / unchecked.
   4. Click **Save**.

   > **Why DPoP has to be off.** DPoP requires the caller to include a freshly-signed proof JWT on every token request. AgentCore Identity's OBO flow uses plain `client_secret_basic` auth and does not mint DPoP proofs. Leaving DPoP enabled causes Okta to reject the exchange with `invalid_dpop_proof: The DPoP proof JWT header is missing.` On newer Okta tenants DPoP is sometimes **on** by default for API Services apps, so explicitly turning it off is not optional.
   >
   > **Why Token Exchange has to be on.** Without it Okta refuses `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` on this client and you get `unauthorized_client`.
6. Generate a client secret for this app. The UI pattern is identical to Step 1.9:

   1. In the **Client Credentials** section (still on the General tab), click **Edit** if it's not already open.
   2. In **CLIENT SECRETS**, click **Save** at the bottom of the panel. (The **Generate new secret** button is disabled until you already have a secret; for your first secret, just **Save**.)
   3. Reveal and copy the value — store it once in `.env` and don't leave it pinned in your clipboard.
7. Copy:
   - **Client ID** → `SERVICE_APP_CLIENT_ID` (paste into `.env` now).
   - **Client secret** value → `SERVICE_APP_CLIENT_SECRET` (paste into `.env` now).

## Step 3 — Configure the Authorization Server

You already picked the auth server in Step 0.5 (`OKTA_AUTH_SERVER_ID`) and verified its discovery URL works. Now add the pieces the OBO flow needs to it: a downstream scope (3a), an upstream access policy (3b), and an OBO access policy (3c).

1. Go to **Security → API → Authorization Servers**.
2. Click the name of the server you chose in Step 0.5 (or click the edit/pencil icon next to it) to open it.
3. Confirm the **Audience** field matches what you expect (typically `api://default` for a `default` server, or whatever custom audience you set when you created it). Copy it → `OKTA_AUDIENCE` in `.env`.

### 3a. Custom scope for the downstream API (API2)

1. In the auth server, go to **Scopes → Add Scope**.
2. Name: `oboe2e.apiC.read`
3. Display phrase: `Read API C resources`
4. Description: `Allows the caller to read API C resources on the user's behalf`.
5. **Check "Include in public metadata"** and **"Set as a default scope"** (required — Okta's OBO flow needs default scopes or you'll hit a ClientCredentials parse error).
6. **Create**.

### 3b. Access policy for the native app

1. **Access Policies → Add New Access Policy**.
2. Name: `Access API1 (upstream)`. Description: `Policy for the native app to get user tokens`.
3. Assign to: `The following clients` → select the Native App you created in Step 1.
4. **Create Policy**.
5. On the new policy, **Add Rule**:
   - Name: `Mobile app to API1`.
   - Grant type: check **Authorization Code**.
   - User is: `Any user assigned the app`.
   - Scopes requested: `Any scopes` is fine; or select `openid`.
   - Access token lifetime: default (1 hour).
6. **Create Rule**.

### 3c. Access policy for the service app (OBO)

1. **Access Policies → Add New Access Policy**.
2. Name: `Access API2 (OBO)`. Description: `Policy allowing API1 to exchange for API2`.
3. Assign to: `The following clients` → select the Service App from Step 2.
4. **Create Policy**.
5. **Add Rule**:
   - Name: `API1 to API2`.
   - Grant type: check **Token Exchange**.
   - Scopes requested: `The following scopes` → select `oboe2e.apiC.read`.
6. **Create Rule**.

## Step 4 — Assign the native app to your user

Open the Native App → **Assignments** tab. What you see depends on how your Okta org is configured:

### Option A — Traditional assignment (most dev orgs, customer orgs)

You'll see an **Assign → Assign to People** button.

1. Click **Assign → Assign to People**.
2. Pick your test user and click **Assign**.
3. **Save**.

### Option B — Implicit assignment (newer Okta Integrator orgs with Federation Broker Mode)

You'll see a message "**This app is implicitly assigned to users — Immediate access is currently enabled with Federation Broker Mode for this app. As a result, user access is determined by app sign-on policies.**"

In this mode you don't assign users or groups one-by-one. Instead, every user in your org can sign into the app by default, and access is gated by the app's sign-on policy. For this demo the default sign-on policy (allow all, no extra MFA) is fine — no action needed.

If you want to tighten it:

1. Click **Configure Sign On Policy** (or go to the **Sign On** tab → **Sign On Policy**).
2. Add a rule that requires the user to be in a specific group, or requires MFA, etc.
3. **Save**.

> **Which mode am I in?** If the Assignments tab shows an **Assign** button, you're in Option A. If it shows the "implicitly assigned" message with a **Configure Sign On Policy** button, you're in Option B. Both work for this demo.
>
> **Production note:** Federation Broker Mode (Option B) pairs well with the group-scoped access setting from Step 1.6 — the app's access is narrowed by the sign-on policy rather than by per-user assignments. For a locked-down production deployment you'd typically use one or the other, not rely on the defaults.

## Step 5 — After creating AgentCore providers (step 01)

When you run `01_create_providers.py`, AgentCore will print a callback URL for the Native App credential provider. Okta needs this URL added to the Native App's allowed redirect URIs, otherwise the 3LO sign-in will fail with `redirect_uri_mismatch`.

1. Open the **Native App → General** tab.
2. Scroll to the **General Settings** section (it's the first editable section on the General tab, containing name, logo, grant types, and the redirect URIs). Click **Edit** at its top-right.
3. Under **Sign-in redirect URIs**, click **Add URI** and paste the AgentCore-provided callback URL exactly as printed.
4. Click **Save** at the bottom of the section.

> **Finding the callback URL later.** The URL is in the AWS console under **AgentCore Identity → Credential providers → *<your client provider name>*** under `oauthDiscovery` / return URL. You can also re-run `01_create_providers.py` — it's idempotent and re-prints the URL.

## Values you should now have for `.env`

| Env var | Value |
|---|---|
| `OKTA_DOMAIN` | e.g., `integrator-1234567.okta.com` |
| `OKTA_AUTH_SERVER_ID` | `default` (or your custom auth server ID) |
| `OKTA_AUDIENCE` | `api://default` |
| `NATIVE_APP_CLIENT_ID` | Native app Client ID |
| `NATIVE_APP_CLIENT_SECRET` | Native app client secret value |
| `SERVICE_APP_CLIENT_ID` | Service app Client ID |
| `SERVICE_APP_CLIENT_SECRET` | Service app client secret value |
| `UPSTREAM_SCOPE` | `openid` |
| `DOWNSTREAM_SCOPE` | `oboe2e.apiC.read` |
| `AWS_REGION` | e.g., `us-west-2` |
| `WORKLOAD_NAME` | `obo-usecase1-okta` |
| `CLIENT_PROVIDER_NAME` | `obo-uc1-okta-client` |
| `ACTOR_PROVIDER_NAME` | `obo-uc1-okta-actor` |
| `USER_ALIAS` | Any short id for the test user session |

## Troubleshooting

### `ValidationException: Token exchange failed with HTTP status 400`

This comes from Okta rejecting the OBO exchange. Check in order:

1. **Missing exchange-time parameters.** Okta requires BOTH `subject_token_type` and an audience on every exchange call. In AgentCore Identity that means:
   ```python
   ac.get_resource_oauth2_token(
       ...,
       oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
       customParameters={
           "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
       },
       audiences=["api://default"],   # or your custom auth server's audience
   )
   ```
   These are NOT configured on the credential provider — they must be passed at exchange time. Note the SDK parameter is `audiences` (plural, list) even though Okta receives it as a single `audience` under the hood. Forgetting either one is the #1 cause of 400s.

2. **Service app is missing the Token Exchange grant.** In the Okta admin console → your service app → **General → Grant types → Advanced**, confirm **Token Exchange** is checked and **DPoP** is unchecked.

3. **Access policy rule doesn't cover the exchange.** In **Security → API → Authorization Servers → your auth server → Access Policies**, confirm that:
   - A policy exists targeting the service app.
   - It has a rule with grant type **Token Exchange** that grants the downstream scope (`oboe2e.apiC.read`).

4. **Downstream scope not marked default / public.** When creating the custom scope on your auth server, you must check **"Include in public metadata"** and **"Set as a default scope"**. Without these, Okta's OBO flow fails with a `ClientCredentials` parse error on the exchange.

5. **Inbound token `aud` doesn't match the auth server audience.** Run:
   ```bash
   python -c "
   import json, base64
   t = json.load(open('.user-jwt-cache.json'))['token']
   p = t.split('.')[1]
   print(json.loads(base64.urlsafe_b64decode(p + '='*(-len(p)%4))))
   "
   ```
   Verify `aud` equals your `OKTA_AUDIENCE`. If it's something else, the native app is probably requesting a token against a different auth server.

6. **Client secret expired or rotated.** Okta secrets don't auto-expire by default but admins sometimes rotate them. Regenerate the secret on the affected app and re-run `01_create_providers.py` to push the new secret to the credential provider.

### `ValidationException: Invalid Discovery URL`

Surfaces when AgentCore Identity can't reach or parse the OIDC discovery document for your Okta authorization server. This happens before any token exchange — it's a config-time check.

**Go back to [Step 0.5](#step-05--find-your-okta-domain-and-authorization-server-id).** It has the full walkthrough for finding `OKTA_DOMAIN` and `OKTA_AUTH_SERVER_ID` and verifying the discovery URL in a browser. The preflight in `01_create_providers.py` catches this at create time with the same guidance.

A quick checklist in case you got here without reading Step 0.5:

1. `OKTA_DOMAIN` is the non-admin host (drop `-admin` from the admin console URL).
2. `OKTA_AUTH_SERVER_ID` is the last path segment of the Issuer URI on the **Security → API → Authorization Servers** page — not the Name column.
3. The discovery URL below returns a JSON document in a browser:
   ```
   https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration
   ```

After fixing `.env`, re-run `python 01_create_providers.py`.

### `AADSTS`-style errors

If you see errors with an `AADSTS` prefix, you're looking at an Entra error, not an Okta one — confirm your credential provider's `discoveryUrl` points at your Okta tenant (`https://<domain>/oauth2/<auth-server-id>/.well-known/openid-configuration`), not Microsoft.

### `invalid_dpop_proof: The DPoP proof JWT header is missing`

Okta's Service App has **Require Demonstrating Proof of Possession (DPoP)** enabled. AgentCore Identity does not use DPoP, so Okta rejects every exchange.

**Fix:** Okta admin → **Applications → your Service App → General** tab → **General Settings → Edit** → set **Proof of possession** to **Not required** (or uncheck the DPoP requirement). Save.

This is step 2.5.3 in Step 2. Newer Okta tenants enable DPoP by default on API Services apps, so it's easy to miss — if you arrive here at runtime, the Service App was probably created before we tightened the step.
