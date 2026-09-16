"""
MCP OAuth Proxy Lambda - Handles OAuth metadata, callback interception, token proxying, and MCP forwarding.

This Lambda function replaces the local mcp_oauth_proxy.py script, enabling serverless deployment.
"""

import json
import os
import re
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
import logging
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Configuration from environment variables
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN", "")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_IDENTITY_POOL_ID = os.environ.get("COGNITO_IDENTITY_POOL_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
CALLBACK_LAMBDA_URL = os.environ.get("CALLBACK_LAMBDA_URL", "")
RESOURCE_SERVER_ID = os.environ.get("RESOURCE_SERVER_ID", "")
MCP_METADATA_KEY = os.environ.get("MCP_METADATA_KEY", "com.example/target")

# RFC 9728 well-known prefix for Protected Resource Metadata
PROTECTED_RESOURCE_PREFIX = "/.well-known/oauth-protected-resource"

# Auth onboarding config
AUTH_ONBOARDING_ROLE_ARN = os.environ.get("AUTH_ONBOARDING_ROLE_ARN", "")
OAUTH_CREDENTIAL_PROVIDER_NAME = os.environ.get("OAUTH_CREDENTIAL_PROVIDER_NAME", "")

# Allowed redirect URIs for the OAuth callback, passed from CDK as a
# JSON-encoded list.  Must match the Cognito client's registered callbackUrls
# to prevent open-redirect attacks.
ALLOWED_REDIRECT_URIS = json.loads(os.environ.get("ALLOWED_REDIRECT_URIS", "[]"))


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

    # Update original request headers
    for key, value in aws_request.headers.items():
        request.add_header(key, value)


def lambda_handler(event, context):
    """Main Lambda handler - routes requests based on path."""
    logger.debug(f"Event: {json.dumps(event)}")

    # Support both ALB and API Gateway v2 (HTTP API) events
    # ALB uses: path, httpMethod
    # HTTP API uses: rawPath, requestContext.http.method
    path = event.get("path") or event.get("rawPath", "/")
    method = event.get("httpMethod") or event.get("requestContext", {}).get(
        "http", {}
    ).get("method", "GET")

    logger.debug(f"Method: {method}, Path: {path}")

    if method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {"Allow": "OPTIONS, GET, POST"},
            "body": "",
        }
    # Route to appropriate handler
    if path == "/ping":
        return handle_ping(event)
    elif path == "/auth":
        return handle_auth_page(event)
    elif path == "/auth/callback":
        return handle_auth_callback_page(event)
    elif path.startswith("/.well-known/oauth-authorization-server"):
        return handle_oauth_metadata(event)
    # Must be checked before the "/mcp" route below: the path-inserted form
    # (/.well-known/oauth-protected-resource/figma/mcp) also ends with "/mcp".
    elif path.startswith(PROTECTED_RESOURCE_PREFIX):
        return handle_protected_resource_metadata(event)
    elif path == "/authorize":
        return handle_authorize(event)
    elif path == "/callback":
        return handle_callback(event)
    elif path == "/token" and method == "POST":
        return handle_token(event)
    elif path == "/register" and method == "POST":
        return handle_dcr(event)
    elif path == "/mcp" or path.endswith("/mcp"):
        return proxy_to_gateway(event)
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "Not found"})}


def handle_ping(event):
    """Health check endpoint for ALB target group."""
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "healthy", "service": "mcp-proxy"}),
    }


def handle_auth_page(event):
    """Serve the auth onboarding SPA — uses Cognito Hosted UI with PKCE.

    Similar to the EntraID example but using Cognito Hosted UI instead of MSAL.js.
    The SPA authenticates with Cognito using PKCE, gets a JWT, then calls POST /mcp
    to trigger outbound auth. If the Gateway returns an elicitation (-32042), the SPA
    extracts the authorization URL and redirects the user to consent.
    """
    api_url = get_api_url(event)
    region = os.environ.get("AWS_REGION", "us-east-1")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auth Onboarding</title>
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
    <button class="btn btn-primary" onclick="signIn()" id="signin-btn">Sign in with Cognito</button>
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

const CONFIG = {{
  apiUrl: "{api_url}",
  region: "{region}",
  cognitoDomain: "{COGNITO_DOMAIN}",
  userPoolId: "{COGNITO_USER_POOL_ID}",
  clientId: "{CLIENT_ID}",
  roleArn: "{AUTH_ONBOARDING_ROLE_ARN}",
  oauthProvider: "{OAUTH_CREDENTIAL_PROVIDER_NAME}",
  redirectUri: "{api_url}/auth",
  resourceServerId: "{RESOURCE_SERVER_ID}",
}};

log("CONFIG", "Loaded", {{
  clientId: CONFIG.clientId,
  userPoolId: CONFIG.userPoolId,
  redirectUri: CONFIG.redirectUri,
  resourceServerId: CONFIG.resourceServerId
}});

// PKCE helper functions
function generateCodeVerifier() {{
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return base64UrlEncode(array);
}}

function base64UrlEncode(buffer) {{
  const base64 = btoa(String.fromCharCode.apply(null, buffer));
  return base64.replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
}}

async function generateCodeChallenge(verifier) {{
  const encoder = new TextEncoder();
  const data = encoder.encode(verifier);
  const hash = await crypto.subtle.digest('SHA-256', data);
  return base64UrlEncode(new Uint8Array(hash));
}}

// Check for OAuth callback on page load
async function handleOAuthCallback() {{
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');

  if (code) {{
    log("1-CALLBACK", "OAuth callback detected", {{ code: code.substring(0, 20) + "..." }});

    // Exchange code for tokens
    const codeVerifier = sessionStorage.getItem('pkce_code_verifier');
    if (!codeVerifier) {{
      throw new Error("Missing PKCE code verifier in session");
    }}

    log("2-TOKEN", "Exchanging authorization code for tokens...");
    const tokenResp = await fetch(CONFIG.cognitoDomain + "/oauth2/token", {{
      method: "POST",
      headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
      body: new URLSearchParams({{
        grant_type: "authorization_code",
        client_id: CONFIG.clientId,
        code: code,
        redirect_uri: CONFIG.redirectUri,
        code_verifier: codeVerifier,
      }}),
    }});

    if (!tokenResp.ok) {{
      const error = await tokenResp.text();
      throw new Error("Token exchange failed: " + error);
    }}

    const tokens = await tokenResp.json();
    log("2-TOKEN", "Got tokens", {{ id_token_length: tokens.id_token.length }});

    // Store tokens in localStorage (persists across tabs)
    localStorage.setItem('id_token', tokens.id_token);
    localStorage.setItem('access_token', tokens.access_token);
    if (tokens.refresh_token) {{
      localStorage.setItem('refresh_token', tokens.refresh_token);
    }}
    sessionStorage.removeItem('pkce_code_verifier');

    // Clear URL params and show authenticated UI
    window.history.replaceState({{}}, document.title, CONFIG.redirectUri);
    await onSignedIn();
    return true;
  }}

  return false;
}}

async function checkAuth() {{
  try {{
    // Check if we have tokens in session (check access token since that's what we need)
    const accessToken = localStorage.getItem('access_token');
    if (accessToken) {{
      log("1-AUTH", "Found cached session");
      await onSignedIn();
      return;
    }}

    log("1-AUTH", "No session - showing sign-in button");
  }} catch (e) {{
    log("1-AUTH", "Auth check failed: " + e.message, {{ _err: true }});
  }}
}}

