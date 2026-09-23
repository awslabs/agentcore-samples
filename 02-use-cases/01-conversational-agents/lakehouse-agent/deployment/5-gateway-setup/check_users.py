#!/usr/bin/env python3
"""
Check Cognito Users

Quick script to check user status and usernames.

Usage:
    python check_users.py
"""

import boto3
import sys


def check_users():
    """Check Cognito users."""
    # Get region and SSM client
    session = boto3.Session()
    region = session.region_name

    ssm = boto3.client("ssm", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    print("=" * 70)
    print("Cognito Users Check")
    print("=" * 70)

    # Get User Pool ID from SSM
    try:
        user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"]["Value"]
        print(f"\n✅ User Pool ID: {user_pool_id}")
    except Exception as e:
        print(f"❌ Error loading User Pool ID from SSM: {e}")
        sys.exit(1)

    # Check User Pool configuration
    print("\n📋 User Pool Configuration:")
    try:
        pool_response = cognito.describe_user_pool(UserPoolId=user_pool_id)
        pool = pool_response["UserPool"]

        username_attrs = pool.get("UsernameAttributes", [])
        alias_attrs = pool.get("AliasAttributes", [])

        print(f"   Username Attributes: {username_attrs if username_attrs else 'None (email can be username)'}")
        print(f"   Alias Attributes: {alias_attrs if alias_attrs else 'None'}")

        if username_attrs and "email" in username_attrs:
            print("\n   ⚠️  WARNING: UsernameAttributes includes 'email'")
            print("      This means users have UUID usernames, not email addresses!")
            print("      You need to delete the User Pool and recreate it without UsernameAttributes")
    except Exception as e:
        print(f"❌ Error describing User Pool: {e}")

    # List users
    print("\n👥 Users in User Pool:")
    try:
        response = cognito.list_users(UserPoolId=user_pool_id, Limit=20)
        users = response.get("Users", [])

        if not users:
            print("   No users found")
            print("\n   Run setup_cognito.py to create test users")
        else:
            for i, user in enumerate(users, 1):
                username = user["Username"]
                status = user["UserStatus"]
                enabled = user.get("Enabled", True)

                email = None
                email_verified = None
                for attr in user.get("Attributes", []):
                    if attr["Name"] == "email":
                        email = attr["Value"]
                    elif attr["Name"] == "email_verified":
                        email_verified = attr["Value"]

                print(f"\n   User {i}:")
                print(f"   ├─ Username: {username}")
                print(f"   ├─ Email: {email}")
                print(f"   ├─ Status: {status}")
                print(f"   ├─ Enabled: {enabled}")
                print(f"   └─ Email Verified: {email_verified}")

                # Check if username is UUID (indicates UsernameAttributes was set)
                if len(username) == 36 and username.count("-") == 4:
                    print("      ⚠️  Username is UUID - User Pool has UsernameAttributes=['email']")
                    print("      ⚠️  Login with email won't work!")

                if status == "FORCE_CHANGE_PASSWORD":
                    print("      ⚠️  User must change temporary password on first login")

    except Exception as e:
        print(f"❌ Error listing users: {e}")

    print("\n" + "=" * 70)
    print("Recommendations:")
    print("=" * 70)

    # Check if any user has UUID username
    try:
        response = cognito.list_users(UserPoolId=user_pool_id, Limit=1)
        if response.get("Users"):
            username = response["Users"][0]["Username"]
            if len(username) == 36 and username.count("-") == 4:
                print("\n❌ ISSUE FOUND: Users have UUID usernames")
                print("\n   Solution:")
                print("   1. Run: python cleanup_test_users.py")
                print("   2. Delete the User Pool from AWS Console")
                print("   3. Run: python setup_cognito.py")
                print("\n   This will create a new User Pool where email IS the username")
            else:
                print("\n✅ Users have email as username - configuration is correct")
                print("\n   If login still fails:")
                print("   1. Run: python test_cognito_login.py")
                print("   2. Try the default password: TempPass123!")
                print("   3. You may need to change password on first login")
    except Exception:
        pass

    print()


if __name__ == "__main__":
    check_users()
