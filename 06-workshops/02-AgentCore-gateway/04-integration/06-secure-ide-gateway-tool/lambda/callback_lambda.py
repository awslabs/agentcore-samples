"""
3LO OAuth Callback Lambda - Handles outbound OAuth callbacks server-side.

When the 3LO provider redirects back to /oauth2/callback, this Lambda:
1. Looks up the user's bearer token from DynamoDB (stored by the MCP proxy)
2. Verifies the cookie user matches the stored token user (sub claim)
3. Calls CompleteResourceTokenAuth directly — no client-side JS needed
"""

import json
import os
import time
import urllib.parse
import urllib.request

import boto3
from jose import jwt, JWTError

# SSM parameter names (static strings set by CDK to break circular deps)
CLIENT_ID_SSM_PARAM = os.environ.get("CLIENT_ID_SSM_PARAM", "")

# Direct env vars (non-cycle-causing values)
AUTH_CODE_TABLE = os.environ.get("AUTH_CODE_TABLE", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")

dynamodb_resource = boto3.resource("dynamodb")
cognito_client = boto3.client("cognito-idp")
ssm_client = boto3.client("ssm")

_jwks_cache = {}
_ssm_cache = {}


def _get_ssm_param(param_name):
    """Fetch an SSM parameter value, caching for the lifetime of the Lambda container."""
    if param_name in _ssm_cache:
        return _ssm_cache[param_name]
    if not param_name:
        return ""
    resp = ssm_client.get_parameter(Name=param_name)
    value = resp["Parameter"]["Value"]
    _ssm_cache[param_name] = value
    return value


def _get_client_id():
    """Resolve CLIENT_ID from SSM or fall back to direct env var."""
    if CLIENT_ID_SSM_PARAM:
        return _get_ssm_param(CLIENT_ID_SSM_PARAM)
    return os.environ.get("CLIENT_ID", "")


def _get_jwks(issuer):
    """Fetch and cache the JWKS for the given issuer URL."""
    if issuer in _jwks_cache:
        return _jwks_cache[issuer]
    jwks_url = f"{issuer}/.well-known/jwks.json"
    if not jwks_url.startswith("https://"):
        raise ValueError("JWKS URL must use the https scheme")
    # scheme is validated to be https above; issuer is a trusted Cognito URL
    with urllib.request.urlopen(jwks_url, timeout=5) as resp:  # nosec B310
        keys = json.loads(resp.read().decode())
    _jwks_cache[issuer] = keys
    return keys


def _verify_cognito_jwt(token):
    """Verify a Cognito JWT signature and return its payload, or None on failure."""
    region = boto3.Session().region_name or "us-east-1"
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{USER_POOL_ID}"
    try:
        keys = _get_jwks(issuer)
        return jwt.decode(
            token,
            keys,
            algorithms=["RS256"],
            audience=_get_client_id(),
            issuer=issuer,
            options={"verify_exp": True},
        )
    except JWTError:
        return None


def _get_access_token_from_cookies(event):
    """Extract and verify access_token from cookies, refreshing if expired.

    Returns (token, payload, refreshed_token). refreshed_token is non-None
    only when the original was expired and a new one was obtained.
    """
    access_token = ""
    refresh_token = ""
    for cookie in event.get("cookies", []):
        if cookie.startswith("access_token="):
            access_token = cookie[len("access_token=") :]
        elif cookie.startswith("refresh_token="):
            refresh_token = cookie[len("refresh_token=") :]

    if access_token:
        payload = _verify_cognito_jwt(access_token)
        if payload:
            return access_token, payload, None

    if refresh_token:
        try:
            resp = cognito_client.initiate_auth(
                ClientId=_get_client_id(),
                AuthFlow="REFRESH_TOKEN_AUTH",
                AuthParameters={"REFRESH_TOKEN": refresh_token},
            )
            new_token = resp["AuthenticationResult"]["AccessToken"]
            payload = _verify_cognito_jwt(new_token)
            if payload:
                return new_token, payload, new_token
        except Exception as e:
            print(f"Token refresh failed: {e}")

    return None, None, None


def lambda_handler(event, context):
    """Main Lambda handler - routes requests based on path."""
    path = event.get("rawPath", event.get("path", "/"))
    if path == "/ping":
        return json_response(200, {"status": "success"})
    elif path == "/oauth2/callback":
        return handle_oauth_callback(event)
    else:
        return json_response(404, {"error": "Not found"})


def handle_oauth_callback(event):
    """Handle 3LO OAuth callback: verify user, complete auth server-side."""
    params = event.get("queryStringParameters", {}) or {}
    session_id = params.get("session_id", "")
    print(f"handle_oauth_callback: session_id={session_id}")
    print(event)
    if not session_id:
        return _result_page("Error", "Missing session_id parameter.", success=False)

    # Authenticate the user from cookies
    access_token, cookie_payload, refreshed_token = _get_access_token_from_cookies(event)
    if not cookie_payload:
        return_to = _build_current_url(event)
        return {
            "statusCode": 302,
            "headers": {
                "Location": f"/authorize?return_to={urllib.parse.quote(return_to, safe='')}",
            },
        }

    # Look up and consume the stored bearer token (single-use)
    table = dynamodb_resource.Table(AUTH_CODE_TABLE)
    try:
        response = table.delete_item(
            Key={"code": f"elicitation:{session_id}"},
            ConditionExpression="attribute_exists(code)",
            ReturnValues="ALL_OLD",
        )
    except dynamodb_resource.meta.client.exceptions.ConditionalCheckFailedException:
        return _result_page(
            "Session Expired",
            "The authorization session has expired or was already used. Please retry.",
            success=False,
        )

    item = response.get("Attributes")
    if not item or "user_token" not in item:
        return _result_page(
            "Session Expired",
            "The authorization session has expired or was already used. Please retry.",
            success=False,
        )

    user_token = item["user_token"]

    # Verify the stored token and check that sub matches the cookie user
    stored_payload = _verify_cognito_jwt(user_token)
    if not stored_payload:
        return _result_page(
            "Token Expired",
            "The stored authorization token has expired. Please retry the operation.",
            success=False,
        )

    if stored_payload.get("sub") != cookie_payload.get("sub"):
        return _result_page(
            "User Mismatch",
            "The logged-in user does not match the user who initiated this authorization.",
            success=False,
        )

    # Call CompleteResourceTokenAuth
    try:
        agentcore_client = boto3.client("bedrock-agentcore")
        agentcore_client.complete_resource_token_auth(
            sessionUri=session_id,
            userIdentifier={"userToken": user_token},
        )
        print(f"CompleteResourceTokenAuth: success for session_id={session_id}")
    except Exception as err:
        print(f"CompleteResourceTokenAuth failed: {err}")
        return _result_page(
            "Authorization Failed",
            "Failed to complete authorization. Please retry the operation.",
            success=False,
        )

    # Build response with optional refreshed cookie
    resp = _result_page("Tools Connected!", "You can close this window now.", success=True)
    if refreshed_token:
        exp = cookie_payload.get("exp", 0)
        max_age = max(int(exp - time.time()), 0)
        resp["cookies"] = [f"access_token={refreshed_token}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"]
    return resp


def _build_current_url(event):
    """Reconstruct the current request URL from the event."""
    path = event.get("rawPath", event.get("path", "/"))
    qs = event.get("rawQueryString", "")
    if qs:
        return f"{path}?{qs}"
    return path


def _result_page(title, message, success=True):
    """Render a simple result page."""
    bg = "#667eea 0%, #764ba2 100%" if success else "#e74c3c 0%, #c0392b 100%"
    icon = "&#10003;" if success else "&#10007;"
    post_msg = "auth-success" if success else "auth-error"

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center;
            height: 100vh; margin: 0;
            background: linear-gradient(135deg, {bg});
            color: white;
        }}
        .container {{ text-align: center; padding: 2rem; }}
        .icon {{ font-size: 4rem; margin-bottom: 1rem; }}
        h1 {{ margin: 0 0 0.5rem 0; font-size: 1.5rem; }}
        p {{ margin: 0; opacity: 0.8; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p>{message}</p>
    </div>
    <script>
        if (window.opener) {{
            window.opener.postMessage({{ type: '{post_msg}' }}, window.location.origin);
            setTimeout(() => window.close(), 1500);
        }}
    </script>
</body>
</html>""",
    }


def json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
