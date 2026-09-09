#!/usr/bin/env python3
"""
Unit test for the DR-11 pre-flight IdP-mismatch guard (utils/idp_config.py).

Runs fully offline — no AWS calls — using small dict fixtures that mimic a
bedrock-agentcore get_gateway/list_gateways item, so it can run in CI or on a
laptop without credentials:

    python test/test_detect_gateway_idp.py

Covers:
  - detect_gateway_idp: Cognito authorizer -> "cognito"; Okta -> "okta"
  - detect_gateway_idp: missing / ambiguous authorizer -> ValueError
  - assert_gateway_idp_matches: match -> no error; mismatch -> RuntimeError
"""

import sys
from pathlib import Path

# Make the project root importable so `utils` resolves when run from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.idp_config import (
    assert_gateway_idp_matches,
    detect_gateway_idp,
)

# --- Fixtures: minimal live-gateway shapes (get_gateway response subset) ---
COGNITO_GW = {
    "name": "lakehouse-gateway",
    "authorizerConfiguration": {
        "customJWTAuthorizer": {
            "discoveryUrl": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC123/.well-known/openid-configuration",
            "allowedClients": ["1example23clientid"],
        }
    },
}

OKTA_GW = {
    "name": "lakehouse-notes-gateway",
    "authorizerConfiguration": {
        "customJWTAuthorizer": {
            "discoveryUrl": "https://dev-12345.okta.com/oauth2/aus.../.well-known/openid-configuration",
            "allowedAudience": ["api://lakehouse-api"],
        }
    },
}

NO_AUTHZ_GW = {"name": "broken-gateway"}

AMBIGUOUS_GW = {
    "name": "ambiguous-gateway",
    "authorizerConfiguration": {
        # No discoveryUrl and both signals set -> undecidable by design.
        "customJWTAuthorizer": {
            "allowedClients": ["x"],
            "allowedAudience": ["y"],
        }
    },
}


results = []


def check(name, condition, detail=""):
    status = "✅ PASS" if condition else "❌ FAIL"
    results.append((name, bool(condition), detail))
    print(f"{status}: {name}" + (f" — {detail}" if detail else ""))


def expect_error(name, fn, exc):
    try:
        fn()
        check(name, False, f"expected {exc.__name__}, none raised")
    except exc as e:
        check(name, True, f"raised {exc.__name__}: {e}")
    except Exception as e:
        check(name, False, f"raised {type(e).__name__}, expected {exc.__name__}: {e}")


def main():
    print("\n" + "=" * 70)
    print("DR-11 PRE-FLIGHT IdP-MISMATCH GUARD — UNIT TEST")
    print("=" * 70 + "\n")

    # --- detect_gateway_idp: happy paths ---
    check("detect(Cognito GW) -> 'cognito'", detect_gateway_idp(COGNITO_GW) == "cognito")
    check("detect(Okta GW) -> 'okta'", detect_gateway_idp(OKTA_GW) == "okta")

    # --- detect_gateway_idp: error paths ---
    expect_error("detect(no authorizer) -> ValueError", lambda: detect_gateway_idp(NO_AUTHZ_GW), ValueError)
    expect_error("detect(ambiguous) -> ValueError", lambda: detect_gateway_idp(AMBIGUOUS_GW), ValueError)

    # Error message should name the offending signals (operator-actionable).
    try:
        detect_gateway_idp(AMBIGUOUS_GW)
    except ValueError as e:
        msg = str(e)
        check(
            "ambiguous message mentions allowedClients/allowedAudience",
            "allowedClients" in msg and "allowedAudience" in msg,
        )

    # --- assert_gateway_idp_matches: match is a no-op ---
    try:
        assert_gateway_idp_matches(COGNITO_GW, "cognito", "lakehouse-gateway")
        assert_gateway_idp_matches(OKTA_GW, "okta", "lakehouse-notes-gateway")
        check("assert(match) does not raise", True)
    except Exception as e:
        check("assert(match) does not raise", False, f"unexpected: {e}")

    # --- assert_gateway_idp_matches: mismatch fails fast ---
    expect_error(
        "assert(cognito live vs okta flag) -> RuntimeError",
        lambda: assert_gateway_idp_matches(COGNITO_GW, "okta", "lakehouse-gateway"),
        RuntimeError,
    )
    expect_error(
        "assert(okta live vs cognito flag) -> RuntimeError",
        lambda: assert_gateway_idp_matches(OKTA_GW, "cognito", "lakehouse-notes-gateway"),
        RuntimeError,
    )

    # Mismatch message must carry both IdPs, the gateway name, and teardown guidance.
    try:
        assert_gateway_idp_matches(COGNITO_GW, "okta", "lakehouse-gateway")
    except RuntimeError as e:
        msg = str(e)
        check(
            "mismatch message names live+flag+gateway",
            "cognito" in msg and "okta" in msg and "lakehouse-gateway" in msg,
        )
        check(
            "mismatch message points at teardown",
            "cleanup_gateway.py" in msg and "06_cleanup_obo_gateway.py" in msg,
        )

    # --- Summary ---
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"SUMMARY: {passed}/{total} passed")
    print("=" * 70 + "\n")

    if passed != total:
        print("❌ Some tests failed.")
        sys.exit(1)
    print("🎉 All DR-11 guard tests passed!")
    sys.exit(0)


if __name__ == "__main__":
    main()
