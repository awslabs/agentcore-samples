# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Customer Profile Agent main.py entry point.

Note: These tests require the bedrock_agentcore SDK which is only available
in the AgentCore container runtime environment. Tests are skipped when the SDK
is not installed.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add agents directory to path for imports
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "customer_profile"),
)

# Check if bedrock_agentcore SDK is available
try:
    import bedrock_agentcore.runtime

    HAS_AGENTCORE_SDK = True
except ImportError:
    HAS_AGENTCORE_SDK = False

pytestmark = pytest.mark.skipif(not HAS_AGENTCORE_SDK, reason="bedrock_agentcore SDK not available (container-only)")


class TestInvokeEntrypoint:
    """Tests for the invoke entrypoint function."""

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_forwarded_claims")
    async def test_successful_invocation(self, mock_validate, mock_create_agent):
        """Test successful invocation with valid claims."""
        # Import after patching
        from main import invoke

        mock_validate.return_value = {
            "valid": True,
            "user_id": "user-123",
            "customer_id": "CUST001",
        }

        mock_agent = MagicMock()
        mock_agent.process_query.return_value = {
            "status": "success",
            "response": "Profile data",
        }
        mock_create_agent.return_value = mock_agent

        payload = {
            "prompt": "show my profile",
            "claims": {
                "sub": "user-123",
                "aud": "https://api",
                "exp": 9999999999,
                "iss": "https://issuer",
                "https://agentcore.example.com/customer_id": "CUST001",
            },
        }

        result = await invoke(payload, None)

        assert result["status"] == "success"
        assert "response" in result
        assert "trace_id" in result

    @pytest.mark.asyncio
    @patch("main.validate_forwarded_claims")
    async def test_authorization_failed(self, mock_validate):
        """Test invocation fails with invalid claims."""
        from main import invoke

        mock_validate.return_value = {
            "valid": False,
            "error": "Missing required claims",
        }

        payload = {"prompt": "show my profile", "claims": {}}

        result = await invoke(payload, None)

        assert result["status"] == "error"
        assert result["error"] == "AUTHORIZATION_FAILED"

    @pytest.mark.asyncio
    async def test_empty_payload(self):
        """Test invocation with empty payload."""
        from main import invoke

        result = await invoke({}, None)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_none_payload(self):
        """Test invocation with None payload."""
        from main import invoke

        result = await invoke(None, None)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_forwarded_claims")
    async def test_agent_exception_handling(self, mock_validate, mock_create_agent):
        """Test that agent exceptions are caught and returned as errors."""
        from main import invoke

        mock_validate.return_value = {
            "valid": True,
            "user_id": "user-123",
            "customer_id": "CUST001",
        }

        mock_agent = MagicMock()
        mock_agent.process_query.side_effect = Exception("Agent error")
        mock_create_agent.return_value = mock_agent

        payload = {
            "prompt": "show my profile",
            "claims": {
                "sub": "user-123",
                "aud": "https://api",
                "exp": 9999999999,
                "iss": "https://issuer",
                "https://agentcore.example.com/customer_id": "CUST001",
            },
        }

        result = await invoke(payload, None)

        assert result["status"] == "error"
        assert result["error"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_forwarded_claims")
    async def test_extracts_query_from_prompt(self, mock_validate, mock_create_agent):
        """Test that query is extracted from 'prompt' field."""
        from main import invoke

        mock_validate.return_value = {
            "valid": True,
            "user_id": "user-123",
            "customer_id": "CUST001",
        }

        mock_agent = MagicMock()
        mock_agent.process_query.return_value = {
            "status": "success",
            "response": "data",
        }
        mock_create_agent.return_value = mock_agent

        payload = {
            "prompt": "test query from prompt field",
            "claims": {
                "sub": "user-123",
                "aud": "https://api",
                "exp": 9999999999,
                "iss": "https://issuer",
                "https://agentcore.example.com/customer_id": "CUST001",
            },
        }

        await invoke(payload, None)

        # Verify the query was passed to agent
        mock_agent.process_query.assert_called_once()
        call_args = mock_agent.process_query.call_args[0]
        assert "test query from prompt field" in call_args[0]

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_forwarded_claims")
    async def test_extracts_query_from_query_field(self, mock_validate, mock_create_agent):
        """Test that query is extracted from 'query' field as fallback."""
        from main import invoke

        mock_validate.return_value = {
            "valid": True,
            "user_id": "user-123",
            "customer_id": "CUST001",
        }

        mock_agent = MagicMock()
        mock_agent.process_query.return_value = {
            "status": "success",
            "response": "data",
        }
        mock_create_agent.return_value = mock_agent

        payload = {
            "query": "test query from query field",
            "claims": {
                "sub": "user-123",
                "aud": "https://api",
                "exp": 9999999999,
                "iss": "https://issuer",
                "https://agentcore.example.com/customer_id": "CUST001",
            },
        }

        await invoke(payload, None)

        mock_agent.process_query.assert_called_once()

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_forwarded_claims")
    async def test_response_includes_session_id(self, mock_validate, mock_create_agent):
        """Test that response includes session_id."""
        from main import invoke

        mock_validate.return_value = {
            "valid": True,
            "user_id": "user-123",
            "customer_id": "CUST001",
        }

        mock_agent = MagicMock()
        mock_agent.process_query.return_value = {
            "status": "success",
            "response": "data",
        }
        mock_create_agent.return_value = mock_agent

        payload = {
            "prompt": "test",
            "claims": {
                "sub": "user-123",
                "aud": "https://api",
                "exp": 9999999999,
                "iss": "https://issuer",
                "https://agentcore.example.com/customer_id": "CUST001",
            },
        }

        result = await invoke(payload, None)

        assert "session_id" in result
        assert "trace_id" in result
        assert "duration_ms" in result
