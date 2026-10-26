"""
Integration tests for Use Case 1 Okta — real AgentCore Identity + Okta.

Skipped by default. Enable with RUN_INTEGRATION=1.

Prerequisites:
  1. Complete IDP_SETUP.md
  2. python 01_create_providers.py
  3. python generate_user_jwt.py  (populates .user-jwt-cache.json)
  4. RUN_INTEGRATION=1 pytest tests/test_integration_obo.py -v
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import boto3
import pytest
from dotenv import load_dotenv

load_dotenv()

CACHE_PATH = Path(__file__).resolve().parent.parent / ".user-jwt-cache.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to exercise real AgentCore Identity + Okta.",
)


def _load_cached_user_jwt() -> tuple[str, dict]:
    if not CACHE_PATH.exists():
        pytest.skip(f"No cached user JWT at {CACHE_PATH.name}. Run `python generate_user_jwt.py` first.")
    data = json.loads(CACHE_PATH.read_text())
    if data["expires_at"] < int(time.time()) + 60:
        pytest.skip("Cached user JWT has expired. Re-run generate_user_jwt.py.")
    return data["token"], data["claims"]


@pytest.fixture(scope="module")
def user_jwt() -> tuple[str, dict]:
    return _load_cached_user_jwt()


@pytest.fixture(scope="module")
def agentcore():
    region = os.environ.get("AWS_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore", region_name=region)


def _decode(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


@pytest.mark.integration
class TestOktaObo:
    def test_wrap_user_jwt(self, agentcore, user_jwt):
        """GetWorkloadAccessTokenForJWT should accept the cached Okta user JWT."""
        token, _ = user_jwt
        workload_name = os.environ["WORKLOAD_NAME"]

        resp = agentcore.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )

        assert "workloadAccessToken" in resp

    def test_obo_exchange_preserves_user_sub(self, agentcore, user_jwt):
        """After OBO, sub should match the user; cid should be the service app."""
        token, user_claims = user_jwt
        workload_name = os.environ["WORKLOAD_NAME"]
        actor_provider = os.environ["ACTOR_PROVIDER_NAME"]
        downstream_scope = os.environ["DOWNSTREAM_SCOPE"]
        audience = os.environ["OKTA_AUDIENCE"]
        service_app_client_id = os.environ["SERVICE_APP_CLIENT_ID"]

        wl = agentcore.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )["workloadAccessToken"]

        obo = agentcore.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=actor_provider,
            scopes=[downstream_scope],
            oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
            customParameters={
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            },
            audiences=[audience],
        )

        downstream_claims = _decode(obo["accessToken"])

        # User identity preserved
        assert downstream_claims["sub"] == user_claims["sub"]
        # Actor changed from native app to service app
        assert downstream_claims["cid"] == service_app_client_id
        # New scope is active
        scp = downstream_claims.get("scp", [])
        if isinstance(scp, str):
            scp = scp.split()
        assert downstream_scope in scp
