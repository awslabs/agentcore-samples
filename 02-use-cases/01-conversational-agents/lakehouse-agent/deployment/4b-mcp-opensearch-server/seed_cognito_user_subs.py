#!/usr/bin/env python3
"""
Seed Cognito test-user `sub` values into SSM for the OpenSearch notes RLS
(Cognito GW2 path — DR-9 match-by-construction).

On the Cognito path, the notes REQUEST interceptor forwards the caller's Cognito
`sub` (pool subject GUID) and the OpenSearch server filters `owner_user_sub`
against it. For the filter to be non-vacuous, `load_sample_opensearch_data.py`
must seed each note's `owner_user_sub` with the SAME value. This script writes,
for every Cognito test user, an SSM parameter:

    /app/lakehouse-agent/cognito-user-<label>-sub = <the user's `sub` attribute>

where <label> is the username local-part (e.g. policyholder001@example.com →
policyholder001) — the Cognito analogue of the Okta `okta-user-<label>-sub`
keys that notebook 07 writes. Because the `sub` attribute IS the pool subject
that appears as the `sub` claim in the user's access token, forwarded-sub ==
seeded-owner_user_sub by construction. (The AWS smoke — Phase 7 — confirms the
live token `sub` equals this attribute.)

Read-only against Cognito (ListUsers), writes only SSM. Idempotent.

Usage:
    python seed_cognito_user_subs.py
"""

import sys

import boto3

SSM_PREFIX = "/app/lakehouse-agent/"


def main():
    session = boto3.Session()
    region = session.region_name
    if not region:
        print("❌ Could not detect AWS region from boto3 session")
        sys.exit(1)

    ssm = boto3.client("ssm", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    print("=" * 70)
    print("Seed Cognito test-user subs into SSM (Cognito notes RLS)")
    print("=" * 70)
    print(f"\n📋 Region: {region}")

    # Resolve the user pool id (written by 1-cognito-setup/setup_cognito.py).
    try:
        user_pool_id = ssm.get_parameter(Name=f"{SSM_PREFIX}cognito-user-pool-id")["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        print(f"❌ {SSM_PREFIX}cognito-user-pool-id not found — run notebook 01 (Cognito setup) first.")
        sys.exit(1)
    print(f"   User Pool: {user_pool_id}")

    # List all users in the pool (paginated) and extract each `sub` attribute.
    print("\n🔎 Listing Cognito users...")
    seeded = 0
    paginator = cognito.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=user_pool_id):
        for user in page.get("Users", []):
            username = user.get("Username", "")
            attrs = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
            sub = attrs.get("sub")
            if not sub:
                print(f"   ⚠️  No 'sub' attribute for user {username}; skipping")
                continue

            # Label = username local-part (email → localpart); the pool uses email
            # as username (setup_cognito.py sets no UsernameAttributes).
            label = username.split("@")[0]
            param_name = f"{SSM_PREFIX}cognito-user-{label}-sub"
            try:
                ssm.put_parameter(
                    Name=param_name,
                    Value=sub,
                    Description=f"Cognito sub (pool subject) for test user {username} — notes RLS owner_user_sub",
                    Type="String",
                    Overwrite=True,
                )
                print(f"   ✅ {param_name} = {sub}")
                seeded += 1
            except Exception as e:
                print(f"   ❌ Error storing {param_name}: {e}")
                raise

    if seeded == 0:
        print("\n❌ No users seeded — is the pool populated? Run notebook 01 (Cognito setup) first.")
        sys.exit(1)

    print(f"\n✅ Seeded {seeded} cognito-user-*-sub parameter(s).")
    print("   Next: load_sample_opensearch_data.py will read these and seed owner_user_sub to match.")
    print("=" * 70)


if __name__ == "__main__":
    main()
