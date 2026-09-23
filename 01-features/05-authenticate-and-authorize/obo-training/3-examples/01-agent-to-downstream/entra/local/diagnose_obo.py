"""
Diagnostic helper for troubleshooting OBO exchange failures (Entra flavor).

Checks the cached user JWT and tries the OBO exchange with detailed error output.

Usage:
    python diagnose_obo.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

CACHE_PATH = Path(__file__).resolve().parent / ".user-jwt-cache.json"


def decode_claims(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


def check(label: str, condition: bool, detail: str = "") -> None:
    icon = "✓" if condition else "✗"
    print(f"  {icon} {label}")
    if detail:
        print(f"    {detail}")


def main() -> int:
    load_dotenv()

    region = os.environ.get("AWS_REGION", "us-west-2")
    agent_client_id = os.environ.get("AGENT_CLIENT_ID", "")
    tenant_id = os.environ.get("TENANT_ID", "")
    workload_name = os.environ.get("WORKLOAD_NAME", "")
    actor_provider = os.environ.get("ACTOR_PROVIDER_NAME", "")
    graph_scope = os.environ.get("GRAPH_SCOPE", "")

    print("═" * 60)
    print("OBO Diagnostic — Entra ID")
    print("═" * 60)
    print()

    print("▶ Environment")
    check("TENANT_ID set", bool(tenant_id), tenant_id)
    check("AGENT_CLIENT_ID set", bool(agent_client_id), agent_client_id)
    check("WORKLOAD_NAME set", bool(workload_name), workload_name)
    check("ACTOR_PROVIDER_NAME set", bool(actor_provider), actor_provider)
    check("GRAPH_SCOPE set", bool(graph_scope), graph_scope)
    check(
        "GRAPH_SCOPE is fully qualified",
        graph_scope.startswith("https://graph.microsoft.com/"),
        "must start with 'https://graph.microsoft.com/'",
    )
    print()

    print("▶ Cached user JWT")
    if not CACHE_PATH.exists():
        check("cache exists", False, "Run `python generate_user_jwt.py` first.")
        return 1
    check("cache exists", True, str(CACHE_PATH))

    data = json.loads(CACHE_PATH.read_text())
    token = data["token"]
    claims = decode_claims(token)

    expires_at = claims.get("exp", 0)
    seconds_left = expires_at - int(time.time())
    check(
        "token not expired",
        seconds_left > 60,
        f"{seconds_left}s remaining" if seconds_left > 0 else "EXPIRED — re-run generate_user_jwt.py",
    )

    aud = claims.get("aud", "")
    check(
        "aud == AGENT_CLIENT_ID",
        aud == agent_client_id or aud == f"api://{agent_client_id}",
        f"token aud = {aud!r}",
    )

    iss = claims.get("iss", "")
    check(
        "iss points to your tenant",
        tenant_id in iss,
        f"iss = {iss}",
    )
    # Parse the issuer URL properly to check the host — `'foo' in url` is a
    # substring match that can be spoofed (e.g., https://evil.example/sts.windows.net/x).
    # Even in a diagnostic display we want the host-based check.
    from urllib.parse import urlparse

    iss_host = urlparse(iss).netloc.lower()
    is_v1 = iss_host == "sts.windows.net"
    print(f"    token version hint: {'v1.0' if is_v1 else 'v2.0'}")
    print()

    if seconds_left <= 60 or (aud != agent_client_id and aud != f"api://{agent_client_id}"):
        print("Fix the above issues, then re-run this script.")
        return 1

    print("▶ Attempting OBO exchange")
    ac = boto3.client("bedrock-agentcore", region_name=region)

    try:
        wl = ac.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )["workloadAccessToken"]
        check("GetWorkloadAccessTokenForJWT succeeded", True)
    except ClientError as e:
        check("GetWorkloadAccessTokenForJWT succeeded", False, str(e))
        return 1

    try:
        obo = ac.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=actor_provider,
            scopes=[graph_scope],
            oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        )
        check("GetResourceOauth2Token (OBO) succeeded", True)
        graph_claims = decode_claims(obo["accessToken"])
        print()
        print("  Graph token claims:")
        for k in ("aud", "iss", "scp", "appid", "sub", "oid"):
            if k in graph_claims:
                print(f"    {k}: {graph_claims[k]}")
        return 0
    except ClientError as e:
        msg = str(e)
        check("GetResourceOauth2Token (OBO) succeeded", False, msg)
        print()
        print("  Likely causes for HTTP 400 from Entra:")
        print("    1. Admin consent not granted. Go to Entra → App registrations → your agent app →")
        print("       API permissions and verify every permission shows 'Granted for <tenant>'.")
        print("    2. GRAPH_SCOPE is wrong or not granted to your agent app.")
        print("    3. Client secret expired — regenerate and re-run 01_create_providers.py.")
        print("    4. Token version mismatch (rare with built-in MicrosoftOauth2 provider).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
