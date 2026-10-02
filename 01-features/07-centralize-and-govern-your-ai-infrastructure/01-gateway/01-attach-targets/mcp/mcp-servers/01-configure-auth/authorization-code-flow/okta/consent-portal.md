# Create an AgentCore Consent Portal using Okta

**AgentCore Consent Portal** is a hosted, AWS-managed web portal that authenticates your end
users against your OIDC identity provider and gathers their consent before an agent reaches
a downstream resource on their behalf. Each portal attaches to exactly one AgentCore
gateway, reads that gateway's targets, and shows the user one row per outbound provider
those targets depend on — with **Connect**, **Reconnect** and **Disconnect** actions.

![demo](./images/demo-portal.gif)

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
    name="okta-consent-portal",
    description="Consent portal backed by Okta",
    executionRoleArn="arn:aws:iam::<account>:role/OktaConsentPortalRole",
    idpConfig={
        # The PRIMARY IdP provider -- who users sign in as. Must issue JWTs.
        "credentialProviderArn": "arn:aws:bedrock-agentcore:<region>:<account>"
                                 ":token-vault/default/oauth2credentialprovider"
                                 "/okta-portal-idp-provider",
        # openid must be listed; the Okta custom scope is the SHORT name.
        "scopes": ["openid", "access_as_user"],
        # The Custom Authorization Server's audiences value.
        "audience": "api://agentcore-gateway",
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
| `idpConfig.scopes` | no | Must include `openid`. With Okta the custom scope is the plain **short** name. [Why](#scope-and-audience-alignment). |
| `idpConfig.audience` | no | Omit it and the portal does not pin `aud`. With Okta, set it to the Custom Authorization Server's `audiences` value. |
| `sources` | yes | Exactly one entry, `type: agentcore-gateway`. **Not updatable** either. |
| `description`, `tags`, `clientToken` | no | |

The response carries what the remaining steps need:

```json
{
  "consentPortalId": "okta-consent-portal-a1b2c3d4",
  "consentPortalArn": "arn:aws:bedrock-agentcore:<region>:<account>:consent-portal/...",
  "portalUrl": "okta-consent-portal-a1b2c3d4.consent.bedrock-agentcore.<region>.amazonaws.com",
  "status": "CREATING"
}
```

`status` walks `CREATING` → `ACTIVE`, or `FAILED` with a `statusReason`. Both `portalUrl`
and `statusReason` may come back `null` while `CREATING`, which is why `deploy_portal.py`
polls `GetConsentPortal` until it sees `ACTIVE` **and** a non-empty `portalUrl` — everything
after this is built from that URL:

- `https://<portalUrl>/callback` → the portal app's Sign-in redirect URI ([Step 4](#step-4-register-the-portal-callback))
- `https://<portalUrl>/connect/callback` → each target's `defaultReturnUrl` (the target step)

> [!NOTE]
> `portalUrl` comes back as a bare host with **no scheme**. Prepend `https://` yourself. The
> scripts store the bare host in `PORTAL_URL` and the two derived URLs with their scheme
> already attached, so nothing downstream has to get this right twice.

### Scope and audience alignment

With Okta there is only **one spelling** of the scope, which keeps this simple — but two rules
still fail late and confusingly if broken.

The **gateway** reads scopes out of an issued token: Okta writes the short name into `scp`, so
the gateway's `allowedScopes` holds `access_as_user`. The **portal** *requests* a token: it
sends a `scope` parameter to Okta's `/authorize`, and Okta accepts that **same short name**.
So `idpConfig.scopes` carries `access_as_user` unchanged — no fully-qualified form, no
`advertisedScopeMapping` to reconcile (see [`gateway.md`](gateway.md#one-scope-one-spelling)).

Two rules that still apply:

- **`openid` must be listed.** A portal always requests `openid` on top of whatever you
  configure, and *every* scope it requests — `openid` included — must be defined and permitted
  on the Custom Authorization Server's access-policy rule, or authorization fails with a policy
  error. The scripts prepend `openid` if you leave it out. This is why
  [`gateway.md`](gateway.md#step-3-add-an-access-policy-and-rule) Step 3 lists `openid` in the
  rule's `scopes.include`.
- **`audience` is the AS `audiences` value.** Okta stamps `aud` from the Custom Authorization
  Server, so `idpConfig.audience` must be `api://agentcore-gateway` (or whatever you set in
  Step 1), never the gateway URL.

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

**A JWT gateway.** [`gateway.md`](gateway.md) leaves you with a `CUSTOM_JWT` gateway and
`GATEWAY_ID` in `scripts/.env`. A gateway with IAM or API-key inbound auth cannot host a
portal, and `CreateConsentPortal` validates at create time that the gateway's authorizer and
the portal's IdP reference the **same OIDC issuer** — a portal pointed at a different Custom
Authorization Server is rejected then and there, not at login.

**A primary IdP that issues JWT access tokens.** Okta's Custom Authorization Server issues JWT
access tokens, so it qualifies. (The OAuth2-only vendors — `GithubOauth2`, `SlackOauth2`,
`SalesforceOauth2`, `AtlasianOauth2`, `LinkedinOauth2` — issue opaque tokens and are rejected
as the primary IdP; they remain valid *outbound* providers, which is what GitHub is used for.)

**A confidential OIDC web application in Okta,** authorization-code grant, **with a client
secret**, created in [Step 1](#step-1-create-the-portals-confidential-login-client) below.
This is a **genuinely separate app** from the gateway's resource — see that step for why Okta
can safely use a dedicated portal client.

**At least one active test user** assigned to that portal app who can sign in.

**Tooling and permissions:**

- An Okta API token (SSWS) with permission to manage apps (`Security → API → Tokens`)
- AWS credentials with permission to create IAM roles, OAuth2 credential providers and
  consent portals
- `uv`, Python ≥ 3.12, and boto3/botocore **≥ 1.43.88** — floored in
  [`../pyproject.toml`](../pyproject.toml). On an older SDK these calls fail with
  `'BedrockAgentCoreControlPlaneFrontingLayer' object has no attribute 'create_consent_portal'`,
  which reads like a missing feature rather than a stale SDK.

All commands run from `authorization-code-flow/`, the parent of this directory. `OKTA_DOMAIN`
and `OKTA_TOKEN` are exported from [`gateway.md`](gateway.md); re-export them in a new shell.

## Step 1: Create the portal's confidential login client

The portal signs users **in** through a dedicated confidential Okta app — created here,
**separate** from both the Custom Authorization Server and the optional public MCP-client app.

> [!IMPORTANT]
> **Okta's `sub` is a stable per-user identifier** — the same user gets the same `sub`
> regardless of which client app issued the token. That is what lets the portal use a genuinely
> separate confidential login client: consent binds under the same identity the gateway later
> resolves for that user, so it is bound once and found on every subsequent invocation. This is
> why the portal login client here is its own app, distinct from the gateway resource.

Create a **web** (confidential, secret-bearing) OIDC app. Okta **requires at least one
`redirect_uris` entry** whenever `grant_types` includes `authorization_code`, but the real
callback (`https://<portalUrl>/callback`) does not exist until you create the portal in Step 3.
Register a throwaway placeholder now; [Step 4](#step-4-register-the-portal-callback) replaces it
with the real one.

```bash
PORTAL_APP=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/apps" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "oidc_client",
    "label": "agentcore-consent-portal",
    "signOnMode": "OPENID_CONNECT",
    "credentials": { "oauthClient": { "token_endpoint_auth_method": "client_secret_basic" } },
    "settings": { "oauthClient": {
      "application_type": "web",
      "grant_types": ["authorization_code"],
      "response_types": ["code"],
      "redirect_uris": ["https://localhost/placeholder"]
    }}
  }')

# App id (0oa...) addresses the /apps/<id> endpoint in Step 4; client_id/secret are the
# OAuth credentials the portal uses. They are distinct values -- keep them apart.
PORTAL_APP_ID=$(echo "$PORTAL_APP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
PORTAL_CLIENT_ID=$(echo "$PORTAL_APP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["credentials"]["oauthClient"]["client_id"])')
PORTAL_SECRET=$(echo "$PORTAL_APP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["credentials"]["oauthClient"]["client_secret"])')

echo "Portal app id:    $PORTAL_APP_ID"
echo "Portal client id: $PORTAL_CLIENT_ID"
```

> [!IMPORTANT]
> `token_endpoint_auth_method: client_secret_basic` + `application_type: web` makes this a
> **confidential** client. The portal holds the secret and exchanges the authorization code
> server-side; a public (PKCE, no-secret) app would be both wrong for the BFF pattern and a
> security downgrade. This is the opposite of the *public* MCP-client app in
> [`gateway.md`](gateway.md#step-6-optional-register-a-public-mcp-client-app) Step 6.

> [!IMPORTANT]
> **Assign your test users to this app.** Okta issues no token to a user who is not assigned,
> and sign-in fails with `User is not assigned to the client application`. Assign every user who
> will sign in to the portal (in the Console: *Applications → agentcore-consent-portal →
> Assignments → Assign*).

## Step 2: Export the environment

`OKTA_DISCOVERY_URL` and `OKTA_AUDIENCE` are already exported if you came straight from the
gateway setup; re-export them in a new shell.

```bash
export OKTA_ALLOWED_SCOPES="access_as_user"
export PORTAL_CLIENT_ID="$PORTAL_CLIENT_ID"
export PORTAL_CLIENT_SECRET="$PORTAL_SECRET"
```

| Variable | Notes |
| :--- | :--- |
| `OKTA_ALLOWED_SCOPES` | The **short** scope name; `openid` is prepended for you if absent. No fully-qualified form. |
| `OKTA_AUDIENCE` | Becomes `idpConfig.audience`: the Custom Authorization Server's `audiences` value (`api://agentcore-gateway`), not the gateway URL. |
| `OKTA_DISCOVERY_URL` | Must contain `/oauth2/<AS_ID>/`; rejected up front otherwise. |
| `PORTAL_CLIENT_ID` | The **separate** confidential portal app from Step 1 — **not** the resource/AS and not the MCP-client app. Okta's stable `sub` is what lets it be separate. |
| `PORTAL_CLIENT_SECRET` | The secret Okta returned in Step 1. If unset the script prompts via `getpass` rather than taking it on the command line, and it is never printed. |
| `GATEWAY_ID` | Read from `scripts/.env`, written when you created the gateway. Becomes `sources[0].identifier`. |

Optional overrides, read by both the create and the cleanup script so setting one is enough:
`PORTAL_NAME` (default `okta-consent-portal`), `IDP_PROVIDER_NAME`, `PORTAL_ROLE_NAME`.

## Step 3: Create the portal

```bash
uv run python scripts/deploy_portal.py --okta
```

Three resources, each idempotent, so the script is safe to re-run:

1. the **primary IdP credential provider** — `CustomOauth2` over your Okta discovery URL.
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
> here and `--okta` is an error on the target scripts. The two namespaces deliberately do
> not cross: these deletes are not recoverable.

### The execution role

The portal assumes a role in **your** account to read your resources. The trust policy names
the public service principal `bedrock-agentcore.amazonaws.com` and constrains it against the
[confused deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)
with `aws:SourceAccount` and an `aws:SourceArn` pinned to the portal. There is a
chicken-and-egg problem — you cannot know the portal ARN before the portal exists — so
`deploy_portal.py` creates the role with a wildcard `consent-portal/*` ARN and re-puts the
trust policy pinned to the exact ARN as soon as `CreateConsentPortal` returns it. The
permissions policy grants only: read your gateway and its targets; read credential providers
in `token-vault/default`; the three token operations; and the identity service's own OAuth
secrets.

`UpdateConsentPortal` can change `description`, `idpConfig` and the execution role, but not
`name` or `sources` — the gateway a portal fronts is fixed for its life. The same operations
are in the console under **Identity → Consent portals**.

## Step 4: Register the portal callback

`deploy_portal.py` prints this with the URL filled in. Add
`https://<portalUrl>/callback` to the portal app's **Sign-in redirect URIs** — in the Console
(*Applications → agentcore-consent-portal → General → Sign-in redirect URIs → Edit*), or by
`PUT`ting the full app object back with `redirect_uris` updated:

```bash
CALLBACK=$(grep '^PORTAL_CALLBACK_URL=' scripts/.env | cut -d= -f2)

curl -s "https://${OKTA_DOMAIN}/api/v1/apps/${PORTAL_APP_ID}" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
| CALLBACK="$CALLBACK" python3 -c '
import sys, json, os
app = json.load(sys.stdin)
# Replace the throwaway placeholder from Step 1 with the real portal callback.
app["settings"]["oauthClient"]["redirect_uris"] = [os.environ["CALLBACK"]]
json.dump(app, sys.stdout)
' \
| curl -s -X PUT "https://${OKTA_DOMAIN}/api/v1/apps/${PORTAL_APP_ID}" \
    -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
    -H "Content-Type: application/json" -d @- \
| python3 -c 'import sys,json; print("redirect_uris:", json.load(sys.stdin)["settings"]["oauthClient"]["redirect_uris"])'
```

> [!IMPORTANT]
> Enter it **exactly**, with **no trailing slash**. A trailing slash makes Okta treat the
> callback as unregistered, and the resulting failure surfaces as a generic login error. Okta
> full-object `PUT`s replace the whole app, which is why the snippet fetches the app, swaps in
> the real callback, and puts the whole object back rather than sending a bare array.

## Step 5: Verify the login leg

```bash
open "https://$(grep '^PORTAL_URL=' scripts/.env | cut -d= -f2)"
```

Expect, in order:

1. The SPA shell loads — `GET /` always returns **200**, whether or not you have a session.
2. **Sign in** redirects you to Okta. Authenticate as a user assigned to the portal app.
3. You land back on the portal with a session cookie set.
4. The **Connections** page is **empty**.

That empty page is the success condition for this guide: the gateway has no
`AUTHORIZATION_CODE` targets yet, so there is nothing to consent to. If you got a session
cookie, the whole identity chain — Okta portal app, user assignment, discovery URL, scopes,
audience, gateway authorizer, callback URL — is correct.

## Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| `object has no attribute 'create_consent_portal'` | boto3/botocore older than 1.43.88. `uv sync` to pick up the floor in `pyproject.toml`. |
| `/?error=login_failed` right after Step 4 | Redirect URI mismatch — most often a trailing slash. Compare the app's `redirect_uris` against `PORTAL_CALLBACK_URL`. |
| `access_denied` / `User is not assigned to the client application` | The signed-in user is not assigned to the portal app (Step 1). Assign them. |
| `Policy evaluation failed` at `/authorize` | The Custom Authorization Server's access-policy rule is missing a requested scope — most often `openid`. Add it in [`gateway.md`](gateway.md#step-3-add-an-access-policy-and-rule) Step 3. |
| `CreateConsentPortal` rejects the IdP provider | The provider issues opaque tokens. Okta's Custom Authorization Server issues JWTs; make sure the discovery URL is the Custom AS, not the org one. |
| `CreateConsentPortal` rejects the gateway | Its inbound auth is not JWT, or its issuer does not match the IdP provider's. |
| Portal create retries with "execution role not assumable yet" | Normal IAM eventual consistency; the script retries for you. |
| `ERROR: OKTA_DISCOVERY_URL must contain /oauth2/` | The guard in `scripts/idp_config.py`. Export the Custom Authorization Server's discovery URL. |
| **400** on every request | The `Host` header is not a valid FQDN. Use `portalUrl` as returned. |

Authorization **fails closed** by design, and the error surface is deliberately not a
diagnostic: a deny and an unreachable authorizer are indistinguishable from outside. Read your
configuration, not the error string.

## Security considerations

- **Tokens stay server-side.** The BFF pattern means the browser never receives an access
  token or an ID token, and nothing is kept in `localStorage`.
- **Session cookies** carry the `__Host-` prefix, are `HttpOnly`, and are encrypted with a
  KMS-backed key. `SameSite=Lax` is deliberate: the outbound provider returns the user by a
  top-level cross-site navigation whose handler reads the cookie server-side.
- **State-changing requests** must carry `X-Requested-With: XMLHttpRequest` and a matching
  `Origin`; a missing `Origin` fails closed and `Referer` is not accepted as a fallback.
- **The portal client secret** is confidential. Okta will not show it again after creation; a
  long-lived deployment needs a rotation plan.
- **Rotate this walkthrough's credentials.** Secrets are read from the environment and the
  portal's ids land in `scripts/.env`, which is gitignored but plaintext. Treat this as a
  sandbox, not a template for production secret handling.
- **The access policy is your boundary.** Access is governed by the Custom Authorization
  Server's access-policy rule plus app assignment — tighten `conditions.clients.include` and
  `conditions.people.groups.include` for anything real.

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
uv run python scripts/cleanup_portal.py --okta
```

It deletes the portal, **waits for the delete to finish**, and only then removes the IdP
credential provider and the execution role. The wait is not cosmetic: the portal references
both, and deleting them from under a still-`DELETING` portal is how you get a stuck delete.
If `scripts/.env` was lost, the script re-finds the portal by name.

Left alone on purpose:

- the **gateway** — [`cleanup_gateway_entra.py --okta`](gateway.md#cleanup)
- your **Okta apps and Custom Authorization Server** — the AS hosts the gateway's audience, the
  portal login client and the optional MCP-client app are yours. `cleanup_portal.py` prints the
  `curl` DELETE to remove an app once you are done (deactivate it first).

## Documentation

- [Consent portal overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
- [Consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html)
- [Consent portal execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-execution-role.html)
- [OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html)
- [Concepts and diagrams](../README.md) — the two target-creation methods and session binding
