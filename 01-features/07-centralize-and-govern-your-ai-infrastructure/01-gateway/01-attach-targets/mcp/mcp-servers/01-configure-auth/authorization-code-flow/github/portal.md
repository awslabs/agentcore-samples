# Connecting GitHub MCP Server to AgentCore gateway - AgentCore consent portal

[GitHub's MCP server](https://github.com/github/github-mcp-server) exposes repository search, user lookup, workflow management, and more as MCP tools, and it requires the OAuth 2.0 authorization code flow for authentication. This tutorial attaches it to an Amazon Bedrock AgentCore Gateway for **end-user consent**.

It is the counterpart to [`README.md`](README.md), and they differ in **who completes the authorization and how**:

- [`README.md`](README.md) — an **admin** authorizes once at target-creation time, and the browser redirect lands on a local [`callback_server.py`](../scripts/callback_server.py) you run and maintain.
- **This guide** — a hosted, AWS-managed [**AgentCore Consent Portal**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html) authenticates each **end user** to your identity provider, gathers their consent, and calls [`CompleteResourceTokenAuth`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CompleteResourceTokenAuth.html) for you. There is no callback server to run and no consent UI to build — the OAuth flow stays server-side and the browser never holds a token.

See [Who calls `CompleteResourceTokenAuth`](../README.md#who-calls-completeresourcetokenauth-your-own-dashboard-or-a-consent-portal) for the concept in full.

The target uses **schema upfront** only. Implicit sync would require an admin to complete a three-legged OAuth flow at create time, which is the exact thing the portal exists to avoid.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- `boto3`/`botocore` **≥ 1.43.88** — the first release whose `bedrock-agentcore-control` model carries the consent-portal operations. `uv run` installs this from [`pyproject.toml`](../pyproject.toml); nothing extra to do.
- The [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az`), logged in to a **Microsoft Entra ID** tenant where you can register applications — or the equivalent for whichever IdP you choose in Steps 1–2.
- A Microsoft Entra ID tenant with **at least one active test user** who can sign in to the portal.
- AWS credentials that can create gateways, IAM roles, OAuth2 credential providers, gateway targets, and consent portals.
- A **GitHub account** that can create an [OAuth App](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps).

All commands run from [`authorization-code-flow/`](../), the parent of this `github/` directory. State passes between steps through `scripts/.env`.

## Step 1: Create the AgentCore gateway with your identity provider

Follow the gateway guide for your identity provider. It ends with a `READY` gateway using `CUSTOM_JWT` inbound auth.

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/gateway.md`](../entra/gateway.md) |

## Step 2: Set up the consent portal with your identity provider

Follow the portal guide for the same identity provider.

| Identity provider | Guide |
| :--- | :--- |
| Microsoft Entra ID | [`../entra/consent-portal.md`](../entra/consent-portal.md) |

## Step 3: Set up the GitHub MCP server target

### Step 3.1: Create the GitHub OAuth App and its credential provider

Create a [GitHub OAuth App](https://github.com/settings/developers). Put anything in its Authorization callback URL for now; you will correct it in a moment with a URL AgentCore vends. Then:

```bash
export GITHUB_CLIENT_ID="<your-github-client-id>"
export GITHUB_CLIENT_SECRET="<your-github-client-secret>"

uv run python scripts/deploy_credential.py --github
```

This creates a `GithubOauth2` credential provider and prints the `callbackUrl` AgentCore vends for it — something of the form:

```
https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>
```

**Paste that into your GitHub OAuth App's Authorization callback URL now.** You do not choose this URL; AgentCore owns the redirect endpoint, because it is AgentCore that exchanges the code.

> [!WARNING]
> Skipping that paste does **not** fail the next step. The target creates cleanly and stays `READY`. The failure appears only when a real user clicks **Connect** and GitHub rejects the `redirect_uri` — by which point it looks like a portal bug.

#### Three different URLs, none interchangeable

| URL | Registered with | Set in |
| :--- | :--- | :--- |
| `https://<portalUrl>/callback` | the **shared gateway / portal Entra app** (`$ENTRA_RESOURCE`) `web.redirectUris` | Step 2 |
| `https://<portalUrl>/connect/callback` | nobody — it is the **target's** `defaultReturnUrl` | [below](#step-32-create-the-target-schema-upfront) |
| `https://bedrock-agentcore.<region>.amazonaws.com/identities/oauth2/callback/<uuid>` | the **GitHub OAuth App's** Authorization callback URL | above |

The first is where the *identity provider* returns the user. The second is where the *outbound provider* returns them. The third is where the OAuth *code* is delivered. Swap any two and you get a failure that presents as a login bug.

### Step 3.2: Create the target (schema upfront)

```bash
uv run python scripts/deploy_target_schema.py --github
```

This attaches [`https://api.githubcopilot.com/mcp`](https://api.githubcopilot.com/mcp) with `grantType: AUTHORIZATION_CODE`, scopes `repo user workflow`, and the tool schema supplied upfront from [`github.json`](github.json). The target is immediately `READY` — no admin authorizes anything at create time.

The script reads `PORTAL_CONNECT_RETURN_URL` from `scripts/.env` and uses it as `defaultReturnUrl`, so having run Step 2 is all it takes to point the target at the portal. It prints which URL it chose:

```
Return URL: https://<portalUrl>/connect/callback (Consent Portal)
```

With no portal deployed the same script falls back to `http://localhost:8080/callback` — the `callback_server.py` flow from [the admin-consent walkthrough](README.md).

> [!IMPORTANT]
> This applies to targets you **already have**, too. A pre-existing `AUTHORIZATION_CODE` target whose `defaultReturnUrl` still points somewhere else will appear on the Connections page and let the user authorize at the provider — and then the consent never binds to their session, because the provider returns them to the wrong place. Update every such target's `defaultReturnUrl` to `<portalUrl>/connect/callback`, not just the new one.

> [!TIP]
> The target's name and the outbound provider's name are **displayed to your end users** on the Connections page. `github-mcp-server-schema-target` is fine for a tutorial; pick something a non-engineer would recognise for anything real.

### Step 3.3: Consent, as a user

```bash
open "https://$(grep '^PORTAL_URL=' scripts/.env | cut -d= -f2)"
```

1. Sign in with Entra, as in Step 2.
2. The **Connections** page now lists a GitHub row.
3. **Connect** — the portal hands you GitHub's authorization URL, you approve, and GitHub returns you to `/connect/callback`. The portal calls `CompleteResourceTokenAuth` with your session, binding that consent to *you*, and redirects to `/?connected=<provider>`.
4. **Disconnect** revokes it; the row returns to its unconnected state.

> [!NOTE]
> A newly created target does not appear immediately — the connections list is cached for up to **5 minutes**. An empty page right after creating the target is expected; wait it out before debugging.

> [!NOTE]
> Authorization URLs and session URIs are valid for **10 minutes**. A consent flow interrupted and resumed later fails in a way that looks like a configuration error. Start over rather than debugging it.

> [!NOTE]
> GitHub does not re-prompt once you have authorized an OAuth App, so a disconnect-then-reconnect cycle may complete with no visible consent screen at all. Revoke the app under your GitHub account settings if you want to see the prompt again.

### Step 3.4: Invoke a tool

> [!TIP]
> Use the [AgentCore gateway MCP Inspector](../../../../../../05-community/gateway-mcp-inspector/) to explore GitHub tools interactively. The Inspector handles the URL-mode elicitation flow (opens the authorization URL, completes session binding) automatically.

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

## What's next: attach another target

The gateway and portal you built are **shared and idempotent** — attach as many MCP server targets to them as you like, in any order. Each target has its own outbound credential provider, so they do not interfere with one another.

| Target | Guide |
| :--- | :--- |
| GitHub MCP server | this guide, [Step 3](#step-3-set-up-the-github-mcp-server-target) |
| Atlassian Remote MCP server (Jira + Confluence) | [`../atlassian/README.md`](../atlassian/README.md) |

## Cleanup

> [!IMPORTANT]
> Clean up before starting another tutorial in this directory. Leftover credential providers and gateway targets cause name conflicts.

Clean up in reverse order. First the target and its GitHub credential provider:

```bash
uv run python scripts/cleanup_targets.py --github
```

That deletes this profile's targets and its GitHub credential provider, and reports any targets it left alone. It is scoped to the profile on purpose: one gateway can front several MCP servers, and deleting your target must not mean deleting someone else's.

Then the portal ([`../entra/consent-portal.md`](../entra/consent-portal.md#cleanup)), then the gateway ([`../entra/gateway.md`](../entra/gateway.md#cleanup)). Your **GitHub OAuth App** is yours to delete in GitHub if you are done with it.

## Documentation

- [Consent portal overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal.html)
- [Consent portal prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-prerequisites.html)
- [Consent portal execution role](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-consent-portal-execution-role.html)
- [Adding gateway targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-building-adding-targets.html)
- [Outbound credential providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-outbound-credential-provider.html)
- [OAuth2 authorization URL session binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
- [Gateway outbound auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
- [The admin-consent walkthrough](README.md) — both target-creation methods, with the `callback_server.py` flow
- [Concepts and diagrams](../README.md)
