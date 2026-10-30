#!/usr/bin/env python3
"""
Unit test for the IDP_PROVIDER flag helper (utils/idp_config.py).

Runs fully offline — no AWS calls — using a small fake SSM client stub, so it
can be executed in CI or on a laptop without credentials:

    python test/test_idp_config.py

Covers:
  - validate_idp_provider: valid value (any case/whitespace) -> normalized
    lower-case; None / empty / unknown value -> ValueError naming the flag and
    the allowed values
  - set_idp_provider: explicit value wins; else the IDP_PROVIDER environment
    variable; else the "cognito" default; result is written to SSM at
    /app/lakehouse-agent/idp-provider
  - get_idp_provider: reads the persisted value back; ValueError when the SSM
    parameter does not exist yet (notebook 01 not run)

Exit code 0 when every check passes, 1 otherwise.
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
    """Minimal in-memory stand-in for a boto3 SSM client (no AWS).

    Implements only the two calls idp_config makes: put_parameter and
    get_parameter, plus the `exceptions.ParameterNotFound` attribute that
    get_idp_provider catches.
    """

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
    """Record one pass/fail result and print it."""
    status = "✅ PASS" if condition else "❌ FAIL"
    results.append((name, bool(condition), detail))
    print(f"{status}: {name}" + (f" — {detail}" if detail else ""))


def expect_value_error(name, fn):
    """Pass only if calling fn() raises ValueError."""
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
    # Both allowed values pass through unchanged.
    check("validate('cognito') returns 'cognito'", validate_idp_provider("cognito") == "cognito")
    check("validate('okta') returns 'okta'", validate_idp_provider("okta") == "okta")
    # Surrounding whitespace is stripped and the value is lower-cased.
    check("validate(' OKTA ') normalizes case/space", validate_idp_provider(" OKTA ") == "okta")
    # None and empty string are "not set"; an unknown IdP name is "invalid". All three raise.
    expect_value_error("validate(None) fails fast", lambda: validate_idp_provider(None))
    expect_value_error("validate('') fails fast", lambda: validate_idp_provider(""))
    expect_value_error("validate('auth0') fails fast", lambda: validate_idp_provider("auth0"))

    # Fail-fast messages must name the flag and the allowed values.
    try:
        validate_idp_provider("nope")
    except ValueError as e:
        msg = str(e)
        check("invalid-value message names the flag", FLAG_NAME in msg, msg)
        check("invalid-value message lists allowed values", "cognito" in msg and "okta" in msg)

    # --- set_idp_provider: explicit value ---
    # An explicit value= is returned normalized and written to the SSM parameter.
    ssm = FakeSSM()
    val = set_idp_provider(ssm, value="okta", verbose=False)
    check("set(explicit 'okta') returns 'okta'", val == "okta")
    check("set(explicit) persists to SSM", ssm._store.get(SSM_PARAM_NAME) == "okta")

    # --- set_idp_provider: from .env (environment variable) ---
    # With no value=, the IDP_PROVIDER environment variable is used.
    os.environ[FLAG_NAME] = "okta"
    ssm = FakeSSM()
    val = set_idp_provider(ssm, verbose=False)
    check("set(from env 'okta') returns 'okta'", val == "okta")

    # --- set_idp_provider: default to cognito when unset ---
    # With neither value= nor the environment variable, the default "cognito" is used.
    os.environ.pop(FLAG_NAME, None)
    ssm = FakeSSM()
    val = set_idp_provider(ssm, verbose=False)
    check("set(unset) defaults to cognito", val == DEFAULT_VALUE == "cognito")

    # --- set_idp_provider: invalid explicit value fails fast ---
    # Validation runs before the SSM write, so nothing is persisted for a bad value.
    expect_value_error("set(invalid) fails fast", lambda: set_idp_provider(FakeSSM(), value="ldap", verbose=False))

    # --- get_idp_provider: reads back persisted value ---
    # A set followed by a get on the same store round-trips the value.
    ssm = FakeSSM()
    set_idp_provider(ssm, value="okta", verbose=False)
    check("get() reads persisted 'okta'", get_idp_provider(ssm) == "okta")

    # --- get_idp_provider: fails fast when not set (notebook 01 not run) ---
    # ParameterNotFound from SSM is re-raised as ValueError telling the reader to run notebook 01.
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
