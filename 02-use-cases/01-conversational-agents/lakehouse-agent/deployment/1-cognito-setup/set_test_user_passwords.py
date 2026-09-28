#!/usr/bin/env python3
"""
Set PERMANENT passwords for the Cognito test personas (test/demo convenience).

⚠️  DEMO DETERMINISM ONLY — NOT PRODUCTION PRACTICE.
    This helper calls AdminSetUserPassword(Permanent=True) so the multi-user
    isolation test (07-optional-multi-user-isolation-test.ipynb) and the manual
    Streamlit walkthrough (08) can log in as each persona deterministically,
    without an interactive first-login / NEW_PASSWORD_REQUIRED dance.

    A REAL deployment MUST NOT do this. Production should use the Cognito
    first-login flow (NEW_PASSWORD_REQUIRED), the hosted UI, or SRP — and enable
    MFA, and must never use a shared password.

    Which password is used, in order:
      1. an explicit `password=` argument, if the caller passes one;
      2. LAKEHOUSE_TEST_PASSWORD from the environment, if set;
      3. otherwise DEFAULT_TEST_PASSWORD below — the SAME documented value that
         setup_cognito.py already assigns as the initial TemporaryPassword, and
         that notebook 08 tells the reader to sign in with.

    Step 3 is the important one and it is a deliberate reversal of this file's
    original behaviour, which generated a RANDOM password and returned it only in
    memory. That was well intentioned — nothing hit disk — but it made this helper
    a silent lockout: notebook 07 calls it, so merely running the isolation test
    replaced every persona's password with a value the reader could not learn,
    printed a "5/5" success line, and left the Streamlit walkthrough (08)
    impossible to complete. A step that removes access must not report success.

    Falling back to the documented default keeps the reader's path working with no
    prerequisite, and does not widen exposure: the value is already published in
    setup_cognito.py and in the notebooks, so this restates a known demo
    credential rather than introducing a new one.

    It is deliberately kept OUT of setup_cognito.py's happy path so the primary
    tutorial still models the production first-login flow; it is invoked only by
    the optional isolation-test prep.

Usage (standalone):
    python set_test_user_passwords.py         # uses the documented default
    LAKEHOUSE_TEST_PASSWORD=... python set_test_user_passwords.py

Usage (imported, e.g. from notebook 07 prep):
    from set_test_user_passwords import set_test_user_passwords, TEST_PERSONAS
    password = set_test_user_passwords(ssm_client, cognito_client)  # in-memory only
"""

import os
import sys

import boto3

# The five seeded personas (created by setup_cognito.py create_test_users).
TEST_PERSONAS = [
    "policyholder001@example.com",
    "policyholder002@example.com",
    "adjuster001@example.com",
    "adjuster002@example.com",
    "admin@example.com",
]

# The documented demo credential. This is intentionally the SAME string
# setup_cognito.py assigns as the initial TemporaryPassword, and the same one
# notebook 08 tells the reader to sign in with. Keeping one value in the sample
# is what makes the reader's instructions true: the previous behaviour generated a
# random password here, so the notebook's stated value and the pool's actual value
# silently disagreed.
#
# If you change this, change it in setup_cognito.py and in notebook 08 too — the
# three must agree, and the pairing is the point rather than a coincidence.
DEFAULT_TEST_PASSWORD = "TempPass123!"


def set_test_user_passwords(ssm_client, cognito_client, password: str | None = None) -> str:
    """
    Set a PERMANENT password (Permanent=True) for each test persona so they are
    CONFIRMED and usable with ADMIN_USER_PASSWORD_AUTH / SRP.

    password: if None, read LAKEHOUSE_TEST_PASSWORD from the environment, else use
    the documented DEFAULT_TEST_PASSWORD. Never a generated value — see the module
    docstring for why: a password nobody can learn makes this helper a silent
    lockout, because notebook 07 calls it and notebook 08 then cannot sign in.
    Returns the password IN MEMORY (never printed) so the caller can mint tokens.
    """
    if password is None:
        env_password = os.environ.get("LAKEHOUSE_TEST_PASSWORD")
        password = env_password or DEFAULT_TEST_PASSWORD
        # Name the SOURCE, never the value. This line is the fix for the failure that
        # produced this code path: the password used and the password documented can
        # differ, and when they do, the only way to sign in is to know which one won.
        # Printing the source costs nothing and makes that difference visible instead
        # of leaving the reader to try the documented value and be told it is wrong.
        source = (
            "LAKEHOUSE_TEST_PASSWORD (environment)"
            if env_password
            else "the documented default (see notebook 08)"
        )
        print(f"   ℹ️  Using password from: {source}")
    else:
        print("   ℹ️  Using password supplied by the caller.")

    pool_id = ssm_client.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]

    set_count = 0
    for username in TEST_PERSONAS:
        try:
            cognito_client.admin_set_user_password(
                UserPoolId=pool_id,
                Username=username,
                Password=password,
                Permanent=True,
            )
            set_count += 1
        except Exception as e:  # keep going; report per-user without leaking the password
            print(f"   ⚠️  Could not set password for {username}: {type(e).__name__}")
    # Status only — NEVER print the password value.
    print(f"✅ Set permanent password for {set_count}/{len(TEST_PERSONAS)} test personas (value not shown).")
    return password


def main():
    region = boto3.Session().region_name or "us-east-1"
    ssm = boto3.client("ssm", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)
    set_test_user_passwords(ssm, cognito)
    print("   (Demo convenience only — production must use first-login/hosted-UI/SRP + MFA.)")


if __name__ == "__main__":
    sys.exit(main())
