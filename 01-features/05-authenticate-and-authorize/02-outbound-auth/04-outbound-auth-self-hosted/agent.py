import json
import os
import sys
import base64
import webbrowser
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import boto3

# ---------------------------------------------------------------------------
# Configuration - all values can be overridden via environment variables.
# ---------------------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "us-east-1")
CREDENTIAL_PROVIDER = os.environ.get("CREDENTIAL_PROVIDER_NAME", "AgentCoreIdentityStandaloneProvider")
USER_ID = os.environ.get("AGENT_USER_ID", "quickstart-user")
CALLBACK_PORT = int(os.environ.get("CALLBACK_PORT", "8080"))
CALLBACK_URL = f"http://127.0.0.1:{CALLBACK_PORT}/callback"

# The control-plane client manages long-lived resources like credential
# providers and workload identities.
control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)

# The data-plane client handles runtime operations: issuing workload tokens,
# initiating OAuth flows, and retrieving access tokens.
data_client = boto3.client("bedrock-agentcore", region_name=REGION)

# Shared flag - the callback handler sets this to True once the user has
# completed authorization in the browser, so the agent can stop polling.
authorization_complete = threading.Event()


# ---------------------------------------------------------------------------
# Web application (session binding handler)
#
# This minimal HTTP server handles the OAuth callback redirect. After the
# user authorizes in the browser, AgentCore Identity redirects here with a
# session_id. The handler calls completeResourceTokenAuth to bind the OAuth
# session to the user - so the agent can later retrieve the access token.
#
# In production, this would be your real web app (e.g. https://myagentapp.com).
# For local development, plain HTTP on 127.0.0.1 works fine.
# ---------------------------------------------------------------------------
class AppHandler(BaseHTTPRequestHandler):
    """Handles the OAuth 2.0 session binding callback from AgentCore Identity."""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        # AgentCore Identity appends ?session_id=... to the callback URL.
        session_id = parse_qs(parsed.query).get("session_id", [None])[0]
        if not session_id:
            self._respond(400, "<h1>Error</h1><p>Missing session_id</p>")
            return

        try:
            # This is the key call: it tells AgentCore Identity that the user
            # has authorized, binding the OAuth session to this user ID.
            data_client.complete_resource_token_auth(
                sessionUri=session_id,
                userIdentifier={"userId": USER_ID},
            )
            self._respond(
                200,
                "<h1>Authorization Complete!</h1><p>Token stored in AgentCore Identity. You can close this tab.</p>",
            )
            print(f"[INFO]  Session bound for session_id={session_id[:20]}...")
            # Signal the agent's polling loop that authorization is done.
            authorization_complete.set()
        except Exception as exc:
            self._respond(500, f"<h1>Error</h1><pre>{exc}</pre>")

    def _respond(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<html><body>{body}</body></html>".encode())

    def log_message(self, format, *args):
        pass  # Suppress default HTTP request logging.


def start_app_server():
    """Start the local callback server in the background."""
    server = HTTPServer(("127.0.0.1", CALLBACK_PORT), AppHandler)
    print(f"[INFO]  App server listening on http://127.0.0.1:{CALLBACK_PORT}/callback")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Agent logic
# ---------------------------------------------------------------------------
def ensure_workload_identity(name="standalone-agent-identity"):
    """Check if the workload identity exists; create it if not."""
    try:
        control_client.get_workload_identity(name=name)
        print(f"[INFO]  Workload identity '{name}' exists - reusing.")
    except control_client.exceptions.ResourceNotFoundException:
        control_client.create_workload_identity(name=name)
        print(f"[INFO]  Workload identity '{name}' created.")
    return name


def decode_jwt(token):
    """Decode a JWT payload without verifying the signature (for display only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def run_agent():
    print("=" * 60)
    print("  AgentCore Identity - Local Agent")
    print("=" * 60)

    # Step A: Ensure the workload identity exists.
    workload_name = ensure_workload_identity()

    # Step B: Get a short-lived workload token. This token identifies the
    # agent (not the end user) and is used to request OAuth tokens.
    token = data_client.get_workload_access_token_for_user_id(
        workloadName=workload_name,
        userId=USER_ID,
    )["workloadAccessToken"]

    # Step C: Ask AgentCore Identity to start an OAuth 2.0 authorization
    # code flow. Because forceAuthentication=True, this always returns an
    # authorizationUrl the user must visit - even if a cached token exists.
    response = data_client.get_resource_oauth2_token(
        workloadIdentityToken=token,
        resourceCredentialProviderName=CREDENTIAL_PROVIDER,
        scopes=["openid", "profile", "email"],
        oauth2Flow="USER_FEDERATION",
        forceAuthentication=True,
        resourceOauth2ReturnUrl=CALLBACK_URL,
    )

    auth_url = response.get("authorizationUrl")
    session_uri = response.get("sessionUri")

    if auth_url:
        # Automatically open the authorization URL in the user's default browser.
        print(f"\n  Opening your browser to authorize...\n\n  {auth_url}\n")
        webbrowser.open(auth_url)

        # Poll until the callback handler signals that authorization is complete,
        # rather than requiring the user to manually press Enter.
        print("  Waiting for you to complete authorization in the browser...")
        while not authorization_complete.wait(timeout=2):
            pass  # Keep waiting in 2-second intervals.

        print("[INFO]  Authorization callback received.")

    # Step D: Get a fresh workload token (the previous one may have expired
    # while the user was authorizing in the browser).
    token = data_client.get_workload_access_token_for_user_id(
        workloadName=workload_name,
        userId=USER_ID,
    )["workloadAccessToken"]

    # Step E: Now retrieve the actual OAuth access token. This time
    # forceAuthentication=False, so AgentCore returns the token that was
    # stored when the user completed the browser flow.
    response = data_client.get_resource_oauth2_token(
        workloadIdentityToken=token,
        resourceCredentialProviderName=CREDENTIAL_PROVIDER,
        scopes=["openid", "profile", "email"],
        oauth2Flow="USER_FEDERATION",
        forceAuthentication=False,
        resourceOauth2ReturnUrl=CALLBACK_URL,
        sessionUri=session_uri,
    )

    access_token = response.get("accessToken")
    if not access_token:
        print("[ERROR] No access token received. Re-run and complete browser authorization.")
        sys.exit(1)

    # The agent now has an OAuth access token for the user.
    # AgentCore Identity handled the entire OAuth flow - authorization code
    # exchange, token storage, and session binding - so you didn't have to
    # write any OAuth code yourself.
    print()
    print("=" * 60)
    print("  Access token retrieved!")
    print()
    print("  Your agent now has consent to act on behalf of the user.")
    print("  AgentCore Identity handled the entire OAuth flow for you -")
    print("  no OAuth code required.")
    print("=" * 60)
    print()
    print(f"  Token preview: {access_token[:50]}...{access_token[-10:]}")

    claims = decode_jwt(access_token)
    if claims:
        print()
        print(json.dumps(claims, indent=2))

    print()
    print("[INFO]  Done. The OAuth flow completed successfully.")


if __name__ == "__main__":
    # Start the callback server in a background thread, then run the agent.
    threading.Thread(target=start_app_server, daemon=True).start()
    run_agent()
