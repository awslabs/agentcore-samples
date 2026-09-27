"""
IDP Lambda - Handles OAuth2 authorization server endpoints.

Serves a custom login page, issues authorization codes via DynamoDB,
exchanges codes for Cognito tokens, and handles dynamic client registration.
"""

import json
import os
import time
import base64
import hashlib
import uuid
import urllib.parse

import boto3

CLIENT_ID = os.environ.get("CLIENT_ID", "")
AUTH_CODE_TABLE = os.environ.get("AUTH_CODE_TABLE", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")

cognito_client = boto3.client("cognito-idp")
dynamodb_resource = boto3.resource("dynamodb")


def lambda_handler(event, context):
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": {"Allow": "OPTIONS, GET, POST"}}

    if path.startswith("/.well-known/oauth-authorization-server"):
        return handle_oauth_metadata(event)
    elif path.startswith("/.well-known/oauth-protected-resource"):
        return handle_protected_resource_metadata(event)
    elif path == "/authorize":
        return handle_authorize(event)
    elif path == "/login" and method == "POST":
        return handle_login(event)
    elif path == "/token" and method == "POST":
        return handle_token(event)
    elif path == "/register" and method == "POST":
        return handle_dcr(event)
    else:
        return {"statusCode": 404}


def handle_oauth_metadata(event):
    api_url = _get_api_url(event)
    return _json_response(
        200,
        {
            "issuer": api_url,
            "authorization_endpoint": f"{api_url}/authorize",
            "token_endpoint": f"{api_url}/token",
            "registration_endpoint": f"{api_url}/register",
            "scopes_supported": ["openid", "profile", "email"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
        },
    )


def handle_protected_resource_metadata(event):
    api_url = _get_api_url(event)
    return _json_response(
        200,
        {
            "resource": api_url,
            "authorization_servers": [api_url],
            "scopes_supported": ["openid", "profile", "email"],
            "bearer_methods_supported": ["header"],
        },
    )


def handle_authorize(event):
    params = event.get("queryStringParameters", {}) or {}
    redirect_uri = params.get("redirect_uri", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    return_to = params.get("return_to", "")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": LOGIN_PAGE_HTML.replace("$REDIRECT_URI", _escape_html(redirect_uri))
        .replace("$STATE", _escape_html(state))
        .replace("$CODE_CHALLENGE", _escape_html(code_challenge))
        .replace("$CODE_CHALLENGE_METHOD", _escape_html(code_challenge_method))
        .replace("$RETURN_TO", _escape_html(return_to)),
    }


def handle_login(event):
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        data = dict(urllib.parse.parse_qsl(body))

    email = data.get("email", "")
    password = data.get("password", "")
    redirect_uri = data.get("redirect_uri", "")
    state = data.get("state", "")
    code_challenge = data.get("code_challenge", "")
    code_challenge_method = data.get("code_challenge_method", "S256")

    action = data.get("action", "")
    if action == "force_change_password":
        return _handle_force_change_password(data)

    if not email or not password:
        return _json_response(400, {"error": "Missing email or password"})

    try:
        response = cognito_client.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except cognito_client.exceptions.NotAuthorizedException:
        return _json_response(401, {"error": "Invalid email or password"})
    except cognito_client.exceptions.UserNotFoundException:
        return _json_response(401, {"error": "Invalid email or password"})
    except Exception as e:
        print(f"initiate_auth error: {e}")
        return _json_response(500, {"error": "Authentication service error"})

    if response.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        return _json_response(
            200,
            {
                "challenge": "NEW_PASSWORD_REQUIRED",
                "session": response["Session"],
                "email": email,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
            },
        )

    return _store_auth_code_and_redirect(
        response["AuthenticationResult"],
        redirect_uri,
        state,
        code_challenge,
        code_challenge_method,
    )


def _handle_force_change_password(data):
    session = data.get("session", "")
    email = data.get("email", "")
    new_password = data.get("new_password", "")
    redirect_uri = data.get("redirect_uri", "")
    state = data.get("state", "")
    code_challenge = data.get("code_challenge", "")
    code_challenge_method = data.get("code_challenge_method", "S256")

    if not session or not email or not new_password:
        return _json_response(
            400, {"error": "Missing required fields for password change"}
        )

    try:
        response = cognito_client.respond_to_auth_challenge(
            ClientId=CLIENT_ID,
            ChallengeName="NEW_PASSWORD_REQUIRED",
            Session=session,
            ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password},
        )
    except Exception as e:
        print(f"respond_to_auth_challenge error: {e}")
        return _json_response(400, {"error": "Failed to set new password"})

    return _store_auth_code_and_redirect(
        response["AuthenticationResult"],
        redirect_uri,
        state,
        code_challenge,
        code_challenge_method,
    )


def _store_auth_code_and_redirect(
    auth_result, redirect_uri, state, code_challenge, code_challenge_method
):
    auth_code = str(uuid.uuid4())

    table = dynamodb_resource.Table(AUTH_CODE_TABLE)
    table.put_item(
        Item={
            "code": auth_code,
            "access_token": auth_result["AccessToken"],
            "id_token": auth_result["IdToken"],
            "refresh_token": auth_result.get("RefreshToken", ""),
            "expires_in": auth_result.get("ExpiresIn", 3600),
            "token_type": auth_result.get("TokenType", "Bearer"),
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "ttl": int(time.time()) + 300,
        }
    )

    redirect_params = urllib.parse.urlencode({"code": auth_code, "state": state})
    redirect_url = f"{redirect_uri}?{redirect_params}"

    expires_in = auth_result.get("ExpiresIn", 3600)
    access_token = auth_result["AccessToken"]
    refresh_token = auth_result.get("RefreshToken", "")
    api_origin = _get_api_origin()

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": api_origin,
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "cookies": [
            f"access_token={access_token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={expires_in}",
            f"refresh_token={refresh_token}; Path=/; HttpOnly; Secure; SameSite=Lax;",
        ],
        "body": json.dumps({"redirect_url": redirect_url}, default=str),
    }


