"""
Integration tests for Use Case 1 Entra — exercise real AgentCore Identity + Entra.

Skipped by default. Enable with RUN_INTEGRATION=1 in the environment.

Prerequisites:
  1. Complete IDP_SETUP.md
  2. Run `python 01_create_providers.py` once
  3. Run `python generate_user_jwt.py` to populate .user-jwt-cache.json
  4. RUN_INTEGRATION=1 pytest tests/test_integration_obo.py -v
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import boto3
import pytest
import requests
from dotenv import load_dotenv

load_dotenv()

CACHE_PATH = Path(__file__).resolve().parent.parent / ".user-jwt-cache.json"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 to exercise real AgentCore Identity + Entra ID.",
)


def _load_cached_user_jwt() -> tuple[str, dict]:
    if not CACHE_PATH.exists():
        pytest.skip(f"No cached user JWT at {CACHE_PATH.name}. Run `python generate_user_jwt.py` first.")
    data = json.loads(CACHE_PATH.read_text())
    if data["expires_at"] < int(time.time()) + 60:
        pytest.skip("Cached user JWT has expired. Re-run `python generate_user_jwt.py`.")
    return data["token"], data["claims"]


@pytest.fixture(scope="module")
def user_jwt() -> tuple[str, dict]:
    return _load_cached_user_jwt()


@pytest.fixture(scope="module")
def agentcore():
    region = os.environ.get("AWS_REGION", "us-west-2")
    return boto3.client("bedrock-agentcore", region_name=region)


@pytest.mark.integration
class TestEntraObo:
    """Exercises the AgentCore Identity OBO flow against real Entra ID."""

    def test_wrap_user_jwt(self, agentcore, user_jwt):
        """GetWorkloadAccessTokenForJWT should accept our cached user JWT."""
        token, _ = user_jwt
        workload_name = os.environ["WORKLOAD_NAME"]

        resp = agentcore.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )

        assert "workloadAccessToken" in resp
        assert len(resp["workloadAccessToken"]) > 50

    def test_obo_exchange_returns_graph_token(self, agentcore, user_jwt):
        """The OBO exchange should yield a Graph-audienced token."""
        import base64
        import json as _json

        token, _ = user_jwt
        workload_name = os.environ["WORKLOAD_NAME"]
        actor_provider = os.environ["ACTOR_PROVIDER_NAME"]
        graph_scope = os.environ["GRAPH_SCOPE"]

        wl = agentcore.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )["workloadAccessToken"]

        obo = agentcore.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=actor_provider,
            scopes=[graph_scope],
            oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        )

        graph_token = obo["accessToken"]
        payload_b64 = graph_token.split(".")[1]
        padding = "=" * (-len(payload_b64) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(payload_b64 + padding))

        # Graph's resource ID
        assert claims["aud"] in {
            "00000003-0000-0000-c000-000000000000",
            "https://graph.microsoft.com",
        }, f"Unexpected aud: {claims['aud']}"
        assert "User.Read" in claims.get("scp", "")

    def test_graph_call_succeeds(self, agentcore, user_jwt):
        """The OBO'd Graph token should actually work against Graph /me."""
        token, user_claims = user_jwt
        workload_name = os.environ["WORKLOAD_NAME"]
        actor_provider = os.environ["ACTOR_PROVIDER_NAME"]
        graph_scope = os.environ["GRAPH_SCOPE"]

        wl = agentcore.get_workload_access_token_for_jwt(
            workloadName=workload_name,
            userToken=token,
        )["workloadAccessToken"]

        graph_token = agentcore.get_resource_oauth2_token(
            workloadIdentityToken=wl,
            resourceCredentialProviderName=actor_provider,
            scopes=[graph_scope],
            oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
        )["accessToken"]

        r = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {graph_token}"},
            timeout=30,
        )
        assert r.status_code == 200, f"{r.status_code}: {r.text}"
        profile = r.json()
        assert "id" in profile
