#!/usr/bin/env python3
"""
Get a Cognito M2M JWT token for authenticating with the Claims Agent Gateway.

Usage:
    python scripts/get_token.py

Requires: COGNITO_CLIENT_SECRET env var (get from AWS Console or Secrets Manager)

Environment variables:
    COGNITO_USER_POOL_ID  (required - from CDK output)
    COGNITO_CLIENT_ID     (required - from CDK output)
    COGNITO_CLIENT_SECRET (required - from CDK output)
    AWS_REGION            (default: us-east-1)
"""

import base64
import json
import os
import sys
import urllib.parse
import urllib.request

REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET", "")
DOMAIN_PREFIX = os.environ.get("COGNITO_DOMAIN_PREFIX", "")

if not CLIENT_SECRET or not CLIENT_ID or not USER_POOL_ID:
    print("ERROR: Set required environment variables:", file=sys.stderr)
    print("  COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET", file=sys.stderr)
    print("  COGNITO_DOMAIN_PREFIX (e.g., claims-agent-<account-id>)", file=sys.stderr)
    sys.exit(1)

# Client credentials grant
token_url = f"https://{DOMAIN_PREFIX}.auth.{REGION}.amazoncognito.com/oauth2/token"
creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

data = urllib.parse.urlencode(
    {
        "grant_type": "client_credentials",
        "scope": "agentcore/invoke",
    }
).encode()

req = urllib.request.Request(
    token_url,
    data=data,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {creds}",
    },
)

try:
    if not token_url.startswith("https://"):
        raise ValueError(f"Only HTTPS URLs are permitted: {token_url}")
    with urllib.request.urlopen(req) as resp:  # nosec B310
        token_data = json.loads(resp.read())
        print(token_data["access_token"])
except urllib.error.HTTPError as e:
    print(f"ERROR: {e.code} {e.reason}", file=sys.stderr)
    print(e.read().decode(), file=sys.stderr)
    sys.exit(1)
