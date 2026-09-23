"""Agent-as-principal M2M auth to the Gateway (spec §10).

The Runtime obtains its own Cognito token via the OAuth2 client_credentials flow
and presents it on the MCP transport. No end-user identity here — the front door
is an S3 event, so the agent authenticates as itself (claims ADR-0004).

Stdlib only, so this stays importable without the agent's heavy deps.
"""

import base64
import json
import urllib.parse
import urllib.request

from config import (
    GATEWAY_CLIENT_ID,
    GATEWAY_CLIENT_SECRET,
    GATEWAY_OAUTH_SCOPES,
    GATEWAY_TOKEN_ENDPOINT,
)


def get_gateway_token() -> str | None:
    """Return an M2M access token for the Gateway, or None if unconfigured."""
    if not (GATEWAY_TOKEN_ENDPOINT and GATEWAY_CLIENT_ID and GATEWAY_CLIENT_SECRET):
        return None
    if not GATEWAY_TOKEN_ENDPOINT.startswith("https://"):
        raise ValueError(f"token endpoint must be https: {GATEWAY_TOKEN_ENDPOINT}")

    creds = base64.b64encode(f"{GATEWAY_CLIENT_ID}:{GATEWAY_CLIENT_SECRET}".encode()).decode()
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": GATEWAY_OAUTH_SCOPES.replace(",", " "),
        }
    ).encode()
    req = urllib.request.Request(
        GATEWAY_TOKEN_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {creds}",
        },
    )
    with urllib.request.urlopen(req) as resp:  # nosec B310 — https enforced above
        return json.loads(resp.read())["access_token"]
