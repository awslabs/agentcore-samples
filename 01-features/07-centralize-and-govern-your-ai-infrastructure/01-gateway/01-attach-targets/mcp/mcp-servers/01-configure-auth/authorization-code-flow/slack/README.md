# Connecting the Slack MCP Server to AgentCore gateway - AgentCore consent portal

[Slack's MCP server](https://docs.slack.dev/ai/slack-mcp-server/) exposes Slack as MCP tools over Streamable HTTP at `https://mcp.slack.com/mcp` — search across messages, files, users, channels and emoji; read channel and thread history; send and draft messages; add reactions; create, read and update canvases and lists; and upload files. Every tool acts as the **signed-in user** (Slack user tokens), so a user only ever sees what they can already see in Slack.

This tutorial attaches the **Slack MCP server** to an Amazon Bedrock AgentCore Gateway for
**end-user consent**, using a hosted, AWS-managed
[**AgentCore Consent Portal**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
that authenticates each end user to your identity provider, gathers their consent, and calls
[`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html)
for you. There is no callback server to run and no consent UI to build — the OAuth flow stays
server-side and the browser never holds a token.

See [Who calls `CompleteResourceTokenAuth`](../README.md#who-calls-completeresourcetokenauth-your-own-dashboard-or-a-consent-portal) for the concept in full.

The gateway and portal are **shared and idempotent**: this target sits alongside any other in
this directory (GitHub, Atlassian, …) on the same gateway and portal. Run this guide first or
last; Steps 1–2 reuse whatever already exists.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- `boto3`/`botocore` **≥ 1.43.88** — the first release whose `bedrock-agentcore-control` model carries the consent-portal operations. `uv run` installs this from [`pyproject.toml`](../pyproject.toml); nothing extra to do.
- The [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az`), logged in to a **Microsoft Entra ID** tenant where you can register applications — or the equivalent for whichever IdP you choose in Steps 1–2.
- A Microsoft Entra ID tenant with **at least one active test user** who can sign in to the portal.
- AWS credentials that can create gateways, IAM roles, OAuth2 credential providers, gateway targets, and consent portals.
- Permission to **create and administer a Slack app** in your workspace (you build it in Step 3.1). Slack only permits MCP access from directory-published or internal apps backed by a fixed app ID, so a personal/dev app you fully control is ideal.

All commands run from [`authorization-code-flow/`](../), the parent of this `slack/` directory. State passes between steps through `scripts/.env`.

## Step 1: Create the AgentCore gateway with your identity provider

Follow the gateway guide for your identity provider. It ends with a `READY` gateway using `CUSTOM_JWT` inbound auth. **If you already did this for another target, the scripts reuse the existing gateway** — skim it and move on.

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/gateway.md`](../entra/gateway.md) |

## Step 2: Set up the consent portal with your identity provider

Follow the portal guide for the same identity provider. **If you already did this for another target, the scripts reuse the existing portal.**

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/consent-portal.md`](../entra/consent-portal.md) |

## Step 3: Set up the Slack MCP server target

**Platform documentation:**
- [Slack MCP server](https://docs.slack.dev/ai/slack-mcp-server/) — endpoint, OAuth, and tool reference

### Step 3.1: Create the Slack app and enable MCP server access (platform side)

Do this on the Slack side first — you need the app's `client_id`/`client_secret` before running Step 3.2, and the app must be enabled for MCP or every call fails.

1. **Create a blank Slack app.** Go to [`https://api.slack.com/apps`](https://api.slack.com/apps) → **Create New App** → **From scratch**. Give it a name, pick your workspace, and create it. Note the **App ID** from the app's **Basic Information** page (it looks like `A0XXXXXXXXX`).
2. **Enable it for Slack MCP server access.** Open the app's **App Assistant** page at `https://api.slack.com/apps/<YOUR_APP_ID>/app-assistant` (substitute the App ID from step 1) and turn on MCP server access.
   > [!IMPORTANT]
   > This step is required and easy to miss. Without it, **every** MCP call — even `initialize`/`tools/list` — fails with `-32600 "App is not enabled for Slack MCP server access."`, no matter how valid your OAuth token is.
3. **Publish the app** as an **internal app** (distributed within your workspace) or to the **Slack App Directory**. Slack only permits MCP access from directory-published or internal apps backed by a fixed app ID.
4. **Grab the OAuth credentials.** On **Basic Information → App Credentials**, copy the **Client ID** and **Client Secret** — you export these in Step 3.2.

You do **not** need to set a redirect URL yet. AgentCore vends the exact callback URL when you create the credential provider in Step 3.2; you register it on the app then.

**What the target needs from Slack (already known — no action required):**

- **Auth model: static OAuth app (`oauth_app`).** Slack supports confidential OAuth with a `client_id`/`client_secret` and does **not** support Dynamic Client Registration, so there is no DCR hop here.
- **MCP endpoint URL:** `https://mcp.slack.com/mcp` (JSON-RPC 2.0 over Streamable HTTP).
- **OAuth authorization-server metadata:** `https://mcp.slack.com/.well-known/oauth-authorization-server`, which advertises the **user-token** endpoints — authorize `https://slack.com/oauth/v2_user/authorize`, token `https://slack.com/api/oauth.v2.user.access`. These are not Slack's standard bot-token endpoints, which is why the profile uses vendor `CustomOauth2` + this discovery URL rather than a named `SlackOauth2` vendor.
- **OAuth scopes:** Slack scopes are **user-token scopes, granted per tool**. The set in [`scripts/servers/slack.json`](../scripts/servers/slack.json) covers all 26 tools in the schema — search (`search:read.public`, `search:read.private`, `search:read.users`, `search:read.files`), channel/DM history (`channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`), messaging (`chat:write`), profiles (`users:read`, `users:read.email`), files (`files:read`, `files:write`), reactions (`reactions:read`, `reactions:write`), emoji (`emoji:read`), canvases (`canvases:read`, `canvases:write`), and lists (`lists:read`, `lists:write`). Trim it to just the tools you expose if you want a narrower grant.

All of this — endpoint, discovery URL, scopes, `authModel`, vendor — is already encoded in the `slack` profile, so you do not configure it by hand.

### Step 3.2: Create the credential provider and the target

**1. Create the OAuth2 credential provider** (uses the `client_id`/`client_secret` from Step 3.1):

```bash
export SLACK_CLIENT_ID="<your-slack-client-id>"
export SLACK_CLIENT_SECRET="<your-slack-client-secret>"
uv run python scripts/deploy_credential.py --slack
```

It prints the AgentCore-vended **callback URL**, e.g.:

```
  Callback URL:   https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/<uuid>
```

**2. Register that callback URL on your Slack app.** In the app you created in Step 3.1, go to **OAuth & Permissions → Redirect URLs**, add the exact URL the script printed, and save. AgentCore owns this endpoint because it exchanges the authorization code — you do not choose the URL, and consent fails with a redirect-URI mismatch if it is not registered.

**3. Create the target** (schema upfront; reads `PORTAL_CONNECT_RETURN_URL` from `.env`):

```bash
uv run python scripts/deploy_target_schema.py --slack
```

There is **no** `deploy_credential_update.py` step for Slack — that is the DCR second phase, and Slack is `oauth_app`.

The target attaches `https://mcp.slack.com/mcp` with `grantType: AUTHORIZATION_CODE`, the user-token scopes from the profile (see Step 3.1), and the **26-tool** schema supplied upfront from [`slack/slack.json`](slack.json). No extra `customParameters` are needed — Slack's authorization request takes no `audience`/`resource` parameter.

### Step 3.3: Consent, as a user

```bash
open "https://$(grep '^PORTAL_URL=' scripts/.env | cut -d= -f2)"
```

1. Sign in with your IdP, as in Step 2.
2. The **Connections** page lists a Slack row (alongside any other target you attached).
3. **Connect** — approve at the provider; the portal calls `CompleteResourceTokenAuth` with your session and binds that consent to *you*.
4. **Disconnect** revokes it.

> [!NOTE]
> A newly created target does not appear immediately — the connections list is cached for up to **5 minutes**.

### Step 3.4: Invoke a tool

> [!TIP]
> Use the [AgentCore gateway MCP Inspector](../../../../../../05-community/gateway-mcp-inspector/) to explore tools interactively. It handles the URL-mode elicitation flow (opens the authorization URL, completes session binding) automatically.

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

`scripts/invoke.py` does **not** work against this Entra gateway — it mints its inbound token from the Cognito stack, which this gateway's authorizer will not accept.

![demo](./images/demo-portal.gif)

## Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| Every tool call fails with `-32600 "App is not enabled for Slack MCP server access."` (even with a valid token) | The Slack app has not been enabled for MCP access | Enable it on the app's **App Assistant** page (`https://api.slack.com/apps/<YOUR_APP_ID>/app-assistant`) — see Step 3.1 |

## Cleanup

> [!IMPORTANT]
> Clean up before starting another tutorial in this directory. Leftover credential providers and gateway targets cause name conflicts.

```bash
uv run python scripts/cleanup_targets.py --slack
```

That deletes this profile's targets and its Slack credential provider, and reports any targets it left alone. Then the portal ([`../entra/consent-portal.md`](../entra/consent-portal.md#cleanup)) and gateway ([`../entra/gateway.md`](../entra/gateway.md#cleanup)) — **only if no other target still uses them.**

## Documentation

- [Slack MCP server — OAuth endpoints](https://docs.slack.dev/ai/slack-mcp-server/#oauth-endpoints)
- [Consent portal overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
- [Consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html)
- [Adding gateway targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-adding-targets.html)
- [OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [Concepts and diagrams](../README.md)
