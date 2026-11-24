"""Trusted request identity and SigV4 transport. No AWS clients at import time."""

import hashlib
import os
import re
from functools import lru_cache
from uuid import UUID

import boto3
import httpx
import jwt
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


@lru_cache(maxsize=1)
def identity_config():
    pool = os.environ["COGNITO_USER_POOL_ID"]
    region = pool.split("_", 1)[0]
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"
    return (
        issuer,
        os.environ["COGNITO_CLIENT_ID"],
        jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True, timeout=5),
    )


def authenticated_actor(payload):
    issuer, audience, keys = identity_config()
    token = payload.get("id_token")
    if not isinstance(token, str) or len(token) > 16384:
        raise ValueError("A Cognito ID token is required")
    key = keys.get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
    if claims.get("token_use") != "id":
        raise ValueError("Expected an ID token")
    actor = str(UUID(claims["sub"]))
    if payload.get("user_id", actor) != actor:
        raise ValueError("Payload identity does not match authenticated subject")
    return actor


def request_ids(payload, actor):
    # Frontend UUIDs and SDK <scenario_id>-<uuid> sessions share this bounded
    # contract. Preserve the original runner ID for trace correlation; only
    # the Memory key is hashed with the independently verified actor.
    prompt = str(UUID(payload["prompt_uuid"]))
    conversation = payload["session_id"]
    if not isinstance(conversation, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{35,99}", conversation):
        raise ValueError("session_id must be 36-100 ASCII letters, digits, underscores or hyphens")
    session = hashlib.sha256(f"{actor}:{conversation}".encode()).hexdigest()
    return prompt, session


class GatewaySigV4(httpx.Auth):
    """Sign every MCP POST/GET/DELETE with refreshed Runtime role credentials."""

    requires_request_body = True

    def __init__(self, region, session=None):
        self.region = region
        self.session = session or boto3.Session()

    def auth_flow(self, request):
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("No Runtime credentials available for Gateway")
        aws_request = AWSRequest(
            method=request.method, url=str(request.url), data=request.content, headers=dict(request.headers)
        )
        SigV4Auth(credentials.get_frozen_credentials(), "bedrock-agentcore", self.region).add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))
        yield request
