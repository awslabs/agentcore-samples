import importlib.util
import json
from pathlib import Path

import pytest
from dotenv import dotenv_values

MODULE_PATH = Path(__file__).resolve().parents[1] / "coinbase_cdp_account_setup.py"
SPEC = importlib.util.spec_from_file_location("coinbase_cdp_account_setup", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_reads_current_portal_api_key_shape(tmp_path):
    key_file = tmp_path / "cdp_api_key.json"
    key_file.write_text(
        '{"name":"organizations/org/apiKeys/key","privateKey":"private-value"}',
        encoding="utf-8",
    )

    assert MODULE.read_api_key_file(key_file) == (
        "organizations/org/apiKeys/key",
        "private-value",
    )


def test_reads_explicit_api_key_shape(tmp_path):
    key_file = tmp_path / "cdp_api_key.json"
    key_file.write_text(
        '{"apiKeyId":"key-id","apiKeySecret":"key-secret"}',
        encoding="utf-8",
    )

    assert MODULE.read_api_key_file(key_file) == ("key-id", "key-secret")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("wallet-secret", "wallet-secret"),
        ('{"walletSecret":"wallet-secret"}', "wallet-secret"),
        ('"wallet-secret"', "wallet-secret"),
    ],
)
def test_reads_wallet_secret_file(tmp_path, content, expected):
    secret_file = tmp_path / "cdp_wallet_secret.txt"
    secret_file.write_text(content, encoding="utf-8")

    assert MODULE.read_wallet_secret_file(secret_file) == expected


def test_rejects_api_key_file_without_both_values(tmp_path):
    key_file = tmp_path / "cdp_api_key.json"
    key_file.write_text('{"name":"key-id"}', encoding="utf-8")

    with pytest.raises(MODULE.CredentialFileError, match="key ID and secret"):
        MODULE.read_api_key_file(key_file)


def test_cdp_cli_receives_file_paths_not_secret_values(tmp_path, monkeypatch):
    key_file = tmp_path / "cdp_api_key.json"
    wallet_file = tmp_path / "cdp_wallet_secret.txt"
    calls = []

    monkeypatch.setattr(MODULE.shutil, "which", lambda command: "/usr/local/bin/cdp")
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )

    MODULE.configure_and_verify_cdp_cli(key_file, wallet_file)

    assert calls == [
        (
            [
                "/usr/local/bin/cdp",
                "env",
                "live",
                "--key-file",
                str(key_file.resolve()),
            ],
            True,
        ),
        (
            [
                "/usr/local/bin/cdp",
                "env",
                "live",
                "--wallet-secret-file",
                str(wallet_file.resolve()),
            ],
            True,
        ),
        (["/usr/local/bin/cdp", "env"], True),
        (["/usr/local/bin/cdp", "evm", "accounts", "list"], True),
    ]


def test_main_round_trips_multiline_private_key_through_dotenv(tmp_path):
    key_file = tmp_path / "cdp_api_key.json"
    wallet_file = tmp_path / "cdp_wallet_secret.txt"
    env_file = tmp_path / ".env"
    private_key = '-----BEGIN EC PRIVATE KEY-----\nline\\"two\n-----END EC PRIVATE KEY-----\n'
    key_file.write_text(
        json.dumps(
            {
                "name": "organizations/org/apiKeys/key",
                "privateKey": private_key,
            }
        ),
        encoding="utf-8",
    )
    wallet_file.write_text("wallet-secret", encoding="utf-8")

    result = MODULE.main(
        [
            "--api-key-file",
            str(key_file),
            "--wallet-secret-file",
            str(wallet_file),
            "--skip-cdp-verify",
            "--env-file",
            str(env_file),
        ]
    )

    values = dotenv_values(env_file)
    assert result == 0
    assert values["COINBASE_API_KEY_ID"] == "organizations/org/apiKeys/key"
    assert values["COINBASE_API_KEY_SECRET"] == private_key
    assert values["COINBASE_WALLET_SECRET"] == "wallet-secret"
    assert env_file.stat().st_mode & 0o777 == 0o600
