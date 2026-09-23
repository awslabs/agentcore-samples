# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
MCP OAuth Proxy Lambda — EntraID variant.

Handles OAuth metadata, authorize/callback/token (EntraID), MCP forwarding,
and the 3LO callback from AgentCore Identity.
"""

import json
import os
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import boto3

# Configuration from environment variables
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
ENTRA_APP_A_CLIENT_ID = os.environ.get("ENTRA_APP_A_CLIENT_ID", "")
ENTRA_DISCOVERY_URL = os.environ.get("ENTRA_DISCOVERY_URL", "")

# Auth onboarding SPA config
AUTH_ONBOARDING_ROLE_ARN = os.environ.get("AUTH_ONBOARDING_ROLE_ARN", "")
OAUTH_CREDENTIAL_PROVIDER_NAME = os.environ.get("OAUTH_CREDENTIAL_PROVIDER_NAME", "")
ENTRA_WEATHER_SCOPE = os.environ.get("ENTRA_WEATHER_SCOPE", "")

# EntraID endpoints — derived from env vars set by CDK stack.
# Supports both CIAM (ciamlogin.com) and standard (login.microsoftonline.com) tenants.
ENTRA_AUTHORITY = os.environ.get(
    "ENTRA_AUTHORITY",
    f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}",
)
ENTRA_AUTHORITY_HOST = os.environ.get("ENTRA_AUTHORITY_HOST", "login.microsoftonline.com")
ENTRA_AUTHORIZE_URL = f"{ENTRA_AUTHORITY}/oauth2/v2.0/authorize"
ENTRA_TOKEN_URL = f"{ENTRA_AUTHORITY}/oauth2/v2.0/token"


def sign_request(request):
    """Sign an HTTP request with AWS SigV4."""
    session = boto3.Session()
    credentials = session.get_credentials()
    region = session.region_name or "us-east-1"

    aws_request = AWSRequest(
        method=request.get_method(),
        url=request.get_full_url(),
        data=request.data,
        headers=request.headers,
    )
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_request)

    for key, value in aws_request.headers.items():
        request.add_header(key, value)


def lambda_handler(event, context):
    """Main Lambda handler — routes requests based on path."""
    path = event.get("path", "/")
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    # Log request metadata only (exclude headers which may contain tokens)
    print(f"Method: {method}, Path: {path}")

    if method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {"Allow": "OPTIONS, GET, POST"},
            "body": "",
        }

    if path == "/ping":
        return handle_ping()
    elif path == "/auth":
        return handle_auth_page(event)
    elif path == "/auth/callback":
        return handle_auth_callback_page(event)
    elif path.startswith("/.well-known/oauth-authorization-server"):
        return handle_oauth_metadata(event)
    elif path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        return handle_protected_resource_metadata(event)
    elif path == "/authorize":
        return handle_authorize(event)
    elif path == "/callback":
        return handle_callback(event)
    elif path == "/token" and method == "POST":
        return handle_token(event)
    elif path == "/register" and method == "POST":
        return handle_dcr(event)
    elif path == "/mcp":
        return proxy_to_gateway(event)
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "Not found"})}


def handle_ping():
    """Health check endpoint."""
    return json_response(200, {"status": "healthy", "service": "mcp-proxy-entraid"})


def handle_auth_page(event):
    """Serve the auth onboarding SPA — uses the same MCP flow as VS Code.

    Instead of calling AgentCore APIs directly, the SPA calls POST /mcp with
    the user's JWT (same as VS Code). The Gateway returns an elicitation (-32042)
    if the user hasn't authorized yet. The SPA extracts the authorization URL
    and redirects the user to consent. After consent, AgentCore redirects to
    /auth/callback where CompleteResourceTokenAuth is called via SigV4.
    """
    api_url = get_api_url(event)
    region = os.environ.get("AWS_REGION", "us-east-1")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auth Onboarding</title>
<script src="https://alcdn.msauth.net/browser/2.38.2/js/msal-browser.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; color: #1a1a2e; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 2rem; }}
.container {{ max-width: 640px; width: 100%; }}
h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
.subtitle {{ color: #666; margin-bottom: 2rem; }}
.card {{ background: #fff; border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.card h2 {{ font-size: 1.1rem; margin-bottom: 0.5rem; }}
.status {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 12px; font-size: 0.85rem; font-weight: 500; }}
.status-authorized {{ background: #d4edda; color: #155724; }}
.status-needs-auth {{ background: #fff3cd; color: #856404; }}
.status-checking {{ background: #e2e3e5; color: #383d41; }}
.status-error {{ background: #f8d7da; color: #721c24; }}
.btn {{ display: inline-block; padding: 0.5rem 1.25rem; border: none; border-radius: 6px; font-size: 0.9rem; cursor: pointer; text-decoration: none; }}
.btn-primary {{ background: #0078d4; color: #fff; }}
.btn-primary:hover {{ background: #106ebe; }}
.btn-primary:disabled {{ background: #ccc; cursor: not-allowed; }}
.btn-outline {{ background: transparent; border: 1px solid #0078d4; color: #0078d4; }}
.btn-outline:hover {{ background: #f0f6ff; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }}
.user-info {{ font-size: 0.85rem; color: #666; }}
.provider-row {{ display: flex; justify-content: space-between; align-items: center; }}
.provider-info {{ flex: 1; }}
.provider-scope {{ font-size: 0.8rem; color: #888; margin-top: 0.25rem; }}
#login-section {{ text-align: center; padding: 3rem 1rem; }}
#registry-section {{ display: none; }}
#error-msg {{ color: #dc3545; margin-top: 1rem; font-size: 0.9rem; display: none; }}
.spinner {{ display: inline-block; width: 16px; height: 16px; border: 2px solid #ccc; border-top-color: #0078d4; border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 0.5rem; vertical-align: middle; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
</head>
<body>
<div class="container">
  <div id="login-section">
    <h1>MCP Server Authorization</h1>
    <p class="subtitle">Sign in to authorize access to your MCP server resources.</p>
    <button class="btn btn-primary" onclick="signIn()" id="signin-btn" disabled>Sign in with Microsoft</button>
    <div id="error-msg"></div>
  </div>
  <div id="registry-section">
    <div class="header">
      <div>
        <h1>MCP Server Authorization</h1>
        <p class="subtitle">Manage resource access for your MCP servers.</p>
      </div>
      <div>
        <span class="user-info" id="user-name"></span>
        <button class="btn btn-outline" onclick="signOut()" style="margin-left:0.5rem;">Sign out</button>
      </div>
    </div>
    <div id="providers-list"></div>
  </div>
  <div id="log-panel" style="margin-top:2rem;background:#1e1e2e;color:#a6e3a1;border-radius:8px;padding:1rem;font-family:monospace;font-size:0.8rem;max-height:400px;overflow-y:auto;display:none;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
      <span style="color:#cdd6f4;font-weight:bold;">Flow Log</span>
      <button onclick="document.getElementById('log-panel').style.display='none'" style="background:none;border:none;color:#cdd6f4;cursor:pointer;font-size:1rem;">&times;</button>
    </div>
    <div id="log-entries"></div>
  </div>
</div>

<script>
function log(step, msg, data) {{
  const panel = document.getElementById("log-panel");
  const entries = document.getElementById("log-entries");
  if (panel) panel.style.display = "block";
  const t = new Date().toLocaleTimeString();
  const colors = {{ ok: "#a6e3a1", err: "#f38ba8", info: "#89b4fa", warn: "#f9e2af" }};
  const color = data && data._err ? colors.err : colors.ok;
  let detail = "";
  if (data) {{
    const clean = {{ ...data }};
    delete clean._err;
    detail = Object.entries(clean).map(([k,v]) => {{
      const s = String(v);
      const display = s.length > 80 ? s.substring(0, 40) + "..." + s.substring(s.length - 20) : s;
      return '  <span style="color:#cdd6f4">' + k + '</span>: ' + display;
    }}).join("\\n");
  }}
  const entry = document.createElement("div");
  entry.style.cssText = "margin-bottom:0.5rem;border-bottom:1px solid #313244;padding-bottom:0.5rem;";
  entry.innerHTML = '<span style="color:#585b70">' + t + '</span> <span style="color:' + color + ';font-weight:bold">[' + step + ']</span> ' + msg + (detail ? "\\n" + detail : "");
  entry.style.whiteSpace = "pre-wrap";
  if (entries) entries.appendChild(entry);
  if (panel) panel.scrollTop = panel.scrollHeight;
}}

// Configuration injected by Lambda
const CONFIG = {{
  tenantId: "{ENTRA_TENANT_ID}",
  clientId: "{ENTRA_APP_A_CLIENT_ID}",
  redirectUri: "{api_url}/auth",
  apiUrl: "{api_url}",
  region: "{region}",
  roleArn: "{AUTH_ONBOARDING_ROLE_ARN}",
}};

log("CONFIG", "Loaded", {{ tenantId: CONFIG.tenantId, clientId: CONFIG.clientId, apiUrl: CONFIG.apiUrl }});

const msalConfig = {{
  auth: {{
    clientId: CONFIG.clientId,
    authority: "{ENTRA_AUTHORITY}",
    knownAuthorities: ["{ENTRA_AUTHORITY_HOST}"],
    redirectUri: CONFIG.redirectUri,
  }},
  cache: {{ cacheLocation: "sessionStorage" }},
}};

let msalInstance = null;
let currentAccount = null;

async function initMsal() {{
  log("1-MSAL", "Initializing MSAL.js...");
  msalInstance = new msal.PublicClientApplication(msalConfig);
  await msalInstance.initialize();
  const resp = await msalInstance.handleRedirectPromise();
  if (resp) {{
    currentAccount = resp.account;
    log("1-MSAL", "Authenticated via redirect", {{ name: resp.account.name, username: resp.account.username }});
    await onSignedIn();
    return;
  }}
  const accounts = msalInstance.getAllAccounts();
  if (accounts.length > 0) {{
    currentAccount = accounts[0];
    log("1-MSAL", "Cached session found", {{ name: accounts[0].name }});
    await onSignedIn();
    return;
  }}
  log("1-MSAL", "No session — showing sign-in button");
  document.getElementById("signin-btn").disabled = false;
}}

initMsal().catch(e => {{
  log("1-MSAL", "Init failed: " + e.message, {{ _err: true }});
  showError("MSAL init failed: " + e.message);
}});

async function signIn() {{
  document.getElementById("signin-btn").disabled = true;
  document.getElementById("signin-btn").innerHTML = '<span class="spinner"></span>Redirecting...';
  hideError();
  msalInstance.loginRedirect({{ scopes: ["openid", "profile", "email"] }});
}}

function signOut() {{ msalInstance.logoutRedirect(); }}

async function getJwt() {{
  const tokenResp = await msalInstance.acquireTokenSilent({{
    scopes: ["api://" + CONFIG.clientId + "/gateway.access"],
    account: currentAccount,
  }});
  return tokenResp.accessToken;
}}

async function onSignedIn() {{
  document.getElementById("login-section").style.display = "none";
  document.getElementById("registry-section").style.display = "block";
  document.getElementById("user-name").textContent = currentAccount.name || currentAccount.username;

  try {{
    const jwt = await getJwt();
    log("2-TOKEN", "Got EntraID JWT (gateway.access scope)", {{ length: jwt.length }});
    await checkMcpAuth(jwt);
  }} catch (e) {{
    log("ERROR", e.message, {{ _err: true }});
    showError("Failed: " + e.message);
  }}
}}

async function checkMcpAuth(jwt) {{
  const list = document.getElementById("providers-list");
  list.innerHTML = "";

  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = '<div class="provider-row"><div class="provider-info"><h2>Weather API</h2><p class="provider-scope">Provider: {OAUTH_CREDENTIAL_PROVIDER_NAME} | Scope: {ENTRA_WEATHER_SCOPE}</p></div><div><span class="status status-checking"><span class="spinner"></span>Checking...</span></div></div>';
  list.appendChild(card);

  try {{
    // Call POST /mcp with tools/call getWeather — triggers outbound auth check.
    // We include _meta.rawElicitation so the interceptor passes the elicitation
    // through raw instead of rewriting it to a friendly message.
    // Gateway returns elicitation (-32042) if user hasn't authorized yet.
    log("3-MCP", "Calling POST /mcp with tools/call getWeather (triggers outbound auth)...");
    const mcpResp = await fetch(CONFIG.apiUrl + "/mcp", {{
      method: "POST",
      headers: {{
        "Content-Type": "application/json",
        "Authorization": "Bearer " + jwt,
        "Mcp-Protocol-Version": "2025-11-25",
      }},
      body: JSON.stringify({{ jsonrpc: "2.0", id: 1, method: "tools/call", params: {{ name: "weather-api___getWeather", arguments: {{ location: "Berlin" }} }}, _meta: {{ rawElicitation: true }} }}),
    }});

    const mcpData = await mcpResp.json();
    log("3-MCP", "Got MCP response", {{
      status: mcpResp.status,
      hasResult: !!mcpData.result,
      hasError: !!mcpData.error,
      errorCode: mcpData.error ? mcpData.error.code : "(none)",
    }});

    if (mcpData.error && mcpData.error.code === -32042) {{
      // Elicitation — user needs to authorize
      const elicitations = mcpData.error.data && mcpData.error.data.elicitations;
      if (elicitations && elicitations.length > 0) {{
        const authUrl = elicitations[0].url;
        log("3-MCP", "Elicitation — authorization needed", {{ authorizationUrl: authUrl }});

        card.querySelector(".status").className = "status status-needs-auth";
        card.querySelector(".status").textContent = "Authorization needed";
        const btnDiv = card.querySelector(".provider-row").lastElementChild;
        const btn = document.createElement("button");
        btn.className = "btn btn-primary";
        btn.style.marginLeft = "1rem";
        btn.textContent = "Authorize";
        btn.onclick = function() {{
          // Save JWT and role ARN to sessionStorage — the callback page needs them
          // for STS AssumeRoleWithWebIdentity → CompleteResourceTokenAuth (direct SigV4).
          // Both stay in the browser only (no server-side storage).
          sessionStorage.setItem("auth_jwt", jwt);
          sessionStorage.setItem("auth_role_arn", CONFIG.roleArn);
          log("4-REDIRECT", "Saved JWT + roleArn to sessionStorage, redirecting to consent...", {{ authorizationUrl: authUrl }});
          window.location.href = authUrl;
        }};
        btnDiv.appendChild(btn);
      }} else {{
        throw new Error("Elicitation response missing authorization URL");
      }}
    }} else if (mcpData.result) {{
      // tools/call succeeded — already authorized (weather data returned)
      card.querySelector(".status").className = "status status-authorized";
      card.querySelector(".status").textContent = "Authorized";
      log("3-MCP", "Already authorized — tool call succeeded");
    }} else if (mcpData.error) {{
      throw new Error("MCP error (" + mcpData.error.code + "): " + (mcpData.error.message || "").substring(0, 200));
    }} else {{
      throw new Error("Unexpected response: " + JSON.stringify(mcpData).substring(0, 200));
    }}
  }} catch (e) {{
    card.querySelector(".status").className = "status status-error";
    card.querySelector(".status").textContent = "Error";
    const errP = document.createElement("p");
    errP.style.cssText = "color:#dc3545;font-size:0.85rem;margin-top:0.5rem;";
    errP.textContent = e.message;
    card.querySelector(".provider-info").appendChild(errP);
    log("ERROR", e.message, {{ _err: true }});
  }}
}}

function showError(msg) {{
  const el = document.getElementById("error-msg");
  el.textContent = msg;
  el.style.display = "block";
}}
function hideError() {{
  document.getElementById("error-msg").style.display = "none";
}}
</script>
</body>
</html>"""

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }


