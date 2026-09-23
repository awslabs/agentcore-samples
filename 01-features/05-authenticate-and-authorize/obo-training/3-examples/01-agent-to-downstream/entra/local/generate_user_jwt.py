"""
Helper: run the 3LO flow once to mint a user JWT and cache it to disk.

Integration tests read this cache. Re-run whenever the token expires (~1 hour).

Usage:
    python generate_user_jwt.py
    # ... browser opens, you sign in ...
    # → .user-jwt-cache.json created with token + expiry.

The cache file is gitignored.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

import boto3
from dotenv import load_dotenv

from callback_server import start_callback_server

CALLBACK_PORT = 8081
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
CACHE_PATH = Path(__file__).resolve().parent / ".user-jwt-cache.json"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set.", file=sys.stderr)
        sys.exit(1)
    return value


def decode_claims(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


def main() -> None:
    load_dotenv()
    region = os.environ.get("AWS_REGION", "us-west-2")
    workload_name = must_env("WORKLOAD_NAME")
    client_provider_name = must_env("CLIENT_PROVIDER_NAME")
    agent_scope = must_env("AGENT_SCOPE")
    user_alias = must_env("USER_ALIAS")

    ac = boto3.client("bedrock-agentcore", region_name=region)

    server, code_future = start_callback_server(CALLBACK_PORT)

    try:
        wl = ac.get_workload_access_token_for_user_id(
            workloadName=workload_name,
            userId=user_alias,
        )["workloadAccessToken"]

        fed = ac.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=client_provider_name,
            scopes=[agent_scope],
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=CALLBACK_URL,
        )
        webbrowser.open(fed["authorizationUrl"])
        print("Waiting for sign-in...")
        code_future.result(timeout=300)

        ac.complete_resource_token_auth(
            userIdentifier={"userId": user_alias},
            sessionUri=fed["sessionUri"],
        )
        token = ac.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=client_provider_name,
            scopes=[agent_scope],
            oauth2Flow="USER_FEDERATION",
            resourceOauth2ReturnUrl=CALLBACK_URL,
        )["accessToken"]

        claims = decode_claims(token)
        CACHE_PATH.write_text(
            json.dumps(
                {
                    "token": token,
                    "claims": claims,
                    "expires_at": claims.get("exp", int(time.time()) + 3600),
                },
                indent=2,
            )
        )
        print(f"\n✓ Cached user JWT to {CACHE_PATH.name}")
        print(f"  Expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(claims.get('exp', 0)))}")
        print(f"  sub: {claims.get('sub')}")
        print(f"  aud: {claims.get('aud')}")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
