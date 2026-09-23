# Okta Setup — Real-World Use Case 2

Three Okta app registrations are needed:

1. **Frontend Web App** — user-facing OIDC client the browser signs into.
2. **Agent API Services** — middle-tier client for OBO #1 (agent → Gateway).
3. **Gateway API Services** — middle-tier client for OBO #2 (Gateway → mock downstream).

Plus three custom scopes and three access policies on your Okta authorization server (default or custom).

> **Two ways to do this setup:**
> - **Automated path** (recommended): set `OKTA_DOMAIN` + `OKTA_ADMIN_TOKEN` in `.env`, then run `python deploy/00_create_okta_apps.py`. ~30 seconds. See [Automated path](#automated-path).
> - **Manual path**: click through the Okta admin console. ~15 minutes. See [Manual path](#manual-path) starting at Step 0.

## Architecture recap

```
👤 User ──signin──▶ FrontendApp (Web App)
                       │ T_user  (aud=api://default, cid=Frontend, scp=[...agent.access])
                       ▼
                    AgentApp (API Services)   —— OAuth client for OBO #1
                       │ T_gateway  (aud=api://default, cid=Agent, scp=[gateway.access])
                       ▼
                    GatewayApp (API Services) —— OAuth client for OBO #2
                       │ T_downstream  (aud=api://default, cid=Gateway, scp=[downstream.access])
                       ▼
                    httpbin.org/anything (mock downstream)
```

Custom scopes (on the authorization server):

- `agent.access`      — what Frontend requests at sign-in
- `gateway.access`    — what Agent requests via OBO #1
- `downstream.access` — what Gateway requests via OBO #2

Custom scopes are required because Okta refuses OIDC scopes (`openid`, `profile`, `email`) on the Token Exchange grant.

## Prerequisites

- An Okta tenant where you can register apps and edit an authorization server's scopes + access policies.
- A test user in your tenant.
- For the automated path: an **Okta API token** (Super Admin or Org Admin), created at Okta admin → Security → API → Tokens → Create Token.

---

## Automated path

Fastest way. `python deploy/00_create_okta_apps.py` uses the Okta Admin API to do everything below in ~30 seconds.

### Step A1 — Bootstrap `.env`

```bash
cd obo-training/examples/02-agent-via-gateway/okta/real-world
cp config.example.env .env
```

Set at least these two values in `.env` before running the script:

```
OKTA_DOMAIN=integrator-1234567.okta.com    # your tenant, NOT the -admin host
OKTA_ADMIN_TOKEN=00...your-api-token...    # from Okta admin -> Security -> API -> Tokens
```

Leave the client ID/secret placeholders alone — the script will fill them in.

### Step A2 — Verify the auth server

By default the script uses `OKTA_AUTH_SERVER_ID=default`. Confirm your tenant has a default authorization server by opening this URL in a browser (with `<domain>` and `<auth-server-id>` from `.env`):

```
https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration
```

You should see a JSON document with `issuer`, `jwks_uri`, `token_endpoint`, etc. If you get:
- A sign-in page → you're using the admin host; drop the `-admin`.
- A 404 → the auth server ID is wrong, or the server doesn't exist. Some newer Integrator tenants ship without a default; create one via Okta admin → Security → API → Authorization Servers → Add Authorization Server with name `default` and audience `api://default`.

### Step A3 — Run the automation

```bash
python deploy/00_create_okta_apps.py
```

This single command performs every action documented in the manual path below:

1. Verifies the auth server exists and captures its audience.
2. Creates three custom scopes on the auth server: `agent.access`, `gateway.access`, `downstream.access`.
3. Creates three app registrations:
   - `AgentCore OBO UC2 Frontend` (Web App with Auth Code + PKCE + client secret).
   - `AgentCore OBO UC2 Agent` (API Services with Token Exchange grant, DPoP OFF).
   - `AgentCore OBO UC2 Gateway` (API Services with Token Exchange grant, DPoP OFF).
4. Mints a client secret for each app (only if the corresponding `.env` value is missing / `replace-me`).
5. Creates three access policies + rules on the auth server, all Active:
   - Frontend → `authorization_code` for `openid profile email offline_access agent.access` (refresh tokens are issued automatically because `offline_access` is in the scope list).
   - Agent    → `token-exchange` for `gateway.access`.
   - Gateway  → `token-exchange` for `downstream.access`.
6. Writes every value to `.env`.

Re-runs are safe: apps, scopes, and policies are looked up by name and only created when missing. Pass `--rotate-secrets` to force fresh client secrets on all three apps.

### Step A3.5 — Verify PKCE + DPoP settings in the Okta console

The two app-level toggles below are applied via a follow-up `PUT /apps/{id}` after each app is created. Okta's admin API validator is inconsistent about accepting these fields on some tenants (E0000003 with an empty `errorCauses`). The automation prints a warning and moves on if that happens; you flip the setting once in the console and you're good.

**Check 1 — PKCE required on the Frontend Web App.** The demo's `frontend/app.py` sends `code_challenge_method=S256` unconditionally, so PKCE technically works without this flag flipped. But leaving it off means the app *accepts* auth requests without a PKCE challenge — a step backwards on defense-in-depth. Turn it on:

1. Okta admin → Applications → **AgentCore OBO UC2 Frontend** → **General** tab.
2. Scroll to **Client Credentials** → click **Edit**.
3. Under **Client Authentication**, confirm **Client secret** is selected.
4. Check **Require PKCE as additional verification**.
5. **Save**.

**Check 2 — DPoP off on both API Services apps.** DPoP-off is **critical for OBO to work** — an ON setting causes runtime errors like `invalid_dpop_proof: The DPoP proof JWT header is missing`. Newer Okta Integrator tenants default DPoP ON on API Services apps, so this may need a manual flip. Do this for **both** `AgentCore OBO UC2 Agent` and `AgentCore OBO UC2 Gateway`:

1. Okta admin → Applications → **AgentCore OBO UC2 Agent** → **General** tab.
2. Under **General Settings**, click **Edit**.
3. Scroll to **Proof of possession** (also labeled *Require Demonstrating Proof of Possession (DPoP) header in token requests*).
4. Set to **Not required** (uncheck the box).
5. **Save**.
6. Repeat for **AgentCore OBO UC2 Gateway**.

**How to tell if the automation already handled it.** Look at the script output during Step 3:

```
✓ Created app: AgentCore OBO UC2 Frontend (client_id=...)
    ✓ Enabled PKCE + set post-logout redirect on AgentCore OBO UC2 Frontend
✓ Created app: AgentCore OBO UC2 Agent (client_id=...)
    ✓ Forced DPoP off on AgentCore OBO UC2 Agent
```

If you saw `⚠ Could not enable PKCE via API` or `⚠ Could not disable DPoP via API` instead, that's your signal to flip the setting manually.

### Step A4 — Confirm Frontend app is assigned to users

The automation attempts to assign the Frontend Web App to Okta's built-in **Everyone** group so any user in your org can sign in. Two ways this can go:

- **Success** (most tenants): script output shows `✓ Assigned AgentCore OBO UC2 Frontend to Everyone group`. Nothing more to do.
- **Warning** (some tenants restrict group assignments via API): script output shows `⚠ Could not assign ... to Everyone via API`. Assign manually:

  1. Okta admin → **Applications** → **AgentCore OBO UC2 Frontend** → **Assignments** tab.
  2. **Assign** → **Assign to People**, pick your test user, click **Assign**, then **Save and Go Back**.
     — or —
     **Assign** → **Assign to Groups**, click **Assign** next to **Everyone**, then **Save and Go Back**.

**How to tell if this step is skipped.** If you try to sign in at `http://localhost:8000` and Okta shows a 400 page with "Your request resulted in an error. User is not assigned to the client application." — that's this. Come back here and complete the assignment.

> **Federation Broker Mode.** A minority of newer Integrator tenants ship in Federation Broker Mode where all users can sign into all apps implicitly, and the group-assign call is a no-op or rejected. Either way is fine — if the sign-in works at Step 13 of the README, you're set.

### Step A5 — Verify

```bash
grep -E '^(OKTA_|FRONTEND_|AGENT_|GATEWAY_|UPSTREAM_|GATEWAY_SCOPE|DOWNSTREAM_)' .env \
  | grep -v _SECRET
```

You should see all the client IDs, scopes, and Okta coordinates populated. The `_SECRET` values are also written but masked here.

You're done with IdP setup. Continue with the [README's Quick Start](./README.md#quick-start) at step 3 (install Python tooling).

### Re-running and tearing down

| Goal | Command |
|---|---|
| Pick up where you left off (re-run is safe) | `python deploy/00_create_okta_apps.py` |
| Force-rotate all three client secrets | `python deploy/00_create_okta_apps.py --rotate-secrets` |
| Delete apps + scopes + policies | `python deploy/00_delete_okta_apps.py --yes` |
| Dry-run the delete (no changes) | `python deploy/00_delete_okta_apps.py --dry-run` |

`00_delete_okta_apps.py` removes everything the create script created and clears the related fields from `.env` when passed `--clean-env`.

---

## Manual path

The walkthrough below produces the same result as the automated path, click by click. Use it if you can't (or don't want to) issue an admin API token, or if you're learning what each step does.

## Step 0 — Bootstrap your local `.env`

```bash
cd obo-training/examples/02-agent-via-gateway/okta/real-world
cp config.example.env .env
```

Open `.env` in your editor and keep it alongside the Okta admin console as you work through the steps below. Every time a step says "Copy X → `SOME_VAR`", paste the value into `SOME_VAR=` in `.env` right away.

## Step 0.5 — Find your Okta domain and authorization server ID

Two values in `.env` — `OKTA_DOMAIN` and `OKTA_AUTH_SERVER_ID` — describe *which Okta tenant* and *which authorization server* everything else will use.

### `OKTA_DOMAIN`

The short hostname of your Okta tenant — the part **before** `/admin/` in the URL of your Okta admin console, WITHOUT the `-admin` suffix.

- Admin URL `https://integrator-1234567-admin.okta.com/admin/dashboard` → `OKTA_DOMAIN=integrator-1234567.okta.com`
- Admin URL `https://dev-987654.okta.com/admin/dashboard` → `OKTA_DOMAIN=dev-987654.okta.com`

> **Important:** use the non-admin host. OIDC discovery is only served from the app-facing host.

Paste your value into `.env`.

### `OKTA_AUTH_SERVER_ID`

1. Okta admin → Security → API → Authorization Servers.
2. Look at the **Issuer URI** column of the server you want to use. The last path segment is the ID:
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/default` → `OKTA_AUTH_SERVER_ID=default`
   - Issuer URI `https://integrator-1234567.okta.com/oauth2/ausXXXXXXXXXX` → `OKTA_AUTH_SERVER_ID=ausXXXXXXXXXX`

> **Table is empty?** Some newer Integrator tenants ship without a default. Create one: Add Authorization Server → Name: `default`, Audience: `api://default`.

Paste into `.env`.

### Verify

Open in a browser (substitute your values):

```
https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration
```

You should see a JSON document with `issuer`, `jwks_uri`, `token_endpoint`. Don't proceed until this URL returns valid JSON.

Also note the auth server's **Audience** field (typically `api://default`) → paste into `.env` as `OKTA_AUDIENCE`.

## Step 1 — Register the Frontend Web App

The user-facing OIDC client the browser signs into.

1. Okta admin → Applications → Applications → **Create App Integration**.
2. Select **OIDC – OpenID Connect**, then **Web Application**. Next.
3. Name: `AgentCore OBO UC2 Frontend`.
4. Grant type: enable **Authorization Code** (default) and **Refresh Token**.
5. Sign-in redirect URIs: `http://localhost:8000/auth/callback`.
6. Sign-out redirect URIs: `http://localhost:8000/`.
7. Controlled access: pick `Allow everyone in your organization to access` for the demo.
8. **Save**.
9. Configure client authentication and PKCE — General tab → Client Credentials → **Edit**:
   - Client Authentication: **Client secret**.
   - PKCE: check **Require PKCE as additional verification**.
   - **Save**.
10. Copy or generate the client secret (Client Credentials section). Paste into `.env` as `FRONTEND_CLIENT_SECRET`.
11. Copy the Client ID (top of General tab) → `FRONTEND_CLIENT_ID` in `.env`.

### Assign to your test user

Web App → Assignments tab. Either "Assign → Assign to People" and pick your user, or (on Federation Broker Mode tenants) you're done automatically.

## Step 2 — Register the Agent API Services app

The middle-tier client that does OBO #1.

1. Applications → **Create App Integration** → **API Services**. Next.
2. Name: `AgentCore OBO UC2 Agent`. **Save**.
3. General tab → **General Settings → Edit**:
   - **Grant types**: click **Show advanced settings** → check **Token Exchange**. Leave everything else default.
   - **Proof of possession**: set to **Not required** (uncheck DPoP if it's checked). **Critical** — see the callout below.
   - **Save**.
4. Client Credentials section:
   - Copy the Client ID → `AGENT_CLIENT_ID` in `.env`.
   - Reveal (or generate) the client secret → `AGENT_CLIENT_SECRET` in `.env`.

> **Why DPoP has to be off.** DPoP requires the caller to include a freshly-signed proof JWT on every token request. AgentCore Identity does not mint DPoP proofs. Leaving DPoP enabled causes Okta to reject the OBO exchange with `invalid_dpop_proof: The DPoP proof JWT header is missing.` Newer Okta Integrator tenants default DPoP **on** for API Services apps, so explicitly turning it off is not optional.
>
> **Why Token Exchange has to be on.** Without it Okta refuses `grant_type=urn:ietf:params:oauth:grant-type:token-exchange` from this client and returns `unauthorized_client`.

## Step 3 — Register the Gateway API Services app

Same steps as Step 2, just a different app.

1. Applications → **Create App Integration** → **API Services**. Next.
2. Name: `AgentCore OBO UC2 Gateway`. **Save**.
3. General Settings → Edit:
   - Grant types: Token Exchange (advanced settings).
   - Proof of possession: **Not required**.
   - **Save**.
4. Copy Client ID → `GATEWAY_CLIENT_ID`.
5. Copy client secret → `GATEWAY_CLIENT_SECRET`.

## Step 4 — Configure the authorization server: custom scopes + access policies

You've already picked the auth server in Step 0.5. Now define three custom scopes for the OBO chain and three access policies (one per app).

### 4a. Custom scopes

Okta refuses two mutually exclusive things on Token Exchange:
- `openid` is not allowed (`outcome.reason=openid_not_allowed_token_exchange`).
- Other OIDC scopes (`profile`, `email`, etc.) may only appear alongside `openid` (`outcome.reason=openid_scope_required`).

Custom scopes sidestep both rules.

1. Okta admin → Security → API → Authorization Servers → click your server → **Scopes** tab.
2. Add three scopes (repeat for each):

   | Name | Display phrase | Description |
   |---|---|---|
   | `agent.access` | Access the agent as the signed-in user | Allows the frontend to invoke the agent on the user's behalf. |
   | `gateway.access` | Access the gateway on the user's behalf | Allows the agent to invoke the AgentCore Gateway on the user's behalf. |
   | `downstream.access` | Access the downstream API on the user's behalf | Allows the gateway to invoke the downstream API on the user's behalf. |

   For each: click **Add Scope**, fill in Name / Display phrase / Description. Check **Include in public metadata**. Do NOT check **Set as a default scope** (we want the scope only when explicitly requested). **Create**.

### 4b. Access policy for the Frontend Web App (upstream — user sign-in)

Tells Okta: "The Frontend Web App is allowed to mint user tokens via Authorization Code."

1. **Access Policies** tab. Click **Add New Access Policy**.
2. Name: `AgentCore OBO UC2 - Frontend`. Description: `Allows the Frontend Web App to mint user tokens for the agent via Auth Code`.
3. Assign to: **The following clients** → pick `AgentCore OBO UC2 Frontend`.
4. **Create Policy**.
5. On the new policy card, click **Add Rule**:
   - Name: `Frontend Auth Code`.
   - Grant type: check **Authorization Code**.
   - User is: `Any user assigned the app` (or tighten per your needs).
   - Scopes requested: **The following scopes** → check `openid`, `profile`, `email`, `offline_access`, `agent.access`.
   - Access token lifetime: default (1 hour).
6. **Create Rule**.
7. **Activate the policy.** On the policy card's top-right corner there's a status dropdown that starts in **Inactive**. Click it and switch to **Active**. An Inactive policy is silently skipped during evaluation.

### 4c. Access policy for the Agent app (OBO #1)

1. **Access Policies** → **Add New Access Policy**.
2. Name: `AgentCore OBO UC2 - Agent OBO`.
3. Assign to: `AgentCore OBO UC2 Agent`. **Create Policy**.
4. **Add Rule**:
   - Name: `Agent Token Exchange`.
   - Grant type: check **Token Exchange**.
   - Scopes requested: **The following scopes** → check ONLY `gateway.access`. Do NOT add OIDC scopes.
5. **Create Rule**.
6. **Activate the policy.**

### 4d. Access policy for the Gateway app (OBO #2)

1. **Access Policies** → **Add New Access Policy**.
2. Name: `AgentCore OBO UC2 - Gateway OBO`.
3. Assign to: `AgentCore OBO UC2 Gateway`. **Create Policy**.
4. **Add Rule**:
   - Name: `Gateway Token Exchange`.
   - Grant type: check **Token Exchange**.
   - Scopes requested: **The following scopes** → check ONLY `downstream.access`.
5. **Create Rule**.
6. **Activate the policy.**

## Step 5 — Verify

On the auth server:

- **Scopes** tab: three custom scopes listed (`agent.access`, `gateway.access`, `downstream.access`).
- **Access Policies** tab: three policies, all **Active**:
  - `AgentCore OBO UC2 - Frontend` → Frontend app, Auth Code rule.
  - `AgentCore OBO UC2 - Agent OBO` → Agent app, Token Exchange rule for `gateway.access`.
  - `AgentCore OBO UC2 - Gateway OBO` → Gateway app, Token Exchange rule for `downstream.access`.

On each app:

- **Frontend Web App**: Grant types = Auth Code + Refresh Token, Redirect URI = `http://localhost:8000/auth/callback`, PKCE required.
- **Agent + Gateway API Services**: Grant type = Token Exchange, DPoP = Not required.

Values in `.env`:

| Env var | Source |
|---|---|
| `OKTA_DOMAIN` | app-facing host (no `-admin`) |
| `OKTA_AUTH_SERVER_ID` | last path segment of the Issuer URI |
| `OKTA_AUDIENCE` | `api://default` or the configured audience |
| `FRONTEND_CLIENT_ID` / `_SECRET` | Frontend Web App |
| `AGENT_CLIENT_ID` / `_SECRET` | Agent API Services |
| `GATEWAY_CLIENT_ID` / `_SECRET` | Gateway API Services |
| `UPSTREAM_SCOPE` | `openid profile email agent.access` |
| `GATEWAY_SCOPE` | `gateway.access` |
| `DOWNSTREAM_SCOPE` | `downstream.access` |
| `FRONTEND_REDIRECT_URI` | `http://localhost:8000/auth/callback` |

## Troubleshooting

### `Policy evaluation failed for this request, please check the policy configurations.`

Shows on Okta's error page after sign-in or after any Token Exchange call. Common causes, in order of likelihood:

1. **Policy status is Inactive.** On the policy card, the top-right dropdown should say **Active**. Inactive policies are silently ignored.
2. **Policy isn't assigned to the right client.** The Frontend policy must be assigned to the Frontend app; the Agent OBO policy to the Agent app; etc.
3. **Rule scopes don't match what was requested.** The rule's scope checklist must include everything the client asked for.
4. **Rule grant type doesn't match.** Auth Code for the Frontend, Token Exchange for the two API Services apps.

### `invalid_dpop_proof: The DPoP proof JWT header is missing`

DPoP is still enabled on one of the API Services apps. Okta admin → the app → General → General Settings → Edit → **Proof of possession: Not required**. Save.

### `unauthorized_client` on the OBO exchange

Either the API Services app doesn't have the Token Exchange grant (fix: Step 2/3 General Settings), or the OBO access policy doesn't apply to it (fix: 4c/4d — confirm assigned client + Token Exchange grant type).

### `outcome.reason = openid_not_allowed_token_exchange` in Okta System Log

Your OBO scope list included `openid`. Okta reserves it for the initial user sign-in. Use only custom scopes (`gateway.access` or `downstream.access`) on the exchange.

### `outcome.reason = openid_scope_required`

You included `profile` or `email` without `openid`. Okta refuses OIDC scopes as a group on Token Exchange. Use only custom scopes.

### `401 Unauthorized` when the agent is invoked

The Runtime rejected the inbound user JWT. Common causes:

1. `T_user` has `aud != OKTA_AUDIENCE`. Decode the token at jwt.io — the `aud` claim on the frontend's access token should equal what `OKTA_AUDIENCE` was set to in `.env` (typically `api://default`).
2. The Runtime's `discoveryUrl` and the frontend's sign-in are against different auth servers. Check `OKTA_AUTH_SERVER_ID` matches on both sides.
3. Token expired (default 1 hour). Sign out and back in.

### `PKCE code challenge is required by the application`

Shows on Okta's error page during sign-in. The Frontend Web App is configured with "Require PKCE" but the frontend isn't sending a `code_challenge`. `frontend/app.py` in this example sends `code_challenge_method=S256` through authlib's `client_kwargs` — if you're running a modified frontend that dropped this kwarg, add it back.

### The automation script says "Auth server not found"

`OKTA_AUTH_SERVER_ID` is wrong or missing. Verify by opening the discovery URL in a browser (Step 0.5 → Verify). On Integrator tenants without a default server, create one first via Okta admin console.

### The automation script says "HTTP 401"

Your `OKTA_ADMIN_TOKEN` is invalid or expired. Regenerate at Okta admin → Security → API → Tokens. The token needs Super Admin or Org Admin privileges to create apps.