async function signIn() {{
  try {{
    document.getElementById("signin-btn").disabled = true;
    document.getElementById("signin-btn").innerHTML = '<span class="spinner"></span>Redirecting...';
    hideError();

    log("1-SIGNIN", "Generating PKCE challenge...");
    const codeVerifier = generateCodeVerifier();
    const codeChallenge = await generateCodeChallenge(codeVerifier);

    // Store verifier for later
    sessionStorage.setItem('pkce_code_verifier', codeVerifier);

    // Build authorization URL
    const authUrl = new URL(CONFIG.cognitoDomain + "/oauth2/authorize");
    authUrl.searchParams.set('client_id', CONFIG.clientId);
    authUrl.searchParams.set('response_type', 'code');
    authUrl.searchParams.set('scope', 'openid profile email ' + CONFIG.resourceServerId + '/mcp.read');
    authUrl.searchParams.set('redirect_uri', CONFIG.redirectUri);
    authUrl.searchParams.set('code_challenge', codeChallenge);
    authUrl.searchParams.set('code_challenge_method', 'S256');

    log("1-SIGNIN", "Redirecting to Cognito Hosted UI...", {{ authUrl: authUrl.toString().substring(0, 100) + "..." }});
    window.location.href = authUrl.toString();
  }} catch (e) {{
    log("1-SIGNIN", "Sign-in failed: " + e.message, {{ _err: true }});
    showError("Sign-in failed: " + e.message);
    document.getElementById("signin-btn").disabled = false;
    document.getElementById("signin-btn").innerHTML = 'Sign in with Cognito';
  }}
}}

async function signOut() {{
  // Clear all tokens from both session and local storage
  sessionStorage.clear();
  localStorage.removeItem('id_token');
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');

  const logoutUrl = new URL(CONFIG.cognitoDomain + "/logout");
  logoutUrl.searchParams.set('client_id', CONFIG.clientId);
  logoutUrl.searchParams.set('logout_uri', CONFIG.redirectUri);
  window.location.href = logoutUrl.toString();
}}

function getAccessToken() {{
  return localStorage.getItem('access_token');
}}

function getIdToken() {{
  return localStorage.getItem('id_token');
}}

async function onSignedIn() {{
  document.getElementById("login-section").style.display = "none";
  document.getElementById("registry-section").style.display = "block";

  try {{
    const accessToken = getAccessToken();
    const idToken = getIdToken();

    if (!accessToken || !idToken) {{
      throw new Error("Missing tokens");
    }}

    // Decode ID token to get username (simple base64 decode)
    const payload = JSON.parse(atob(idToken.split('.')[1]));
    document.getElementById("user-name").textContent = payload['cognito:username'] || payload.email || 'User';

    log("2-TOKEN", "Got Cognito tokens", {{
      access_token_length: accessToken.length,
      id_token_length: idToken.length,
      username: payload['cognito:username']
    }});

    // Use access token for MCP API calls, but save ID token for callback page
    await checkMcpAuth(accessToken, idToken);
  }} catch (e) {{
    log("ERROR", e.message, {{ _err: true }});
    showError("Failed: " + e.message);
  }}
}}

async function checkMcpAuth(accessToken, idToken) {{
  const list = document.getElementById("providers-list");
  list.innerHTML = "";

  try {{
    // Step 1: Discover all available tools via tools/list with pagination (use access token)
    log("3-MCP", "Calling tools/list to discover available tools...");
    const allTools = [];
    let cursor = undefined;
    let pageCount = 0;

    do {{
      pageCount++;
      const requestBody = {{
        jsonrpc: "2.0",
        id: pageCount,
        method: "tools/list"
      }};

      if (cursor) {{
        requestBody.params = {{ cursor: cursor }};
      }}

      const toolsListResp = await fetch(CONFIG.apiUrl + "/mcp", {{
        method: "POST",
        headers: {{
          "Content-Type": "application/json",
          "Authorization": "Bearer " + accessToken,
          "Mcp-Protocol-Version": "2025-11-25",
        }},
        body: JSON.stringify(requestBody),
      }});

      const toolsListData = await toolsListResp.json();
      if (toolsListData.error) {{
        throw new Error("tools/list failed: " + toolsListData.error.message);
      }}

      const pageTools = toolsListData.result?.tools || [];
      allTools.push(...pageTools);
      cursor = toolsListData.result?.nextCursor;

      log("3-MCP", `Discovered tools (page ${{pageCount}})`, {{
        pageTools: pageTools.length,
        totalTools: allTools.length,
        hasMore: !!cursor
      }});
    }} while (cursor);

    const tools = allTools;
    log("3-MCP", "Discovered all tools", {{ count: tools.length, pages: pageCount }});

    // Step 2: Group tools by target (prefix before ___)
    const targetMap = new Map();
    for (const tool of tools) {{
      const match = tool.name.match(/^(.+?)___/);
      if (match) {{
        const target = match[1];
        if (!targetMap.has(target)) {{
          targetMap.set(target, []);
        }}
        targetMap.get(target).push(tool);
      }}
    }}

    if (targetMap.size === 0) {{
      list.innerHTML = '<div class="card"><p style="color:#666;">No OAuth-protected tools found.</p></div>';
      return;
    }}

    log("3-MCP", "Found targets", {{ targets: Array.from(targetMap.keys()) }});

    // Step 3: For each target, create a card and check authorization
    for (const [target, targetTools] of targetMap) {{
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = '<div class="provider-row"><div class="provider-info"><h2>' + target + '</h2><p class="provider-scope">Tools: ' + targetTools.map(t => t.name.replace(target + '___', '')).join(', ') + '</p></div><div><span class="status status-checking"><span class="spinner"></span>Checking...</span></div></div>';
      list.appendChild(card);

      // Check authorization using the tool least likely to fail input validation
      const probeTool = selectProbeTool(targetTools);
      log("3-MCP", "Selected probe tool for " + target, {{
        tool: probeTool.name,
        required_params: probeTool.inputSchema?.required || [],
      }});
      await checkTargetAuth(accessToken, idToken, target, probeTool, card);
    }}
  }} catch (e) {{
    list.innerHTML = '<div class="card"><div class="status status-error">Error</div><p style="color:#dc3545;margin-top:0.5rem;">' + e.message + '</p></div>';
    log("ERROR", e.message, {{ _err: true }});
  }}
}}

// Count how many required parameters a tool has (0 = safest to call blind)
function requiredParamCount(tool) {{
  const required = tool.inputSchema?.required || [];
  return required.length;
}}

// Pick the tool most likely to succeed as an auth probe:
//   1. Tools with no required parameters (e.g. whoami, get_libraries)
//   2. Otherwise the tool with the fewest required parameters
//   3. Ties broken by name preference (whoami/me/list/get first), then original order
function selectProbeTool(tools) {{
  const preferredNames = ['whoami', 'me', 'ping', 'health', 'list', 'get'];
  const nameScore = (tool) => {{
    const short = tool.name.replace(/^.+?___/, '').toLowerCase();
    const idx = preferredNames.findIndex(p => short === p || short.startsWith(p + '_') || short.endsWith('_' + p));
    return idx === -1 ? preferredNames.length : idx;
  }};

  const ranked = tools
    .map((tool, index) => ({{ tool, index, required: requiredParamCount(tool), name: nameScore(tool) }}))
    .sort((a, b) => a.required - b.required || a.name - b.name || a.index - b.index);

  return ranked[0].tool;
}}

