"""
3LO OAuth Callback Lambda - Handles outbound OAuth callbacks and calls CompleteResourceTokenAuth.

This Lambda function replaces the local oauth2_callback_server.py script.
"""

import json
import os
import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")

# In-memory token storage (use DynamoDB for production)
USER_TOKEN = {}


def lambda_handler(event, context):
    """Main Lambda handler - routes requests based on path."""
    path = event.get("rawPath", event.get("path", "/"))
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if path == "/ping":
        return json_response(200, {"status": "success"})
    elif path == "/userIdentifier/token" and method == "POST":
        return handle_store_token(event)
    elif path == "/oauth2/callback":
        return handle_oauth_callback(event)
    else:
        return json_response(404, {"error": "Not found"})


def handle_store_token(event):
    """Store user token for 3LO session binding."""
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode()

    data = json.loads(body)
    USER_TOKEN["value"] = data.get("user_token", "")
    return json_response(200, {"status": "stored"})


def handle_oauth_callback(event):
    """Handle 3LO OAuth callback and call CompleteResourceTokenAuth."""
    params = event.get("queryStringParameters", {}) or {}
    session_id = params.get("session_id", "")

    if not session_id:
        return json_response(400, {"error": "Missing session_id"})

    if not USER_TOKEN.get("value"):
        return json_response(500, {"error": "No user token stored"})

    # Call AgentCore CompleteResourceTokenAuth
    # The correct boto3 service is 'bedrock-agentcore' (not 'bedrock-agentcore-identity')
    try:
        agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)
        agentcore_client.complete_resource_token_auth(
            sessionUri=session_id, userIdentifier={"userToken": USER_TOKEN["value"]}
        )

        return html_response(
            200,
            """
        <!DOCTYPE html>
        <html>
        <head><title>OAuth2 Success</title>
        <style>
            body { font-family: Arial; display: flex; justify-content: center; 
                   align-items: center; height: 100vh; background: #f5f5f5; }
            .container { text-align: center; padding: 2rem; background: white; 
                        border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h1 { color: #28a745; }
        </style>
        </head>
        <body>
            <div class="container">
                <h1>✓ OAuth2 3LO flow completed successfully</h1>
                <p>You can close this window and retry the tool in VS Code.</p>
            </div>
        </body>
        </html>
        """,
        )
    except Exception as e:
        return json_response(500, {"error": str(e)})


def json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def html_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html"},
        "body": body,
    }
