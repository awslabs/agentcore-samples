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

    The password is never generated. Notebook 07 calls this helper before it mints
    persona tokens, so a value the reader cannot learn would lock every persona out
    of the Streamlit walkthrough (08) while this step still reported success.
    Falling back to the documented default keeps the reader's path working with no
    prerequisite and adds no exposure: the value is already in setup_cognito.py
    and in the notebooks.

    setup_cognito.py calls it at the end of setup(), so a reader's first Streamlit
    sign-in is not stopped by a forced password change; notebook 07's isolation-test
    prep calls it again, which leaves the same value in place.

    Reads /app/lakehouse-agent/cognito-user-pool-id from SSM to find the pool.

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

# The five seeded personas (created by setup_cognito.py setup(), one per group).
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
# is what makes the notebook's instructions true.
#
# If you change this, change it in setup_cognito.py and in notebook 08 too — the
# three must agree.
DEFAULT_TEST_PASSWORD = "TempPass123!"


def set_test_user_passwords(ssm_client, cognito_client, password: str | None = None) -> str:
    """
    Set a PERMANENT password (Permanent=True) for each test persona so they are
    CONFIRMED and usable with ADMIN_USER_PASSWORD_AUTH / SRP.

    password: if None, read LAKEHOUSE_TEST_PASSWORD from the environment, else use
    the documented DEFAULT_TEST_PASSWORD. Never a generated value (see the module
    docstring). A failure on one persona is reported and the loop continues, so
    the final count shows how many of the five were set.
    Returns the password IN MEMORY (never printed) so the caller can mint tokens.
    """
    if password is None:
        env_password = os.environ.get("LAKEHOUSE_TEST_PASSWORD")
        password = env_password or DEFAULT_TEST_PASSWORD
        # Name the SOURCE, never the value. When the environment overrides the default,
        # the password in use and the one notebook 08 documents differ, and the reader
        # needs to know which one won in order to sign in.
        source = "LAKEHOUSE_TEST_PASSWORD (environment)" if env_password else "the documented default (see notebook 08)"
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