def handle_auth_callback_page(event):
    """Serve the auth callback page — completes 3LO directly from the browser.

    After the user consents in EntraID, AgentCore redirects here with
    ?session_id=<urn:ietf:params:oauth:request_uri:...>. The page:
    1. Reads the JWT from sessionStorage (saved by the main page before redirect)
    2. Calls STS AssumeRoleWithWebIdentity(JWT) to get temporary AWS credentials
    3. Calls CompleteResourceTokenAuth(sessionUri, userToken) with SigV4 signing

    No Lambda proxy needed — the browser calls AWS APIs directly using temp credentials.
    The IAM role has secretsmanager:GetSecretValue gated by aws:CalledVia condition,
    so only AgentCore can access secrets internally via Forward Access Sessions (FAS).
    """
    api_url = get_api_url(event)
    region = os.environ.get("AWS_REGION", "us-east-1")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Authorization Callback</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; color: #1a1a2e; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 2rem; }}
.card {{ background: #fff; border-radius: 8px; padding: 2rem; max-width: 480px; width: 100%; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
h2 {{ margin-bottom: 1rem; }}
.spinner {{ display: inline-block; width: 24px; height: 24px; border: 3px solid #ccc; border-top-color: #0078d4; border-radius: 50%; animation: spin 0.6s linear infinite; margin-bottom: 1rem; }}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
.success {{ color: #155724; }}
.error {{ color: #721c24; }}
.btn {{ display: inline-block; padding: 0.5rem 1.25rem; border: none; border-radius: 6px; font-size: 0.9rem; cursor: pointer; background: #0078d4; color: #fff; text-decoration: none; margin-top: 1rem; }}
.btn:hover {{ background: #106ebe; }}
</style>
</head>
<body>
<div class="card">
  <div id="loading">
    <div class="spinner"></div>
    <h2>Completing authorization...</h2>
    <p>Please wait while we finalize your access.</p>
  </div>
  <div id="result" style="display:none;"></div>
</div>
<div id="log-panel" style="margin-top:2rem;background:#1e1e2e;color:#a6e3a1;border-radius:8px;padding:1rem;font-family:monospace;font-size:0.8rem;max-height:400px;overflow-y:auto;max-width:640px;width:100%;display:none;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
    <span style="color:#cdd6f4;font-weight:bold;">Callback Flow Log</span>
    <button onclick="document.getElementById('log-panel').style.display='none'" style="background:none;border:none;color:#cdd6f4;cursor:pointer;font-size:1rem;">&times;</button>
  </div>
  <div id="log-entries"></div>
</div>

<script type="module">
import {{ STSClient, AssumeRoleWithWebIdentityCommand }} from "https://cdn.jsdelivr.net/npm/@aws-sdk/client-sts/+esm";
import {{ BedrockAgentCoreClient, CompleteResourceTokenAuthCommand }} from "https://cdn.jsdelivr.net/npm/@aws-sdk/client-bedrock-agentcore/+esm";

const AUTH_PAGE_URL = "{api_url}/auth";
const REGION = "{region}";

function log(step, msg, data) {{
  const panel = document.getElementById("log-panel");
  const entries = document.getElementById("log-entries");
  if (panel) panel.style.display = "block";
  const t = new Date().toLocaleTimeString();
  const color = data && data._err ? "#f38ba8" : "#a6e3a1";
  let detail = "";
  if (data) {{
    const clean = {{ ...data }};
    delete clean._err;
    detail = Object.entries(clean).map(([k,v]) => {{
      const s = String(v);
      const display = s.length > 80 ? s.substring(0, 40) + "..." + s.substring(s.length - 20) : s;
      return '  <span style="color:#cdd6f4">' + k + '</span>: ' + display;
    }}).join("\\n");
  }}
  const entry = document.createElement("div");
  entry.style.cssText = "margin-bottom:0.5rem;border-bottom:1px solid #313244;padding-bottom:0.5rem;white-space:pre-wrap;";
  entry.innerHTML = '<span style="color:#585b70">' + t + '</span> <span style="color:' + color + ';font-weight:bold">[' + step + ']</span> ' + msg + (detail ? "\\n" + detail : "");
  if (entries) entries.appendChild(entry);
  if (panel) panel.scrollTop = panel.scrollHeight;
}}

async function completeAuth() {{
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session_id");
  const jwt = sessionStorage.getItem("auth_jwt");
  const roleArn = sessionStorage.getItem("auth_role_arn");

  log("CB-INIT", "Callback page loaded", {{
    sessionId: sessionId || "(none)",
    jwt: jwt ? "(present, " + jwt.length + " chars)" : "(missing)",
    roleArn: roleArn || "(missing)",
    queryString: window.location.search,
  }});

  if (!sessionId || !jwt || !roleArn) {{
    const missing = [!sessionId && "session_id", !jwt && "JWT", !roleArn && "roleArn"].filter(Boolean).join(", ");
    log("CB-INIT", "Missing data: " + missing, {{ _err: true }});
    showResult(false, "Missing session data (" + missing + "). Did you start from the auth page?");
    return;
  }}

  try {{
    // Step 1: Get temporary AWS credentials via STS AssumeRoleWithWebIdentity
    log("CB-STS", "Calling AssumeRoleWithWebIdentity...", {{ roleArn: roleArn }});
    const stsClient = new STSClient({{ region: REGION }});
    const stsResp = await stsClient.send(new AssumeRoleWithWebIdentityCommand({{
      RoleArn: roleArn,
      RoleSessionName: "auth-callback-" + Date.now(),
      WebIdentityToken: jwt,
    }}));

    const creds = stsResp.Credentials;
    log("CB-STS", "Got temporary credentials", {{
      accessKeyId: creds.AccessKeyId.substring(0, 8) + "...",
      expiration: creds.Expiration.toISOString(),
    }});

    // Step 2: Call CompleteResourceTokenAuth directly with SigV4
    log("CB-COMPLETE", "Calling CompleteResourceTokenAuth via SigV4...", {{ sessionUri: sessionId }});
    const acClient = new BedrockAgentCoreClient({{
      region: REGION,
      credentials: {{
        accessKeyId: creds.AccessKeyId,
        secretAccessKey: creds.SecretAccessKey,
        sessionToken: creds.SessionToken,
      }},
    }});

    await acClient.send(new CompleteResourceTokenAuthCommand({{
      sessionUri: sessionId,
      userIdentifier: {{ userToken: jwt }},
    }}));

    log("CB-COMPLETE", "Success — token stored in vault");

    // Clean up — JWT and role ARN served their purpose
    sessionStorage.removeItem("auth_jwt");
    sessionStorage.removeItem("auth_role_arn");
    log("CB-DONE", "Authorization complete — no Lambda proxy involved");
    showResult(true, "Authorization complete. You can now use MCP tools in VS Code and the web app.");
  }} catch (e) {{
    log("CB-ERROR", e.message || String(e), {{ _err: true }});
    showResult(false, e.message || String(e));
  }}
}}

function showResult(success, message) {{
  document.getElementById("loading").style.display = "none";
  const r = document.getElementById("result");
  r.style.display = "block";
  const cls = success ? "success" : "error";
  const title = success ? "Authorization Successful" : "Authorization Failed";
  r.innerHTML = '<h2 class="' + cls + '">' + title + '</h2><p>' + message + '</p><a class="btn" href="' + AUTH_PAGE_URL + '">Back to Auth Onboarding</a>';
}}

completeAuth();
</script>
</body>
</html>"""

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": html,
    }


def handle_oauth_metadata(event):
    """Serve OAuth Authorization Server Metadata (RFC 8414) — pointing to EntraID."""
    api_url = get_api_url(event)
    return json_response(
        200,
        {
            "issuer": api_url,
            "authorization_endpoint": f"{api_url}/authorize",
            "token_endpoint": f"{api_url}/token",
            "registration_endpoint": f"{api_url}/register",
            "scopes_supported": [
                f"api://{ENTRA_APP_A_CLIENT_ID}/gateway.access",
                "openid",
                "profile",
                "email",
            ],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        },
    )


def handle_protected_resource_metadata(event):
    """Serve OAuth Protected Resource Metadata (RFC 9728)."""
    api_url = get_api_url(event)
    return json_response(
        200,
        {
            "resource": f"{api_url}/mcp",
            "authorization_servers": [api_url],
            "bearer_methods_supported": ["header"],
        },
    )


def handle_authorize(event):
    """Redirect /authorize to EntraID with callback interception.

    Encodes the original redirect_uri in the state parameter so it survives
    across Lambda invocations (Lambda is stateless).
    """
    params = event.get("queryStringParameters", {}) or {}
    print("=== HANDLE_AUTHORIZE (EntraID) ===")
    print(f"Original params: {json.dumps(params)}")

    # Remove unsupported parameters
    params.pop("resource", None)

    # Fix scope: convert + to spaces
    if "scope" in params:
        params["scope"] = params["scope"].replace("+", " ")

    # Override client_id with EntraID App A
    params["client_id"] = ENTRA_APP_A_CLIENT_ID

    # Inject App A gateway scope — without this, EntraID issues a token for
    # Microsoft Graph (aud=00000003-...) instead of for our API (aud=App A client ID).
    # The Gateway validates aud == ENTRA_APP_A_CLIENT_ID, so we MUST request this scope.
    gateway_scope = f"api://{ENTRA_APP_A_CLIENT_ID}/gateway.access"
    current_scope = params.get("scope", "openid profile email")
    if gateway_scope not in current_scope:
        params["scope"] = f"{gateway_scope} {current_scope}"

    # Encode original redirect_uri and state in compound state
    original_redirect_uri = params.get("redirect_uri", "")
    original_state = params.get("state", "")

    if original_redirect_uri:
        decoded_state = urllib.parse.unquote(original_state)
        decoded_redirect_uri = urllib.parse.unquote(original_redirect_uri)

        compound_state = {
            "state": decoded_state,
            "redirect_uri": decoded_redirect_uri,
        }
        encoded_state = base64.urlsafe_b64encode(json.dumps(compound_state).encode()).decode()
        params["state"] = encoded_state

        # Replace redirect_uri with our callback
        api_url = get_api_url(event)
        params["redirect_uri"] = f"{api_url}/callback"

    redirect_url = f"{ENTRA_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    print(f"Redirect URL: {redirect_url}")
    return {"statusCode": 302, "headers": {"Location": redirect_url}, "body": ""}


def handle_callback(event):
    """Handle OAuth callback from EntraID and forward to VS Code.

    Decodes the compound state to extract original redirect_uri and state.
    """
    params = event.get("queryStringParameters", {}) or {}
    code = params.get("code", "")
    encoded_state = params.get("state", "")
    error = params.get("error", "")

    print("=== HANDLE_CALLBACK (EntraID) ===")
    if error:
        return json_response(
            400,
            {"error": error, "error_description": params.get("error_description", "")},
        )

    try:
        encoded_state_clean = urllib.parse.unquote(encoded_state).replace(" ", "+")
        decoded = base64.urlsafe_b64decode(encoded_state_clean).decode()
        compound_state = json.loads(decoded)
        original_state = compound_state.get("state", "")
        original_redirect_uri = compound_state.get("redirect_uri", "")
    except Exception as e:
        print(f"Error decoding state: {e}")
        return json_response(400, {"error": "Invalid state parameter"})

    if not original_redirect_uri:
        return json_response(400, {"error": "Missing redirect_uri in state"})

    forward_params = urllib.parse.urlencode({"code": code, "state": original_state})
    forward_url = f"{original_redirect_uri}?{forward_params}"
    return {"statusCode": 302, "headers": {"Location": forward_url}, "body": ""}


def handle_token(event):
    """Proxy token requests to EntraID with redirect_uri rewriting."""
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    params = dict(urllib.parse.parse_qsl(body))

    # Remove 'resource' parameter — EntraID v2.0 uses scopes, not resources.
    # VS Code sends resource per RFC 9728 but it causes AADSTS9010010 on EntraID.
    params.pop("resource", None)

    # Override client_id — EntraID App A is a public client (SPA), no secret needed
    params["client_id"] = ENTRA_APP_A_CLIENT_ID

    # Rewrite redirect_uri to our callback
    if "redirect_uri" in params:
        api_url = get_api_url(event)
        params["redirect_uri"] = f"{api_url}/callback"

    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(ENTRA_TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    # EntraID requires an Origin header for SPA (public client) token redemption.
    # Without it, EntraID returns AADSTS9002327: "may only be redeemed via cross-origin requests".
    req.add_header("Origin", get_api_url(event))

    # Validate URL scheme to prevent file:// or other unexpected schemes (bandit B310)
    if not req.full_url.startswith("https://"):
        return json_response(400, {"error": "Invalid token endpoint URL scheme"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            token_data = json.loads(resp.read().decode())
            if "created_at" not in token_data:
                token_data["created_at"] = int(time.time() * 1000)
            # Log token metadata for debugging (NOT the token itself)
            print(f"Token response keys: {list(token_data.keys())}")
            print(
                f"Token type: {token_data.get('token_type')}, expires_in: {token_data.get('expires_in')}, scope: {token_data.get('scope')}"
            )
            return json_response(200, token_data)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"EntraID token error (HTTP {e.code}): {error_body}")
        return json_response(e.code, {"error": error_body})


def handle_dcr(event):
    """Handle Dynamic Client Registration — return pre-registered EntraID App A client_id."""
    return json_response(
        200,
        {
            "client_id": ENTRA_APP_A_CLIENT_ID,
            "client_name": "VS Code MCP Client (EntraID)",
            "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [f"{get_api_url(event)}/callback"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def proxy_to_gateway(event):
    """Forward MCP requests to AgentCore Gateway."""
    print("proxy_to_gateway")
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    headers = event.get("headers", {})
    body = event.get("body", "")

    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body)

    target_url = GATEWAY_URL

    # Validate URL scheme to prevent file:// or other unexpected schemes (bandit B310)
    if not target_url.startswith("https://"):
        return json_response(502, {"error": "Invalid gateway URL scheme"})

    req_headers = {
        "Content-Type": headers.get("content-type", "application/json"),
        "Accept": headers.get("accept", "application/json"),
    }

    # Forward MCP headers
    for h in ["mcp-protocol-version", "mcp-session-id"]:
        if headers.get(h):
            req_headers[h.title()] = headers[h]
    req_headers["Mcp-Protocol-Version"] = "2025-11-25"

    try:
        if method == "POST" and body:
            data = body.encode() if isinstance(body, str) else body
            req = urllib.request.Request(target_url, data=data, method="POST")
        else:
            req = urllib.request.Request(target_url, method=method)

        for k, v in req_headers.items():
            req.add_header(k, v)

        # Forward the EntraID JWT as Authorization header.
        # When the Gateway uses custom JWT auth (not IAM), it expects ONLY the
        # Bearer token — no SigV4 signing. SigV4 headers would confuse it.
        auth = headers.get("authorization")
        if auth:
            req.add_header("Authorization", auth)
            print(f"Forwarding Authorization header (first 50 chars): {auth[:50]}...")
        else:
            # No JWT from client — fall back to SigV4 for IAM-based auth
            print("No Authorization header from client — signing with SigV4 only")
            sign_request(req)

        print(
            "{}\n{}\r\n{}\r\n\r\n{}".format(
                "-----------START-----------",
                (req.method or "GET") + " " + req.full_url,
                "\r\n".join("{}: {}".format(k, v) for k, v in req.headers.items()),
                req.data,
            )
        )

        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            resp_body = resp.read().decode()
            print(resp_body)
            resp_headers = {"Content-Type": resp.headers.get("Content-Type", "application/json")}

            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                resp_headers["Mcp-Session-Id"] = session_id

            # Rewrite Gateway URLs in WWW-Authenticate header to use our endpoint
            www_auth = resp.headers.get("WWW-Authenticate")
            if www_auth:
                api_url = get_api_url(event)
                gateway_base = GATEWAY_URL[:-4] if GATEWAY_URL.endswith("/mcp") else GATEWAY_URL
                www_auth_rewritten = www_auth.replace(gateway_base, api_url)
                resp_headers["WWW-Authenticate"] = www_auth_rewritten

            return {
                "statusCode": resp.status,
                "headers": resp_headers,
                "body": resp_body,
            }
    except urllib.error.HTTPError as e:
        error = e.read().decode()
        print(f"Gateway error response: {error}")

        api_url = get_api_url(event)
        gateway_base = GATEWAY_URL[:-4] if GATEWAY_URL.endswith("/mcp") else GATEWAY_URL
        error_rewritten = error.replace(gateway_base, api_url)

        resp_headers = {"Content-Type": "application/json"}

        www_auth = e.headers.get("WWW-Authenticate")
        if www_auth:
            www_auth_rewritten = www_auth.replace(gateway_base, api_url)
            resp_headers["WWW-Authenticate"] = www_auth_rewritten

        return {
            "statusCode": e.code,
            "headers": resp_headers,
            "body": error_rewritten,
        }
    except Exception as e:
        return json_response(502, {"error": {"code": -32603, "message": str(e)}})


def get_api_url(event):
    """Extract API URL from event (supports both ALB and API Gateway)."""
    headers = event.get("headers", {})
    host = headers.get("host") or headers.get("Host")
    if host:
        return f"https://{host}"

    ctx = event.get("requestContext", {})
    domain = ctx.get("domainName", "")
    stage = ctx.get("stage", "")
    if domain and stage and stage != "$default":
        return f"https://{domain}/{stage}"
    elif domain:
        return f"https://{domain}"
    return "http://localhost"


def json_response(status_code, body):
    """Create JSON response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