// Generate minimal valid arguments for a tool based on its input schema
function generateTestArguments(tool) {{
  if (!tool.inputSchema || !tool.inputSchema.properties) {{
    return {{}};
  }}

  const args = {{}};
  const props = tool.inputSchema.properties;
  const required = tool.inputSchema.required || [];

  // Helper to generate string matching a pattern
  function generateStringForPattern(pattern) {{
    // Handle common Figma patterns
    if (pattern === '^$|^(?:-?\\\\d+[:-]-?\\\\d+)$') {{
      return '';  // Empty string is valid for Figma nodeId
    }}
    if (pattern === '^[0-9a-zA-Z]{{1,128}}$') {{
      return 'test123';  // Alphanumeric, 1-128 chars
    }}
    // Default: return empty string (often valid)
    return '';
  }}

  // Only generate arguments for required fields
  for (const key of required) {{
    const prop = props[key];
    if (!prop) continue;

    // Generate minimal value based on type
    if (prop.type === 'string') {{
      if (prop.enum && prop.enum.length > 0) {{
        args[key] = prop.enum[0];
      }} else if (prop.default !== undefined) {{
        args[key] = prop.default;
      }} else if (prop.pattern) {{
        args[key] = generateStringForPattern(prop.pattern);
      }} else {{
        // Use empty string as default (more likely to be valid than 'test')
        args[key] = '';
      }}
    }} else if (prop.type === 'number' || prop.type === 'integer') {{
      args[key] = prop.default !== undefined ? prop.default : 0;
    }} else if (prop.type === 'boolean') {{
      args[key] = prop.default !== undefined ? prop.default : false;
    }} else if (prop.type === 'array') {{
      args[key] = [];
    }} else if (prop.type === 'object') {{
      args[key] = {{}};
    }} else {{
      args[key] = null;
    }}
  }}

  return args;
}}

