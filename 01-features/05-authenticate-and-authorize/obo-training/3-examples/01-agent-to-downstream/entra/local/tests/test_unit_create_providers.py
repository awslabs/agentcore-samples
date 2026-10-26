"""Unit tests for 01_create_providers.py — all boto3 calls mocked."""

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


class TestEnsureWorkloadIdentity:
    def test_creates_when_missing(self):
        mod = _load_module()
        client = MagicMock()
        client.create_workload_identity.return_value = {"name": "w"}

        result = mod.ensure_workload_identity(client, "w")

        assert result == {"name": "w"}
        client.create_workload_identity.assert_called_once_with(name="w")
        client.get_workload_identity.assert_not_called()

    def test_fetches_when_conflict(self):
        mod = _load_module()
        client = MagicMock()
        client.create_workload_identity.side_effect = _conflict_error()
        client.get_workload_identity.return_value = {"name": "w", "existing": True}

        result = mod.ensure_workload_identity(client, "w")

        assert result["existing"] is True
        client.get_workload_identity.assert_called_once_with(name="w")

    def test_propagates_unexpected_errors(self):
        mod = _load_module()
        client = MagicMock()
        client.create_workload_identity.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "CreateWorkloadIdentity"
        )

        with pytest.raises(ClientError):
            mod.ensure_workload_identity(client, "w")


class TestEnsureMicrosoftProvider:
    def test_creates_with_expected_config(self):
        mod = _load_module()
        client = MagicMock()
        client.create_oauth2_credential_provider.return_value = {}

        mod.ensure_microsoft_provider(
            client,
            name="p",
            client_id="cid",
            client_secret="sec",
            tenant_id="tid",
        )

        args = client.create_oauth2_credential_provider.call_args.kwargs
        assert args["name"] == "p"
        assert args["credentialProviderVendor"] == "MicrosoftOauth2"
        cfg = args["oauth2ProviderConfigInput"]["microsoftOauth2ProviderConfig"]
        assert cfg == {
            "clientId": "cid",
            "clientSecret": "sec",
            "tenantId": "tid",
        }

    def test_returns_existing_on_conflict(self):
        mod = _load_module()
        client = MagicMock()
        client.create_oauth2_credential_provider.side_effect = _conflict_error()
        client.get_oauth2_credential_provider.return_value = {"name": "p"}

        result = mod.ensure_microsoft_provider(client, name="p", client_id="cid", client_secret="sec", tenant_id="tid")

        assert result == {"name": "p"}


class TestMain:
    def test_missing_env_exits(self, monkeypatch, capsys):
        mod = _load_module()
        # Clear all required vars
        for var in [
            "TENANT_ID",
            "AGENT_CLIENT_ID",
            "AGENT_CLIENT_SECRET",
            "WORKLOAD_NAME",
            "CLIENT_PROVIDER_NAME",
            "ACTOR_PROVIDER_NAME",
        ]:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit):
            mod.main()

    def test_happy_path(self, required_env, monkeypatch):
        mod = _load_module()

        fake_control = MagicMock()
        fake_control.create_workload_identity.return_value = {"name": "w"}
        fake_control.create_oauth2_credential_provider.return_value = {}

        monkeypatch.setattr(mod.boto3, "client", lambda *_, **__: fake_control)

        mod.main()

        # Created exactly one workload identity and two credential providers
        fake_control.create_workload_identity.assert_called_once()
        assert fake_control.create_oauth2_credential_provider.call_count == 2
