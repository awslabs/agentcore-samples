#!/usr/bin/env python3
"""
Decode JWT Token to inspect claims (Okta-issued tokens)

This script gets a token via the Resource Owner Password Credentials (ROPC)
flow for one of the test users and decodes it to see what's inside. Used to
verify the custom authorization server emits the expected claims (sub, aud,
scp, groups).

Usage:
    python decode_token.py
"""

import base64
import json
import os
import sys

import boto3
import requests


def decode_jwt(token: str):
    """Decode JWT token without verification (for inspection only)."""
    parts = token.split(".")
    if len(parts) != 3:
        print("Invalid JWT token format")
        return None, None

    # Pad each segment to a multiple of 4 for base64 decode.
    def pad(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    header = json.loads(base64.urlsafe_b64decode(pad(parts[0])))
    payload = json.loads(base64.urlsafe_b64decode(pad(parts[1])))
    return header, payload


def main():
    session = boto3.Session()
    region = session.region_name
    ssm = boto3.client("ssm", region_name=region)

    print("=" * 70)
    print("Decode Okta-Issued JWT Token")
    print("=" * 70)

    # Load configuration from SSM.
    try:
        org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
        auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")["Parameter"]["Value"]
        client_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-app-client-id")["Parameter"]["Value"]
        client_secret = ssm.get_parameter(
            Name="/app/lakehouse-agent/okta-app-client-secret",
            WithDecryption=True,
        )["Parameter"]["Value"]
        audience = ssm.get_parameter(Name="/app/lakehouse-agent/okta-resource-server-audience")["Parameter"]["Value"]

        print("\n✅ Configuration loaded:")
        print(f"   Org URL:        {org_url}")
        print(f"   Auth Server ID: {auth_server_id}")
        print(f"   Client ID:      {client_id}")
        print(f"   Audience:       {audience}")
    except Exception as e:
        print(f"\n❌ Error loading configuration from SSM: {e}")
        print("   Run setup_okta.py first.")
        sys.exit(1)

    # Acquire a token via Resource Owner Password Credentials flow. The
    # operator supplies a test-user login + password (default TempPass123!
    # from setup_okta.py — user must change on first login). ROPC is
    # acceptable for tutorial token-inspection; Streamlit UI uses
    # Authorization Code + PKCE.
    print("\n🔐 Acquiring user token via ROPC flow...")
    print("   Default test-user password: TempPass123! (change required on first login)")
    username = input("   Username (e.g. policyholder001@example.com): ").strip()
    password = input("   Password: ").strip()

    token_url = f"https://{org_url}/oauth2/{auth_server_id}/v1/token"
    # `groups` is a CLAIM, not a scope, on this auth server (alwaysIncludeInToken=True
    # in setup_okta.py's OAuth2Claim block); it emits unconditionally and must NOT
    # be requested as a scope.
    response = requests.post(
        token_url,
        auth=(client_id, client_secret),
        data={
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": "openid profile email claims.query",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )

    if response.status_code != 200:
        print(f"\n❌ Token request failed: HTTP {response.status_code}")
        print(f"   Response: {response.text}")
        sys.exit(1)

    access_token = response.json()["access_token"]
    print("✅ Token received")

    # Decode and display.
    header, payload = decode_jwt(access_token)
    if header is None or payload is None:
        sys.exit(1)

    print("\n📋 JWT Header:")
    print(json.dumps(header, indent=2))

    print("\n📦 JWT Payload:")
    print(json.dumps(payload, indent=2))

    # Surface the claims that downstream interceptors and authorizers care about.
    print("\n🔍 Important Claims:")
    print(f"   Issuer (iss):              {payload.get('iss', 'N/A')}")
    print(f"   Subject (sub):             {payload.get('sub', 'N/A')}")
    print(f"   Audience (aud):            {payload.get('aud', 'N/A')}")
    print(f"   Client ID (cid):           {payload.get('cid', 'N/A')}")
    print(f"   Scopes (scp):              {payload.get('scp', 'N/A')}")
    print(f"   Groups:                    {payload.get('groups', 'N/A')}")
    print(f"   Username (uid):            {payload.get('uid', 'N/A')}")
    print(f"   Token version (ver):       {payload.get('ver', 'N/A')}")
    print(f"   Expires (exp):             {payload.get('exp', 'N/A')}")

    # Sanity-check the issuer matches expected format.
    expected_issuer = f"https://{org_url}/oauth2/{auth_server_id}"
    if payload.get("iss") == expected_issuer:
        print("\n   ✅ Issuer matches expected format")
    else:
        print("\n   ⚠️  Issuer mismatch!")
        print(f"      Expected: {expected_issuer}")
        print(f"      Got:      {payload.get('iss')}")

    # Sanity-check audience matches the resource server identifier.
    if payload.get("aud") == audience:
        print("   ✅ Audience matches resource server audience")
    else:
        print("   ⚠️  Audience mismatch!")
        print(f"      Expected: {audience}")
        print(f"      Got:      {payload.get('aud')}")

    # Sanity-check the groups claim is present (per design §7a — required
    # for the interceptor's claim-to-tenant-role exchange).
    if "groups" in payload:
        print(f"   ✅ groups claim present (count: {len(payload.get('groups', []))})")
    else:
        print(
            "   ⚠️  groups claim missing — verify the auth server's `groups` claim is configured with always_include_in_token=True"
        )

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