def handle_token(event):
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()

    params = dict(urllib.parse.parse_qsl(body))
    grant_type = params.get("grant_type", "")

    if grant_type == "authorization_code":
        return _handle_token_auth_code(params)
    elif grant_type == "refresh_token":
        return _handle_token_refresh(params)
    else:
        return _json_response(400, {"error": "unsupported_grant_type"})


def _handle_token_auth_code(params):
    code = params.get("code", "")
    code_verifier = params.get("code_verifier", "")

    table = dynamodb_resource.Table(AUTH_CODE_TABLE)

    try:
        response = table.delete_item(
            Key={"code": code},
            ConditionExpression="attribute_exists(code)",
            ReturnValues="ALL_OLD",
        )
    except dynamodb_resource.meta.client.exceptions.ConditionalCheckFailedException:
        return _json_response(
            400,
            {
                "error": "invalid_grant",
                "error_description": "Invalid or expired authorization code",
            },
        )

    item = response.get("Attributes")
    if not item:
        return _json_response(
            400,
            {
                "error": "invalid_grant",
                "error_description": "Invalid or expired authorization code",
            },
        )

    code_challenge = item.get("code_challenge", "")
    if not code_challenge or not code_verifier:
        return _json_response(
            400,
            {
                "error": "invalid_grant",
                "error_description": "PKCE code_challenge and code_verifier are required",
            },
        )

    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if computed != code_challenge:
        return _json_response(
            400,
            {
                "error": "invalid_grant",
                "error_description": "PKCE validation failed",
            },
        )

    return _json_response(
        200,
        {
            "access_token": item["access_token"],
            "id_token": item["id_token"],
            "refresh_token": item.get("refresh_token", ""),
            "expires_in": int(item.get("expires_in", 3600)),
            "token_type": item.get("token_type", "Bearer"),
            "scope": "openid profile email",
            "created_at": int(time.time() * 1000),
        },
    )


