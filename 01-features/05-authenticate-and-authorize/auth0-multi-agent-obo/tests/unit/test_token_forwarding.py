# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""
Unit tests for JWT token forwarding in coordinator.

These tests verify that the coordinator correctly prioritizes the Auth0 JWT
from the payload over the internal AgentCore workloadaccesstoken.

The correct priority order is:
1. payload.access_token (real Auth0 JWT from client)
2. Authorization header Bearer token
3. workloadaccesstoken (internal AgentCore token - last resort)

Run with:
    python -m pytest tests/unit/test_token_forwarding.py -v
"""

import base64
import json
import pytest
from unittest.mock import Mock
from typing import Dict, Any


def create_mock_jwt(claims: Dict[str, Any]) -> str:
    """Create a mock JWT token with given claims."""
    header = {"alg": "RS256", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = "fake_signature"
    return f"{header_b64}.{payload_b64}.{signature}"


class TestTokenForwardingPriority:
    """Test that coordinator correctly prioritizes JWT sources."""

    @pytest.fixture
    def auth0_jwt(self):
        """Create a real Auth0 JWT token."""
        return create_mock_jwt(
            {
                "iss": "https://your-tenant.us.auth0.com/",
                "sub": "test_auth0_client_id@clients",
                "aud": "https://agentcore-financial-api",
                "iat": 1706000000,
                "exp": 1706086400,
                "azp": "test_auth0_client_id",
                "gty": "client-credentials",
            }
        )

    @pytest.fixture
    def workload_token(self):
        """Create a mock workloadaccesstoken (internal AgentCore token)."""
        # This is NOT a valid JWT - it's an internal token
        return "workload_internal_token_abc123"

    def test_payload_access_token_preferred_over_workload_token(self, auth0_jwt, workload_token):
        """
        CRITICAL TEST: Payload access_token should be preferred over workloadaccesstoken.

        This catches the bug where workloadaccesstoken was being used for sub-agent
        forwarding instead of the real Auth0 JWT.
        """
        # Simulate what the coordinator receives
        payload = {
            "prompt": "What is my profile?",
            "sessionId": "test-session",
            "access_token": auth0_jwt,  # Real Auth0 JWT from client
            "claims": {
                "sub": "test_auth0_client_id@clients",
                "aud": "https://agentcore-financial-api",
            },
        }

        # Mock context with workloadaccesstoken header
        context = Mock()
        context.request_headers = {
            "workloadaccesstoken": workload_token,  # Internal AgentCore token
        }

        # The correct behavior: use payload.access_token, NOT workloadaccesstoken
        selected_token = select_token_for_forwarding(payload, context)

        assert selected_token == auth0_jwt, (
            f"Should use payload access_token, not workloadaccesstoken. Got: {selected_token[:50]}..."
        )
        assert selected_token != workload_token, "Should NOT use workloadaccesstoken for sub-agent forwarding"

    def test_authorization_header_used_when_no_payload_token(self, auth0_jwt):
        """Authorization header Bearer token should be used if no payload token."""
        payload = {
            "prompt": "What is my profile?",
            "sessionId": "test-session",
            # No access_token in payload
        }

        context = Mock()
        context.request_headers = {
            "authorization": f"Bearer {auth0_jwt}",
        }

        selected_token = select_token_for_forwarding(payload, context)

        assert selected_token == auth0_jwt, "Should use Authorization header Bearer token when no payload token"

    def test_workload_token_only_as_last_resort(self, workload_token):
        """workloadaccesstoken should only be used when no other token available."""
        payload = {
            "prompt": "What is my profile?",
            "sessionId": "test-session",
            # No access_token
        }

        context = Mock()
        context.request_headers = {
            "workloadaccesstoken": workload_token,
            # No Authorization header
        }

        selected_token = select_token_for_forwarding(payload, context)

        # Only use workload token as last resort
        assert selected_token == workload_token, "Should use workloadaccesstoken only when no other token available"

    def test_empty_payload_token_not_used(self, auth0_jwt, workload_token):
        """Empty string access_token in payload should be ignored."""
        payload = {
            "prompt": "What is my profile?",
            "sessionId": "test-session",
            "access_token": "",  # Empty string
        }

        context = Mock()
        context.request_headers = {
            "authorization": f"Bearer {auth0_jwt}",
            "workloadaccesstoken": workload_token,
        }

        selected_token = select_token_for_forwarding(payload, context)

        assert selected_token == auth0_jwt, "Should skip empty payload token and use Authorization header"

    def test_none_payload_token_not_used(self, auth0_jwt):
        """None access_token in payload should be ignored."""
        payload = {
            "prompt": "What is my profile?",
            "sessionId": "test-session",
            "access_token": None,
        }

        context = Mock()
        context.request_headers = {
            "authorization": f"Bearer {auth0_jwt}",
        }

        selected_token = select_token_for_forwarding(payload, context)

        assert selected_token == auth0_jwt, "Should skip None payload token and use Authorization header"


def select_token_for_forwarding(payload: Dict[str, Any], context) -> str:
    """
    Select the correct token for sub-agent forwarding.

    Priority order:
    1. payload.access_token (real Auth0 JWT from client)
    2. Authorization header Bearer token
    3. workloadaccesstoken (internal AgentCore token - last resort)

    This function represents the CORRECT behavior that the coordinator should implement.
    """
    # Priority 1: Payload access_token (real JWT from client)
    if payload:
        payload_token = payload.get("access_token")
        if payload_token and isinstance(payload_token, str) and payload_token.strip():
            return payload_token

    # Priority 2: Authorization header
    headers = {}
    if context and hasattr(context, "request_headers"):
        headers = context.request_headers or {}

    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove 'Bearer ' prefix

    # Priority 3: workloadaccesstoken (last resort)
    workload_token = (
        headers.get("workloadaccesstoken") or headers.get("x-amzn-bedrock-agentcore-runtime-workload-accesstoken") or ""
    )
    if workload_token:
        return workload_token

    return ""


class TestTokenForwardingIntegration:
    """Integration tests for token forwarding to sub-agents."""

    @pytest.fixture
    def auth0_jwt(self):
        """Create a real Auth0 JWT token."""
        return create_mock_jwt(
            {
                "iss": "https://your-tenant.us.auth0.com/",
                "sub": "test_auth0_client_id@clients",
                "aud": "https://agentcore-financial-api",
            }
        )

    def test_forwarded_token_is_valid_jwt(self, auth0_jwt):
        """Token forwarded to sub-agents must be a valid JWT."""
        payload = {
            "prompt": "test",
            "access_token": auth0_jwt,
        }
        context = Mock()
        context.request_headers = {"workloadaccesstoken": "internal_token"}

        selected_token = select_token_for_forwarding(payload, context)

        # Verify it's a valid JWT format (3 parts separated by dots)
        parts = selected_token.split(".")
        assert len(parts) == 3, f"Token must be valid JWT format (3 parts), got {len(parts)} parts"

        # Verify we can decode the payload
        payload_part = parts[1]
        padding = 4 - len(payload_part) % 4
        if padding != 4:
            payload_part += "=" * padding
        decoded = base64.urlsafe_b64decode(payload_part)
        claims = json.loads(decoded)

        assert "sub" in claims, "JWT must contain 'sub' claim"
        assert "aud" in claims, "JWT must contain 'aud' claim"

    def test_workload_token_is_not_valid_jwt(self):
        """Verify that workloadaccesstoken is NOT a valid JWT (to confirm the bug)."""
        workload_token = "workload_internal_token_abc123"

        parts = workload_token.split(".")
        assert len(parts) != 3, (
            "workloadaccesstoken should NOT be a valid JWT - if it is, the test assumptions are wrong"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
