# Entra ID setup

**Two** app registrations, not three — the agent forwards the caller's token to
the gateway unchanged, so there is no separate agent identity:

1. **`agentcore-consent-github-gateway`** — the *resource* app. Its application
   GUID is the `aud` of every inbound token, validated by both the runtime and
   the gateway. It is **also** the consent portal's login client
   (see [detail 1](#1-every-sign-in-targets-the-same-audience)).
2. **`agentcore-consent-github-frontend`** — the OIDC client the BFF signs users
   into.

"Resource app" is a role, not a name: in Entra terms it is the registration you
**Expose an API** on, so other apps can request a scope *for* it. Nothing is
literally named `ResourceApp`.

> **Two ways to do this setup:**
> - **Automated** (recommended): `python deploy/00_create_entra_apps.py`.
>   ~30 seconds. See [Automated path](#automated-path).
> - **Manual**: click through the Entra admin center, ~15 minutes. See
>   [Manual path](#manual-path).
>
> Both produce the same result. The manual walkthrough is there for environments
> without the Azure CLI, and for understanding what each step does.

## Architecture recap

`$GW` below is the **gateway app's GUID** — `GATEWAY_CLIENT_ID` in `.env`, and
also `IDP_AUDIENCE`:

```
👤 User ──sign in──▶ …-github-frontend         (the OIDC client)
                        │ token: aud = $GW, scp = access_as_user
                        ▼
                     AgentCore Runtime         (allowedAudience: $GW)
                        │ same token, forwarded unchanged
                        ▼
                     AgentCore Gateway         (allowedAudience: $GW)

👤 User ──sign in──▶ Consent portal ──as …-github-gateway──▶ Entra
                        │ binds GitHub consent to this user's `sub`
                        ▼
                     AgentCore token vault
```

One audience, two sign-in paths. That is the whole design: because both the BFF
and the portal request `api://$GW/access_as_user`, both tokens carry the same
`aud` — and therefore the same `sub` — so consent granted on the portal is found
when the gateway looks it up.

Both authorizers list two spellings of that audience, `$GW` and `api://$GW`.
Entra puts the bare GUID in `aud`, but the identifier URI is what several tools
display, so accepting both removes a class of copy-paste mismatch.

## Prerequisites

- An Entra tenant where you can register apps **and grant admin consent**.
  Application Administrator is enough to create apps; granting tenant-wide
  consent needs Privileged Role Administrator or Global Administrator.
- At least one active test user in the tenant. For a single-tenant registration,
  directory membership is enough.
- For the automated path: [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
  2.50+.

---

## Automated path

### Step A1 — Bootstrap `.env`

```bash
cp config.example.env .env
```

Leave the Entra placeholders alone; the script fills them in.

### Step A2 — Sign in and confirm the tenant

```bash
az login
az account show --query "{tenantId: tenantId, user: user.name}"
```

If the wrong tenant comes back, re-run `az login --tenant <tenant-id>`.

### Step A3 — Run the automation

```bash
python deploy/00_create_entra_apps.py          # --rotate-secrets to force new secrets
```

It performs every action in the manual path below:

1. Creates `agentcore-consent-github-gateway` and
   `agentcore-consent-github-frontend`.
2. Sets the frontend's web redirect to `http://localhost:8000/auth/callback`.
3. On the resource app: `identifierUris = api://<appId>`,
   `api.requestedAccessTokenVersion = 2`, and an `access_as_user` scope.
4. Mints a client secret for each app.
5. Grants the frontend delegated permission to the resource app's
   `access_as_user`, **and** the resource app the same permission on itself —
   each with `add`, `grant` **and** `admin-consent`.
6. Writes everything to `.env`.

Idempotent: apps are reused by display name, and secrets are only minted when
`.env` is missing them.

### Step A4 — Verify

```bash
grep -E '^(IDP|TENANT_ID|GATEWAY_CLIENT_ID|PORTAL_CLIENT_ID|FRONTEND_CLIENT_ID|IDP_DISCOVERY_URL|IDP_AUDIENCE|GATEWAY_SCOPE|PORTAL_SCOPES)=' .env
```

Expect `IDP=entra`, `PORTAL_CLIENT_ID` **equal to** `GATEWAY_CLIENT_ID`, an
`IDP_DISCOVERY_URL` containing `/v2.0/`, and `IDP_AUDIENCE` as a bare GUID.

Then confirm Entra agrees:

```bash
az ad app show --id "$GATEWAY_CLIENT_ID" \
  --query "{v:api.requestedAccessTokenVersion, uris:identifierUris, scopes:api.oauth2PermissionScopes[].value}"

# a tenant-wide grant should exist for each app
az ad app permission list-grants --id "$GATEWAY_CLIENT_ID" \
  --filter "consentType eq 'AllPrincipals'"
```

`v` must be `2`. You are done — continue at step 3 of the
[README quick start](README.md#quick-start).

> **If `admin-consent` failed**, the script says which app needs it. Ask an
> admin to run `az ad app permission admin-consent --id <appId>`, or use
> App registrations → the app → API permissions → *Grant admin consent*.

### Re-running and tearing down

| Goal | Command |
| :--- | :--- |
| Re-run safely (picks up existing apps) | `python deploy/00_create_entra_apps.py` |
| Force-rotate both client secrets | `python deploy/00_create_entra_apps.py --rotate-secrets` |
| Delete both app registrations | `python deploy/00_delete_entra_apps.py --yes` |

Run the delete **after** `deploy/teardown.py` — the resource app is the
gateway's audience and the portal's login client, so removing it first breaks
both in ways that look like configuration bugs.

---

## How the four Entra settings fit together

Four values do the real work in this setup. The automation sets all four for
you, so this section is background: read it if you are configuring by hand,
adapting the sample to apps that already exist, or curious *why* a value is what
it is.

### 1. Every sign-in targets the same audience

Under Entra the `sub` claim is a **pairwise identifier (PPID)**, not a stable
per-user value. What it is keyed on is easy to get backwards: `sub` is pairwise
on the **resource** the token is audienced at (`aud`), *not* on the client that
requested it (`azp`).

This matters because consent is stored under the `sub` the portal authenticated
and looked up under the `sub` the gateway resolves. If those differed, AgentCore
would bind consent under one identity and look it up under another, and every
tool call would re-prompt even though the user had just consented — with nothing
reporting an error.

They do not differ here, and the reason is the shared audience: the frontend and
the portal both request `api://<GUID>/access_as_user`, so both tokens carry
`aud = <GUID>` and therefore the same `sub`, even though they sign in through two
different client apps. **Verified live** — the frontend's token has
`azp = <frontend app>` while the portal signs in as the resource app, and the two
`sub` values are byte-identical.

Practical consequence: keep `GATEWAY_SCOPE` and `PORTAL_SCOPES` pointed at the
**same** `api://<GUID>/...` audience. Using this app as the portal's login client
(so `PORTAL_CLIENT_ID == GATEWAY_CLIENT_ID`) is convenient because it already
exists and holds a secret, but it is not what makes the subjects match — the
shared audience is. `/debug/token` prints `aud`, `azp` and `sub` side by side.

### 2. Access tokens are v2 (`requestedAccessTokenVersion: 2`)

With this set, Entra issues v2 access tokens. Left at the default, it issues
v1-style tokens whose `iss` is
`https://sts.windows.net/<tenant>/`. The gateway, the runtime and the portal all
validate against the **`/v2.0/`** discovery document, which advertises
`https://login.microsoftonline.com/<tenant>/v2.0`. A v1 token therefore surfaces as
`Claim 'iss' value mismatch with configuration` or `insufficient_scope` — worth
recognising, because both read like scope problems.

```bash
az ad app show --id "$GATEWAY_CLIENT_ID" --query "api.requestedAccessTokenVersion"   # want 2
az ad app update --id "$GATEWAY_CLIENT_ID" --set 'api.requestedAccessTokenVersion=2'
```

**Sign out and back in after changing this.** Your BFF session still holds the
v1 token it was issued earlier; the new setting only takes effect on the next
authentication.

### 3. `audience` is the bare GUID

With v2 tokens Entra sets `aud` to the **application id**. The `api://<GUID>` form is the
*identifier URI*, a separate string that never appears in `aud`. Give
`idpConfig.audience` the GUID, matching what the token carries.

### 4. The portal requests the *fully qualified* scope

Two readers want the same permission spelled differently. The gateway reads the
short name out of an issued token's `scp` claim. The portal and the BFF *request*
a token, and Entra's `/authorize` only accepts
`api://<GUID>/access_as_user`. So `PORTAL_SCOPES` and `GATEWAY_SCOPE` both carry
the qualified form, and the gateway still matches on the short name it finds in
`scp`.

---

## Manual path

Produces the same result as the automation, click by click.

### Step 0 — Bootstrap `.env`

```bash
cp config.example.env .env
az login
TENANT_ID=$(az account show --query tenantId -o tsv)
```

Keep `.env` open beside the admin center. Whenever a step says "copy X → `VAR`",
paste it immediately rather than backtracking.

### Step 1 — Register the resource app (`agentcore-consent-github-gateway`)

1. **Entra admin center → App registrations → New registration**.
2. Name `agentcore-consent-github-gateway`; supported account types **Accounts
   in this organizational directory only**; leave Redirect URI blank.
3. **Register**, then copy from Overview:
   - Application (client) ID → `GATEWAY_CLIENT_ID` **and** `PORTAL_CLIENT_ID`
     **and** `IDP_AUDIENCE`
   - Directory (tenant) ID → `TENANT_ID`
4. **Expose an API → Set** the Application ID URI (accept `api://<appId>`).
5. **Add a scope**: name `access_as_user`, who can consent **Admins and users**,
   display name "Access AgentCore as the signed-in user", **Enabled**.
   The full string `api://<GUID>/access_as_user` → `GATEWAY_SCOPE`, and
   `openid api://<GUID>/access_as_user` → `PORTAL_SCOPES`.
6. **Certificates & secrets → New client secret** → copy the Value →
   `PORTAL_CLIENT_SECRET`. (This app is the portal's login client, so this is
   the portal's secret.)
7. Force v2 tokens — no console field exists for this, so use the CLI:
   ```bash
   OBJ=$(az ad app show --id "$GATEWAY_CLIENT_ID" --query id -o tsv)
   az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$OBJ" \
     --headers "Content-Type=application/json" \
     --body '{"api":{"requestedAccessTokenVersion":2}}'
   ```
8. Grant the app its own scope, so the portal's server-side login needs no
   per-user consent screen:
   ```bash
   SCOPE_ID=$(az ad app show --id "$GATEWAY_CLIENT_ID" \
     --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)
   az ad app permission add --id "$GATEWAY_CLIENT_ID" \
     --api "$GATEWAY_CLIENT_ID" --api-permissions "$SCOPE_ID=Scope"
   az ad app permission grant --id "$GATEWAY_CLIENT_ID" \
     --api "$GATEWAY_CLIENT_ID" --scope access_as_user
   az ad app permission admin-consent --id "$GATEWAY_CLIENT_ID"
   ```
   All three matter. `add` records the permission on the app registration, but
   until `grant` creates the grant object there is nothing for Entra to
   evaluate at token time — so you can see `admin-consent` report success and
   still be refused a token for want of consent.

Do **not** set a redirect URI yet — `<portalUrl>` does not exist until
quick-start step 4. Do **not** mark the app a public client; it holds a secret
and exchanges the code server-side.

### Step 2 — Register the frontend app (`agentcore-consent-github-frontend`)

1. **New registration**, name `agentcore-consent-github-frontend`, same account
   types, Redirect URI **Web** → `http://localhost:8000/auth/callback`.
   > For an `http://` redirect URI, Entra accepts only the hostname
   > `localhost` spelled out; `127.0.0.1` and other loopback addresses are
   > rejected.
2. Copy Application (client) ID → `FRONTEND_CLIENT_ID`.
3. **Certificates & secrets → New client secret** → Value →
   `FRONTEND_CLIENT_SECRET`.
4. Grant it the resource app's scope, and consent it:
   ```bash
   az ad app permission add --id "$FRONTEND_CLIENT_ID" \
     --api "$GATEWAY_CLIENT_ID" --api-permissions "$SCOPE_ID=Scope"
   az ad app permission grant --id "$FRONTEND_CLIENT_ID" \
     --api "$GATEWAY_CLIENT_ID" --scope access_as_user
   az ad app permission admin-consent --id "$FRONTEND_CLIENT_ID"
   ```

### Step 3 — Finish `.env`

```bash
IDP=entra
TENANT_ID=<tenant id>
GATEWAY_CLIENT_ID=<gateway app GUID>
PORTAL_CLIENT_ID=<the same GUID>            # same audience — see detail 1
PORTAL_CLIENT_SECRET=<gateway app secret>
FRONTEND_CLIENT_ID=<frontend GUID>
FRONTEND_CLIENT_SECRET=<frontend secret>
IDP_DISCOVERY_URL=https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
IDP_AUDIENCE=<gateway app GUID>             # bare GUID — see detail 3
GATEWAY_SCOPE=api://<GUID>/access_as_user   # qualified — see detail 4
PORTAL_SCOPES=openid api://<GUID>/access_as_user
```

`deploy/02_create_portal.py` adds `<portalUrl>/callback` to the resource app's
`web.redirectUris` once the portal exists. Note `az ad app update
--web-redirect-uris` **replaces** the whole array, so if you ever run it by
hand, pass every URI you need in one call.

### Step 4 — Verify

Walk one app at a time; switching between them mid-check is how mistakes happen.

**Resource app** (`agentcore-consent-github-gateway`):

- **Expose an API**: Application ID URI is `api://<GUID>`, `access_as_user` Enabled.
- **API permissions**: its own `access_as_user` shows ✓ Granted.
- **Manifest**: `api.requestedAccessTokenVersion` is `2`.
- **Authentication**: after quick-start step 4, `https://<portalUrl>/callback`
  is listed under Web — with **no trailing slash**.

**Frontend app** (`agentcore-consent-github-frontend`):

- **Authentication**: Web redirect `http://localhost:8000/auth/callback`.
- **API permissions**: the resource app's `access_as_user` shows ✓ Granted.

Finally, sign in through the BFF and open <http://localhost:8000/debug/token>.
In the decoded claims: `aud` equals `GATEWAY_CLIENT_ID`, `iss` ends in `/v2.0`,
`scp` contains `access_as_user`. Sign in to the consent portal as the **same**
user and confirm the `sub` there matches.

## Troubleshooting

| `AADSTS` code | Meaning | Fix |
| :--- | :--- | :--- |
| `AADSTS65001` | Consent not granted | Run all three permission commands on the affected app, or re-run `00_create_entra_apps.py` |
| `AADSTS500113` | No reply address registered | The redirect URI does not match. Check `web.redirectUris` against `PORTAL_CALLBACK_URL` / `FRONTEND_REDIRECT_URI`, exactly, no trailing slash |
| `AADSTS7000215` | Invalid client secret | Rotate: `00_create_entra_apps.py --rotate-secrets`, then re-run `02_create_portal.py` so the provider gets the new secret |
| `AADSTS50013` | Assertion invalid or expired | Sign out and back in |
| `AADSTS50105` | User not assigned to the application | The test user is a guest or from another directory; use a member of this tenant |
| `AADSTS9002313` | Malformed request | Usually a scope typo — check the fully qualified form |

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `Claim 'iss' value mismatch` or `insufficient_scope` | v1/v2 mismatch | Set `requestedAccessTokenVersion: 2` (detail 2), then **sign out and back in** |
| `/?error=login_failed` right after the portal is created | Redirect URI mismatch, usually a trailing slash | Compare `az ad app show --id "$PORTAL_CLIENT_ID" --query web.redirectUris` against `PORTAL_CALLBACK_URL` |
| A consent-required error although `admin-consent` succeeded | The explicit `az ad app permission grant` was skipped | Run `add`, `grant` **and** `admin-consent` |
| Portal shows Connected but tool calls re-prompt | Portal user ≠ app user | Compare `sub` at `/debug/token` against the portal header |
| Login stops working after about a year | The client secret expired (`--years 1`) | Rotate it and re-run `02_create_portal.py` |