def _handle_token_refresh(params):
    refresh_token = params.get("refresh_token", "")
    if not refresh_token:
        return _json_response(
            400,
            {"error": "invalid_request", "error_description": "Missing refresh_token"},
        )

    try:
        response = cognito_client.initiate_auth(
            ClientId=CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
        auth_result = response["AuthenticationResult"]
        token_data = {
            "access_token": auth_result["AccessToken"],
            "id_token": auth_result["IdToken"],
            "expires_in": auth_result.get("ExpiresIn", 3600),
            "token_type": auth_result.get("TokenType", "Bearer"),
            "scope": "openid profile email",
            "created_at": int(time.time() * 1000),
        }
        token_data["refresh_token"] = auth_result.get("RefreshToken", refresh_token)
        return _json_response(200, token_data)
    except Exception as e:
        print(f"refresh_token error: {e}")
        return _json_response(
            400, {"error": "invalid_grant", "error_description": "Token refresh failed"}
        )


def handle_dcr(event):
    body = event.get("body", "")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode()
    try:
        request_data = json.loads(body) if body else {}
    except (json.JSONDecodeError, TypeError):
        request_data = {}

    scope = request_data.get("scope", "openid profile email")

    return _json_response(
        200,
        {
            "client_id": CLIENT_ID,
            "client_name": "VS Code Copilot MCP Client",
            "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [f"{_get_api_url(event)}/callback"],
            "response_types": ["code"],
            "scope": scope,
            "token_endpoint_auth_method": "none",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_url(event):
    ctx = event.get("requestContext", {})
    domain = ctx.get("domainName", "")
    stage = ctx.get("stage", "")
    if domain and stage and stage != "$default":
        return f"https://{domain}/{stage}"
    elif domain:
        return f"https://{domain}"
    return "http://localhost"


def _get_api_origin():
    url = os.environ.get("CALLBACK_LAMBDA_URL", "")
    if url:
        parsed = urllib.parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _escape_html(s):
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": _get_api_origin(),
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": json.dumps(body, default=str),
    }


# ---------------------------------------------------------------------------
# Login page HTML
# ---------------------------------------------------------------------------

LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign In</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
        }
        .card {
            background: white; border-radius: 12px; padding: 2.5rem;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%; max-width: 400px;
        }
        h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #1a1a2e; }
        .subtitle { color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }
        label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 0.3rem; color: #444; }
        input[type="email"], input[type="password"] {
            width: 100%; padding: 0.7rem 0.9rem; border: 1px solid #ddd;
            border-radius: 8px; font-size: 1rem; margin-bottom: 1rem;
            transition: border-color 0.2s;
        }
        input:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 3px rgba(102,126,234,0.2); }
        button {
            width: 100%; padding: 0.8rem; background: #667eea; color: white;
            border: none; border-radius: 8px; font-size: 1rem; font-weight: 600;
            cursor: pointer; transition: background 0.2s;
        }
        button:hover { background: #5a6fd6; }
        button:disabled { background: #aab; cursor: not-allowed; }
        .error { color: #e74c3c; font-size: 0.85rem; margin-bottom: 1rem; display: none; }
        .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #fff;
            border-top-color: transparent; border-radius: 50%; animation: spin 0.6s linear infinite;
            vertical-align: middle; margin-right: 0.5rem; }
        @keyframes spin { to { transform: rotate(360deg); } }
        #change-pw-section { display: none; }
        .info { color: #2980b9; font-size: 0.85rem; margin-bottom: 1rem;
            background: #eaf2f8; padding: 0.7rem; border-radius: 8px; }
    </style>
</head>
<body>
    <div class="card">
        <div id="login-section">
            <h1>Sign In</h1>
            <p class="subtitle">Sign in to continue to AgentCore</p>
            <div class="error" id="login-error"></div>
            <form id="login-form">
                <label for="email">Email</label>
                <input type="email" id="email" name="email" required autocomplete="username" autofocus>
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required autocomplete="current-password">
                <button type="submit" id="login-btn">Sign In</button>
            </form>
        </div>
        <div id="change-pw-section">
            <h1>Set New Password</h1>
            <p class="info">Your account requires a new password. Please set one below.</p>
            <div class="error" id="change-pw-error"></div>
            <form id="change-pw-form">
                <label for="new-password">New Password</label>
                <input type="password" id="new-password" name="new_password" required autocomplete="new-password">
                <label for="confirm-password">Confirm Password</label>
                <input type="password" id="confirm-password" name="confirm_password" required autocomplete="new-password">
                <button type="submit" id="change-pw-btn">Set Password &amp; Continue</button>
            </form>
        </div>
    </div>
    <script>
        const oauthParams = {
            redirect_uri: "$REDIRECT_URI",
            state: "$STATE",
            code_challenge: "$CODE_CHALLENGE",
            code_challenge_method: "$CODE_CHALLENGE_METHOD",
        };
        const returnTo = "$RETURN_TO";
        let challengeData = null;

        document.getElementById('login-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('login-btn');
            const errEl = document.getElementById('login-error');
            errEl.style.display = 'none';
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Signing in...';
            try {
                const res = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: document.getElementById('email').value,
                        password: document.getElementById('password').value,
                        ...oauthParams,
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Authentication failed');
                if (data.challenge === 'NEW_PASSWORD_REQUIRED') {
                    challengeData = data;
                    document.getElementById('login-section').style.display = 'none';
                    document.getElementById('change-pw-section').style.display = 'block';
                    document.getElementById('new-password').focus();
                    return;
                }
                if (data.redirect_url || returnTo) {
                    window.location.href = returnTo || data.redirect_url;
                    return;
                }
                throw new Error('Unexpected response');
            } catch (err) {
                errEl.textContent = err.message;
                errEl.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Sign In';
            }
        });

        document.getElementById('change-pw-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('change-pw-btn');
            const errEl = document.getElementById('change-pw-error');
            errEl.style.display = 'none';
            const newPw = document.getElementById('new-password').value;
            const confirmPw = document.getElementById('confirm-password').value;
            if (newPw !== confirmPw) {
                errEl.textContent = 'Passwords do not match';
                errEl.style.display = 'block';
                return;
            }
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Setting password...';
            try {
                const res = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: 'force_change_password',
                        session: challengeData.session,
                        email: challengeData.email,
                        new_password: newPw,
                        redirect_uri: challengeData.redirect_uri,
                        state: challengeData.state,
                        code_challenge: challengeData.code_challenge,
                        code_challenge_method: challengeData.code_challenge_method,
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Failed to set password');
                if (data.redirect_url || returnTo) {
                    window.location.href = returnTo || data.redirect_url;
                    return;
                }
                throw new Error('Unexpected response');
            } catch (err) {
                errEl.textContent = err.message;
                errEl.style.display = 'block';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Set Password & Continue';
            }
        });
    </script>
</body>
</html>"""
