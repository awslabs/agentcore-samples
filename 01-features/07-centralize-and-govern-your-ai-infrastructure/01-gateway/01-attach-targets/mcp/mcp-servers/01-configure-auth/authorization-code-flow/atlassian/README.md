# Connecting the Atlassian Remote MCP Server to AgentCore gateway - AgentCore consent portal

[Atlassian's Remote MCP server](https://mcp.atlassian.com) exposes Jira and Confluence — issue and page search, reads and writes, comments, user lookup — as MCP tools, and it requires the OAuth 2.0 authorization code flow for authentication. This tutorial attaches it to an Amazon Bedrock AgentCore Gateway for **end-user consent**, using a hosted, AWS-managed [**AgentCore Consent Portal**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html) that authenticates each end user to your identity provider, gathers their consent, and calls [`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html) for you. There is no callback server to run and no consent UI to build — the OAuth flow stays server-side and the browser never holds a token.

See [Who calls `CompleteResourceTokenAuth`](../README.md#who-calls-completeresourcetokenauth-your-own-dashboard-or-a-consent-portal) for the concept in full.


Atlassian differs from a hand-created OAuth app in one important way: its authorization server **only supports [Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591) (DCR)**. You do not create an OAuth app in a settings page and copy a secret. Instead you create the credential provider with placeholder credentials so it vends a callback URL, allowlist that callback domain in Atlassian, register a client at Atlassian's DCR endpoint, and store the returned id/secret back on the provider. The scripts drive all of this.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- `boto3`/`botocore` **≥ 1.43.88** — the first release whose `bedrock-agentcore-control` model carries the consent-portal operations. `uv run` installs this from [`pyproject.toml`](../pyproject.toml); nothing extra to do.
- The [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az`), logged in to a **Microsoft Entra ID** tenant where you can register applications — or the equivalent for whichever IdP you choose in Steps 1–2.
- A Microsoft Entra ID tenant with **at least one active test user** who can sign in to the portal.
- AWS credentials that can create gateways, IAM roles, OAuth2 credential providers, gateway targets, and consent portals.
- **Atlassian organization admin access** to *Security → Rovo MCP server settings*, so you can allowlist the AgentCore callback domain. **No Atlassian OAuth app is created by hand** — DCR registers the client for you.
- `curl` for the one-line DCR registration call.

All commands run from [`authorization-code-flow/`](../), the parent of this `atlassian/` directory. State passes between steps through `scripts/.env`.

## Step 1: Create the AgentCore gateway with your identity provider

Follow the gateway guide for your identity provider. It ends with a `READY` gateway using `CUSTOM_JWT` inbound auth. **If you already did this for another target, the scripts reuse the existing gateway** — skim it and move on.

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/gateway.md`](../entra/gateway.md) |
| Okta | [`../okta/gateway.md`](../okta/gateway.md) |

## Step 2: Set up the consent portal with your identity provider

Follow the portal guide for the same identity provider. **If you already did this for another target, the scripts reuse the existing portal.**

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/consent-portal.md`](../entra/consent-portal.md) |
| Okta | [`../okta/consent-portal.md`](../okta/consent-portal.md) |

## Step 3: Set up the Atlassian MCP server target

### Step 3.1: Create the credential provider and register a DCR client

Atlassian has no OAuth-app settings page, so this is a three-hop dance the script walks you through. **You do not need any Atlassian credentials yet** — do not export `ATLASSIAN_CLIENT_ID` / `ATLASSIAN_CLIENT_SECRET` before this step.

First create the credential provider with placeholder credentials:

```bash
uv run python scripts/deploy_credential.py --atlassian
```

This creates a `CustomOauth2` credential provider whose `oauthDiscovery.discoveryUrl` is Atlassian's generic `https://auth.atlassian.com/.well-known/openid-configuration`, and prints the `callbackUrl` AgentCore vends for it — something of the form:

```
https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>
```

It then prints the exact next actions, filled in. Follow them in order:

1. **Allowlist the callback domain in Atlassian.** In your Atlassian org admin, go to *Security → Rovo MCP server settings* and add the domain of the vended `callbackUrl` (`bedrock-agentcore.<region>.amazonaws.com`). See [Control Atlassian Rovo MCP server settings](https://support.atlassian.com/security-and-access-policies/docs/control-atlassian-rovo-mcp-server-settings/).

2. **Register a client via DCR.** Run the `curl` command the script printed — it POSTs to Atlassian's DCR endpoint with the vended `callbackUrl` as the `redirect_uris`, requesting `token_endpoint_auth_method: none` (a public client using PKCE):

   ```bash
   curl -s -X POST "https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3/dcr/register" \
     -H "Content-Type: application/json" \
     -d '{
       "client_name": "AgentCore Gateway",
       "redirect_uris": ["https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>"],
       "grant_types": ["authorization_code", "refresh_token"],
       "response_types": ["code"],
       "token_endpoint_auth_method": "none"
     }'
   ```

   Replace the `redirect_uris` value with **your own** vended `callbackUrl` from the previous step (the one above is an example). The response contains a `client_id` and `client_secret`.

3. **Store the DCR credentials on the provider.** Export the returned values and run the update script, which switches the provider's discovery URL to Atlassian's DCR-tenant one and stores the real id/secret:

   ```bash
   export ATLASSIAN_CLIENT_ID="<client_id from the DCR response>"
   export ATLASSIAN_CLIENT_SECRET="<client_secret from the DCR response>"

   uv run python scripts/deploy_credential_update.py --atlassian
   ```

   (`ATLASSIAN_CLIENT_SECRET` may be left unset and typed at the interactive prompt instead, so it stays out of your shell history.)

> [!WARNING]
> Skipping the allowlist in step 1 does **not** fail target creation. The target creates cleanly and stays `READY`. The failure appears only when a real user clicks **Connect** and Atlassian blocks the redirect to a domain it does not recognise — by which point it looks like a portal bug.

> [!NOTE]
> The DCR endpoint and discovery URLs baked into [`../scripts/servers/atlassian.json`](../scripts/servers/atlassian.json) (the tenant segment `VCeDsk8ZHncYF1g234fKtc4lNipbBhu3`) are Atlassian's published values. If Atlassian changes them, update that profile — nothing here is derived from your own account.

### Step 3.2: Create the target (schema upfront)

```bash
uv run python scripts/deploy_target_schema.py --atlassian
```

This attaches [`https://mcp.atlassian.com/v1/mcp/authv2`](https://mcp.atlassian.com/v1/mcp/authv2) with `grantType: AUTHORIZATION_CODE`, the Jira + Confluence scope set, and the tool schema supplied upfront from [`atlassian.json`](atlassian.json). The target is immediately `READY` — no admin authorizes anything at create time. It binds to the Atlassian credential provider **by name**, so it is unaffected by any other target's provider sharing the same `.env`.

Two Atlassian-specific `customParameters` travel with the authorization request, from the profile:

| Parameter | Value | Why |
| :--- | :--- | :--- |
| `aud` | `api.atlassian.com` | Atlassian only issues tokens that work against its product APIs when the audience is set. |
| `resource` | `https://mcp.atlassian.com/v1/mcp/authv2` | **Mandatory.** Without it, Atlassian's consent page does not render the site selector and the **Accept** button stays greyed out. |

The script reads `PORTAL_CONNECT_RETURN_URL` from `scripts/.env` and uses it as `defaultReturnUrl`, so having run Step 2 is all it takes to point the target at the portal. It prints which URL it chose:

```
Return URL: https://<portalUrl>/connect/callback (Consent Portal)
```

> [!IMPORTANT]
> This applies to targets you **already have**, too. A pre-existing `AUTHORIZATION_CODE` target whose `defaultReturnUrl` still points somewhere else will appear on the Connections page and let the user authorize at the provider — and then the consent never binds to their session, because the provider returns them to the wrong place. Update every such target's `defaultReturnUrl` to `<portalUrl>/connect/callback`, not just the new one.

> [!TIP]
> The target's name and the outbound provider's name are **displayed to your end users** on the Connections page. `atlassian-mcp-server-schema-target` is fine for a tutorial; pick something a non-engineer would recognise for anything real.

### Step 3.3: Consent, as a user

```bash
open "https://$(grep '^PORTAL_URL=' scripts/.env | cut -d= -f2)"
```

1. Sign in with Entra, as in Step 2.
2. The **Connections** page now lists an Atlassian row (alongside any other target you attached).
3. **Connect** — the portal hands you Atlassian's authorization URL, you pick your site and approve, and Atlassian returns you to `/connect/callback`. The portal calls `CompleteResourceTokenAuth` with your session, binding that consent to *you*, and redirects to `/?connected=<provider>`.
4. **Disconnect** revokes it; the row returns to its unconnected state.

> [!NOTE]
> A newly created target does not appear immediately — the connections list is cached for up to **5 minutes**. An empty page right after creating the target is expected; wait it out before debugging.

> [!NOTE]
> Authorization URLs and session URIs are valid for **10 minutes**. A consent flow interrupted and resumed later fails in a way that looks like a configuration error. Start over rather than debugging it.

### Step 3.4: Invoke a tool

> [!TIP]
> Use the [AgentCore gateway MCP Inspector](../../../../../../05-community/gateway-mcp-inspector/) to explore Atlassian tools interactively. The Inspector handles the URL-mode elicitation flow (opens the authorization URL, completes session binding) automatically.

> [!IMPORTANT]
> **Register the Inspector's callback URIs under `publicClient` first.** The Inspector redeems
> the auth code over a back channel (its local proxy POSTs to the token endpoint — no browser
> `Origin` header), so it is a **public-client / native** app to Entra. Register its redirect
> URIs on your resource app — `$ENTRA_RESOURCE` from [`../entra/gateway.md`](../entra/gateway.md)
> — in the `publicClient.redirectUris` array, or token redemption fails with `AADSTS9002327`.
> This is a different array from the portal's `web` callback
> ([`../entra/consent-portal.md`](../entra/consent-portal.md) Step 4); `publicClient` and `web`
> coexist.
>
> ```bash
> OBJECT_ID=$(az ad app show --id "$ENTRA_RESOURCE" --query id -o tsv)
> az rest --method PATCH \
>   --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
>   --headers "Content-Type=application/json" \
>   --body '{"publicClient":{"redirectUris":["http://localhost:6274/oauth/callback","http://localhost:6274/oauth/callback/debug"]}}'
> ```
>
> The PATCH **replaces** the whole `publicClient.redirectUris` array, so list every URI you need
> in this one call. Check the current value first with
> `az ad app show --id "$ENTRA_RESOURCE" --query publicClient.redirectUris`.

![demo](./images/demo-portal.gif)

## Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **Accept** button greyed out on the consent page | Missing `resource` parameter, or only identity scopes requested | Ensure the target carries `resource=https://mcp.atlassian.com/v1/mcp/authv2` (it is in the profile's `customParameters`) and that the MCP scopes are requested. |
| MCP tools return "trouble completing this action" | The token is not from a DCR-registered client | Confirm you ran `deploy_credential_update.py --atlassian` with the DCR-returned id/secret, not the placeholders. |
| `403 error_code 1010` on MCP server calls | User-Agent blocked by Cloudflare | Set a custom `User-Agent` header on requests to `mcp.atlassian.com`. |
| Consent page shows "scopes not added to the app" | Requesting a scope a DCR client is not granted | Keep to the scope list in [`atlassian.json`'s profile](../scripts/servers/atlassian.json); add others only if your org grants them. |
| Redirect blocked by Atlassian | Callback domain not allowlisted | Add `bedrock-agentcore.<region>.amazonaws.com` in *Security → Rovo MCP server settings* (Step 3.1). |

The profile ships a conservative Jira + Confluence scope set. Atlassian's Rovo MCP server can grant more (for example Compass and Teamwork Graph scopes) to organizations that enable them; add those to [`../scripts/servers/atlassian.json`](../scripts/servers/atlassian.json) only if your org grants them, since requesting an ungranted scope fails the consent page.

## Cleanup

> [!IMPORTANT]
> Clean up before starting another tutorial in this directory. Leftover credential providers and gateway targets cause name conflicts.

Clean up in reverse order. First the target and its Atlassian credential provider:

```bash
uv run python scripts/cleanup_targets.py --atlassian
```

That deletes this profile's targets and its Atlassian credential provider, and reports any targets it left alone. It is scoped to the profile on purpose: one gateway can front several MCP servers, and deleting your target must not mean deleting someone else's.

Then the portal ([`../entra/consent-portal.md`](../entra/consent-portal.md#cleanup)), then the gateway ([`../entra/gateway.md`](../entra/gateway.md#cleanup)) — **but only if no other target still uses them.** The DCR-registered client lives in Atlassian; remove it in your Atlassian org settings if you are done with it.

## Documentation

- [Consent portal overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
- [Consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html)
- [Consent portal execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-execution-role.html)
- [Adding gateway targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-adding-targets.html)
- [Outbound credential providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-outbound-credential-provider.html)
- [OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [Control Atlassian Rovo MCP server settings](https://support.atlassian.com/security-and-access-policies/docs/control-atlassian-rovo-mcp-server-settings/)
- [Atlassian Remote MCP Server](https://support.atlassian.com/rovo/docs/getting-started-with-the-atlassian-remote-mcp-server/)
- [RFC 7591 — OAuth 2.0 Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [Concepts and diagrams](../README.md)
