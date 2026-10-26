"""Shared test fixtures and utilities for Use Case 1 Okta tests."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
if str(EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_DIR))


def make_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "none", "typ": "JWT"}

    def b64(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64(header)}.{b64(payload)}.fake-signature"


@pytest.fixture
def required_env(monkeypatch) -> None:
    env = {
        "OKTA_DOMAIN": "integrator-1234567.okta.com",
        "OKTA_AUTH_SERVER_ID": "default",
        "OKTA_AUDIENCE": "api://default",
        "NATIVE_APP_CLIENT_ID": "native-client-id",
        "NATIVE_APP_CLIENT_SECRET": "native-secret",
        "SERVICE_APP_CLIENT_ID": "service-client-id",
        "SERVICE_APP_CLIENT_SECRET": "service-secret",
        "UPSTREAM_SCOPE": "openid",
        "DOWNSTREAM_SCOPE": "oboe2e.apiC.read",
        "AWS_REGION": "us-west-2",
        "WORKLOAD_NAME": "obo-usecase1-okta",
        "CLIENT_PROVIDER_NAME": "obo-uc1-okta-client",
        "ACTOR_PROVIDER_NAME": "obo-uc1-okta-actor",
        "USER_ALIAS": "demo-user",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
