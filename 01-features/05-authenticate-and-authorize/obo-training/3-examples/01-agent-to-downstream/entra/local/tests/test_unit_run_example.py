"""Unit tests for 02_run_example.py — AWS + Graph calls mocked."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import make_jwt


def _load_module():
    path = Path(__file__).resolve().parent.parent / "02_run_example.py"
    spec = importlib.util.spec_from_file_location("run_example", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mock_ac_identity():
    """Build a mocked AgentCore Identity client with realistic responses."""
    client = MagicMock()

    client.get_workload_access_token_for_user_id.return_value = {"workloadAccessToken": "wl-token-for-user"}

    client.get_resource_oauth2_token.side_effect = [
        # 1st call: USER_FEDERATION init — returns session/authorizationUrl
        {
            "sessionUri": "session-uri-xyz",
            "authorizationUrl": "https://login.microsoftonline.com/authorize?...",
        },
        # 2nd call: USER_FEDERATION after completion — returns user token
        {
            "accessToken": make_jwt(
                {
                    "aud": "agent-client-id",
                    "oid": "alice-oid",
                    "scp": "access_as_user",
                }
            ),
        },
        # 3rd call: OBO exchange — returns Graph token
        {
            "accessToken": make_jwt(
                {
                    "aud": "00000003-0000-0000-c000-000000000000",
                    "oid": "alice-oid",
                    "scp": "User.Read",
                    "appid": "agent-client-id",
                }
            ),
        },
    ]

    client.complete_resource_token_auth.return_value = {}
    client.get_workload_access_token_for_jwt.return_value = {"workloadAccessToken": "obo-wl-token"}
    return client


class TestRunExample:
    def test_happy_path_makes_expected_api_calls(self, required_env, mock_ac_identity, monkeypatch):
        mod = _load_module()

        # Mock boto3.client to return our fake AgentCore Identity client
        monkeypatch.setattr(mod.boto3, "client", lambda *_, **__: mock_ac_identity)

        # Mock the callback server
        fake_server = MagicMock()
        fake_future = MagicMock()
        fake_future.result.return_value = {"code": "auth-code-xyz"}
        monkeypatch.setattr(
            mod,
            "start_callback_server",
            lambda port: (fake_server, fake_future),
        )

        # Don't actually open a browser
        monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)

        # Mock the Graph request
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "displayName": "Alice",
            "mail": "alice@example.com",
        }
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(mod.requests, "get", lambda *_, **__: fake_response)

        mod.main()

        # Assert AgentCore Identity calls were made in the right order
        mock_ac_identity.get_workload_access_token_for_user_id.assert_called_once()
        assert mock_ac_identity.get_resource_oauth2_token.call_count == 3
        mock_ac_identity.complete_resource_token_auth.assert_called_once()
        mock_ac_identity.get_workload_access_token_for_jwt.assert_called_once()

        # The OBO call specifically — verify oauth2Flow
        obo_call = mock_ac_identity.get_resource_oauth2_token.call_args_list[2]
        assert obo_call.kwargs["oauth2Flow"] == "ON_BEHALF_OF_TOKEN_EXCHANGE"

    def test_missing_env_exits(self, monkeypatch):
        mod = _load_module()
        for var in ["WORKLOAD_NAME", "AGENT_SCOPE", "GRAPH_SCOPE"]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit):
            mod.main()

    def test_cached_user_token_skips_3lo(self, required_env, monkeypatch):
        """If AgentCore has a cached user token, the first call returns accessToken
        directly and we should skip the browser/callback dance."""
        mod = _load_module()

        # Override the fixture: first call returns a cached token, no sessionUri.
        client = MagicMock()
        client.get_workload_access_token_for_user_id.return_value = {"workloadAccessToken": "wl-token-for-user"}
        client.get_resource_oauth2_token.side_effect = [
            # First USER_FEDERATION call — cached token branch
            {"accessToken": make_jwt({"aud": "agent-client-id", "oid": "alice-oid"})},
            # OBO exchange
            {
                "accessToken": make_jwt(
                    {
                        "aud": "00000003-0000-0000-c000-000000000000",
                        "oid": "alice-oid",
                        "appid": "agent-client-id",
                    }
                )
            },
        ]
        client.get_workload_access_token_for_jwt.return_value = {"workloadAccessToken": "obo-wl-token"}

        monkeypatch.setattr(mod.boto3, "client", lambda *_, **__: client)

        fake_server = MagicMock()
        fake_future = MagicMock()
        monkeypatch.setattr(
            mod,
            "start_callback_server",
            lambda port: (fake_server, fake_future),
        )
        # If the cached-token branch works correctly, webbrowser.open and
        # code_future.result should NOT be called.
        monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)

        fake_response = MagicMock()
        fake_response.json.return_value = {"displayName": "Alice"}
        fake_response.raise_for_status.return_value = None
        monkeypatch.setattr(mod.requests, "get", lambda *_, **__: fake_response)

        mod.main()

        # Only 2 get_resource_oauth2_token calls (not 3) because 3LO was skipped
        assert client.get_resource_oauth2_token.call_count == 2
        client.complete_resource_token_auth.assert_not_called()
        fake_future.result.assert_not_called()
