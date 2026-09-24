# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Cognito ID-token validation for the console API.

The browser signs in against Cognito directly (USER_PASSWORD_AUTH) and sends the
resulting ID token as a bearer token. This module verifies that token against
the user pool's published JWKS before any request is allowed to reach AWS.

Without this the API would be an open proxy holding IAM credentials, since the
Lambda Function URL itself is public (auth_type NONE) so the browser can stream
from it without signing requests.
"""

import logging
import os
import time
from typing import Any
from urllib.request import urlopen

from fastapi import HTTPException, Request
from jose import jwt
from jose.exceptions import JWTError

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "us-east-1")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
CLIENT_ID = os.environ.get("USER_POOL_CLIENT_ID", "")

# Local development sets this to skip auth. It is ignored inside Lambda
# (AWS_LAMBDA_FUNCTION_NAME is always set there) so the bypass is structurally
# impossible in the deployed console, regardless of how the env var is set.
AUTH_DISABLED = (
    os.environ.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes")
    and not os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
)

_JWKS_TTL_SECONDS = 3600
# Minimum gap between forced refetches on an unknown `kid`. Without this, junk
# tokens carrying random `kid`s each trigger a blocking outbound fetch, and
# verify_request runs in a bounded threadpool — a cheap way to saturate it.
_JWKS_REFETCH_COOLDOWN_SECONDS = 300
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at = 0.0
_jwks_last_forced_refetch = 0.0


def _issuer() -> str:
    return f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"


def _jwks() -> dict[str, Any]:
    """Fetch and cache the pool's JWKS.

    Cached because Lambda reuses warm containers across invocations, and the
    signing keys rotate on the order of years.
    """
    global _jwks_cache, _jwks_fetched_at

    if _jwks_cache and (time.time() - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache

    url = f"{_issuer()}/.well-known/jwks.json"
    # nosec B310 — the URL is built from our own pool ID, not user input.
    with urlopen(url, timeout=5) as response:  # noqa: S310
        import json

        _jwks_cache = json.load(response)
    _jwks_fetched_at = time.time()
    logger.info("Fetched JWKS with %d key(s)", len(_jwks_cache.get("keys", [])))
    return _jwks_cache


def _signing_key(token: str) -> dict[str, Any]:
    """Find the JWKS entry matching the token's `kid` header."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Malformed token") from exc

    for key in _jwks().get("keys", []):
        if key.get("kid") == kid:
            return key

    # An unknown kid may mean the keys rotated, so refetch — but at most once per
    # cooldown window. Cognito rotates keys on the order of years, so a flood of
    # unknown kids is far more likely an attack than a real rotation, and must
    # not each force a blocking network round trip.
    global _jwks_last_forced_refetch
    now = time.time()
    if now - _jwks_last_forced_refetch >= _JWKS_REFETCH_COOLDOWN_SECONDS:
        _jwks_last_forced_refetch = now
        _jwks_cache.clear()
        for key in _jwks().get("keys", []):
            if key.get("kid") == kid:
                return key

    raise HTTPException(status_code=401, detail="Unknown token signing key")


def verify_request(request: Request) -> dict[str, Any]:
    """Validate the request's bearer token and return its claims.

    Raises:
        HTTPException: 401 if the token is missing, malformed, expired, or not
            issued by this user pool for this client.
    """
    if AUTH_DISABLED:
        return {"sub": "local-dev", "email": "local@dev"}

    if not USER_POOL_ID or not CLIENT_ID:
        # Fail closed: a misconfigured deployment must not serve traffic
        # unauthenticated.
        raise HTTPException(
            status_code=503, detail="Authentication is not configured on this API"
        )

    # The Function URL uses AWS_IAM authorization, so SigV4 owns the
    # Authorization header. The ID token therefore arrives on X-Id-Token.
    # Bearer tokens are still accepted for local development and CLI testing,
    # where requests are unsigned.
    token = request.headers.get("x-id-token", "").strip()

    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header.split(" ", 1)[1].strip()

    if not token:
        raise HTTPException(
            status_code=401, detail="Missing ID token (X-Id-Token or Bearer)"
        )

    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            audience=CLIENT_ID,
            issuer=_issuer(),
        )
    except JWTError as exc:
        # Log the reason server-side, but return a flat message: telling the
        # caller *why* a token failed (bad signature vs expired vs wrong
        # audience) is an oracle that aids forging attempts.
        logger.warning("Token rejected: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    # An access token would also validate against the pool but carries no
    # audience claim and a different use; require the ID token explicitly.
    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Expected a Cognito ID token")

    return claims
