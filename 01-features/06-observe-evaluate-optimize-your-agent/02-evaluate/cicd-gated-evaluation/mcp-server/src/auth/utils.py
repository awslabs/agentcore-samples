# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Token extraction from HTTP headers.

AgentCore validates the JWT at the platform edge before the request reaches this
container. We re-verify the signature here as defence in depth: if anything ever
reaches this server without passing through that authorizer — a misconfigured
request_header_allowlist, a future private-network topology, or a direct in-VPC call —
claim-based authorization would otherwise trust an attacker-supplied, unsigned token.
"""

import logging
import os

import jwt
from fastmcp.server.dependencies import get_http_headers
from jwt import PyJWKClient, PyJWKClientError, PyJWTError
from src.auth.models import AccessToken
from src.exceptions import AuthError

logger = logging.getLogger(__name__)

ROLES_META_KEY = "Roles"
SCOPES_META_KEY = "Scopes"

# Signature verification requires the pool's JWKS. USER_POOL_ID is set on the runtime by
# the CDK stack. If it is absent we fail closed rather than silently trusting claims.
_USER_POOL_ID = os.getenv("USER_POOL_ID")
_AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2")
_ISSUER = f"https://cognito-idp.{_AWS_REGION}.amazonaws.com/{_USER_POOL_ID}" if _USER_POOL_ID else None

# PyJWKClient caches signing keys in-process and refreshes on unknown kid, so this does
# not add a network call per request.
_jwk_client = PyJWKClient(f"{_ISSUER}/.well-known/jwks.json", cache_keys=True) if _ISSUER else None


def auth_meta(roles: list[str] | str | None = None, scopes: list[str] | str | None = None) -> dict:
    """Build metadata dict for role/scope-gated tools."""
    meta = {}
    if roles:
        meta[ROLES_META_KEY] = [roles] if isinstance(roles, str) else roles
    if scopes:
        meta[SCOPES_META_KEY] = [scopes] if isinstance(scopes, str) else scopes
    return meta


def _decode_verified(token: str) -> dict:
    """Decode a JWT, verifying its signature, expiry, issuer and token type.

    Cognito access tokens carry no `aud` claim (the audience is `client_id`), so audience
    verification is disabled; the platform authorizer already restricts allowed clients.

    Because audience is not checked, `token_use` must be. Cognito signs ID tokens with the
    same JWKS keys and the same issuer as access tokens, and ID tokens also carry
    `custom:roles` — so without this check an ID token would satisfy every other condition
    here and its role claims would be trusted for tool authorization.
    """
    if _jwk_client is None:
        raise AuthError("USER_POOL_ID is not configured — refusing to trust unverified token")

    signing_key = _jwk_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=_ISSUER,
        options={"verify_aud": False, "verify_signature": True, "verify_exp": True},
    )

    if claims.get("token_use") != "access":
        raise AuthError(f"Expected a Cognito access token, got token_use={claims.get('token_use')!r}")

    return claims


def get_access_token() -> AccessToken:
    """Get and verify the access token from the current HTTP request headers."""
    headers = get_http_headers(include={"authorization"})
    if not headers:
        raise AuthError("No HTTP headers found")

    auth_header = headers.get("Authorization") or headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError("No Authorization header found")

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        claims: dict = _decode_verified(token)
    except PyJWKClientError as e:
        # JWKS unreachable, or no signing key matching the token's kid. This is a
        # verification-infrastructure problem, not a bad token, and must be distinguishable
        # in logs — otherwise an outage presents as a flood of rejected tokens. Listed
        # before PyJWTError because PyJWKClientError subclasses it.
        logger.error(f"Token verification unavailable: {type(e).__name__}: {e}")
        raise AuthError("Unable to verify access token") from e
    except PyJWTError as e:
        # Bad signature, expired token, wrong issuer.
        logger.warning(f"Rejected token: {type(e).__name__}")
        raise AuthError(f"Invalid access token: {e}") from e
    except AuthError:
        # Raised by _decode_verified for missing config or a non-access token.
        raise
    except Exception as e:
        # Anything unforeseen still fails closed: never fall back to an unverified decode.
        logger.error(f"Token verification failed unexpectedly: {type(e).__name__}: {e}")
        raise AuthError("Unable to verify access token") from e

    # Cognito uses custom:roles (comma-separated string), Entra ID uses roles (list)
    roles = claims.get("roles", [])
    if not roles:
        roles_str = claims.get("custom:roles", "")
        roles = [r.strip() for r in roles_str.split(",") if r.strip()] if roles_str else []

    scopes = []
    for key in ("scp", "scope"):
        val = claims.get(key, "")
        if val:
            scopes = val.split(" ") if isinstance(val, str) else val
            break

    return AccessToken(token=token, roles=roles, scopes=scopes, claims=claims)
