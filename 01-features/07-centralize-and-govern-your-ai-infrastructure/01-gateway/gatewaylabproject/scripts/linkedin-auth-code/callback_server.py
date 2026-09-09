# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Localhost callback server for AgentCore OAuth session binding.

Receives the OAuth redirect after user authorization and calls
CompleteResourceTokenAuth to complete session binding.
Optionally opens the authorization URL in the browser.

Usage (admin target creation - Method 1):
    uv run python scripts/linkedin-auth-code/callback_server.py --user-id "<user-id>" --auth-url "<url>"

Usage (gateway user tool invocation):
    uv run python scripts/linkedin-auth-code/callback_server.py --user-token "<jwt>" --auth-url "<url>"
"""

import argparse
import os
import threading

import boto3
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI()

user_identifier: dict = {}
agentcore_client = None


@app.get("/callback", response_class=HTMLResponse)
async def callback(request: Request):
    session_uri = request.query_params.get("session_id")

    if not session_uri:
        return HTMLResponse(
            content="<h1>Error</h1><p>Missing session_id parameter.</p>",
            status_code=400,
        )

    try:
        agentcore_client.complete_resource_token_auth(
            userIdentifier=user_identifier,
            sessionUri=session_uri,
        )
    except Exception as e:
        print(f"Session binding error: {e}")
        return HTMLResponse(
            content="<h1>Session Binding Failed</h1><p>An error occurred during session binding. Check the console for details.</p>",
            status_code=500,
        )

    threading.Timer(0.5, lambda: os._exit(0)).start()

    return HTMLResponse(
        content=(
            "<h1>Session Binding Complete</h1>"
            "<p>Authorization code flow completed successfully. "
            "You can close this tab.</p>"
        ),
        status_code=200,
    )


def main():
    global user_identifier, agentcore_client

    parser = argparse.ArgumentParser(
        description="Localhost callback server for AgentCore OAuth session binding"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-id", help="User ID for admin target creation flow")
    group.add_argument(
        "--user-token", help="User JWT token for gateway user tool invocation flow"
    )
    parser.add_argument(
        "--region", default=None, help="AWS region (defaults to session region)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    parser.add_argument(
        "--auth-url", default=None, help="Authorization URL to open in browser"
    )
    args = parser.parse_args()

    if args.user_id:
        user_identifier = {"userId": args.user_id}
        print("Mode: admin (userId)")
    else:
        user_identifier = {"userToken": args.user_token}
        print("Mode: gateway user (userToken)")

    agentcore_client = boto3.client("bedrock-agentcore", region_name=args.region)

    print(f"Callback URL: http://localhost:{args.port}/callback")

    if args.auth_url:
        import webbrowser

        auth_url = args.auth_url.replace("\\", "")
        print("Opening authorization URL in browser...")
        webbrowser.open(auth_url)

    print("Waiting for OAuth redirect...")

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")  # nosec B104


if __name__ == "__main__":
    main()
