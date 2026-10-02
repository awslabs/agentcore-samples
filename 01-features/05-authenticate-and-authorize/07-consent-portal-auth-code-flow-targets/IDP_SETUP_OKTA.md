# Okta setup

**Two** OIDC web apps, plus one custom scope and two access policies on a
**custom authorization server**. There is no agent app — the agent forwards the
caller's token to the gateway unchanged:

1. **Frontend app** — the OIDC client the BFF signs users into.
2. **Portal login app** — a *separate* confidential client the consent portal
   signs users in as ([why it can be separate](#one-difference-from-entra-the-portal-gets-its-own-app)).

> **Two ways to do this setup:**
> - **Automated** (recommended): set `OKTA_DOMAIN` + `OKTA_ADMIN_TOKEN`, then
>   `python deploy/00_create_okta_apps.py`. ~30 seconds. See
>   [Automated path](#automated-path).
> - **Manual**: click through the Okta admin console, ~15 minutes. See
>   [Manual path](#manual-path).

## Architecture recap

```
👤 User ──sign in──▶ Frontend app
                        │ token: iss = custom AS, aud = api://default, scp = access_as_user
                        ▼
                     AgentCore Runtime   (authorizer: aud = api://default)
                        │ same token, forwarded unchanged
                        ▼
                     AgentCore Gateway   (authorizer: aud = api://default)

👤 User ──sign in──▶ Consent portal ──as the portal login app──▶ Okta
                        │ binds GitHub consent to this user's `sub`
                        ▼
                     AgentCore token vault
```

Both sign-ins go to the **same custom authorization server**, so both tokens
carry the same `iss` and `aud`. Okta's `sub` is stable per user, so consent binds
under the identity the gateway later resolves.

## Prerequisites

- An Okta org with **API Access Management** — the paid add-on that provides
  custom authorization servers. Without it this variant cannot work; use Entra ID
  instead. See [detail 2](#2-a-custom-authorization-server).
- An admin API token: Okta admin → **Security → API → Tokens → Create Token**,
  minted by an Org or Super Admin. Used only by the setup scripts and by
  quick-start step 4's callback registration — never at runtime.
- A test user you can sign in as, assigned to both apps.

---

## Automated path

### Step A1 — Find your domain and authorization server

**`OKTA_DOMAIN`** is the app-facing host — your admin console URL **without**
`-admin`:

| Admin URL | `OKTA_DOMAIN` |
| :--- | :--- |
| `https://integrator-1234567-admin.okta.com/admin/dashboard` | `integrator-1234567.okta.com` |
| `https://dev-987654.okta.com/admin/dashboard` | `dev-987654.okta.com` |

The scripts normalise a pasted scheme, a trailing slash, and the `-admin` host,
but OIDC discovery is only served from the app-facing host.

**`OKTA_AUTH_SERVER_ID`**: Okta admin → **Security → API → Authorization
Servers**. Take the last path segment of the **Issuer URI** column —
`…/oauth2/default` → `default`; `…/oauth2/ausXXXX` → `ausXXXX`. Note the
**Audience** too (usually `api://default`).

> The built-in server answers to *both* `default` and its literal `aus…` id.
> Either works; copy the Issuer URI's segment and don't switch later. Mixing the
> two forms is [detail 1](#1-one-spelling-of-the-authorization-server-everywhere).

> **Table empty?** Some newer Integrator tenants ship without one. Create it:
> *Add Authorization Server*, name `default`, audience `api://default`. If the
> option is missing entirely, your org lacks API Access Management.

### Step A2 — Verify the discovery document *before* creating anything

```bash
curl -s "https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/.well-known/openid-configuration" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['issuer']); print(d['scopes_supported'])"
```

You want an `issuer` containing `/oauth2/<id>`. Interpret failures:

| What you get | Meaning |
| :--- | :--- |
| Valid JSON, issuer contains `/oauth2/…` | Correct — proceed |
| Issuer is the bare domain | You hit the **org** server — see [detail 2](#2-a-custom-authorization-server) |
| A sign-in page | You used the `-admin` host |
| 404 | Wrong auth server id, or none exists |

Do not proceed until this returns valid JSON.

### Step A3 — Configure and run

```bash
cp config.example.env .env
# set in .env:
#   OKTA_DOMAIN=integrator-1234567.okta.com
#   OKTA_ADMIN_TOKEN=00...
#   OKTA_AUTH_SERVER_ID=default
python deploy/00_create_okta_apps.py          # --rotate-secrets to force new secrets
```

It performs every action in the manual path:

1. Verifies the authorization server and captures its audience.
2. Creates the `access_as_user` custom scope (`consent: IMPLICIT`, published).
3. Creates both web apps — `client_secret_basic`, `authorization_code` +
   `refresh_token` — the frontend with the localhost redirect, the portal login
   app with a **placeholder** redirect that quick-start step 4 replaces.
4. Assigns both to the built-in **Everyone** group.
5. Mints a client secret for each.
6. Creates one access policy + `ACTIVE` rule per app, admitting
   `authorization_code` with `openid profile email access_as_user`.
7. Writes everything to `.env`.

Re-runs are safe: apps, scope and policies are looked up by name.

### Step A4 — Confirm the apps are assigned to users

The script assigns the **Everyone** group where the API allows it.

- **Success**: output shows `✓ Assigned … to Everyone`. Nothing more to do.
- **Warning**: output shows `⚠ 'Everyone' group not found` or an assign failure.
  Assign manually: Okta admin → **Applications** → the app → **Assignments** →
  *Assign → Assign to People* (or *to Groups → Everyone*).

**How you'll know if it was skipped:** sign-in fails with Okta's error page,
*"User is not assigned to the client application."*

> **Federation Broker Mode.** On some newer Integrator tenants every user can
> reach every app without an explicit assignment, so this call has nothing to do
> and may be rejected outright. Treat successful sign-in as the real test.

### Step A5 — Verify

```bash
grep -E '^(IDP|OKTA_DOMAIN|OKTA_AUTH_SERVER_ID|PORTAL_APP_ID|FRONTEND_CLIENT_ID|PORTAL_CLIENT_ID|IDP_DISCOVERY_URL|IDP_AUDIENCE|GATEWAY_SCOPE|PORTAL_SCOPES)=' .env
```

Expect `IDP=okta`, an `IDP_DISCOVERY_URL` whose path segment matches
`OKTA_AUTH_SERVER_ID` exactly, `IDP_AUDIENCE=api://default`, and
`GATEWAY_SCOPE=access_as_user` (short form — Okta uses one spelling end to end).

You are done — continue at step 3 of the
[README quick start](README.md#quick-start).

### Re-running and tearing down

| Goal | Command |
| :--- | :--- |
| Re-run safely | `python deploy/00_create_okta_apps.py` |
| Force-rotate both client secrets | `python deploy/00_create_okta_apps.py --rotate-secrets` |
| Delete both apps | `python deploy/00_delete_okta_apps.py --yes` |
| Also delete this sample's two access policies | `python deploy/00_delete_okta_apps.py --yes --policies` |

Run these **after** `deploy/teardown.py`. The `access_as_user` scope is left in
place either way, since an authorization server is usually shared.

---

## How the four Okta settings fit together

Four values do the real work in this setup. `00_create_okta_apps.py` sets all
four for you, so this section is background: read it if you are configuring by
hand, adapting the sample to an authorization server that already exists, or
curious *why* a value is what it is.

### 1. One spelling of the authorization server, everywhere

The built-in custom authorization server answers to **two** path segments — the
alias `default` and its literal id (`aus…`):

```
https://<domain>/oauth2/default/.well-known/openid-configuration
https://<domain>/oauth2/aus1a2b3c4d5EXAMPLE/.well-known/openid-configuration
```

Both return HTTP 200, and both report the *same* `issuer`
(`https://<domain>/oauth2/default`) — verified live. So for **token validation**
they are equivalent, which is exactly what makes the failure confusing.

The **consent portal** treats them differently: it compares the gateway
authorizer's `discoveryUrl` against the IdP credential provider's as *strings*,
so the two need the same spelling. Verified by testing all four combinations of
(provider form × gateway form) — matching pairs work; a mismatch fails closed
with `login_unavailable`, an error that says nothing about the cause.

`00_create_okta_apps.py` therefore keeps **the segment you configured** in
`OKTA_AUTH_SERVER_ID` and builds every URL from it, using the resolved id only
for Admin API calls. Both legs read that one `IDP_DISCOVERY_URL`, so they agree
by construction. Only hand-editing one side breaks it.

### 2. A *custom* authorization server

A **custom** authorization server issues verifiable JWTs, which is what the
gateway's JWT authorizer and the consent portal both need. Okta's **org** server
(`https://<domain>/.well-known/…`) issues **opaque** access tokens instead, and
cannot host a custom audience or scope, so `CreateConsentPortal` does not accept
it.

A **custom** server (`https://<domain>/oauth2/<AS_ID>/…`) issues verifiable JWTs.
The built-in one is named `default`; it exists only on orgs with **API Access
Management**.

### 3. The access policy admits `openid`

The consent portal always requests `openid` **in addition to** whatever is in
`PORTAL_SCOPES`, so every scope it requests needs to be defined on the
authorization server *and* admitted by that client's access-policy rule. When
one is missing, `/authorize` returns a policy-evaluation error that does not name
it.

Two things to know: a brand-new custom authorization server has **no policy at
all**, and an **Inactive** policy is silently skipped during evaluation.

### 4. Users are assigned to both apps

Okta issues a token only to users assigned to the application, so assign every
user who will sign in — to **both** apps. An unassigned user sees
`User is not assigned to the client application` at sign-in.

### One difference from Entra: the portal gets its own app

In Okta, one user carries one `sub`, and the client that asked for the token
does not change it. A dedicated portal login app therefore authenticates people
under the identity the gateway will resolve later, which is what lets the portal
keep its own confidential client.

Under Entra this is not true: `sub` is pairwise per resource, which is why that
variant reuses the resource app. See
[IDP_SETUP_ENTRA.md](IDP_SETUP_ENTRA.md#1-every-sign-in-targets-the-same-audience).

---

## Manual path

```bash
export OKTA_DOMAIN=integrator-1234567.okta.com     # app-facing host
export OKTA_TOKEN=<your SSWS token>
export AS_ID=default
```

### Step 1 — Add the custom scope

Okta admin → **Security → API → Authorization Servers** → your server →
**Scopes** → **Add Scope**:

| Field | Value |
| :--- | :--- |
| Name | `access_as_user` |
| Display phrase | Access AgentCore on the user's behalf |
| Description | Allows calling the AgentCore runtime and gateway as the signed-in user. |
| Include in public metadata | ✅ |
| Set as a default scope | ❌ (only when explicitly requested) |
| User consent | **Implicit** — the GitHub consent is the one that matters here |

### Step 2 — Register the two web apps

For **each** of `AgentCore Consent GitHub Frontend` and
`agentcore-consent-portal-login`:

1. **Applications → Create App Integration → OIDC – OpenID Connect → Web
   Application**.
2. Grant types: **Authorization Code** (+ **Refresh Token**).
3. Sign-in redirect URI:
   - frontend → `http://localhost:8000/auth/callback`
   - portal login → `https://localhost/placeholder` (a throwaway; Okta requires
     at least one with the auth-code grant, and quick-start step 4 replaces it)
4. Controlled access: *Allow everyone in your organization to access*.
5. **Save**, then General → Client Credentials → **Edit**: Client Authentication
   = **Client secret**. Optionally check **Require PKCE as additional
   verification** — the BFF sends `code_challenge_method=S256` regardless, so
   this is defense in depth.
6. Copy the **Client ID** and **client secret** →
   `FRONTEND_CLIENT_ID`/`_SECRET` and `PORTAL_CLIENT_ID`/`_SECRET`.
7. For the portal app, also copy its **app id** (the `0oa…` in the URL) →
   `PORTAL_APP_ID`. Quick-start step 4 needs it to `PUT` the real redirect.

> For Okta OIDC apps the app `id` and the OAuth `client_id` are normally the
> **same** value. Both are `0oa…`, which makes them easy to confuse, but seeing
> them equal is not a mistake.

### Step 3 — One access policy per app

**Access Policies → Add New Access Policy**, twice:

| Policy name | Assign to | Rule |
| :--- | :--- | :--- |
| `Consent sample - Frontend` | the frontend app | grant **Authorization Code**; scopes `openid`, `profile`, `email`, `offline_access`, `access_as_user` |
| `Consent sample - Portal login` | the portal login app | grant **Authorization Code**; scopes `openid`, `profile`, `email`, `access_as_user` |

For each: create the policy, **Add Rule** with the grant type and scope
checklist above, then **switch the policy status to Active** — the card starts
**Inactive**, and an inactive policy is silently ignored (detail 3).

### Step 4 — Fill in `.env`

```bash
IDP=okta
OKTA_DOMAIN=<app-facing host>
OKTA_ADMIN_TOKEN=<SSWS token>
OKTA_AUTH_SERVER_ID=<the Issuer URI's last segment: default or ausXXXX>
PORTAL_APP_ID=<the portal app's 0oa… id>
FRONTEND_CLIENT_ID=<…>
FRONTEND_CLIENT_SECRET=<…>
PORTAL_CLIENT_ID=<…>
PORTAL_CLIENT_SECRET=<…>
IDP_DISCOVERY_URL=https://<domain>/oauth2/<AS_ID>/.well-known/openid-configuration
IDP_AUDIENCE=api://default        # the AS `audiences` value, never the gateway URL
GATEWAY_SCOPE=access_as_user      # short name — Okta uses one spelling
PORTAL_SCOPES=openid access_as_user
```

### Step 5 — Verify

On the authorization server:

- **Scopes**: `access_as_user` listed.
- **Access Policies**: both policies present and **Active**, each assigned to its
  own client, each with an Authorization Code rule.

On each app:

- Grant types Authorization Code (+ Refresh Token); redirect URI as above;
  client authentication = client secret.
- **Assignments**: your test user (or Everyone).

Then sign in through the BFF and open <http://localhost:8000/debug/token>:
`aud` equals `IDP_AUDIENCE`, `iss` is the custom-AS issuer, `scp` contains
`access_as_user`. Sign in to the consent portal as the **same** user and confirm
the `sub` matches.

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `E0000011 Invalid token provided` on every admin call | `OKTA_ADMIN_TOKEN` expired (Okta expires tokens after 30 days of inactivity), revoked, or from another org | Mint a new one: Security → API → Tokens → Create Token |
| Script says the authorization server was not found | `OKTA_AUTH_SERVER_ID` wrong, or the org has no custom server | Re-check step A1; create one, or use Entra if API Access Management is unavailable |
| `CreateConsentPortal` rejects the IdP provider | You pointed at the **org** server, which issues opaque tokens | Use the custom AS discovery URL (detail 2) |
| `GET /login` → `/?error=login_unavailable` | Discovery URL forms differ between gateway and provider, **or** the portal never resolved the authorizer | Make the two byte-identical (detail 1), then `python deploy/01_create_gateway.py --okta --reapply-authorizer` |
| `Policy evaluation failed` at `/authorize` | Policy Inactive, assigned to the wrong client, or its rule omits a requested scope — often `openid` | Activate it, check the client, add the scope (detail 3) |
| `User is not assigned to the client application` | The user is not assigned to that app (detail 4) | Assign the user to that app |
| `PKCE code challenge is required` | The app requires PKCE but the client did not send one | The BFF sends S256 via authlib's `client_kwargs`; restore it if you edited `auth_okta.py` |
| `/?error=login_failed` right after the portal is created | Redirect URI mismatch, usually a trailing slash | Compare the portal app's `redirect_uris` against `PORTAL_CALLBACK_URL` |
| 401 when the agent is invoked | `aud` ≠ `IDP_AUDIENCE`, or the runtime and the frontend use different authorization servers | Decode at `/debug/token`; confirm `OKTA_AUTH_SERVER_ID` matches on both sides |
| `https://https://…` in a URL | `OKTA_DOMAIN` pasted with a scheme | Harmless — `okta_domain()` normalises it |
