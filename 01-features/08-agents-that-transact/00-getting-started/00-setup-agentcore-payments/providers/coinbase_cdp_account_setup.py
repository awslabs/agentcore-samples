"""Configure Coinbase CDP credentials for the AgentCore payments tutorials.

Coinbase requires the project owner to create the API key and Wallet Secret in
the CDP Portal. This helper opens the exact pages, consumes the two downloaded
files without printing their contents, configures the official CDP CLI, verifies
the credentials, and writes the values needed by AgentCore to the shared .env.

Usage:
    python providers/coinbase_cdp_account_setup.py --open-portal

    python providers/coinbase_cdp_account_setup.py \
        --api-key-file ~/Downloads/cdp_api_key.json \
        --wallet-secret-file ~/Downloads/cdp_wallet_secret.txt
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

PROVIDERS_DIR = Path(__file__).resolve().parent
TUTORIAL_DIR = PROVIDERS_DIR.parent
GETTING_STARTED_DIR = TUTORIAL_DIR.parent
ENV_FILE = GETTING_STARTED_DIR / ".env"

sys.path.append(str(GETTING_STARTED_DIR))
from utils import update_env_file

API_KEY_URL = "https://portal.cdp.coinbase.com/api-keys/secret"
WALLET_SECRET_URL = "https://portal.cdp.coinbase.com/wallets/non-custodial/security"
CDP_AUTH_DOCS_URL = "https://docs.cdp.coinbase.com/wallets/quickstart/api-key-auth"


class CredentialFileError(ValueError):
    """Raised when a downloaded Coinbase credential file cannot be parsed."""


def _first_string(
    data: dict[str, Any],
    keys: tuple[str, ...],
    *,
    preserve_whitespace: bool = False,
) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value if preserve_whitespace else value.strip()
    return None


def read_api_key_file(path: Path) -> tuple[str, str]:
    """Return the CDP key ID and secret from a portal-downloaded JSON file."""
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CredentialFileError(f"API key file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CredentialFileError(f"API key file is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise CredentialFileError("API key file must contain a JSON object.")

    key_id = _first_string(data, ("id", "keyId", "apiKeyId", "name"))
    key_secret = _first_string(
        data,
        ("secret", "keySecret", "apiKeySecret", "privateKey"),
        preserve_whitespace=True,
    )
    if not key_id or not key_secret:
        raise CredentialFileError(
            "Could not find the key ID and secret in the API key file. "
            "Download a current Secret API Key JSON file from the CDP Portal."
        )
    return key_id, key_secret


def read_wallet_secret_file(path: Path) -> str:
    """Return a Wallet Secret from a portal-downloaded text or JSON file."""
    try:
        raw = path.expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise CredentialFileError(f"Wallet Secret file not found: {path}") from exc

    if not raw:
        raise CredentialFileError("Wallet Secret file is empty.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    if isinstance(data, str) and data.strip():
        return data.strip()
    if isinstance(data, dict):
        wallet_secret = _first_string(data, ("walletSecret", "wallet_secret", "secret"))
        if wallet_secret:
            return wallet_secret
    raise CredentialFileError("Could not find a Wallet Secret in the downloaded file.")


def _dotenv_quote(value: str) -> str:
    """Encode a value for a double-quoted python-dotenv assignment."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def configure_and_verify_cdp_cli(api_key_file: Path, wallet_secret_file: Path) -> None:
    """Import the credential files into the official CDP CLI and verify access."""
    cdp = shutil.which("cdp")
    if not cdp:
        raise RuntimeError(
            "The Coinbase CDP CLI is not installed. Install Node.js 22+ and run "
            "`npm install -g @coinbase/cdp-cli`, then rerun this helper."
        )

    commands = [
        [cdp, "env", "live", "--key-file", str(api_key_file.expanduser().resolve())],
        [
            cdp,
            "env",
            "live",
            "--wallet-secret-file",
            str(wallet_secret_file.expanduser().resolve()),
        ],
        [cdp, "env"],
        [cdp, "evm", "accounts", "list"],
    ]
    for command in commands:
        subprocess.run(command, check=True)


def _prompt_for_file(label: str) -> Path:
    while True:
        path = Path(input(f"{label}: ").strip()).expanduser()
        if path.is_file():
            return path
        print(f"File not found: {path}")


def _open_portal_pages() -> None:
    print("Opening the two Coinbase CDP credential pages in your browser...")
    webbrowser.open(API_KEY_URL)
    webbrowser.open(WALLET_SECRET_URL)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import Coinbase CDP credential downloads for AgentCore payments.")
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="Path to the Secret API Key JSON downloaded from the CDP Portal.",
    )
    parser.add_argument(
        "--wallet-secret-file",
        type=Path,
        help="Path to the Wallet Secret file downloaded from the CDP Portal.",
    )
    parser.add_argument(
        "--open-portal",
        action="store_true",
        help="Open the official API Key and Wallet Secret pages before prompting.",
    )
    parser.add_argument(
        "--skip-cdp-verify",
        action="store_true",
        help="Write .env without importing the files into the official CDP CLI.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ENV_FILE,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    print("Coinbase CDP onboarding for AgentCore payments")
    print("Coinbase requires API key and Wallet Secret creation in the CDP Portal.")
    print("This helper never asks for your Coinbase password, MFA code, or raw secrets.")
    print(f"Official guide: {CDP_AUTH_DOCS_URL}\n")

    if args.open_portal:
        _open_portal_pages()
        print(
            "\nIn the portal, create and download a Secret API Key JSON file, "
            "then generate and download a Wallet Secret file."
        )
        print(
            "Turn on the project's Delegated signing toggle. Consent for the "
            "specific wallet is granted later through the WalletHub URL that "
            "AgentCore returns.\n"
        )

    api_key_file = args.api_key_file or _prompt_for_file("Secret API Key JSON path")
    wallet_secret_file = args.wallet_secret_file or _prompt_for_file("Wallet Secret file path")

    try:
        api_key_id, api_key_secret = read_api_key_file(api_key_file)
        wallet_secret = read_wallet_secret_file(wallet_secret_file)
    except CredentialFileError as exc:
        print(f"Credential import failed: {exc}", file=sys.stderr)
        return 1

    if not args.skip_cdp_verify:
        print("\nImporting credential files into the official CDP CLI...")
        try:
            configure_and_verify_cdp_cli(api_key_file, wallet_secret_file)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"CDP CLI verification failed: {exc}", file=sys.stderr)
            return 1
        print("CDP API key and Wallet Secret verified.")

    update_env_file(
        str(args.env_file),
        {
            "CREDENTIAL_PROVIDER_TYPE": "CoinbaseCDP",
            "COINBASE_API_KEY_ID": _dotenv_quote(api_key_id),
            "COINBASE_API_KEY_SECRET": _dotenv_quote(api_key_secret),
            "COINBASE_WALLET_SECRET": _dotenv_quote(wallet_secret),
        },
    )
    os.chmod(args.env_file, 0o600)

    print(f"\nAgentCore credentials saved to {args.env_file.resolve()}.")
    print("Secret values were not printed. Do not commit the .env file.")
    print("\nNext:")
    print(f"  cd {TUTORIAL_DIR}")
    print("  python setup_agentcore_payments.py")
    print("\nThe created wallet needs free testnet USDC before a paid x402 test.")
    print("No real money is required. Fund Base Sepolia at https://faucet.circle.com/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
