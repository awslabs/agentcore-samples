"""Unit tests for 01_create_providers.py (Okta flavor) — boto3 calls mocked."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError


def _load_module():
    path = Path(__file__).resolve().parent.parent / "01_create_providers.py"
    spec = importlib.util.spec_from_file_location("create_providers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _conflict_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConflictException", "Message": "exists"}},
        "CreateOauth2CredentialProvider",
    )


class TestEnsureClientProvider:
    def test_uses_custom_oauth2_without_obo_config(self):
        mod = _load_module()
        client = MagicMock()
        client.create_oauth2_credential_provider.return_value = {}

        mod.ensure_okta_client_provider(
            client,
            name="p",
            domain="foo.okta.com",
            auth_server_id="default",
            client_id="cid",
            client_secret="sec",
        )

        args = client.create_oauth2_credential_provider.call_args.kwargs
        assert args["credentialProviderVendor"] == "CustomOauth2"
        cfg = args["oauth2ProviderConfigInput"]["customOauth2ProviderConfig"]
        assert cfg["clientAuthenticationMethod"] == "CLIENT_SECRET_BASIC"
        assert "onBehalfOfTokenExchangeConfig" not in cfg
        assert cfg["oauthDiscovery"]["discoveryUrl"] == (
            "https://foo.okta.com/oauth2/default/.well-known/openid-configuration"
        )

    def test_fetches_existing_on_conflict(self):
        mod = _load_module()
        client = MagicMock()
        client.create_oauth2_credential_provider.side_effect = _conflict_error()
        client.get_oauth2_credential_provider.return_value = {"name": "p"}

        result = mod.ensure_okta_client_provider(
            client,
            name="p",
            domain="x",
            auth_server_id="default",
            client_id="c",
            client_secret="s",
        )

        assert result == {"name": "p"}


class TestEnsureActorProvider:
    def test_uses_token_exchange_grant_with_actor_none(self):
        mod = _load_module()
        client = MagicMock()
        client.create_oauth2_credential_provider.return_value = {}

        mod.ensure_okta_actor_provider(
            client,
            name="p",
            domain="foo.okta.com",
            auth_server_id="default",
            client_id="cid",
            client_secret="sec",
        )

        cfg = client.create_oauth2_credential_provider.call_args.kwargs["oauth2ProviderConfigInput"][
            "customOauth2ProviderConfig"
        ]
        obo = cfg["onBehalfOfTokenExchangeConfig"]
        assert obo["grantType"] == "TOKEN_EXCHANGE"
        assert obo["tokenExchangeGrantTypeConfig"]["actorTokenContent"] == "NONE"


class TestMain:
    def test_missing_env_exits(self, monkeypatch):
        mod = _load_module()
        for var in ["OKTA_DOMAIN", "NATIVE_APP_CLIENT_ID", "SERVICE_APP_CLIENT_ID"]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit):
            mod.main()

    def test_happy_path_creates_all_resources(self, required_env, monkeypatch):
        mod = _load_module()

        fake_control = MagicMock()
        fake_control.create_workload_identity.return_value = {"name": "w"}
        fake_control.create_oauth2_credential_provider.return_value = {}

        monkeypatch.setattr(mod.boto3, "client", lambda *_, **__: fake_control)

        mod.main()

        fake_control.create_workload_identity.assert_called_once()
        assert fake_control.create_oauth2_credential_provider.call_count == 2
