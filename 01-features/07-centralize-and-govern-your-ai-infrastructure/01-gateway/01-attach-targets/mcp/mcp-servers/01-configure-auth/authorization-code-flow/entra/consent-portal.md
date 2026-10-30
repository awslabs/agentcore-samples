# Create an AgentCore Consent Portal using Entra ID

**AgentCore Consent Portal** is a hosted, AWS-managed web portal that authenticates your end
users against your OIDC identity provider and gathers their consent before an agent reaches
a downstream resource on their behalf. Each portal attaches to exactly one AgentCore
gateway, reads that gateway's targets, and shows the user one row per outbound provider
those targets depend on — with **Connect**, **Reconnect** and **Disconnect** actions.

![demo](../consent-portal/images/demo-portal.gif)

It is a managed front end over the
[OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
primitive. Session binding is what guarantees the user who *started* an authorization is the
user who *granted* it: `GetResourceOauth2Token` hands back an authorization URL **and** a
session URI, and something has to call
[`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html)
with both the caller identity and that session URI once consent is given. Without a portal,
that something is code you write — see
[`../github/README.md`](../github/README.md), where a local `callback_server.py` does it. With
a portal, AWS does it.

### The Backend-For-Frontend shape

The portal is a **Backend-For-Frontend (BFF)**: the OAuth exchange happens entirely
server-side and the browser never receives an access token or an ID token. What the browser
loads is a **Single-Page Application** — a JavaScript app served once from `portalUrl` that
then makes JSON calls rather than full page loads. That SPA talks only to the portal's own
API; the tokens live in an encrypted, `HttpOnly` cookie the JavaScript cannot read.

Practically, that means nothing sensitive is in `localStorage`, there is no token for a
cross-site script to steal, and you cannot inspect the flow by reading the browser's
network tab — which is the point.

### What creating a portal actually looks like

The whole portal is one API call.
[`scripts/deploy_portal.py`](../scripts/deploy_portal.py) makes it for you, but it is worth
seeing the shape first, because every subtlety on this page is about one of these four
fields:

```python
response = client.create_consent_portal(
    name="entra-consent-portal",
    description="Consent portal backed by Microsoft Entra ID",
    executionRoleArn="arn:aws:iam::<account>:role/EntraConsentPortalRole",
    idpConfig={
        # The PRIMARY IdP provider -- who users sign in as. Must issue JWTs.
        "credentialProviderArn": "arn:aws:bedrock-agentcore:<region>:<account>"
                                 ":token-vault/default/oauth2credentialprovider"
                                 "/entra-portal-idp-provider",
        # openid must be listed, and the Entra scope must be FULLY QUALIFIED.
        "scopes": ["openid", "api://<APP_ID>/access_as_user"],
        # The resource app GUID, not api://<GUID>.
        "audience": "<APP_ID>",
    },
    # What the portal shows connections for. Exactly one gateway, by id or ARN.
    sources=[{"identifier": "<GATEWAY_ID>", "type": "agentcore-gateway"}],
)
```

| Field | Required | Notes |
| :--- | :--- | :--- |
| `name` | yes | 1–50 characters, and **not updatable**. It is also the portal's identity for idempotency: `deploy_portal.py` scans `ListConsentPortals` for this name and reuses a match rather than creating a second portal. |
| `executionRoleArn` | yes | Trusts `bedrock-agentcore.amazonaws.com`. The portal assumes it to read your gateway, its targets and the credential providers, and to call the token operations. |
| `idpConfig.credentialProviderArn` | yes | The **primary IdP** provider — who users log in as — not the outbound provider they connect to. |
| `idpConfig.scopes` | no | Must include `openid`, and with Entra the qualified scope. [Why](#scope-and-audience-alignment). |
| `idpConfig.audience` | no | Omit it and the portal does not pin `aud`. With Entra, set it. |
| `sources` | yes | Exactly one entry, `type: agentcore-gateway`. **Not updatable** either. |
| `description`, `tags`, `clientToken` | no | |

The response carries what the remaining steps need:

```json
{
  "consentPortalId": "entra-consent-portal-a1b2c3d4",
  "consentPortalArn": "arn:aws:bedrock-agentcore:<region>:<account>:consent-portal/...",
  "portalUrl": "entra-consent-portal-a1b2c3d4.consent.bedrock-agentcore.<region>.amazonaws.com",
  "status": "CREATING"
}
```

`status` walks `CREATING` → `ACTIVE`, or `FAILED` with a `statusReason`. Both `portalUrl`
and `statusReason` may come back `null` while `CREATING`, which is why `deploy_portal.py`
polls `GetConsentPortal` until it sees `ACTIVE` **and** a non-empty `portalUrl` — everything
after this is built from that URL:

- `https://<portalUrl>/callback` → the portal Entra app's web redirect URI ([Step 4](#step-4-register-the-portal-callback))
- `https://<portalUrl>/connect/callback` → each target's `defaultReturnUrl` (the GitHub target step)

> [!NOTE]
> `portalUrl` comes back as a bare host with **no scheme**. Prepend `https://` yourself. The
> scripts store the bare host in `PORTAL_URL` and the two derived URLs with their scheme
> already attached, so nothing downstream has to get this right twice.

### Scope and audience alignment

This is the one section most likely to cost you an afternoon, so here is the concept rather
than a list of variables.

You are configuring **the same permission in two places for two different readers**, and
each reader wants it spelled differently.

The **gateway** reads scopes out of a token that has already been issued. Entra writes the
short name into the `scp` claim, so the gateway's `allowedScopes` holds `access_as_user` —
a plain string comparison against a claim.

The **portal** is on the other side of that: it *requests* a token. It sends a `scope`
parameter to Entra's `/authorize` endpoint, and Entra only recognises the fully qualified
form, `api://<APP_ID>/access_as_user`. Send the short name there and the login fails.

When you created the gateway you set `advertisedScopeMapping` precisely to reconcile these
two spellings — but it reconciles them **only for MCP clients**, which discover the qualified
form by reading the gateway's RFC 9728 protected-resource metadata or the `scope=` hint in a
`401`.

> [!IMPORTANT]
> The portal is not an MCP client. It never fetches that metadata document, so
> `advertisedScopeMapping` cannot help it. The portal has to be told the qualified scope
> directly. That is why this is a real configuration step and not a restatement of the
> gateway setup.

Two more rules, both of which fail late and confusingly if broken:

- **`openid` must be listed.** A portal always requests `openid` on top of whatever you
  configure, and *every* scope it requests — `openid` included — must be defined and
  permitted on the IdP application, or authorization fails with `invalid_scope`. The scripts
  prepend `openid` if you leave it out, so you cannot forget it by accident.
- **`audience` is the bare GUID.** With `requestedAccessTokenVersion: 2`, Entra sets `aud`
  to the application id. `api://<GUID>` is the *identifier URI*, a different string, and
  pinning it means every token fails validation.

### `grantType` decides what appears as a connection

- `AUTHORIZATION_CODE` — three-legged, per-user consent. **Requires `defaultReturnUrl`.**
  Appears on the Connections page.
- `CLIENT_CREDENTIALS` — two-legged, machine-to-machine. There is no per-user consent to
  gather, so it never appears.

A portal whose gateway has no `AUTHORIZATION_CODE` targets shows an empty list. After this
guide, that is exactly what you will have.

## Prerequisites

From the
[consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html),
plus what this walkthrough needs specifically:

**A JWT gateway.** [`gateway.md`](gateway.md) leaves you with a `CUSTOM_JWT`
gateway and `GATEWAY_ID` in `scripts/.env`. A gateway with IAM or API-key inbound auth
cannot host a portal, and `CreateConsentPortal` validates at create time that the gateway's
authorizer and the portal's IdP reference the **same OIDC issuer** — a portal pointed at a
different tenant is rejected then and there, not at login.

**A primary IdP that issues JWT access tokens.** This is a hard constraint on the vendor,
not a preference. The OAuth2-only vendors — `GithubOauth2`, `SlackOauth2`,
`SalesforceOauth2`, `AtlasianOauth2`, `LinkedinOauth2` — issue opaque tokens and are
**rejected as the primary IdP**. They remain perfectly valid *outbound* providers, which is
what GitHub is used for as the outbound target.

**An OIDC web application in that IdP,** authorization-code grant, **with a client secret**,
and with no real redirect URI yet — `<portalUrl>` does not exist until you create the
portal. With Entra this **is the gateway's resource app**, not a new registration; Step 1
adds the secret and (in Step 4) the redirect to it. [Why](#step-1-prepare-the-resource-app-as-the-portals-login-client).

**At least one active test user** who can sign in to that application. For a single-tenant
registration, membership in the tenant is enough.

**Tooling and permissions:**

- Azure CLI (`az`), signed in, with permission to create app registrations **and grant admin
  consent**
- AWS credentials with permission to create IAM roles, OAuth2 credential providers and
  consent portals
- `uv`, Python ≥ 3.12, and boto3/botocore **≥ 1.43.88** — floored in
  [`../pyproject.toml`](../pyproject.toml). On an older SDK these calls fail with
  `'BedrockAgentCoreControlPlaneFrontingLayer' object has no attribute 'create_consent_portal'`,
  which reads like a missing feature rather than a stale SDK.

All commands run from `authorization-code-flow/`, the parent of this directory.

## Step 1: Prepare the resource app as the portal's login client

The portal signs users **in** through the *same* Entra app that represents your gateway —
the resource app `$ENTRA_RESOURCE` you created in [`gateway.md`](gateway.md) Step 1. It is
**not** a new registration. There are only **two** apps in this whole flow: this shared
resource/login app, and the GitHub outbound provider.

> [!IMPORTANT]
> Under Microsoft Entra, the `sub` claim is a **pairwise identifier (PPID), keyed on the app
> a token is issued for** — not a stable per-user value. If the portal signed users in
> through a *separate* client app, that app's login `sub` would differ from the `sub` the
> gateway resolves for the same user (which is pairwise for the resource app). AgentCore
> would then bind consent under one identity and look it up under the other, and every tool
> invocation would re-prompt for authorization instead of using the stored consent. Signing
> in through the resource app itself makes the two `sub` values identical.

The app already exists and already has a service principal (from `gateway.md` Step 1). To
act as a confidential login client it needs two more things: a client secret, and — so the
server-side login needs no per-user consent screen — its own `access_as_user` scope granted
and admin-consented **to itself**.

```bash
PORTAL_SECRET=$(az ad app credential reset \
  --id "$ENTRA_RESOURCE" --years 1 \
  --query password -o tsv)

SCOPE_ID=$(az ad app show --id "$ENTRA_RESOURCE" \
  --query "api.oauth2PermissionScopes[?value=='access_as_user'].id | [0]" -o tsv)

az ad app permission add --id "$ENTRA_RESOURCE" \
  --api "$ENTRA_RESOURCE" --api-permissions "$SCOPE_ID=Scope"
az ad app permission grant --id "$ENTRA_RESOURCE" \
  --api "$ENTRA_RESOURCE" --scope "access_as_user"
az ad app permission admin-consent --id "$ENTRA_RESOURCE"
```

All three permission commands are needed — without the explicit `grant`, the consent is
*declared* but never *activated*, and tokens fail with a consent-required error even though
`admin-consent` reported success.

> [!NOTE]
> `--is-fallback-public-client` is deliberately omitted. The app holds a secret and
> exchanges the authorization code server-side, so a public registration would be both wrong
> and a security downgrade — anyone could then present its `client_id` without the secret.

No redirect URI is set yet. You will register it in Step 4, once `<portalUrl>` exists.

## Step 2: Export the environment

`ENTRA_RESOURCE` and `ENTRA_DISCOVERY_URL` are already exported if you came straight from
the gateway setup; re-export them in a new shell.

```bash
export ENTRA_ALLOWED_SCOPES="api://${ENTRA_RESOURCE}/access_as_user"
export PORTAL_CLIENT_ID="$ENTRA_RESOURCE"
export PORTAL_CLIENT_SECRET="$PORTAL_SECRET"
```

| Variable | Notes |
| :--- | :--- |
| `ENTRA_ALLOWED_SCOPES` | Space-separated and **fully qualified**. `openid` is prepended for you if absent. |
| `ENTRA_RESOURCE` | Becomes `idpConfig.audience`: the resource app **GUID**, not `api://<GUID>`. |
| `ENTRA_DISCOVERY_URL` | Must contain `/v2.0/`; rejected up front otherwise. |
| `PORTAL_CLIENT_ID` | The login client, which **is** the resource app — so this equals `$ENTRA_RESOURCE`. See [Step 1](#step-1-prepare-the-resource-app-as-the-portals-login-client) for why. |
| `PORTAL_CLIENT_SECRET` | The secret you reset onto that app in Step 1. If unset the script prompts via `getpass` rather than taking it on the command line, and it is never printed. |
| `GATEWAY_ID` | Read from `scripts/.env`, written when you created the gateway. Becomes `sources[0].identifier`. |

Optional overrides, read by both the create and the cleanup script so setting one is enough:
`PORTAL_NAME` (default `entra-consent-portal`), `IDP_PROVIDER_NAME`, `PORTAL_ROLE_NAME`.

## Step 3: Create the portal

```bash
uv run python scripts/deploy_portal.py --entra
```

Three resources, each idempotent, so the script is safe to re-run:

1. the **primary IdP credential provider** — `CustomOauth2` over your Entra discovery URL.
   Not `MicrosoftOauth2`: that vendor's config takes a `tenantId` and leaves nowhere to state
   a discovery URL, and with Entra the discovery URL is exactly the thing that has to be
   right.
2. the **execution role** the portal assumes.
3. the **portal**, polled until `status` is `ACTIVE` and `portalUrl` is present.

> [!NOTE]
> **If a portal named `$PORTAL_NAME` already exists, the script reuses it,** writes the same
> variables a fresh create would, and exits 0. It never creates a second portal under a
> different id. The role's inline policy is re-applied on every run, so drift is repaired
> rather than ignored.

Saved to `scripts/.env`: `PORTAL_ID`, `PORTAL_ARN`, `PORTAL_URL`, `PORTAL_CALLBACK_URL`,
`PORTAL_CONNECT_RETURN_URL`, `IDP_PROVIDER_ARN`, `EXECUTION_ROLE_ARN`.

> [!NOTE]
> Like the rest of `scripts/`, this takes a required profile flag — but from
> [`scripts/idps/`](../scripts/idps/), not `scripts/servers/`. A portal belongs to the
> gateway and its identity provider, not to any one MCP server, so `--github` is an error
> here and `--entra` is an error on the target scripts. The two namespaces deliberately do
> not cross: these deletes are not recoverable.

### The execution role

The portal assumes a role in **your** account to read your resources. The trust policy names
the public service principal `bedrock-agentcore.amazonaws.com` and constrains it against the
[confused deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html):

```json
{
  "Effect": "Allow",
  "Principal": { "Service": "bedrock-agentcore.amazonaws.com" },
  "Action": "sts:AssumeRole",
  "Condition": {
    "StringEquals": { "aws:SourceAccount": "<account>" },
    "ArnLike": { "aws:SourceArn": "arn:aws:bedrock-agentcore:<region>:<account>:consent-portal/<id>" }
  }
}
```

There is a chicken-and-egg problem in that `aws:SourceArn`: you cannot know the portal ARN
before the portal exists, and you cannot create the portal without a role. The AWS docs
resolve it by having you create the role account-scoped and then tighten it by hand
afterwards. `deploy_portal.py` does the same thing without the manual pass — it creates the
role with a wildcard `consent-portal/*` ARN, and re-puts the trust policy pinned to the
exact ARN as soon as `CreateConsentPortal` returns it.

The permissions policy grants, and nothing more:

| Actions | On |
| :--- | :--- |
| `GetGateway`, `GetGatewayTarget`, `ListGatewayTargets` | your gateway's ARN only |
| `GetOauth2CredentialProvider`, `ListOauth2CredentialProviders` | `token-vault/default` and its `oauth2credentialprovider/*` |
| `CompleteResourceTokenAuth`, `GetResourceOauth2Token`, `GetWorkloadAccessTokenForJWT` | `*` |
| `secretsmanager:GetSecretValue` | `secret:bedrock-agentcore-identity!default/oauth2/*`, further conditioned on the `aws:secretsmanager:owningService` resource tag |

`UpdateConsentPortal` can change `description`, `idpConfig` and the execution role; updating
`idpConfig` walks the portal `UPDATING` → `ACTIVE` or `UPDATE_FAILED`. It cannot change
`name` or `sources` — the gateway a portal fronts is fixed for the portal's life, so
switching gateways means delete and recreate. The same operations are available in the
console under **Identity → Consent portals**, where the delete dialog asks you to type
`confirm`.

## Step 4: Register the portal callback

`deploy_portal.py` prints this with the URL filled in. Run it:

```bash
az ad app update --id "$PORTAL_CLIENT_ID" \
  --web-redirect-uris "$(grep '^PORTAL_CALLBACK_URL=' scripts/.env | cut -d= -f2)"
```

> [!IMPORTANT]
> Enter it **exactly**, with **no trailing slash**. A trailing slash makes the IdP treat the
> callback as unregistered, and the resulting failure surfaces as a generic login error.

> [!IMPORTANT]
> `--web-redirect-uris` **replaces** the whole `web.redirectUris` array rather than appending
> to it. This app is the shared resource app, so it may already carry other web redirects
> (for example a credential provider's vended callback). List **every** URI you need in this
> one call, or you will silently drop the others. It must be a **web** redirect, not a
> public-client one. (The gateway identifier URIs live in `identifierUris`, a separate array
> that this command does not touch.)

## Step 5: Verify the login leg

```bash
open "https://$(grep '^PORTAL_URL=' scripts/.env | cut -d= -f2)"
```

Expect, in order:

1. The SPA shell loads — `GET /` always returns **200**, whether or not you have a session.
2. **Sign in** redirects you to Microsoft. Authenticate with your own Entra account in the
   tenant.
3. You land back on the portal with a session cookie set.
4. The **Connections** page is **empty**.

That empty page is the success condition for this guide: the gateway has no
`AUTHORIZATION_CODE` targets yet, so there is nothing to consent to. If you got a session
cookie, the whole identity chain — Entra app, admin consent, discovery URL, scopes,
audience, gateway authorizer, callback URL — is correct.

You can also confirm the two ends independently:

```bash
aws bedrock-agentcore-control get-consent-portal \
  --consent-portal-identifier "$(grep '^PORTAL_ID=' scripts/.env | cut -d= -f2)" \
  --query '{status:status,url:portalUrl}'

az ad app show --id "$PORTAL_CLIENT_ID" --query web.redirectUris
```

## Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| `object has no attribute 'create_consent_portal'` | boto3/botocore older than 1.43.88. `uv sync` to pick up the floor in `pyproject.toml`. |
| `/?error=login_failed` right after Step 4 | Redirect URI mismatch — most often a trailing slash. Compare `az ad app show --id "$PORTAL_CLIENT_ID" --query web.redirectUris` against `PORTAL_CALLBACK_URL`. |
| `/?error=login_failed` with unqualified scopes | Entra rejects the short form on `/authorize`. Use `api://<ENTRA_RESOURCE>/access_as_user`. |
| `invalid_scope` from the IdP | A requested scope is not defined or not permitted on the IdP app — including `openid`. |
| `/?error=login_rejected` | The gateway's authorizer returned a deny for this user. |
| `/?error=login_unavailable` | The authorization decision could not be reached at all. |
| `/?error=session_expired` | The ID token expired; expiry is enforced server-side. Sign in again. |
| A consent-required error although `admin-consent` succeeded | The explicit `az ad app permission grant` in Step 1 was skipped: consent was declared but never activated. |
| `CreateConsentPortal` rejects the IdP provider | The provider issues opaque tokens. Use a JWT-issuing vendor. |
| `CreateConsentPortal` rejects the gateway | Its inbound auth is not JWT, or its issuer does not match the IdP provider's. |
| Portal create retries with "execution role not assumable yet" | Normal IAM eventual consistency; the script retries for you. |
| `ERROR: ENTRA_DISCOVERY_URL must contain /v2.0/` | The guard in `scripts/idp_config.py`. This one fails up front rather than at login. |
| `insufficient_scope`, or an `iss` mismatch in the gateway logs | Same cause, if the check was bypassed: the v1.0 document advertises an `sts.windows.net` issuer that v2.0 tokens do not carry. |
| Login stops working after about a year | The resource app's client secret expired (`--years 1` in Step 1). Reset it on `$ENTRA_RESOURCE` and re-run Step 3. |
| **400** on every request | The `Host` header is not a valid FQDN. `localhost` and bare hostnames will not work; use `portalUrl` as returned. |

Authorization **fails closed** by design, and the error surface is deliberately not a
diagnostic: a deny and an unreachable authorizer are indistinguishable from outside, and a
portal that does not exist responds the same as one that exists but is not servable. That is
not a bug — read your configuration, not the error string.

## Security considerations

- **Tokens stay server-side.** The BFF pattern means the browser never receives an access
  token or an ID token, and nothing is kept in `localStorage` or `sessionStorage`.
- **Session cookies** carry the `__Host-` prefix (`Secure`, `Path=/`, no `Domain`), are
  `HttpOnly`, and are encrypted with a KMS-backed key.
- **`SameSite=Lax` is deliberate,** not an oversight: the outbound provider returns the user
  to `/connect/callback` by a top-level cross-site navigation whose handler must read the
  session cookie server-side.
- **State-changing requests** must carry `X-Requested-With: XMLHttpRequest` and an `Origin`
  matching the browser-visible scheme and host. There is no CSRF token; a missing `Origin`
  fails closed, and `Referer` is not accepted as a fallback.
- **Errors are terse on purpose** — RFC 9457 `application/problem+json`, with no stack
  traces, parameter names, IdP error text, claim values, or ARNs.
- **The Entra client secret expires.** Step 1 issues it with `--years 1` and Entra will not
  show it again after creation. A long-lived deployment needs a rotation plan.
- **Rotate this walkthrough's credentials.** Secrets are read from the environment and the
  portal's ids land in `scripts/.env`, which is gitignored but plaintext. Treat this as a
  sandbox, not a template for production secret handling.

## Next

Attach an MCP server target — it puts a row on the Connections page. Pick either (or both, in any order; the gateway and portal are shared and idempotent):

| Target | Guide |
| :--- | :--- |
| GitHub MCP server | [Set up the GitHub target](../github/portal.md#step-3-set-up-the-github-mcp-server-target) |
| Atlassian Remote MCP server (Jira + Confluence) | [Set up the Atlassian target](../atlassian/README.md#step-3-set-up-the-atlassian-mcp-server-target) |
| Slack MCP server | [Set up the Slack target](../slack/README.md#step-3-set-up-the-slack-mcp-server-target) |


## Cleanup

Delete any targets on the gateway first — each target's own cleanup — then:

```bash
uv run python scripts/cleanup_portal.py --entra
```

It deletes the portal, **waits for the delete to finish**, and only then removes the IdP
credential provider and the execution role. The wait is not cosmetic: the portal references
both, and deleting them from under a still-`DELETING` portal is how you get a stuck delete.
If `scripts/.env` was lost, the script re-finds the portal by name.

Left alone on purpose:

- the **gateway** — [`cleanup_gateway_entra.py --entra`](gateway.md#cleanup)
- the Entra **resource app** and its `access_as_user` scope — this is **also** the portal's
  login client, and deleting it breaks the gateway's authorizer, the portal's login, and
  every other client of that scope. Do not delete it here; [`gateway.md` cleanup](gateway.md#cleanup)
  covers it once you are done with the gateway. The client secret you reset onto it in Step 1
  can be left in place or removed with `az ad app credential delete`.

## Documentation

- [Consent portal overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
- [Consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html)
- [Consent portal execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-execution-role.html)
- [OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html)
- [Concepts and diagrams](../README.md) — the two target-creation methods and session binding
