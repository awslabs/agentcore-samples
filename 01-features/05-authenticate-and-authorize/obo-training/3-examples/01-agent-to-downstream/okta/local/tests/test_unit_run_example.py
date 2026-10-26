"""Unit tests for 02_run_example.py (Okta flavor) — AWS calls mocked."""

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
    client = MagicMock()

    client.get_workload_access_token_for_user_id.return_value = {"workloadAccessToken": "wl-token-for-user"}

    client.get_resource_oauth2_token.side_effect = [
        # 1st: USER_FEDERATION init — returns session URI + authorization URL
        {
            "sessionUri": "session-uri-xyz",
            "authorizationUrl": "https://integrator-x.okta.com/oauth2/default/authorize?...",
        },
        # 2nd: USER_FEDERATION after completion — returns user token
        {
            "accessToken": make_jwt(
                {
                    "aud": "api://default",
                    "sub": "alice@example.com",
                    "cid": "native-client-id",
                    "scp": ["openid"],
                }
            ),
        },
        # 3rd: OBO exchange — returns downstream token
        {
            "accessToken": make_jwt(
                {
                    "aud": "api://default",
                    "sub": "alice@example.com",
                    "cid": "service-client-id",
                    "scp": ["oboe2e.apiC.read"],
                }
            ),
        },
    ]

    client.complete_resource_token_auth.return_value = {}
    client.get_workload_access_token_for_jwt.return_value = {"workloadAccessToken": "obo-wl-token"}
    return client


class TestRunExample:
    def test_obo_call_has_okta_specific_parameters(self, required_env, mock_ac_identity, monkeypatch):
        """Okta OBO must include subject_token_type in customParameters and audience."""
        mod = _load_module()
        monkeypatch.setattr(mod.boto3, "client", lambda *_, **__: mock_ac_identity)

        fake_server = MagicMock()
        fake_future = MagicMock()
        fake_future.result.return_value = {"code": "c"}
        monkeypatch.setattr(
            mod,
            "start_callback_server",
            lambda port: (fake_server, fake_future),
        )
        monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)

        mod.main()

        obo_call = mock_ac_identity.get_resource_oauth2_token.call_args_list[2]
        assert obo_call.kwargs["oauth2Flow"] == "ON_BEHALF_OF_TOKEN_EXCHANGE"
        assert obo_call.kwargs["audiences"] == ["api://default"]
        assert obo_call.kwargs["customParameters"] == {
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        }

    def test_missing_env_exits(self, monkeypatch):
        mod = _load_module()
        for var in ["WORKLOAD_NAME", "OKTA_AUDIENCE", "DOWNSTREAM_SCOPE"]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit):
            mod.main()

    def test_cached_user_token_skips_3lo(self, required_env, monkeypatch):
        """If a cached token is returned, skip the browser flow."""
        mod = _load_module()

        client = MagicMock()
        client.get_workload_access_token_for_user_id.return_value = {"workloadAccessToken": "wl-token-for-user"}
        client.get_resource_oauth2_token.side_effect = [
            # Cached token branch on first USER_FEDERATION call
            {"accessToken": make_jwt({"sub": "alice@example.com", "cid": "native-client-id"})},
            # OBO
            {"accessToken": make_jwt({"sub": "alice@example.com", "cid": "service-client-id"})},
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
        monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)

        mod.main()

        assert client.get_resource_oauth2_token.call_count == 2
        client.complete_resource_token_auth.assert_not_called()
        fake_future.result.assert_not_called()
