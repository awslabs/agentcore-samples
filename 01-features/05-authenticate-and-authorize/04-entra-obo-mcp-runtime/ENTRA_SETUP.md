# Entra ID setup

This document walks you through registering the two Microsoft Entra ID apps required by this sample. It is longer than the Prerequisites section in the notebook because it explains **why** each step is needed - not just what to click.

If you only want the short version, the notebook's Prerequisites cell has everything in one page. Use this doc if something doesn't work, or if you want to understand what each setting does.

---

## The big picture

We're going to create **two** Entra app registrations:

1. **`AgentCore - Agent`** - the app users sign in to, that performs OBO to get Graph tokens, and that also authenticates to the MCP server with M2M. One app, three jobs.
2. **`AgentCore - MCP Server`** - the app whose ID is the expected audience of the M2M token at the MCP server. Holds no secrets and is never signed in to.

The Agent app handles sign-in, OBO token exchange, and M2M authentication to the MCP server. The MCP Server app is a separate resource identity that gives the MCP authorizer a distinct audience (`api://<MCP_CLIENT_ID>`) and an app role (`mcp_invoke`) to validate. Microsoft's OBO documentation endorses using a single app for sign-in and middle-tier roles (see [*"Use of a single application"*](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow#use-of-a-single-application)).

---

## Prerequisites

