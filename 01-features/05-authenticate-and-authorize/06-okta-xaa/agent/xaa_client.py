"""XAA token-exchange client (Okta AI Agents / Cross App Access model).

Implements the two-leg Identity Assertion Authorization Grant (ID-JAG) flow as
Okta productizes it for **AI Agents**, where the resource is an Okta **custom
Authorization Server**:

  Leg 1  (Okta ORG authz server, /oauth2/v1/token): the user's OIDC ID token is
          exchanged (RFC 8693 token-exchange) for an ID-JAG whose `aud` is the
          resource's custom Authorization Server.

  Leg 2  (the resource custom AS, /oauth2/<as-id>/v1/token): the ID-JAG is
          redeemed (RFC 7523 jwt-bearer) for a normal access token for the API.

In BOTH legs the caller authenticates as the **AI Agent** (a `wlp…` workload
principal) with the SAME `private_key_jwt` key registered on that agent in Okta.
There is no separate "resource client" -- Okta brokers the app-to-app trust via
the agent's Resource Connection.

A small per-(subject) TTL cache avoids repeating the exchange on every tool call.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx
import jwt
from client_auth import apply_client_auth

# OAuth grant/token-type URIs (RFC 8693 / draft ID-JAG) — not credentials.
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # nosec B105
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"  # nosec B105
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"  # nosec B105
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"  # nosec B105


def _load_key_from_secrets_manager(secret_id: str, region: str) -> str:
    """Load the agent's PEM private key from AWS Secrets Manager (runtime path)."""
    import boto3  # imported lazily so local/CLI use doesn't require boto3

    client = boto3.client("secretsmanager", region_name=region)
    return client.get_secret_value(SecretId=secret_id)["SecretString"]


@dataclass
class XaaConfig:
    # Okta ORG issuer (mints the ID-JAG at Leg 1).
    okta_issuer: str
    # The AI Agent id (Directory > AI Agents) -- a `wlp…` id that IS the OAuth
    # client_id and the iss/sub of the client assertion in both legs.
    agent_client_id: str
    # The resource's Okta custom Authorization Server issuer (redeems the ID-JAG
    # at Leg 2 and mints the downstream access token).
    resource_as_issuer: str
    # The custom AS audience (the downstream token's `aud`, e.g. api://todo).
    resource_audience: str
    # The HTTP base URL of the resource API to call with the downstream token.
    resource_api_url: str
    scopes: str = "todos.read"
    client_auth_method: str = "private_key_jwt"
    client_assertion_alg: str = "RS256"
    # The AI Agent's signing key (registered on the agent in Okta). Provide ONE
    # of: inline PEM, a PEM file path, or an AWS Secrets Manager secret id.
    private_key: str = ""
    private_key_path: str = ""
    private_key_kid: str = ""

    @property
    def org_token_endpoint(self) -> str:
        return f"{self.okta_issuer}/oauth2/v1/token"

    @property
    def resource_token_endpoint(self) -> str:
        return f"{self.resource_as_issuer}/v1/token"

    @classmethod
    def from_env(cls) -> XaaConfig:
        private_key = os.environ.get("OKTA_PRIVATE_KEY", "")
        secret_id = os.environ.get("XAA_KEY_SECRET_ID", "")
        if not private_key and secret_id:
            private_key = _load_key_from_secrets_manager(
                secret_id, os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
            )
        return cls(
            okta_issuer=os.environ["OKTA_ISSUER"].rstrip("/"),
            agent_client_id=os.environ["OKTA_CLIENT_ID"],
            resource_as_issuer=os.environ["RESOURCE_AS_ISSUER"].rstrip("/"),
            resource_audience=os.environ.get("RESOURCE_API", "api://todo"),
            resource_api_url=os.environ["RESOURCE_API_URL"].rstrip("/"),
            scopes=os.environ.get("XAA_SCOPES", "todos.read"),
            client_auth_method=os.environ.get("CLIENT_AUTH_METHOD", "private_key_jwt"),
            client_assertion_alg=os.environ.get("CLIENT_ASSERTION_ALG", "RS256"),
            private_key=private_key,
            private_key_path=os.environ.get("OKTA_PRIVATE_KEY_PATH", ""),
            private_key_kid=os.environ.get("OKTA_PRIVATE_KEY_KID", ""),
        )


