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
CLIENT_ID = os.environ.get("CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "")
CALLBACK_LAMBDA_URL = os.environ.get("CALLBACK_LAMBDA_URL", "")
RESOURCE_SERVER_ID = os.environ.get("RESOURCE_SERVER_ID", "")
MCP_METADATA_KEY = os.environ.get("MCP_METADATA_KEY", "com.example/target")

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
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")

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
    elif path.startswith("/.well-known/oauth-authorization-server"):
        return handle_oauth_metadata(event)
    elif path == "/.well-known/oauth-protected-resource" or path == "/.well-known/oauth-protected-resource/mcp":
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
    """Serve OAuth Protected Resource Metadata."""
    api_url = get_api_url(event)

    # Per RFC 9728, the 'resource' must match the URL where clients access the service
    # This should be the ALB endpoint, not the Gateway endpoint
    return json_response(
        200,
        {
            "resource": f"{api_url}/mcp",
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
        encoded_state = base64.urlsafe_b64encode(json.dumps(compound_state).encode()).decode()
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
        logger.warning(f"Rejected redirect_uri not in allowlist: {original_redirect_uri}")
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
            "redirect_uris": [f"{get_api_url(event)}/callback"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def proxy_to_gateway(event):
    """Forward MCP requests to AgentCore Gateway with optional target filtering."""
    logger.info("proxy_to_gateway")
    path = event.get("path", "/")
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
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
                logger.debug(f"Modified MCP request: {json.dumps(mcp_request, indent=2)}")
            else:
                if not target_filter:
                    logger.debug("No target filter - NOT injecting _meta (will return all tools)")
                else:
                    logger.debug(f"Method '{mcp_request.get('method')}' - not injecting _meta")

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

        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
            resp_body = resp.read().decode()
            logger.debug(resp_body)
            logger.debug(resp.headers)
            resp_headers = {"Content-Type": resp.headers.get("Content-Type", "application/json")}

            # Forward session ID
            session_id = resp.headers.get("Mcp-Session-Id")
            if session_id:
                resp_headers["Mcp-Session-Id"] = session_id

            # Rewrite Gateway URLs in WWW-Authenticate header to use ALB endpoint
            www_auth = resp.headers.get("WWW-Authenticate")
            if www_auth:
                api_url = get_api_url(event)
                # Replace any Gateway URL references with ALB URL
                # Use removesuffix or string slicing to properly remove /mcp suffix
                gateway_base = GATEWAY_URL[:-4] if GATEWAY_URL.endswith("/mcp") else GATEWAY_URL
                www_auth_rewritten = www_auth.replace(gateway_base, api_url)
                resp_headers["WWW-Authenticate"] = www_auth_rewritten
                logger.debug(f"Rewrote WWW-Authenticate: {www_auth} -> {www_auth_rewritten}")

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
        # Use string slicing to properly remove /mcp suffix
        gateway_base = GATEWAY_URL[:-4] if GATEWAY_URL.endswith("/mcp") else GATEWAY_URL
        error_rewritten = error.replace(gateway_base, api_url)
        if error != error_rewritten:
            logger.debug("Rewrote Gateway URL in error body")

        resp_headers = {"Content-Type": "application/json"}

        # Rewrite WWW-Authenticate header if present
        www_auth = e.headers.get("WWW-Authenticate")
        if www_auth:
            www_auth_rewritten = www_auth.replace(gateway_base, api_url)
            resp_headers["WWW-Authenticate"] = www_auth_rewritten
            logger.debug(f"Rewrote WWW-Authenticate in error: {www_auth} -> {www_auth_rewritten}")

        return {
            "statusCode": e.code,
            "headers": resp_headers,
            "body": error_rewritten,
        }
    except Exception as e:
        return json_response(502, {"error": {"code": -32603, "message": str(e)}})


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
