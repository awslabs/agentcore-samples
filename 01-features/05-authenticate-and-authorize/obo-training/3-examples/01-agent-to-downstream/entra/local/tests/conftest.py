"""Shared test fixtures and utilities for Use Case 1 Entra tests."""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# Make the example scripts importable from tests/
EXAMPLE_DIR = Path(__file__).resolve().parent.parent
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))


def make_jwt(payload: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload. Signature is a placeholder.

    Used by unit tests to feed synthetic tokens into decode helpers.
    """
    header = {"alg": "none", "typ": "JWT"}

    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64(header)}.{b64(payload)}.fake-signature"


@pytest.fixture
def required_env(monkeypatch) -> None:
    """Populate all required env vars with placeholder values."""
    env = {
        "TENANT_ID": "tenant-id-123",
        "AGENT_CLIENT_ID": "agent-client-id",
        "AGENT_CLIENT_SECRET": "agent-secret",
        "AGENT_SCOPE": "api://agent-client-id/access_as_user",
        "GRAPH_SCOPE": "https://graph.microsoft.com/User.Read",
        "AWS_REGION": "us-west-2",
        "WORKLOAD_NAME": "obo-usecase1-entra",
        "CLIENT_PROVIDER_NAME": "obo-uc1-entra-client",
        "ACTOR_PROVIDER_NAME": "obo-uc1-entra-actor",
        "USER_ALIAS": "demo-user",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def user_jwt_cache_path(tmp_path: Path) -> Path:
    """Path to a JWT cache inside a temp dir."""
    return tmp_path / ".user-jwt-cache.json"


@pytest.fixture
def cached_user_jwt(user_jwt_cache_path: Path) -> str:
    """Build a valid-shaped user JWT cache file and return the token."""
    claims = {
        "aud": "agent-client-id",
        "iss": "https://sts.windows.net/tenant-id-123/",
        "sub": "user-alice-ppid",
        "oid": "00000000-user-alice-oid",
        "appid": "frontend-client-id",
        "scp": "access_as_user",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = make_jwt(claims)
    user_jwt_cache_path.write_text(json.dumps({"token": token, "claims": claims, "expires_at": claims["exp"]}))
    return token