class XaaError(RuntimeError):
    pass


# subject -> (access_token, expires_at_epoch)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_CACHE_SKEW = 30  # refresh a bit before actual expiry
_CACHE_MAX_ENTRIES = 1024


def _agent_auth(cfg: XaaConfig, token_endpoint: str):
    """private_key_jwt as the AI Agent -- same key/kid for both legs."""
    return apply_client_auth(
        method=cfg.client_auth_method,
        client_id=cfg.agent_client_id,
        client_secret="",  # nosec B106 - private_key_jwt; no secret used
        token_endpoint=token_endpoint,
        private_key=cfg.private_key,
        private_key_path=cfg.private_key_path,
        kid=cfg.private_key_kid,
        alg=cfg.client_assertion_alg,
    )


def exchange_id_token_for_id_jag(cfg: XaaConfig, id_token: str) -> str:
    """Leg 1: ID token -> ID-JAG at the Okta ORG authorization server."""
    data = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "requested_token_type": ID_JAG_TOKEN_TYPE,
        "subject_token": id_token,
        "subject_token_type": ID_TOKEN_TYPE,
        "audience": cfg.resource_as_issuer,
        "scope": cfg.scopes,
    }
    extra, auth = _agent_auth(cfg, cfg.org_token_endpoint)
    data.update(extra)
    resp = httpx.post(cfg.org_token_endpoint, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        raise XaaError(f"ID-JAG exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def exchange_id_jag_for_access_token(cfg: XaaConfig, id_jag: str) -> tuple[str, int]:
    """Leg 2: ID-JAG -> access token at the resource custom AS."""
    data = {"grant_type": JWT_BEARER_GRANT, "assertion": id_jag}
    extra, auth = _agent_auth(cfg, cfg.resource_token_endpoint)
    data.update(extra)
    resp = httpx.post(cfg.resource_token_endpoint, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        raise XaaError(f"Access token exchange failed ({resp.status_code}): {resp.text}")
    body = resp.json()
    return body["access_token"], int(body.get("expires_in", 3600))


def _cache_put(subject: str, access_token: str, expires_at: float) -> None:
    now = time.time()
    for key, (_, exp) in list(_TOKEN_CACHE.items()):
        if exp <= now:
            del _TOKEN_CACHE[key]
    if len(_TOKEN_CACHE) >= _CACHE_MAX_ENTRIES and subject not in _TOKEN_CACHE:
        oldest = min(_TOKEN_CACHE, key=lambda k: _TOKEN_CACHE[k][1])
        del _TOKEN_CACHE[oldest]
    _TOKEN_CACHE[subject] = (access_token, expires_at)


def get_resource_access_token(cfg: XaaConfig, id_token: str) -> str:
    """Return a cached or freshly minted resource access token for the user.

    The ID token's signature is validated upstream by the AgentCore Runtime
    inbound JWT authorizer before this code runs; here we only decode it
    (without re-verifying) to read `sub` for use as the cache key.
    """
    subject = jwt.decode(id_token, options={"verify_signature": False}).get("sub", "unknown")

    cached = _TOKEN_CACHE.get(subject)
    if cached and cached[1] - _CACHE_SKEW > time.time():
        return cached[0]

    id_jag = exchange_id_token_for_id_jag(cfg, id_token)
    access_token, expires_in = exchange_id_jag_for_access_token(cfg, id_jag)
    _cache_put(subject, access_token, time.time() + expires_in)
    return access_token