- Sign in at [https://entra.microsoft.com](https://entra.microsoft.com) with **tenant admin** or **Application Administrator + Cloud Application Administrator**. You will grant admin consent twice during this walkthrough.
- If the Authentication blade shows the Preview UI, switch to the classic UI first: look for the banner link *"To switch to the old experience, please click here"* near the top.
- From the Entra admin center **Overview** page, note your **Tenant ID**. This is `ENTRA_TENANT_ID` in the notebook.

Total time: 10-20 minutes.

---

## Part 1 - Register the Agent app

### 1.1 Create the app

1. Left nav: **Applications → App registrations**
2. Click **+ New registration** at the top
3. Fill in:
   - **Name**: `AgentCore - Agent`
   - **Supported account types**: **Accounts in this organizational directory only (Single tenant)**
   - **Redirect URI**: leave blank
4. Click **Register**

On the Overview page that loads, copy **Application (client) ID**. This is `ENTRA_AGENT_CLIENT_ID`.

### 1.2 Authentication - enable device code flow

**Why**: our notebook authenticates users via MSAL's device code flow (the "go to aka.ms/devicelogin and enter code XXXXX" pattern). Microsoft classifies device code flow as a **public client** flow, and it needs a specific redirect URI (`https://login.microsoftonline.com/common/oauth2/nativeclient`) registered on the app. We also need to toggle "Allow public client flows" so the app registration advertises itself as a valid target for this flow.

1. Left nav of the app: **Authentication**
2. Under **Platform configurations**, click **+ Add a platform**
3. In the panel that opens, click the tile **Mobile and desktop applications** (the one whose description mentions Windows, UWP, Console, IoT, Classic iOS + Android). Not iOS/macOS, not Android - those are for apps built with those specific SDKs and require bundle IDs we don't have.
4. In the next panel, tick the box next to `https://login.microsoftonline.com/common/oauth2/nativeclient`. Leave the others unchecked.
5. Click **Configure**
6. Back on the main Authentication page, scroll down to **Advanced settings**
7. Find **Allow public client flows** and flip the toggle to **Yes**
8. Click **Save** at the top

### 1.3 Certificates & secrets - create a client secret

**Why**: even though this app allows public-client flows (for device code sign-in), it is *also* acting as a confidential client when it performs OBO. Confidential clients authenticate to Microsoft with a client secret (or certificate). We will configure the secret in the notebook's AgentCore Identity credential provider so AgentCore can do the OBO exchange on behalf of the agent.

1. Left nav: **Certificates & secrets**
2. On the **Client secrets** tab, click **+ New client secret**
3. Fill in:
   - **Description**: `AgentCore OBO sample secret`
   - **Expires**: whatever your org policy allows - 6 or 12 months is fine for a sample
4. Click **Add**
5. A new row appears with a **Value** column. **Copy the Value now** - this is `ENTRA_AGENT_CLIENT_SECRET`. Once you navigate away, Entra masks the value permanently; if you miss it, you'll have to delete this secret and create a new one.

### 1.4 Expose an API - Application ID URI and delegated scope

**Why**: the app needs an Application ID URI so that tokens issued for it have a stable audience value. The exposed scope below is required for sign-in to succeed; if it's missing, sign-in fails with `AADSTS65002`.

1. Left nav: **Expose an API**
2. Next to **Application ID URI**, click **Add** (or **Set**). Entra proposes `api://<client-id>`. Click **Save**.
3. Under **Scopes defined by this API**, click **+ Add a scope**. Fill in:
   - **Scope name**: `user_delegation`
   - **Who can consent**: **Admins and users**
   - **Admin consent display name**: `Delegate to the agent on the signed-in user's behalf`
   - **Admin consent description**: `Allows the agent to call downstream APIs such as Microsoft Graph on behalf of the signed-in user, via Microsoft's OAuth 2.0 On-Behalf-Of flow.`
   - **User consent display name**: `Let the agent act on your behalf`
   - **User consent description**: `Allows the agent to read your profile on your behalf.`
   - **State**: **Enabled**
4. Click **Add scope**

You should now see a row reading `api://<AGENT_CLIENT_ID>/user_delegation` with State = Enabled.

### 1.5 API permissions - Microsoft Graph delegated permission

**Why**: for the OBO exchange to produce a Graph token, the Agent app must first be granted delegated Graph permissions. When the user consents at sign-in, Entra combines this permission into the consent prompt so the user authorises the agent to use Graph on their behalf. We admin-consent here to avoid the user being prompted individually.

1. Left nav: **API permissions**
2. Click **+ Add a permission**
3. Click **Microsoft Graph**
4. Click **Delegated permissions**
5. In the search box, type `User.Read` and tick the box next to it
6. Click **Add permissions** at the bottom
7. Back on the API permissions list, `User.Read` shows up with status **"Not granted for \<tenant\>"**
8. Click **Grant admin consent for \<tenant\>** near the top of the list. Confirm.

The row must flip to green check marks **"Granted for \<tenant\>"** before you move on. If the button is greyed out, you need admin privileges - ask your tenant admin to do this step.

### 1.6 API permissions - MCP Server app (come back after Part 2)

The Agent app also needs permission to call the MCP server as itself (M2M). That permission is defined on the MCP Server app, which doesn't exist yet. **Skip to Part 2**, then come back here.

---

## Part 2 - Register the MCP Server app

This app has one job: define the audience and app role that the MCP server's authorizer validates against. No secrets, no sign-in, no scopes.

### 2.1 Create the app

1. Left nav: **Applications → App registrations**, click **+ New registration**
2. Fill in:
   - **Name**: `AgentCore - MCP Server`
   - **Supported account types**: **Accounts in this organizational directory only (Single tenant)**
   - **Redirect URI**: leave blank
3. Click **Register**

On Overview, copy **Application (client) ID**. This is `ENTRA_MCP_CLIENT_ID`.

### 2.2 Expose an API - Application ID URI only

**Why**: we need a stable audience value for the M2M token the agent will send to the MCP server. The MCP server's `customJWTAuthorizer` validates `aud = "api://<MCP_CLIENT_ID>"`. No scope needed - we authorise via an **app role**, not a scope.

1. Left nav: **Expose an API**
2. Click **Add** next to **Application ID URI**, accept the proposed `api://<client-id>`, click **Save**

Do not add a scope here. That's deliberate.

### 2.3 App roles - define `mcp_invoke`

**Why**: when the Agent app calls the MCP server with a client-credentials (M2M) token, that token's authorisation claim is `roles`, not `scp` (because there's no user involved). The MCP server's `customJWTAuthorizer` looks for the string `mcp_invoke` in the `roles` claim. We define the role here; we grant it in Part 3.

1. Left nav: **App roles**
2. Click **+ Create app role**
3. Fill in:
   - **Display name**: `Invoke MCP Server`
   - **Allowed member types**: **Applications** (critical - not Users/Groups; this is an app-only role)
   - **Value**: `mcp_invoke`
   - **Description**: `Apps that can invoke tools on this MCP Server.`
   - **Do you want to enable this app role?**: ticked
4. Click **Apply**

---

## Part 3 - Finish step 1.6 on the Agent app

Now that the MCP Server app exists with its `mcp_invoke` role, go back to the Agent app and grant it.

1. Left nav: **Applications → App registrations**, select **AgentCore - Agent**
2. Left nav: **API permissions**
3. Click **+ Add a permission**
4. Click the **APIs my organization uses** tab
5. Search for `AgentCore - MCP Server`, click it in the results
6. Choose **Application permissions** (not Delegated - this is M2M)
7. Tick `mcp_invoke`
8. Click **Add permissions**

