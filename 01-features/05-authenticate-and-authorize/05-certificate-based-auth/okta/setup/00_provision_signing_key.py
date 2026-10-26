"""
Create an AWS KMS asymmetric signing key that AgentCore identity will use to
sign the JWT client assertion for PRIVATE_KEY_JWT client authentication.

What this script does:
  1. Creates a KMS RSA_2048 key with usage SIGN_VERIFY, in the region set by
     AWS_REGION.
  2. Attaches a key policy that:
       - Grants full key admin to the account root (so the customer can
         manage the key).
       - Grants kms:Sign and kms:DescribeKey to callers when the request
         originates through the AgentCore identity service, via
         kms:ViaService = bedrock-agentcore-identity.*.amazonaws.com.
  3. Creates a KMS alias pointing at the new key (from KMS_KEY_ALIAS env var).
  4. Writes SIGNING_KMS_KEY_ARN back into the .env file for use by later
     setup steps.

Idempotent: if a key already exists under KMS_KEY_ALIAS, the script fetches
its ARN and re-uses it.

Usage:
    python setup/00_provision_signing_key.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} is not set. See config.example.env.", file=sys.stderr)
        sys.exit(1)
    return value


def build_key_policy(account_id: str) -> str:
    """Build the KMS key policy JSON.

    The account-root Principal on the admin statement is the standard KMS
    pattern for "let IAM policies in this account decide who can manage the
    key". The service statement scopes kms:Sign and kms:DescribeKey to
    calls that pass through the AgentCore identity service.
    """
    policy = {
        "Version": "2012-10-17",
        "Id": "pkjwt-signing-key-policy",
        "Statement": [
            {
                "Sid": "EnableKeyAdmin",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": "kms:*",
                "Resource": "*",
            },
            {
                "Sid": "BedrockAgentCoreIdentityPrivateKeyJwtAccess",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": ["kms:Sign", "kms:DescribeKey"],
                "Resource": "*",
                "Condition": {"StringLike": {"kms:ViaService": "bedrock-agentcore-identity.*.amazonaws.com"}},
            },
        ],
    }
    return json.dumps(policy)


def find_key_by_alias(kms, alias_name: str) -> str | None:
    """Return the target key ARN for an alias, or None if the alias doesn't exist."""
    try:
        resp = kms.describe_key(KeyId=alias_name)
        return resp["KeyMetadata"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            return None
        raise


def ensure_key_and_alias(kms, alias_name: str, key_policy: str) -> str:
    """Create-or-fetch a KMS key + alias. Returns the key ARN."""
    existing = find_key_by_alias(kms, alias_name)
    if existing:
        print(f"• KMS key already exists under {alias_name}: {existing}")
        return existing

    print("Creating KMS RSA_2048 signing key...")
    resp = kms.create_key(
        Description="AgentCore identity PRIVATE_KEY_JWT signing key (Okta sample)",
        KeyUsage="SIGN_VERIFY",
        KeySpec="RSA_2048",
        Policy=key_policy,
        Tags=[
            {"TagKey": "Purpose", "TagValue": "agentcore-private-key-jwt-sample"},
            {"TagKey": "IdentityProvider", "TagValue": "okta"},
        ],
    )
    key_arn = resp["KeyMetadata"]["Arn"]
    key_id = resp["KeyMetadata"]["KeyId"]
    print(f"✓ Created KMS key: {key_arn}")

    kms.create_alias(AliasName=alias_name, TargetKeyId=key_id)
    print(f"✓ Created alias: {alias_name}")

    return key_arn


def write_env_var(name: str, value: str) -> None:
    """Persist a KEY=VALUE line into the .env file used by later steps.

    Creates .env from config.example.env if it doesn't exist. Updates in
    place if the key is already present, appends otherwise.
    """
    if not ENV_FILE.exists():
        example = ENV_FILE.with_name("config.example.env")
        if not example.exists():
            print(
                f"ERROR: neither {ENV_FILE} nor {example} exists. Copy config.example.env to .env first.",
                file=sys.stderr,
            )
            sys.exit(1)
        ENV_FILE.write_text(example.read_text())
        print(f"• Created {ENV_FILE} from config.example.env")

    text = ENV_FILE.read_text()
    pattern = rf"^{re.escape(name)}=.*$"
    replacement = f"{name}={value}"
    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{replacement}\n"
    ENV_FILE.write_text(new_text)
    print(f"✓ Wrote {name} to .env")


def main() -> None:
    load_dotenv(ENV_FILE)
    region = must_env("AWS_REGION")
    alias_name = must_env("KMS_KEY_ALIAS")

    session = boto3.session.Session(region_name=region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    kms = session.client("kms")

    print(f"Region:   {region}")
    print(f"Account:  {account_id}")
    print(f"Alias:    {alias_name}")
    print()

    key_policy = build_key_policy(account_id)
    key_arn = ensure_key_and_alias(kms, alias_name, key_policy)

    print()
    write_env_var("SIGNING_KMS_KEY_ARN", key_arn)

    key_id = key_arn.rsplit("/", 1)[-1]
    print()
    print("=" * 70)
    print("  Summary - step 00: KMS signing key")
    print("=" * 70)
    print("  What was created (or reused, if the alias already existed):")
    print(f"    KMS key ARN  : {key_arn}")
    print("    Key spec     : RSA_2048, usage SIGN_VERIFY")
    print(f"    Alias        : {alias_name}")
    print("    Key policy   : root admin + kms:Sign for AgentCore identity")
    print("                   (via kms:ViaService ")
    print("                    bedrock-agentcore-identity.*.amazonaws.com)")
    print()
    print("  Where to inspect it in the AWS console:")
    print(f"    KMS → Customer managed keys → filter by alias '{alias_name}'")
    print(f"    Region: {region}, account: {account_id}")
    print("    Direct URL:")
    print(f"      https://{region}.console.aws.amazon.com/kms/home?region={region}#/kms/keys/{key_id}")
    print()
    print("  Written to .env:")
    print(f"    SIGNING_KMS_KEY_ARN={key_arn}")
    print()
    print("  Why this step matters:")
    print("    AgentCore identity signs the PRIVATE_KEY_JWT client assertion")
    print("    by calling kms:Sign against this key. The private key material")
    print("    never leaves KMS - the sample only ever uses the public key")
    print("    (published as a JWK on your Okta apps in the next step).")
    print()
    print("  Next step:")
    print("    python setup/01_create_okta_service_app.py")
    print("    (creates the Okta OIDC service app and registers this key's")
    print("     public half as a JWK on it)")


if __name__ == "__main__":
    main()
