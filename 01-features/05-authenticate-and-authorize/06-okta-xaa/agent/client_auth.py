"""OAuth client authentication for XAA token endpoints.

Two methods are supported, selectable via configuration:

  client_secret   HTTP Basic client authentication (client_secret_basic).
  private_key_jwt Signed JWT client assertion (RFC 7523) -- the asymmetric /
                  certificate-based method. No shared secret crosses the wire;
                  the authorization server verifies the assertion against the
                  public key registered for the client.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import jwt

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _load_private_key(pem: str, path: str) -> str:
    if pem:
        return pem
    if path:
        return Path(path).read_text()
    raise ValueError("private_key_jwt requires a private key (PRIVATE_KEY or PRIVATE_KEY_PATH)")


def build_client_assertion(
    client_id: str,
    token_endpoint: str,
    private_key: str,
    kid: str = "",
    alg: str = "RS256",
    lifetime: int = 300,
) -> str:
    """Build a signed JWT proving control of the client's private key."""
    now = int(time.time())
    headers = {"kid": kid} if kid else {}
    return jwt.encode(
        {
            "iss": client_id,
            "sub": client_id,
            "aud": token_endpoint,
            "iat": now,
            "exp": now + lifetime,
            "jti": uuid.uuid4().hex,
        },
        private_key,
        algorithm=alg,
        headers=headers,
    )


def apply_client_auth(
    *,
    method: str,
    client_id: str,
    client_secret: str,
    token_endpoint: str,
    private_key: str = "",
    private_key_path: str = "",
    kid: str = "",
    alg: str = "RS256",
) -> tuple[dict, tuple[str, str] | None]:
    """Return (extra_body_params, httpx_basic_auth) for a token request.

    For private_key_jwt, credentials go in the request body and no Basic auth
    tuple is returned. For client_secret, HTTP Basic auth is used.
    """
    if method == "private_key_jwt":
        key = _load_private_key(private_key, private_key_path)
        assertion = build_client_assertion(client_id, token_endpoint, key, kid, alg)
        return (
            {
                "client_id": client_id,
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": assertion,
            },
            None,
        )

    if method == "client_secret":
        return ({}, (client_id, client_secret))

    raise ValueError(f"Unsupported CLIENT_AUTH_METHOD: {method}")