You're back on the API permissions list. You should now see two rows:

| Permission | Type | Status |
|---|---|---|
| `User.Read` (Microsoft Graph) | Delegated | Granted for \<tenant\> |
| `mcp_invoke` (AgentCore - MCP Server) | Application | Not granted for \<tenant\> |

9. Click **Grant admin consent for \<tenant\>**. Confirm.

Both rows must show green check marks before you move on.

---

## Part 4 - Collect the four values

| Env var | Source |
|---|---|
| `ENTRA_TENANT_ID` | Entra admin center → Overview → **Tenant ID** |
| `ENTRA_AGENT_CLIENT_ID` | Agent app → Overview → **Application (client) ID** |
| `ENTRA_AGENT_CLIENT_SECRET` | The secret value you copied in step 1.3 |
| `ENTRA_MCP_CLIENT_ID` | MCP Server app → Overview → **Application (client) ID** |

These four values go into the notebook's **Step 2** cell.

---

## How the pieces fit together at runtime

- User signs in to the Agent app via MSAL device code flow. Entra issues a user JWT.
- AgentCore Runtime validates the JWT and delivers the agent request. The agent calls `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE`, and AgentCore Identity exchanges the user JWT for a Microsoft Graph delegation token on the agent's behalf.
- Separately, AgentCore Identity fetches an M2M token for the agent (client_credentials against the same Agent app) audienced to the MCP Server app and carrying the `mcp_invoke` role.
- The agent calls the MCP server with the M2M token in `Authorization` and the Graph delegation token in a custom request header. The MCP server's authorizer accepts the M2M token based on audience and role, and the tool uses the delegation token to call Graph.

---

## Sanity checks via the app manifest

If anything fails at runtime, open each app's **Manifest** (left nav) and verify:

**Agent app:**
- `"allowPublicClient": true`
- `"identifierUris": ["api://<AGENT_CLIENT_ID>"]`
- `"oauth2Permissions"` has one entry with `"value": "user_delegation"` and `"isEnabled": true`
- `"requiredResourceAccess"` has one entry for Microsoft Graph (resource appId `00000003-0000-0000-c000-000000000000`) with the `User.Read` scope ID, and one for the MCP Server app with the `mcp_invoke` role ID

**MCP Server app:**
- `"identifierUris": ["api://<MCP_CLIENT_ID>"]`
- `"appRoles"` has one entry with `"value": "mcp_invoke"` and `"allowedMemberTypes": ["Application"]`
- No entries under `"oauth2Permissions"` (we deliberately did not add a scope)

---

## Common errors and how to fix them

| Symptom | Most likely cause | Fix |
|---|---|---|
| Sign-in: `AADSTS65002: Resource for this token request is not a valid scope` | Application ID URI not set, or `user_delegation` scope missing/disabled | Redo step 1.4 |
| Sign-in: `AADSTS500011: resource principal does not exist` | Application ID URI not set, or user belongs to a different tenant | Verify step 1.4 saved; confirm `ENTRA_TENANT_ID` |
| Sign-in: hangs on device code, never progresses | "Allow public client flows" is **No**, or `nativeclient` URI missing | Redo step 1.2 |
| Agent returns 401 from the authorizer | Agent app's `allowedAudience` must be the bare GUID `<ENTRA_AGENT_CLIENT_ID>`, no `api://` prefix | Check the agent's `configure()` call in the notebook |
| OBO: `AADSTS500131: Assertion audience does not match the Client app` | MSAL scope must produce a JWT with `aud = <ENTRA_AGENT_CLIENT_ID>` | Check the MSAL sign-in cell |
| Sign-in: `AADSTS90009: Application is requesting a token for itself. This scenario is supported only if resource is specified using the GUID based App Identifier.` | MSAL scope must use the bare-GUID form `<ENTRA_AGENT_CLIENT_ID>/.default`, not `api://` | Check the MSAL sign-in cell |
| OBO: `AADSTS65001: user or administrator has not consented` | Graph delegated permissions exist but admin consent wasn't granted | Redo admin consent in step 1.5 |
| MCP returns 401, or agent says tools are unavailable | M2M token missing `roles` claim - either `mcp_invoke`'s Allowed member types is wrong (must be **Applications**), or admin consent wasn't granted | Verify step 2.3 and the consent in Part 3 |

---

## References

- [Microsoft identity platform - OAuth 2.0 On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Microsoft identity platform - Use of a single application (OBO simplification)](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow#use-of-a-single-application)
- [Amazon Bedrock AgentCore - On-behalf-of token exchange](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
