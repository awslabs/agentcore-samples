"""
MCP OAuth Proxy Lambda - Handles OAuth metadata, callback interception, token proxying, and MCP forwarding.

This Lambda function replaces the local mcp_oauth_proxy.py script, enabling serverless deployment.
"""

import json
import os
import time
import base64
import urllib.request
import urllib.parse
import urllib.error

# Configuration from environment variables
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
CALLBACK_LAMBDA_URL = os.environ.get("CALLBACK_LAMBDA_URL", "")


def lambda_handler(event, context):
    """Main Lambda handler - routes requests based on path."""
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    # Route to appropriate handler
    if path == "/.well-known/oauth-authorization-server":
        return handle_oauth_metadata(event)
    elif path == "/.well-known/oauth-protected-resource":
        return handle_protected_resource_metadata(event)
    elif path == "/authorize":
        return handle_authorize(event)
    elif path == "/callback":
        return handle_callback(event)
    elif path == "/token" and method == "POST":
        return handle_token(event)
    elif path == "/register" and method == "POST":
        return handle_dcr(event)
    else:
        return proxy_to_gateway(event)


def handle_oauth_metadata(event):
    """Serve OAuth Authorization Server Metadata (RFC 8414)."""
    api_url = get_api_url(event)

    metadata = {
        "issuer": api_url,
        "authorization_endpoint": f"{api_url}/authorize",
        "token_endpoint": f"{api_url}/token",
        "registration_endpoint": f"{api_url}/register",
        "scopes_supported": ["openid", "profile", "email"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
    }

    return json_response(200, metadata)


def handle_protected_resource_metadata(event):
    """Serve OAuth Protected Resource Metadata (RFC 9728).

    The proxy acts as the MCP server from the client's perspective, so the
    resource identifier must be the proxy URL (API Gateway), not the underlying
    AgentCore Gateway URL. This ensures the 'resource' parameter in OAuth token
    requests matches this metadata, avoiding resource mismatch errors.

    Note: We intentionally do NOT proxy the AgentCore Gateway's metadata here
    because that would return the Gateway URL as the resource, causing a mismatch
    when the client (VS Code) uses the proxy URL in its token requests.
    """
    api_url = get_api_url(event)
    return json_response(
        200,
        {
            "resource": api_url,
            "authorization_servers": [api_url],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile", "email"],
        },
    )


def handle_authorize(event):
    """Redirect /authorize to Cognito with callback interception.

    Since Lambda is stateless, we encode the original redirect_uri in the state parameter
    so it survives across Lambda invocations.
    """
    params = event.get("queryStringParameters", {}) or {}

    # Override client_id
    params["client_id"] = CLIENT_ID

    # Encode original redirect_uri and state together in a new state parameter
    original_redirect_uri = params.get("redirect_uri", "")
    original_state = params.get("state", "")

    if original_redirect_uri:
        # Create compound state: base64(json({original_state, original_redirect_uri}))
        compound_state = {
            "state": original_state,
            "redirect_uri": urllib.parse.unquote(original_redirect_uri),
        }
        encoded_state = base64.urlsafe_b64encode(json.dumps(compound_state).encode()).decode()
        params["state"] = encoded_state

        # Replace redirect_uri with our callback
        api_url = get_api_url(event)
        params["redirect_uri"] = f"{api_url}/callback"

    redirect_url = f"{COGNITO_DOMAIN.rstrip('/')}/oauth2/authorize?{urllib.parse.urlencode(params)}"

    return {"statusCode": 302, "headers": {"Location": redirect_url}, "body": ""}


def handle_callback(event):
    """Handle OAuth callback from Cognito and forward to VS Code.

    Decodes the compound state parameter to extract original redirect_uri and state.
    """
    params = event.get("queryStringParameters", {}) or {}
    code = params.get("code", "")
    encoded_state = params.get("state", "")
    error = params.get("error", "")

    if error:
        return json_response(400, {"error": error})

    # Decode compound state to get original redirect_uri and state
    try:
        # Handle URL encoding issues (spaces become + or %20)
        encoded_state_clean = encoded_state.replace(" ", "+")
        # Add padding if needed
        padding = 4 - len(encoded_state_clean) % 4
        if padding != 4:
            encoded_state_clean += "=" * padding

        decoded = base64.urlsafe_b64decode(encoded_state_clean).decode()
        compound_state = json.loads(decoded)
        original_state = compound_state.get("state", "")
        original_redirect_uri = compound_state.get("redirect_uri", "")
    except Exception as e:
        print(f"Error decoding state: {e}, state={encoded_state}")
        return json_response(400, {"error": "Invalid state parameter"})

    if not original_redirect_uri:
        return json_response(400, {"error": "Missing redirect_uri in state"})

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
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
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
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def proxy_to_gateway(event):
    """Forward MCP requests to AgentCore Gateway."""
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    headers = event.get("headers", {})
    body = event.get("body", "")

    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body)

    target_url = f"{GATEWAY_URL.rstrip('/')}{path}" if path != "/" else GATEWAY_URL

    # Build request headers
    req_headers = {
        "Content-Type": headers.get("content-type", "application/json"),
        "Accept": headers.get("accept", "application/json"),
    }

    # Forward auth header
    auth = headers.get("authorization")
    if auth:
        req_headers["Authorization"] = auth

    # Forward MCP headers
    for h in ["mcp-protocol-version", "mcp-session-id"]:
        if headers.get(h):
            req_headers[h.title()] = headers[h]

    try:
        if method == "POST" and body:
            data = body.encode() if isinstance(body, str) else body
            req = urllib.request.Request(target_url, data=data, method="POST")
        else:
            req = urllib.request.Request(target_url, method=method)

        for k, v in req_headers.items():
            req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            resp_body = resp.read().decode()
            resp_headers = {"Content-Type": resp.headers.get("Content-Type", "application/json")}

            # Forward session ID
            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                resp_headers["Mcp-Session-Id"] = session_id

            # Check for 3LO elicitation and store token
            try:
                data = json.loads(resp_body)
                if is_elicitation(data) and CALLBACK_LAMBDA_URL:
                    store_token_for_3lo(req_headers.get("Authorization", ""))
            except (json.JSONDecodeError, KeyError):
                pass

            return {
                "statusCode": resp.status,
                "headers": resp_headers,
                "body": resp_body,
            }
    except urllib.error.HTTPError as e:
        resp_headers = {"Content-Type": "application/json"}
        if e.headers.get("WWW-Authenticate"):
            resp_headers["WWW-Authenticate"] = e.headers["WWW-Authenticate"]
        return {
            "statusCode": e.code,
            "headers": resp_headers,
            "body": e.read().decode(),
        }
    except Exception as e:
        return json_response(502, {"error": {"code": -32603, "message": str(e)}})


def is_elicitation(data):
    """Check if response is a 3LO elicitation."""
    if not isinstance(data, dict):
        return False
    error = data.get("error", {})
    return isinstance(error, dict) and error.get("code") == -32042


def store_token_for_3lo(auth_header):
    """Store user token in callback Lambda for 3LO session binding."""
    if not auth_header or not CALLBACK_LAMBDA_URL:
        return

    token = auth_header.removeprefix("Bearer ")
    url = f"{CALLBACK_LAMBDA_URL}/userIdentifier/token"

    try:
        data = json.dumps({"user_token": token}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=5)  # nosec B310
    except Exception as e:
        print(f"Error storing token for 3LO: {e}")


def get_api_url(event):
    """Extract API Gateway URL from event."""
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
