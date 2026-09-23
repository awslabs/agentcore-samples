"""Env-file plumbing for the pay-for-api tutorial.

Provides a small set of helpers the notebook + utility scripts use to
seed ``.env`` from ``env-sample.txt`` and write non-secret values
(``USER_ID``, role ARNs, manager IDs, etc.) into it.

User-supplied wallet-provider secrets (Coinbase / Privy keys, Privy
authorization private key) are pasted into ``.env`` by hand. The
notebook's §2 cell opens ``.env`` in the editor for the user and lists
the keys that still need values. The notebook's §4 then reads those
secrets once, passes them to ``CreatePaymentCredentialProvider``, and
AgentCore Identity stores them in AWS Secrets Manager under KMS
encryption and surfaces only the secret ARN to the agent. The local
``.env`` copy is no longer needed at runtime after that point and can
be cleared by hand. Nothing in this module ever logs, transmits, or
reads back secret material.

Entry points:

- ``python3 test/integration/setup_env.py`` — CLI; seeds ``.env`` and
  generates a fresh ``USER_ID`` if missing.
- ``from setup_env import seed_env, write_env_var`` — programmatic API.
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import uuid

# ── Path plumbing ─────────────────────────────────────────────────────
# The Python module lives at test/integration/setup_env.py; walk up two
# levels to land at the use-case root where env-sample.txt and .env live.
HERE = pathlib.Path(__file__).resolve().parent
USE_CASE_ROOT = HERE.parent.parent
TEMPLATE = USE_CASE_ROOT / "env-sample.txt"
ENV_FILE = USE_CASE_ROOT / ".env"

# Tokens that mean "this slot has not been filled yet" — treat like empty.
PLACEHOLDER_PREFIXES = ("<",)
PLACEHOLDER_SUBSTRINGS = ("<ACCOUNT_ID>",)


def _is_empty(value: str) -> bool:
    """True if the value is unset, blank, or a template placeholder."""
    if not value:
        return True
    if any(value.startswith(p) for p in PLACEHOLDER_PREFIXES):
        return True
    if any(s in value for s in PLACEHOLDER_SUBSTRINGS):
        return True
    return False


def _read_env_lines() -> list[str]:
    return ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []


def _current_value(key: str) -> str:
    for line in _read_env_lines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


def write_env_var(key: str, value: str) -> None:
    """Update or append KEY=VALUE in .env without touching other lines.

    Only intended for non-secret values written programmatically by the
    notebook (USER_ID, role ARNs, manager IDs, instrument IDs, session
    IDs, wallet addresses). Wallet-provider secrets (Coinbase / Privy
    keys, Privy authorization private key) are pasted into ``.env`` by
    the user manually and never flow through this function. Once §4 of
    the notebook calls ``CreatePaymentCredentialProvider``, those
    secrets are stored in AWS Secrets Manager under AgentCore Identity
    and only the credential-provider ARN remains in ``.env``.
    """
    lines = _read_env_lines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")


def seed_env() -> bool:
    """Create .env from env-sample.txt if it doesn't exist and ensure
    USER_ID is set to a unique UUID.

    Returns True if .env was created on this call, False if it was
    already there.
    """
    seeded = False
    if not ENV_FILE.exists():
        if not TEMPLATE.exists():
            raise FileNotFoundError(
                f"env-sample.txt not found at {TEMPLATE}. Run this from the use-case root with the template in place."
            )
        shutil.copy2(TEMPLATE, ENV_FILE)
        seeded = True

    # Auto-generate USER_ID on first run. The notebook uses USER_ID as
    # the operator identifier on CreatePaymentSession headers. A fixed
    # value across runs caused collisions in the service's vendor-user
    # mapping, so each fresh .env gets its own UUID.
    #
    # The `pay-for-api-` prefix marks this as a tutorial-scoped
    # identifier; production code should generate USER_IDs from your
    # own auth system rather than reusing this format.
    if _is_empty(_current_value("USER_ID")):
        write_env_var("USER_ID", f"pay-for-api-{uuid.uuid4()}")

    return seeded


def _cli() -> int:
    if seed_env():
        print(f"✅ Seeded {ENV_FILE} from env-sample.txt.")
    else:
        print(f"↷ Found existing {ENV_FILE} — left in place.")
    print()
    print(
        "Open .env in your editor and fill in any missing values "
        "(secrets are paste-only; non-secrets are written for you by "
        "later notebook cells)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
