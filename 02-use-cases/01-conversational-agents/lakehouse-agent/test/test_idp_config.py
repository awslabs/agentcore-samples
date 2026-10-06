#!/usr/bin/env python3
"""
Unit test for the IDP_PROVIDER flag helper (utils/idp_config.py).

Runs fully offline — no AWS calls — using a small fake SSM client stub, so it
can be executed in CI or on a laptop without credentials:

    python test/test_idp_config.py

Covers:
  - validate: valid value (any case) -> normalized; missing/invalid -> fail fast
  - set_idp_provider: explicit value, .env value, and default-to-cognito
  - get_idp_provider: reads persisted value; fails fast when not yet set
"""

import os
import sys
from pathlib import Path

# Make the project root importable so `utils` resolves when run from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.idp_config import (
    DEFAULT_VALUE,
    FLAG_NAME,
    SSM_PARAM_NAME,
    get_idp_provider,
    set_idp_provider,
    validate_idp_provider,
)


class _ParameterNotFound(Exception):
    """Mimics boto3 ssm client's ParameterNotFound exception class."""


class FakeSSM:
    """Minimal in-memory stand-in for a boto3 SSM client (no AWS)."""

    def __init__(self):
        self._store = {}

        class _Exceptions:
            ParameterNotFound = _ParameterNotFound

        self.exceptions = _Exceptions()

    def put_parameter(self, Name, Value, Type="String", Overwrite=False):
        self._store[Name] = Value

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self._store:
            raise self.exceptions.ParameterNotFound(Name)
        return {"Parameter": {"Value": self._store[Name]}}


results = []


def check(name, condition, detail=""):
    status = "✅ PASS" if condition else "❌ FAIL"
    results.append((name, bool(condition), detail))
    print(f"{status}: {name}" + (f" — {detail}" if detail else ""))


def expect_value_error(name, fn):
    try:
        fn()
        check(name, False, "expected ValueError, none raised")
    except ValueError as e:
        check(name, True, f"raised ValueError: {e}")


def main():
    print("\n" + "=" * 70)
    print("IDP_PROVIDER FLAG HELPER — UNIT TEST")
    print("=" * 70 + "\n")

    # --- validate_idp_provider ---
    check("validate('cognito') returns 'cognito'", validate_idp_provider("cognito") == "cognito")
    check("validate('okta') returns 'okta'", validate_idp_provider("okta") == "okta")
    check("validate(' OKTA ') normalizes case/space", validate_idp_provider(" OKTA ") == "okta")
    expect_value_error("validate(None) fails fast", lambda: validate_idp_provider(None))
    expect_value_error("validate('') fails fast", lambda: validate_idp_provider(""))
    expect_value_error("validate('auth0') fails fast", lambda: validate_idp_provider("auth0"))

    # Fail-fast messages must name the flag and the allowed values (R1.3).
    try:
        validate_idp_provider("nope")
    except ValueError as e:
        msg = str(e)
        check("invalid-value message names the flag", FLAG_NAME in msg, msg)
        check("invalid-value message lists allowed values", "cognito" in msg and "okta" in msg)

    # --- set_idp_provider: explicit value ---
    ssm = FakeSSM()
    val = set_idp_provider(ssm, value="okta", verbose=False)
    check("set(explicit 'okta') returns 'okta'", val == "okta")
    check("set(explicit) persists to SSM", ssm._store.get(SSM_PARAM_NAME) == "okta")

    # --- set_idp_provider: from .env (environment variable) ---
    os.environ[FLAG_NAME] = "okta"
    ssm = FakeSSM()
    val = set_idp_provider(ssm, verbose=False)
    check("set(from env 'okta') returns 'okta'", val == "okta")

    # --- set_idp_provider: default to cognito when unset (R1.4) ---
    os.environ.pop(FLAG_NAME, None)
    ssm = FakeSSM()
    val = set_idp_provider(ssm, verbose=False)
    check("set(unset) defaults to cognito", val == DEFAULT_VALUE == "cognito")

    # --- set_idp_provider: invalid explicit value fails fast ---
    expect_value_error("set(invalid) fails fast", lambda: set_idp_provider(FakeSSM(), value="ldap", verbose=False))

    # --- get_idp_provider: reads back persisted value ---
    ssm = FakeSSM()
    set_idp_provider(ssm, value="okta", verbose=False)
    check("get() reads persisted 'okta'", get_idp_provider(ssm) == "okta")

    # --- get_idp_provider: fails fast when not set (notebook 01 not run) ---
    expect_value_error("get() fails fast when unset", lambda: get_idp_provider(FakeSSM()))

    # --- Summary ---
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"SUMMARY: {passed}/{total} passed")
    print("=" * 70 + "\n")

    if passed != total:
        print("❌ Some tests failed.")
        sys.exit(1)
    print("🎉 All flag-helper tests passed!")
    sys.exit(0)


if __name__ == "__main__":
    main()