async function checkTargetAuth(accessToken, idToken, target, tool, card) {{
  try {{
    log("4-AUTH-CHECK", "Checking authorization for target: " + target, {{ tool: tool.name }});

    // Log the access token being used for this call
    const accessTokenPayload = JSON.parse(atob(accessToken.split('.')[1]));
    log("4-AUTH-CHECK", "Access token details for tools/call", {{
      jti: accessTokenPayload.jti,
      exp: new Date(accessTokenPayload.exp * 1000).toISOString(),
      sub: accessTokenPayload.sub,
    }});

    // Generate test arguments based on tool schema
    const testArgs = generateTestArguments(tool);
    log("4-AUTH-CHECK", "Generated test arguments", {{
      tool: tool.name,
      args: testArgs,
      required_params: tool.inputSchema?.required || [],
    }});

    // Make a test tools/call (use access token for API call)
    const mcpResp = await fetch(CONFIG.apiUrl + "/mcp", {{
      method: "POST",
      headers: {{
        "Content-Type": "application/json",
        "Authorization": "Bearer " + accessToken,
        "Mcp-Protocol-Version": "2025-11-25",
      }},
      body: JSON.stringify({{
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: {{ name: tool.name, arguments: testArgs }},
        _meta: {{ rawElicitation: true }}
      }}),
    }});

    const mcpData = await mcpResp.json();
    log("4-AUTH-CHECK", "Response for " + target, {{
      hasResult: !!mcpData.result,
      hasError: !!mcpData.error,
      isError: mcpData.result?.isError,
      errorCode: mcpData.error ? mcpData.error.code : "(none)",
      errorMessage: mcpData.error ? mcpData.error.message?.substring(0, 100) : "(none)",
    }});

    // Check for MCP server errors in result.isError (server unreachable, etc.)
    if (mcpData.result?.isError && mcpData.result.content) {{
      const errorText = mcpData.result.content
        .filter(c => c.type === "text")
        .map(c => c.text)
        .join(" ");

      log("4-AUTH-CHECK", "⚠️ MCP server error for " + target, {{
        _err: true,
        errorText: errorText,
      }});

      card.querySelector(".status").className = "status status-error";
      card.querySelector(".status").textContent = "Server Error";
      const errP = document.createElement("p");
      errP.style.cssText = "color:#dc3545;font-size:0.85rem;margin-top:0.5rem;";
      errP.textContent = errorText;
      card.querySelector(".provider-info").appendChild(errP);
      return;
    }}

    // Check for validation errors (means our test args are bad)
    if (mcpData.error && (mcpData.error.code === -32602 || mcpData.error.message?.includes("validation failed"))) {{
      log("4-AUTH-CHECK", "⚠️ Test arguments validation failed - cannot check auth status", {{
        _err: true,
        errorMessage: mcpData.error.message,
        testArgs: testArgs,
      }});
      card.querySelector(".status").className = "status status-error";
      card.querySelector(".status").textContent = "Cannot check auth (invalid test args)";
      const errP = document.createElement("p");
      errP.style.cssText = "color:#dc3545;font-size:0.85rem;margin-top:0.5rem;";
      errP.textContent = "Validation error: " + (mcpData.error.message?.substring(0, 100) || "Unknown");
      card.querySelector(".provider-info").appendChild(errP);
      return;
    }}

    if (mcpData.error && mcpData.error.code === -32042) {{
      // Elicitation — needs authorization
      const elicitations = mcpData.error.data && mcpData.error.data.elicitations;
      if (elicitations && elicitations.length > 0) {{
        const authUrl = elicitations[0].url;

        // Extract session_id from authorization URL for debugging
        let elicitedSessionId = "N/A";
        try {{
          const urlObj = new URL(authUrl);
          const callbackUrl = urlObj.searchParams.get("redirect_uri");
          if (callbackUrl) {{
            const callbackUrlObj = new URL(callbackUrl);
            elicitedSessionId = callbackUrlObj.searchParams.get("session_id") || "N/A";
          }}
        }} catch(e) {{
          console.warn("Could not extract session_id from auth URL:", e);
        }}

        log("4-AUTH-CHECK", target + " needs authorization", {{
          authorizationUrl: authUrl.substring(0, 100) + "...",
          elicited_session_id: elicitedSessionId,
        }});

        card.querySelector(".status").className = "status status-needs-auth";
        card.querySelector(".status").textContent = "Authorization needed";
        const btnDiv = card.querySelector(".provider-row").lastElementChild;
        const btn = document.createElement("button");
        btn.className = "btn btn-primary";
        btn.style.marginLeft = "1rem";
        btn.textContent = "Authorize";
        btn.onclick = function() {{
          // IMPORTANT: Use localStorage, not sessionStorage!
          // sessionStorage is cleared during cross-origin redirects (to Figma and back)
          // localStorage persists across redirects within the same domain

          // Save the SAME token type that was used in tools/call (access token)
          // The session was created with accessToken, so we must use accessToken in CompleteResourceTokenAuth
          localStorage.setItem("auth_flow_access_token", accessToken);
          localStorage.setItem("auth_flow_id_token", idToken);
          localStorage.setItem("auth_flow_role_arn", CONFIG.roleArn);
          localStorage.setItem("auth_flow_elicited_session_id", elicitedSessionId);

          // Save timestamp and JWT hash for debugging
          localStorage.setItem("auth_flow_started_at", new Date().toISOString());
          const jwtPayload = JSON.parse(atob(accessToken.split('.')[1]));
          localStorage.setItem("auth_flow_jwt_jti", jwtPayload.jti || "N/A");
          localStorage.setItem("auth_flow_jwt_exp", jwtPayload.exp || "N/A");
          localStorage.setItem("auth_flow_jwt_first_50", accessToken.substring(0, 50));
          localStorage.setItem("auth_flow_jwt_last_50", accessToken.substring(accessToken.length - 50));

          console.log("=== SAVING ACCESS TOKEN TO LOCALSTORAGE ===");
          console.log("Access token JTI:", jwtPayload.jti);
          console.log("Access token first 50 chars:", accessToken.substring(0, 50));
          console.log("Access token last 50 chars:", accessToken.substring(accessToken.length - 50));
          console.log("Full token length:", accessToken.length);
          console.log("Elicited session ID:", elicitedSessionId);

          log("5-REDIRECT", "Saved to localStorage (survives cross-origin redirect), redirecting to consent...", {{
            target: target,
            authorizationUrl: authUrl.substring(0, 100) + "...",
            elicited_session_id: elicitedSessionId,
            access_token_jti: jwtPayload.jti,
            access_token_length: accessToken.length,
            access_token_first_50: accessToken.substring(0, 50),
            access_token_exp: new Date(jwtPayload.exp * 1000).toISOString(),
            started_at: new Date().toISOString(),
          }});
          window.location.href = authUrl;
        }};
        btnDiv.appendChild(btn);
      }} else {{
        throw new Error("Elicitation missing authorization URL");
      }}
    }} else if (mcpData.result) {{
      // Already authorized (tool executed successfully)
      card.querySelector(".status").className = "status status-authorized";
      card.querySelector(".status").textContent = "Authorized ✓";
      log("4-AUTH-CHECK", target + " is already authorized — tool call succeeded");
    }} else if (mcpData.error) {{
      // Other error (not elicitation) - could be tool error, validation error, etc.
      // If we got here without elicitation, auth likely succeeded but tool execution failed
      const errorCode = mcpData.error.code;
      const errorMsg = mcpData.error.message || "";

      if (errorCode === -32602 || errorMsg.includes("Invalid params") || errorMsg.includes("required")) {{
        // Parameter validation error - auth succeeded but our test args were invalid
        card.querySelector(".status").className = "status status-authorized";
        card.querySelector(".status").textContent = "Authorized (⚠ param check failed)";
        log("4-AUTH-CHECK", target + " — auth OK, but test args invalid", {{ errorCode: errorCode, message: errorMsg.substring(0, 100) }});
      }} else {{
        // Other tool error - auth likely succeeded
        card.querySelector(".status").className = "status status-authorized";
        card.querySelector(".status").textContent = "Authorized (⚠ tool error: " + errorCode + ")";
        log("4-AUTH-CHECK", target + " — auth OK, tool returned error", {{ errorCode: errorCode, message: errorMsg.substring(0, 100) }});
      }}
    }} else {{
      throw new Error("Unexpected response");
    }}
  }} catch (e) {{
    card.querySelector(".status").className = "status status-error";
    card.querySelector(".status").textContent = "Error";
    const errP = document.createElement("p");
    errP.style.cssText = "color:#dc3545;font-size:0.85rem;margin-top:0.5rem;";
    errP.textContent = e.message;
    card.querySelector(".provider-info").appendChild(errP);
    log("ERROR", "Auth check failed for " + target + ": " + e.message, {{ _err: true }});
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

// Initialize and check auth
(async function() {{
  try {{
    log("INIT", "Starting initialization...", {{ userPoolId: CONFIG.userPoolId, clientId: CONFIG.clientId }});

    // Check if this is an OAuth callback
    const isCallback = await handleOAuthCallback();
    if (!isCallback) {{
      // Not a callback, check for existing session
      await checkAuth();
    }}
  }} catch (e) {{
    log("INIT", "Initialization failed: " + e.message, {{ _err: true }});
    showError("Initialization failed: " + e.message);
    document.getElementById("signin-btn").disabled = false;
    document.getElementById("signin-btn").innerHTML = 'Sign in with Cognito';
  }}
}})();
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

    After the user consents, AgentCore redirects here with ?session_id=<urn:...>.
    The page:
    1. Reads the JWT from sessionStorage (saved by the main page before redirect)
    2. Calls STS AssumeRoleWithWebIdentity(JWT) to get temporary AWS credentials
    3. Calls CompleteResourceTokenAuth(sessionUri, userToken) with SigV4 signing

    No Lambda proxy needed — the browser calls AWS APIs directly using temp credentials.
    """
    logger.info("=== /auth/callback HANDLER START ===")

    # Log query parameters
    query_params = event.get("queryStringParameters", {}) or {}
    logger.info(f"Query parameters: {json.dumps(query_params)}")

    # Log specific session_id if present
    session_id = query_params.get("session_id")
    if session_id:
        logger.info(f"Session ID (raw): {session_id}")
        # Try to decode if URL-encoded
        try:
            decoded_session_id = urllib.parse.unquote(session_id)
            logger.info(f"Session ID (decoded): {decoded_session_id}")
        except Exception as e:
            logger.warning(f"Could not decode session_id: {e}")
    else:
        logger.warning("No session_id in query parameters!")

    # Log relevant headers
    headers = event.get("headers", {})
    logger.info(f"Referer: {headers.get('referer', 'N/A')}")
    logger.info(f"User-Agent: {headers.get('user-agent', 'N/A')}")
    logger.info(f"Host: {headers.get('host', 'N/A')}")

    # Log environment config being used
    logger.info(f"COGNITO_IDENTITY_POOL_ID: {COGNITO_IDENTITY_POOL_ID}")
    logger.info(f"COGNITO_USER_POOL_ID: {COGNITO_USER_POOL_ID}")
    logger.info(f"AUTH_ONBOARDING_ROLE_ARN: {AUTH_ONBOARDING_ROLE_ARN}")

    api_url = get_api_url(event)
    logger.info(f"API URL: {api_url}")
    region = os.environ.get("AWS_REGION", "us-east-1")
    logger.info(f"Region: {region}")

    logger.info("Serving auth callback HTML page...")
    logger.info("=== /auth/callback HANDLER - HTML PAGE BEING SERVED ===")

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
import {{ CognitoIdentityClient, GetIdCommand, GetCredentialsForIdentityCommand }} from "https://cdn.jsdelivr.net/npm/@aws-sdk/client-cognito-identity/+esm";
import {{ BedrockAgentCoreClient, CompleteResourceTokenAuthCommand }} from "https://cdn.jsdelivr.net/npm/@aws-sdk/client-bedrock-agentcore/+esm";

const AUTH_PAGE_URL = "{api_url}/auth";
const REGION = "{region}";
const IDENTITY_POOL_ID = "{COGNITO_IDENTITY_POOL_ID}";
const USER_POOL_ID = "{COGNITO_USER_POOL_ID}";
const ROLE_ARN = "{AUTH_ONBOARDING_ROLE_ARN}";

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
  try {{
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get("session_id");

    // Get tokens from localStorage (not sessionStorage - it doesn't survive cross-origin redirects)
    // auth_flow_access_token = access token (used for CompleteResourceTokenAuth - must match tools/call)
    // auth_flow_id_token = ID token (used for Cognito Identity Pool authentication)
    let accessToken = localStorage.getItem("auth_flow_access_token");
    let idToken = localStorage.getItem("auth_flow_id_token");
    let roleArn = localStorage.getItem("auth_flow_role_arn");

    // Get timing and JWT info from localStorage
    const flowStartedAt = localStorage.getItem("auth_flow_started_at");
    const savedJwtJti = localStorage.getItem("auth_flow_jwt_jti");
    const savedJwtExp = localStorage.getItem("auth_flow_jwt_exp");
    const elicitedSessionId = localStorage.getItem("auth_flow_elicited_session_id");
    const callbackTime = new Date();
    const timeElapsed = flowStartedAt ? (callbackTime - new Date(flowStartedAt)) / 1000 : "N/A";

    // Decode current access token to compare
    let currentJwtJti = "N/A";
    let currentJwtExp = "N/A";
    let jwtExpiresIn = "N/A";
    if (accessToken) {{
      try {{
        const payload = JSON.parse(atob(accessToken.split('.')[1]));
        currentJwtJti = payload.jti || "N/A";
        currentJwtExp = payload.exp || "N/A";
        jwtExpiresIn = currentJwtExp !== "N/A" ? (currentJwtExp - Math.floor(Date.now() / 1000)) : "N/A";
      }} catch(e) {{
        console.error("Failed to decode access token:", e);
      }}
    }}

    // Check if session_id matches what was elicited
    const sessionIdMatches = elicitedSessionId && sessionId === elicitedSessionId;

    log("CB-INIT", "Callback page loaded", {{
      callback_session_id: sessionId || "(none)",
      elicited_session_id: elicitedSessionId || "(not saved)",
      session_ids_match: sessionIdMatches ? "YES" : "NO - MISMATCH!",
      session_id_length: sessionId ? sessionId.length : 0,
      access_token: accessToken ? "(present)" : "(missing)",
      access_token_length: accessToken ? accessToken.length : 0,
      id_token: idToken ? "(present)" : "(missing)",
      id_token_length: idToken ? idToken.length : 0,
      jwt_jti: currentJwtJti,
      jwt_jti_matches: savedJwtJti === currentJwtJti ? "YES" : "NO",
      jwt_exp: currentJwtExp !== "N/A" ? new Date(currentJwtExp * 1000).toISOString() : "N/A",
      jwt_expires_in_seconds: jwtExpiresIn,
      roleArn: roleArn || "(missing)",
      queryString: window.location.search,
      href: window.location.href,
      flow_started_at: flowStartedAt || "N/A",
      callback_time: callbackTime.toISOString(),
      time_elapsed_seconds: timeElapsed,
    }});

    // Compare tokens
    const savedFirst50 = localStorage.getItem("auth_flow_jwt_first_50");
    const savedLast50 = localStorage.getItem("auth_flow_jwt_last_50");
    const currentFirst50 = accessToken ? accessToken.substring(0, 50) : "N/A";
    const currentLast50 = accessToken ? accessToken.substring(accessToken.length - 50) : "N/A";
    const tokenExactMatch = savedFirst50 === currentFirst50 && savedLast50 === currentLast50;

    console.log("=== /auth/callback CLIENT-SIDE DEBUG ===");
    console.log("Session ID from callback URL:", sessionId);
    console.log("Session ID from elicitation:", elicitedSessionId);
    console.log("Session IDs match:", sessionIdMatches, sessionIdMatches ? "✓" : "✗ PROBLEM!");
    console.log("---");
    console.log("access token present:", !!accessToken);
    console.log("id token present:", !!idToken);
    console.log("---");
    console.log("Access Token JTI (saved):", savedJwtJti, "vs (current):", currentJwtJti);
    console.log("Token JTI matches:", savedJwtJti === currentJwtJti, savedJwtJti === currentJwtJti ? "✓" : "✗");
    console.log("---");
    console.log("Access token first 50 (saved):", savedFirst50);
    console.log("Access token first 50 (current):", currentFirst50);
    console.log("Token first 50 matches:", savedFirst50 === currentFirst50);
    console.log("---");
    console.log("Access token last 50 (saved):", savedLast50);
    console.log("Access token last 50 (current):", currentLast50);
    console.log("Token last 50 matches:", savedLast50 === currentLast50);
    console.log("---");
    console.log("TOKEN EXACT MATCH:", tokenExactMatch, tokenExactMatch ? "✓✓✓" : "✗✗✗ PROBLEM!");
    console.log("---");
    console.log("Access token expires in:", jwtExpiresIn, "seconds");
    console.log("Time elapsed since auth started:", timeElapsed, "seconds");
    console.log("roleArn:", roleArn);

    if (!sessionIdMatches && elicitedSessionId && sessionId) {{
      console.error("⚠️ SESSION ID MISMATCH - This is likely the problem!");
      console.error("Expected:", elicitedSessionId);
      console.error("Got:", sessionId);
    }}

    if (!tokenExactMatch) {{
      console.error("⚠️ TOKEN MISMATCH - Access token has changed!");
      console.error("This means the token used in CompleteResourceTokenAuth is different from tools/call");
    }}

  // Check if we got the auth flow tokens
  if (!accessToken || !idToken) {{
    log("CB-ERROR", "Missing auth flow tokens in localStorage", {{
      _err: true,
      has_access_token: !!accessToken,
      has_id_token: !!idToken,
      has_regular_access_token: !!localStorage.getItem("access_token"),
      has_regular_id_token: !!localStorage.getItem("id_token"),
    }});
    console.error("⚠️ Auth flow tokens not found in localStorage!");
    console.error("This means the redirect to consent cleared the data, or authorization was not started from /auth page");
  }}

  if (!sessionId || !accessToken || !idToken || !roleArn) {{
    const missing = [!sessionId && "session_id", !accessToken && "access_token", !idToken && "id_token", !roleArn && "roleArn"].filter(Boolean).join(", ");
    log("CB-INIT", "Missing data: " + missing, {{ _err: true }});

    let message = "Missing session data (" + missing + ").\\n\\n";
    if (!accessToken || !idToken || !roleArn) {{
      message += "Please sign in first at the auth onboarding page, then retry the authorization.\\n\\n";
      message += "Redirecting to auth page in 3 seconds...";

      setTimeout(() => {{
        window.location.href = AUTH_PAGE_URL + "?from=callback";
      }}, 3000);
    }} else {{
      message += "Did you start from the auth page?";
    }}

    showResult(false, message);
    return;
  }}

  // Check if this session was already completed
  const completedSessions = JSON.parse(localStorage.getItem("completed_sessions") || "[]");
  if (completedSessions.includes(sessionId)) {{
    log("CB-INIT", "Session already completed - this session was already used", {{ _err: true, sessionId: sessionId }});
    console.warn("⚠️ This session_id was already completed. CompleteResourceTokenAuth can only be called once per session.");
    console.warn("If you're seeing this after a page refresh, you need to start a new authorization flow.");
    showResult(false, "This authorization session was already completed. Please start a new authorization from the auth page if you need to re-authorize.");
    return;
  }}

  try {{
    // Step 1: Get Cognito Identity ID using the User Pool ID token
    // Note: Cognito Identity Pool requires ID token, not access token
    log("CB-IDENTITY", "Getting Cognito Identity ID...", {{ identityPoolId: IDENTITY_POOL_ID }});
    const cognitoIdentityClient = new CognitoIdentityClient({{ region: REGION }});

    const getIdResp = await cognitoIdentityClient.send(new GetIdCommand({{
      IdentityPoolId: IDENTITY_POOL_ID,
      Logins: {{
        [`cognito-idp.${{REGION}}.amazonaws.com/${{USER_POOL_ID}}`]: idToken,
      }},
    }}));

    const identityId = getIdResp.IdentityId;
    log("CB-IDENTITY", "Got Identity ID", {{ identityId: identityId }});

    // Step 2: Get temporary AWS credentials for this identity
    log("CB-CREDS", "Getting temporary AWS credentials from Identity Pool...");
    const getCredsResp = await cognitoIdentityClient.send(new GetCredentialsForIdentityCommand({{
      IdentityId: identityId,
      Logins: {{
        [`cognito-idp.${{REGION}}.amazonaws.com/${{USER_POOL_ID}}`]: idToken,
      }},
    }}));

    const creds = getCredsResp.Credentials;

    // Note: Cognito Identity Pool returns "SecretKey" but AWS SDK expects "secretAccessKey"
    if (!creds || !creds.AccessKeyId || !creds.SecretKey || !creds.SessionToken) {{
      throw new Error("Invalid credentials received from Identity Pool: " + JSON.stringify(Object.keys(creds || {{}})));
    }}

    log("CB-CREDS", "Got temporary credentials", {{
      accessKeyId: creds.AccessKeyId.substring(0, 8) + "...",
      expiration: new Date(creds.Expiration * 1000).toISOString(),
      hasSecretKey: !!creds.SecretKey,
      hasSessionToken: !!creds.SessionToken,
    }});

    // Step 3: Call CompleteResourceTokenAuth with the credentials
    // IMPORTANT: Use the SAME token that was used in tools/call (access token)
    log("CB-COMPLETE", "Calling CompleteResourceTokenAuth via SigV4...", {{
      sessionUri: sessionId,
      sessionUri_length: sessionId.length,
      access_token_length: accessToken.length,
    }});

    console.log("=== CompleteResourceTokenAuth REQUEST ===");
    console.log("sessionUri:", sessionId);
    console.log("userToken (access token) length:", accessToken.length);
    console.log("Access token decoded payload:", JSON.parse(atob(accessToken.split('.')[1])));

    // Map Cognito Identity credentials to AWS SDK format
    const acClient = new BedrockAgentCoreClient({{
      region: REGION,
      credentials: {{
        accessKeyId: creds.AccessKeyId,
        secretAccessKey: creds.SecretKey,  // Cognito returns "SecretKey", SDK expects "secretAccessKey"
        sessionToken: creds.SessionToken,
        expiration: new Date(creds.Expiration * 1000),
      }},
    }});

    const completeAuthCommand = new CompleteResourceTokenAuthCommand({{
      sessionUri: sessionId,
      userIdentifier: {{ userToken: accessToken }},  // Use access token, not ID token
    }});

    console.log("CompleteResourceTokenAuth command input:", {{
      sessionUri: sessionId,
      userIdentifier: {{ userToken: accessToken.substring(0, 50) + "..." }},
    }});

    const response = await acClient.send(completeAuthCommand);

    console.log("CompleteResourceTokenAuth response:", response);
    log("CB-COMPLETE", "Response received", {{ response: JSON.stringify(response) }});

    log("CB-COMPLETE", "Success — token stored in vault");

    sessionStorage.removeItem("auth_jwt");
    sessionStorage.removeItem("auth_role_arn");
    log("CB-DONE", "Authorization complete - using Cognito Identity Pool");

    // Mark this session as completed to prevent reuse
    completedSessions.push(sessionId);
    localStorage.setItem("completed_sessions", JSON.stringify(completedSessions));

    // Clean up auth flow data from localStorage (no longer needed)
    localStorage.removeItem("auth_flow_access_token");
    localStorage.removeItem("auth_flow_id_token");
    localStorage.removeItem("auth_flow_role_arn");
    localStorage.removeItem("auth_flow_elicited_session_id");
    localStorage.removeItem("auth_flow_started_at");
    localStorage.removeItem("auth_flow_jwt_jti");
    localStorage.removeItem("auth_flow_jwt_exp");
    localStorage.removeItem("auth_flow_jwt_first_50");
    localStorage.removeItem("auth_flow_jwt_last_50");
    log("CB-DONE", "Cleaned up auth flow data from localStorage");

    showResult(true, "Authorization complete. You can now use MCP tools in VS Code and the web app.");
  }} catch (e) {{
    console.error("=== ERROR in completeAuth ===");
    console.error("Error object:", e);
    console.error("Error name:", e.name);
    console.error("Error message:", e.message);
    console.error("Error stack:", e.stack);

    // If it's an AWS SDK error, log more details
    if (e.$metadata) {{
      console.error("AWS SDK Error metadata:", e.$metadata);
    }}
    if (e.$response) {{
      console.error("AWS SDK Error response:", e.$response);

      // Try to read the response body for more details
      if (e.$response.body) {{
        try {{
          const reader = e.$response.body.getReader();
          reader.read().then(({{ value }}) => {{
            if (value) {{
              const bodyText = new TextDecoder().decode(value);
              console.error("AWS SDK Error response body:", bodyText);
              log("CB-ERROR-DETAIL", "Response body: " + bodyText, {{ _err: true }});
            }}
          }});
        }} catch (bodyError) {{
          console.error("Could not read response body:", bodyError);
        }}
      }}
    }}

    const errorMsg = e.message || String(e);
    log("CB-ERROR", errorMsg, {{
      _err: true,
      name: e.name || "Unknown",
      code: e.code || e.$metadata?.httpStatusCode || "N/A",
      httpStatusCode: e.$metadata?.httpStatusCode || "N/A",
      requestId: e.$metadata?.requestId || "N/A",
      sessionUri: sessionId,
      jwt_jti: currentJwtJti,
      jwt_expires_in_seconds: jwtExpiresIn,
      time_elapsed_seconds: timeElapsed,
      stack: e.stack ? e.stack.substring(0, 200) : "N/A",
    }});

    // Clean up auth flow data on error (allow retry with fresh state)
    localStorage.removeItem("auth_flow_access_token");
    localStorage.removeItem("auth_flow_id_token");
    localStorage.removeItem("auth_flow_role_arn");
    localStorage.removeItem("auth_flow_elicited_session_id");
    localStorage.removeItem("auth_flow_started_at");
    localStorage.removeItem("auth_flow_jwt_jti");
    localStorage.removeItem("auth_flow_jwt_exp");
    localStorage.removeItem("auth_flow_jwt_first_50");
    localStorage.removeItem("auth_flow_jwt_last_50");

    showResult(false, "Error: " + errorMsg + " (Check browser console for details)");
  }}
}} catch (outerError) {{
  console.error("=== FATAL ERROR in completeAuth wrapper ===");
  console.error(outerError);
  log("CB-FATAL", String(outerError), {{ _err: true }});
  showResult(false, "Fatal error: " + String(outerError));
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

// Ensure log panel is always visible for debugging
document.getElementById("log-panel").style.display = "block";
console.log("=== /auth/callback page loaded ===");
console.log("Starting completeAuth()...");

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
    """Serve OAuth Authorization Server Metadata (RFC 8414)."""
    api_url = get_api_url(event)

    metadata = {
        "issuer": api_url,
        "authorization_endpoint": f"{api_url}/authorize",
        "token_endpoint": f"{api_url}/token",
        "registration_endpoint": f"{api_url}/register",
        "scopes_supported": [
            "openid",
            "profile",
            "email",
            f"{RESOURCE_SERVER_ID}/mcp.read",
            f"{RESOURCE_SERVER_ID}/mcp.write",
        ],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    }

    return json_response(200, metadata)


def handle_protected_resource_metadata(event):
    """Serve OAuth Protected Resource Metadata (RFC 9728).

    The resource path is derived from the request path so that per-target
    endpoints work.  Per RFC 9728 a client connecting to
    https://host/figma/mcp looks up metadata at
    https://host/.well-known/oauth-protected-resource/figma/mcp, and rejects the
    response if 'resource' is not the URL it connected to.  Hardcoding /mcp here
    makes every target other than the default one fail with
    "Protected resource ... does not match expected ...".
    """
    api_url = get_api_url(event)
    path = event.get("path") or event.get("rawPath", "/")

    # Everything after the well-known prefix is the resource path
    # ("/figma/mcp", "/weather-tool/mcp", or "" for the bare lookup).
    suffix = path[len(PROTECTED_RESOURCE_PREFIX) :].strip("/")
    resource_path = f"/{suffix}" if suffix else "/mcp"
    logger.debug(f"Protected resource metadata for path {path} -> {resource_path}")

    # The resource is the ALB endpoint, not the Gateway endpoint
    return json_response(
        200,
        {
            "resource": f"{api_url}{resource_path}",
            "authorization_servers": [api_url],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [
                "openid",
                "profile",
                "email",
                f"{RESOURCE_SERVER_ID}/mcp.read",
                f"{RESOURCE_SERVER_ID}/mcp.write",
            ],
        },
    )


def handle_authorize(event):
    """Redirect /authorize to Cognito with callback interception.

    Since Lambda is stateless, we encode the original redirect_uri in the state parameter
    so it survives across Lambda invocations.
    """
    logger.debug("=== HANDLE_AUTHORIZE DEBUG ===")
    params = event.get("queryStringParameters", {}) or {}
    logger.debug(f"Original params: {json.dumps(params)}")

    # Remove unsupported parameters (Cognito doesn't support 'resource' parameter)
    if "resource" in params:
        logger.debug(f"Removing 'resource' parameter: {params['resource']}")
        params.pop("resource", None)

    # Fix scope parameter: URL-decode and normalize spaces
    if "scope" in params:
        # URL-decode first (handles %2F etc.), then normalize + to spaces
        params["scope"] = urllib.parse.unquote(params["scope"]).replace("+", " ")
        logger.debug(f"Fixed scope parameter: {params['scope']}")

    # Override client_id
    logger.debug(f"Original client_id: {params.get('client_id', 'N/A')}")
    params["client_id"] = CLIENT_ID
    logger.debug(f"Overridden client_id: {CLIENT_ID}")

    # Encode original redirect_uri and state together in a new state parameter
    original_redirect_uri = params.get("redirect_uri", "")
    original_state = params.get("state", "")

    logger.debug(f"Original redirect_uri (URL encoded): {original_redirect_uri}")
    logger.debug(f"Original state (URL encoded): {original_state}")

    if original_redirect_uri:
        # URL-decode both state and redirect_uri before storing
        decoded_state = urllib.parse.unquote(original_state)
        decoded_redirect_uri = urllib.parse.unquote(original_redirect_uri)

        logger.debug(f"Decoded state: {decoded_state}")
        logger.debug(f"Decoded redirect_uri: {decoded_redirect_uri}")

        # Create compound state: base64(json({original_state, original_redirect_uri}))
        compound_state = {
            "state": decoded_state,
            "redirect_uri": decoded_redirect_uri,
        }
        encoded_state = base64.urlsafe_b64encode(
            json.dumps(compound_state).encode()
        ).decode()
        params["state"] = encoded_state

        logger.debug(f"Compound state created: {json.dumps(compound_state)}")
        logger.debug(f"Encoded state: {encoded_state}")

        # Replace redirect_uri with our callback
        api_url = get_api_url(event)
        params["redirect_uri"] = f"{api_url}/callback"
        logger.debug(f"New redirect_uri: {params['redirect_uri']}")

    logger.debug(f"Final params being sent to Cognito: {json.dumps(params)}")
    redirect_url = f"{COGNITO_DOMAIN.rstrip('/')}/oauth2/authorize?{urllib.parse.urlencode(params)}"
    logger.debug(f"Redirect URL: {redirect_url}")
    logger.debug("=== END HANDLE_AUTHORIZE DEBUG ===")

    return {"statusCode": 302, "headers": {"Location": redirect_url}, "body": ""}


def handle_callback(event):
    """Handle OAuth callback from Cognito and forward to VS Code.

    Decodes the compound state parameter to extract original redirect_uri and state.
    """
    params = event.get("queryStringParameters", {}) or {}
    code = params.get("code", "")
    encoded_state = params.get("state", "")
    error = params.get("error", "")

    logger.debug("=== HANDLE_CALLBACK DEBUG ===")
    logger.debug(f"Code: {code}")
    logger.debug(f"State (URL encoded): {encoded_state}")
    logger.debug(f"Error: {error}")

    if error:
        return json_response(400, {"error": error})

    # Decode compound state to get original redirect_uri and state
    try:
        # First, URL-decode the state parameter (Cognito sends it URL-encoded)
        encoded_state_clean = urllib.parse.unquote(encoded_state)
        logger.debug(f"State (URL decoded): {encoded_state_clean}")

        # Handle any remaining URL encoding issues (spaces become + or %20)
        encoded_state_clean = encoded_state_clean.replace(" ", "+")

        # The state should now be proper base64, no padding needed
        logger.debug(f"State (ready for base64 decode): {encoded_state_clean}")
        logger.debug(f"State length: {len(encoded_state_clean)}")

        decoded = base64.urlsafe_b64decode(encoded_state_clean).decode()
        logger.debug(f"Decoded JSON: {decoded}")

        compound_state = json.loads(decoded)
        original_state = compound_state.get("state", "")
        original_redirect_uri = compound_state.get("redirect_uri", "")

        logger.debug(f"Original state: {original_state}")
        logger.debug(f"Original redirect_uri: {original_redirect_uri}")
        logger.debug("=== END HANDLE_CALLBACK DEBUG ===")
    except Exception as e:
        logger.error(f"Error decoding state: {e}, state={encoded_state}")
        logger.error("=== END HANDLE_CALLBACK DEBUG (ERROR) ===")
        return json_response(400, {"error": "Invalid state parameter"})

    if not original_redirect_uri:
        return json_response(400, {"error": "Missing redirect_uri in state"})

    # Validate redirect_uri against the allowlist to prevent open-redirect attacks.
    # A crafted state blob could otherwise redirect the authorization code to an
    # attacker-controlled URL.
    #
    # Localhost URIs with any port are allowed because IDE clients (VS Code, Kiro)
    # spin up an ephemeral local server on a random port for the OAuth callback.
    normalized = original_redirect_uri.rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    is_localhost = parsed.scheme == "http" and parsed.hostname in (
        "localhost",
        "127.0.0.1",
    )
    allowed_normalized = [u.rstrip("/") for u in ALLOWED_REDIRECT_URIS]
    if not is_localhost and normalized not in allowed_normalized:
        logger.warning(
            f"Rejected redirect_uri not in allowlist: {original_redirect_uri}"
        )
        logger.debug(f"Normalized redirect_uri: {normalized}")
        logger.debug(f"Allowed URIs (raw): {ALLOWED_REDIRECT_URIS}")
        logger.debug(f"Allowed URIs (normalized): {allowed_normalized}")
        return json_response(400, {"error": "invalid_redirect_uri"})

    # Forward to VS Code's callback with original state
    forward_params = urllib.parse.urlencode({"code": code, "state": original_state})
    forward_url = f"{original_redirect_uri}?{forward_params}"

    return {"statusCode": 302, "headers": {"Location": forward_url}, "body": ""}


def handle_token(event):
    """Proxy token requests to Cognito with redirect_uri rewriting."""
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    params = dict(urllib.parse.parse_qsl(body))

    # Override client_id and add secret
    params["client_id"] = CLIENT_ID
    if CLIENT_SECRET:
        params["client_secret"] = CLIENT_SECRET

    # Rewrite redirect_uri
    if "redirect_uri" in params:
        api_url = get_api_url(event)
        params["redirect_uri"] = f"{api_url}/callback"

    token_url = f"{COGNITO_DOMAIN.rstrip('/')}/oauth2/token"
    data = urllib.parse.urlencode(params).encode()

    req = urllib.request.Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode())
            if "created_at" not in token_data:
                token_data["created_at"] = int(time.time() * 1000)
            return json_response(200, token_data)
    except urllib.error.HTTPError as e:
        return json_response(e.code, {"error": e.read().decode()})


def handle_dcr(event):
    """Handle Dynamic Client Registration - return pre-registered client_id."""
    return json_response(
        200,
        {
            "client_id": CLIENT_ID,
            "client_name": "VS Code Copilot MCP Client",
            "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [f"{get_api_url(event)}/callback"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def proxy_to_gateway(event):
    """Forward MCP requests to AgentCore Gateway with optional target filtering."""
    logger.info("proxy_to_gateway")
    path = event.get("path", "/")
    method = event.get("httpMethod") or event.get("requestContext", {}).get(
        "http", {}
    ).get("method", "GET")
    headers = event.get("headers", {})
    body = event.get("body", "")
    logger.info(f"Proxying to gateway - Method: {method}, Path: {path}")
    logger.debug(f"Headers: {json.dumps(headers)}")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body)

    # === EXTRACT TARGET FROM PATH ===
    # /mcp → no filter (return all tools)
    # /gitlab/mcp → filter = "gitlab"
    # /weather/mcp → filter = "weather"
    target_filter = None

    if path and path != "/mcp":
        # Remove leading/trailing slashes and split
        parts = path.strip("/").split("/")

        # Check if path has format: <target>/mcp
        if len(parts) == 2 and parts[-1] == "mcp":
            target_filter = parts[0]
            logger.info(f"Target filter extracted from path: '{target_filter}'")
        elif len(parts) > 2 and parts[-1] == "mcp":
            # Handle nested paths like /api/v1/gitlab/mcp
            target_filter = parts[-2]
            logger.info(f"Target filter extracted from nested path: '{target_filter}'")
        else:
            logger.debug(f"Path '{path}' does not match target pattern, no filtering")
    else:
        logger.debug("Default path '/mcp' - returning all tools (no filtering)")

    # === INJECT INTO MCP _meta ONLY IF TARGET FILTER EXISTS ===
    if method == "POST" and body:
        try:
            # Parse MCP JSON-RPC request
            mcp_request = json.loads(body if isinstance(body, str) else body.decode())

            # Only inject _meta if we have a target filter AND it's a tool-related method
            if target_filter and mcp_request.get("method") in [
                "tools/list",
                "tools/call",
            ]:
                # Ensure _meta exists
                if "_meta" not in mcp_request:
                    mcp_request["_meta"] = {}

                # Inject target filter using reverse DNS notation
                mcp_request["_meta"][MCP_METADATA_KEY] = target_filter

                logger.info(f"Injected _meta: {MCP_METADATA_KEY} = '{target_filter}'")
                logger.debug(
                    f"Modified MCP request: {json.dumps(mcp_request, indent=2)}"
                )
            else:
                if not target_filter:
                    logger.debug(
                        "No target filter - NOT injecting _meta (will return all tools)"
                    )
                else:
                    logger.debug(
                        f"Method '{mcp_request.get('method')}' - not injecting _meta"
                    )

            # Re-serialize (possibly modified) request
            body = json.dumps(mcp_request).encode()

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MCP request: {e}")
            # Continue with original body if parsing fails

    # target_url = f"{GATEWAY_URL.rstrip('/mcp')}{path}" if path != "/" else GATEWAY_URL
    target_url = GATEWAY_URL
    # Build request headers
    req_headers = {
        "Content-Type": headers.get("content-type", "application/json"),
        "Accept": headers.get("accept", "application/json"),
    }

    # Forward MCP headers
    for h in ["mcp-protocol-version", "mcp-session-id"]:
        if headers.get(h):
            req_headers[h.title()] = headers[h]

    logger.debug(json.dumps(req_headers))
    try:
        if method == "POST" and body:
            data = body.encode() if isinstance(body, str) else body
            req = urllib.request.Request(target_url, data=data, method="POST")
        else:
            req = urllib.request.Request(target_url, method=method)

        for k, v in req_headers.items():
            req.add_header(k, v)

        # This code is here in case ACG will support 3LO outbound with IAM auth in the future
        if os.environ.get("GATEWAY_AUTH", None) == "IAM":
            # Extract the userId from the inbound authorization token
            auth = headers.get("authorization")
            if auth:
                token = auth.split(" ")[1]
                user_id = json.loads(base64.b64decode(token.split(".")[1]))["sub"]
                req.add_header("X-Amzn-Bedrock-AgentCore-Runtime-User-Id", user_id)
            sign_request(req)
        else:
            # Forward auth header
            auth = headers.get("authorization")
            if auth:
                req.add_header("Authorization", auth)

        logger.debug(
            "{}\n{}\r\n{}\r\n\r\n{}".format(
                "-----------START-----------",
                (req.method or "GET") + " " + req.full_url,
                "\r\n".join("{}: {}".format(k, v) for k, v in req.headers.items()),
                req.data,
            )
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            resp_body = resp.read().decode()
            logger.debug(resp_body)
            logger.debug(resp.headers)
            resp_headers = {
                "Content-Type": resp.headers.get("Content-Type", "application/json")
            }

            # Forward session ID
            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                resp_headers["Mcp-Session-Id"] = session_id

            # Rewrite Gateway URLs in WWW-Authenticate header to use ALB endpoint
            www_auth = resp.headers.get("WWW-Authenticate")
            if www_auth:
                resp_headers["WWW-Authenticate"] = rewrite_www_authenticate(
                    www_auth, get_api_url(event), path
                )

            return {
                "statusCode": resp.status,
                "headers": resp_headers,
                "body": resp_body,
            }
    except urllib.error.HTTPError as e:
        error = e.read().decode()
        logger.error(f"Gateway error response: {error}")

        # Rewrite any Gateway URLs in error response body
        api_url = get_api_url(event)
        error_rewritten = error.replace(gateway_base_url(), api_url)
        if error != error_rewritten:
            logger.debug("Rewrote Gateway URL in error body")

        resp_headers = {"Content-Type": "application/json"}

        # Rewrite WWW-Authenticate header if present
        www_auth = e.headers.get("WWW-Authenticate")
        if www_auth:
            resp_headers["WWW-Authenticate"] = rewrite_www_authenticate(
                www_auth, api_url, path
            )

        return {
            "statusCode": e.code,
            "headers": resp_headers,
            "body": error_rewritten,
        }
    except Exception as e:
        return json_response(502, {"error": {"code": -32603, "message": str(e)}})


def gateway_base_url():
    """GATEWAY_URL without its trailing /mcp."""
    return GATEWAY_URL[:-4] if GATEWAY_URL.endswith("/mcp") else GATEWAY_URL


def rewrite_www_authenticate(www_auth, api_url, path):
    """Point a Gateway 401 challenge at this proxy, for the path that was called.

    Besides swapping the Gateway host for the ALB host, the resource_metadata URL
    is rewritten to the path-inserted form (RFC 9728) for the request path.  The
    Gateway always advertises the bare /.well-known/oauth-protected-resource
    document, which names <host>/mcp as its resource -- a client that connected to
    /weather-tool/mcp follows resource_metadata, sees the wrong resource and fails
    with "Protected resource ... does not match expected ...".
    """
    rewritten = www_auth.replace(gateway_base_url(), api_url)

    metadata_url = f"{api_url}{PROTECTED_RESOURCE_PREFIX}/{path.strip('/')}"
    rewritten, count = re.subn(
        r'resource_metadata="[^"]*"',
        lambda _: f'resource_metadata="{metadata_url}"',
        rewritten,
    )
    if not count:
        rewritten = f'{rewritten}, resource_metadata="{metadata_url}"'

    logger.debug(f"Rewrote WWW-Authenticate: {www_auth} -> {rewritten}")
    return rewritten


def is_elicitation(data):
    """Check if response is a 3LO elicitation."""
    if not isinstance(data, dict):
        return False
    error = data.get("error", {})
    return isinstance(error, dict) and error.get("code") == -32042


def get_api_url(event):
    """Extract API URL from event (supports both ALB and API Gateway)."""
    # For ALB, use Host header
    headers = event.get("headers", {})
    host = headers.get("host") or headers.get("Host")
    if host:
        # ALB passes the actual domain in Host header
        return f"https://{host}"

    # Fallback to API Gateway format
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
