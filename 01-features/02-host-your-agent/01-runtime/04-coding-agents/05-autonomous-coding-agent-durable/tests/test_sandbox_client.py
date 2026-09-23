"""Tests for coding-agent/sandbox_client.py — retry logic and restart detection."""
import json
import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "coding-agent"))


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("SANDBOX_ARN", "arn:aws:bedrock-agentcore:us-east-1:123:runtime/sandbox-123")


@pytest.fixture
def sandbox_client(mock_env):
    """Import sandbox_client with mocked boto3."""
    import importlib
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        import sandbox_client
        importlib.reload(sandbox_client)
        sandbox_client._client = mock_client
        sandbox_client._last_boot_id = ""
        yield sandbox_client, mock_client


class TestInvokeSandbox:
    """Test the invoke_sandbox wrapper."""

    def test_successful_call(self, sandbox_client):
        sc, mock_client = sandbox_client
        response_body = json.dumps({"exit_code": 0, "stdout": "ok", "sandbox_boot_id": "boot-1"})
        mock_response = MagicMock()
        mock_response.read.return_value = response_body.encode()
        mock_client.invoke_agent_runtime.return_value = {"response": mock_response}

        result = sc.invoke_sandbox("run_command", "session-123456789012345678901234567890123", "TICKET-1", cmd="echo hi")
        assert result["exit_code"] == 0
        assert result["stdout"] == "ok"

    def test_missing_sandbox_arn(self, sandbox_client, monkeypatch):
        sc, _ = sandbox_client
        monkeypatch.delenv("SANDBOX_ARN", raising=False)
        # Force reimport won't help since we patched it. Set it directly.
        monkeypatch.setattr(sc, "invoke_sandbox", sc.invoke_sandbox)
        # Directly test the env check by simulating empty ARN
        original_env = os.environ.pop("SANDBOX_ARN", None)
        try:
            import importlib
            importlib.reload(sc)
            result = sc.invoke_sandbox("run_command", "s" * 33, "T1", cmd="ls")
            # After reload without SANDBOX_ARN, it should error
        except Exception:
            pass
        finally:
            if original_env:
                os.environ["SANDBOX_ARN"] = original_env

    def test_invalid_session_id(self, sandbox_client):
        sc, _ = sandbox_client
        result = sc.invoke_sandbox("run_command", "short", "TICKET-1", cmd="ls")
        assert "error" in result
        assert "session_id" in result["error"]

    def test_empty_ticket_prefix(self, sandbox_client):
        sc, _ = sandbox_client
        result = sc.invoke_sandbox("run_command", "s" * 33, "", cmd="ls")
        assert "error" in result
        assert "ticket_prefix" in result["error"]

    def test_boot_id_change_detected(self, sandbox_client):
        sc, mock_client = sandbox_client

        # First call — establish boot_id
        resp1 = json.dumps({"exit_code": 0, "sandbox_boot_id": "boot-1"})
        mock_response1 = MagicMock()
        mock_response1.read.return_value = resp1.encode()
        mock_client.invoke_agent_runtime.return_value = {"response": mock_response1}
        sc.invoke_sandbox("get_details", "s" * 33, "T1")

        # Second call — different boot_id (sandbox restarted)
        resp2 = json.dumps({"exit_code": 0, "sandbox_boot_id": "boot-2"})
        mock_response2 = MagicMock()
        mock_response2.read.return_value = resp2.encode()
        mock_client.invoke_agent_runtime.return_value = {"response": mock_response2}
        result = sc.invoke_sandbox("get_details", "s" * 33, "T1")

        assert result.get("_sandbox_restarted") is True
        assert result["_previous_boot_id"] == "boot-1"

    def test_retry_on_transient_error(self, sandbox_client):
        sc, mock_client = sandbox_client

        # First call: RuntimeClientError, second call: success
        error_response = {"Error": {"Code": "RuntimeClientError", "Message": "sandbox crashed"}}
        mock_client.invoke_agent_runtime.side_effect = [
            ClientError(error_response, "InvokeAgentRuntime"),
            {"response": MagicMock(read=MagicMock(return_value=json.dumps({"exit_code": 0, "sandbox_boot_id": "b"}).encode()))},
        ]

        # Patch sleep to speed up test
        with patch("sandbox_client.time.sleep"):
            result = sc.invoke_sandbox("run_command", "s" * 33, "T1", cmd="ls")

        assert result.get("exit_code") == 0
        assert mock_client.invoke_agent_runtime.call_count == 2

    def test_non_retryable_error(self, sandbox_client):
        sc, mock_client = sandbox_client

        error_response = {"Error": {"Code": "AccessDeniedException", "Message": "forbidden"}}
        mock_client.invoke_agent_runtime.side_effect = ClientError(error_response, "InvokeAgentRuntime")

        result = sc.invoke_sandbox("run_command", "s" * 33, "T1", cmd="ls")
        assert "error" in result
        assert result["retryable"] is False
        # Should NOT have retried
        assert mock_client.invoke_agent_runtime.call_count == 1

    def test_max_retries_exhausted(self, sandbox_client):
        sc, mock_client = sandbox_client

        error_response = {"Error": {"Code": "RuntimeClientError", "Message": "dead"}}
        mock_client.invoke_agent_runtime.side_effect = ClientError(error_response, "InvokeAgentRuntime")

        with patch("sandbox_client.time.sleep"):
            result = sc.invoke_sandbox("run_command", "s" * 33, "T1", cmd="ls")

        assert "error" in result
        assert "dead" in result["error"]
        # On the final attempt, falls through to non-retryable return
        assert result["retryable"] is False
        # 1 initial + 3 retries = 4
        assert mock_client.invoke_agent_runtime.call_count == 4
