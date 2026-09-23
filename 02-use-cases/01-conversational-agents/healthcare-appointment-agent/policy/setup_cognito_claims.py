"""
Setup Amazon Cognito Pre-Token-Generation AWS Lambda trigger to inject custom claims
(role, patient_id) into JWT tokens for Cedar policy evaluation.

How it works:
  - The Amazon Cognito Pre-Token-Generation V3_0 trigger fires before issuing a token.
  - The AWS Lambda function reads role/sub from its environment variables and injects them
    as custom claims via claimsToAddOrOverride in the access token.
  - Cedar policies then evaluate these claims via principal.getTag("role")
    and principal.getTag("patient_id").
  - Switching roles is just a Lambda env var update — no redeployment needed.

In production, these claims would come from the user's identity provider
(e.g., Amazon Cognito user attributes, SAML assertions, or OIDC claims) via the
authorization code flow. This AWS Lambda function simulates that for demo purposes.

Usage:
  # Set up with patient role (default)
  python policy/setup_cognito_claims.py --role patient --sub adult-patient-001

  # Switch to doctor role for provider testing
  python policy/setup_cognito_claims.py --role doctor --sub doctor-001

  # Switch to a different patient
  python policy/setup_cognito_claims.py --role patient --sub pediatric-patient-001

  # Fast switch (just update env vars, skip full setup)
  python policy/setup_cognito_claims.py --role doctor --sub doctor-001 --update-only

  # Verify current token claims
  python policy/setup_cognito_claims.py --verify

Requirements:
  - Amazon Cognito User Pool must be Essentials or Plus tier (for V3_0 triggers)
  - AWS Identity and Access Management (IAM) permissions: cognito-idp:*, lambda:*, iam:*
"""

import argparse
import json
import os
import time
import zipfile
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

LAMBDA_FUNCTION_NAME = "healthcare-cognito-claims"

LAMBDA_CODE = '''\
import json
import os

def lambda_handler(event, context):
    """
    Pre-Token-Generation V3_0 Lambda trigger.
    Injects role and patient_id claims into Cognito access tokens.

    These claims are used by Cedar policies in AgentCore Policy:
      - "role" → principal.getTag("role") — determines user type (patient, doctor, etc.)
      - "patient_id" → principal.getTag("patient_id") — used for identity scoping
        (ensures patients can only access their own data)

    Claims are read from environment variables so they can be
    changed without redeploying the function (--update-only flag).
    """
    role = os.environ.get("CLAIM_ROLE", "patient")
    sub_value = os.environ.get("CLAIM_SUB", "adult-patient-001")

    print(f"Injecting claims: role={role}, sub={sub_value}")
    print(f"Trigger source: {event.get('triggerSource', 'unknown')}")

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {
                "claimsToAddOrOverride": {
                    "role": role,
                    "patient_id": sub_value,
                }
            }
        }
    }

    return event
'''


# Secret name used by AWS Secrets Manager to cache the Amazon Cognito client secret.
COGNITO_SECRET_NAME = "healthcare-agent/cognito-client-secret"


def get_client_secret(session):
    """Retrieve the Amazon Cognito client secret, preferring AWS Secrets Manager.

    Lookup order:
      1. AWS Secrets Manager (production-ready, supports rotation)
      2. Amazon Cognito DescribeUserPoolClient API (demo fallback)

    On first run the secret is fetched from Amazon Cognito and cached in
    Secrets Manager so subsequent calls use the cached value.
    """
    region = os.getenv("aws_default_region", "us-east-1")
    secret_name = os.getenv("cognito_secret_name", COGNITO_SECRET_NAME)
    sm = session.client("secretsmanager", region_name=region)

    # 1. Try Secrets Manager first
    try:
        return sm.get_secret_value(SecretId=secret_name)["SecretString"]
    except sm.exceptions.ResourceNotFoundException:
        pass  # Fall through to Amazon Cognito API

    # 2. Fallback: retrieve from Amazon Cognito API
    pool_id = os.getenv("cognito_user_pool_id")
    client_id = os.getenv("cognito_client_id")
    cognito = session.client("cognito-idp", region_name=region)
    resp = cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)
    secret = resp["UserPoolClient"]["ClientSecret"]

    # Cache in Secrets Manager for future calls
    try:
        sm.create_secret(
            Name=secret_name,
            Description="Amazon Cognito client secret for healthcare appointment agent (auto-cached)",
            SecretString=secret,
        )
    except (sm.exceptions.ResourceExistsException, Exception):
        pass  # Non-fatal — secret still works even if caching fails

    return secret


def get_session():
    """Create boto3 session with profile support."""
    profile = os.getenv("awscred_profile_name")
    region = os.getenv("aws_default_region", "us-east-1")
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def get_account_id(session):
    return session.client("sts").get_caller_identity()["Account"]


def ensure_essentials_tier(session):
    """Check and upgrade Amazon Cognito User Pool to Essentials tier if needed."""
    pool_id = os.getenv("cognito_user_pool_id")
    region = os.getenv("aws_default_region", "us-east-1")
    cognito = session.client("cognito-idp", region_name=region)

    print("\n📋 Checking Cognito User Pool tier...")
    pool = cognito.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    tier = pool.get("UserPoolTier", "LITE")
    print(f"   Current tier: {tier}")

    if tier in ("ESSENTIALS", "PLUS"):
        print("   ✅ Already Essentials or Plus — V3_0 triggers supported")
        return

    print("   ⬆️  Upgrading to ESSENTIALS tier (required for V3_0 Lambda triggers)...")
    cognito.update_user_pool(UserPoolId=pool_id, UserPoolTier="ESSENTIALS")
    print("   ✅ Upgraded to ESSENTIALS")


def create_or_update_lambda(session, role, sub_value, children=""):
    """Create or update the claims injection Lambda."""
    region = os.getenv("aws_default_region", "us-east-1")
    account_id = get_account_id(session)
    lambda_client = session.client("lambda", region_name=region)

    print(f"\n📝 Configuring Lambda: {LAMBDA_FUNCTION_NAME}")
    print(f"   Claims: role={role}, sub={sub_value}")

    # Create zip deployment package
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = tmp.name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("lambda_function.py", LAMBDA_CODE)

    with open(zip_path, "rb") as f:
        zip_content = f.read()
    os.unlink(zip_path)

    env_vars = {
        "Variables": {
            "CLAIM_ROLE": role,
            "CLAIM_SUB": sub_value,
            "CLAIM_CHILDREN": children,
        }
    }

    try:
        # Try updating existing function
        lambda_client.update_function_code(FunctionName=LAMBDA_FUNCTION_NAME, ZipFile=zip_content)
        print("   ✅ Updated Lambda code")

        # Wait for update to complete
        time.sleep(3)

        # Update environment variables
        lambda_client.update_function_configuration(FunctionName=LAMBDA_FUNCTION_NAME, Environment=env_vars)
        print(f"   ✅ Updated Lambda env vars: role={role}, sub={sub_value}")

        fn = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        return fn["Configuration"]["FunctionArn"]

    except lambda_client.exceptions.ResourceNotFoundException:
        pass

    # Create IAM role for Lambda
    role_name = f"{LAMBDA_FUNCTION_NAME}-role"
    iam = session.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Role for Amazon Cognito pre-token-generation AWS Lambda function",
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"   ✅ Created IAM role: {role_name}")
        print("   ⏳ Waiting 10s for IAM role propagation...")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"   ✅ IAM role already exists: {role_name}")

    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    # Create Lambda function
    response = lambda_client.create_function(
        FunctionName=LAMBDA_FUNCTION_NAME,
        Runtime="python3.12",
        Role=role_arn,
        Handler="lambda_function.lambda_handler",
        Code={"ZipFile": zip_content},
        Environment=env_vars,
        Timeout=10,
        Description="Injects role/sub claims into Amazon Cognito JWT tokens for Cedar policy evaluation",
    )
    print(f"   ✅ Created Lambda: {response['FunctionArn']}")
    return response["FunctionArn"]


def add_cognito_permission(session, lambda_arn):
    """Allow Amazon Cognito to invoke the AWS Lambda function."""
    region = os.getenv("aws_default_region", "us-east-1")
    pool_id = os.getenv("cognito_user_pool_id")
    account_id = get_account_id(session)
    lambda_client = session.client("lambda", region_name=region)

    print("\n🔐 Adding Cognito invoke permission to Lambda...")
    try:
        lambda_client.add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId="CognitoInvoke",
            Action="lambda:InvokeFunction",
            Principal="cognito-idp.amazonaws.com",
            SourceArn=f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{pool_id}",
        )
        print("   ✅ Permission added")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            print("   ✅ Permission already exists")
        else:
            raise


def attach_trigger(session, lambda_arn):
    """Attach AWS Lambda as Pre-Token-Generation V3_0 trigger to Amazon Cognito."""
    region = os.getenv("aws_default_region", "us-east-1")
    pool_id = os.getenv("cognito_user_pool_id")
    cognito = session.client("cognito-idp", region_name=region)

    print("\n📎 Attaching Lambda trigger to Cognito User Pool...")

    # Get current pool config to preserve existing settings
    pool = cognito.describe_user_pool(UserPoolId=pool_id)["UserPool"]

    # Build lambda config preserving any existing triggers
    lambda_config = pool.get("LambdaConfig", {})
    lambda_config["PreTokenGenerationConfig"] = {
        "LambdaArn": lambda_arn,
        "LambdaVersion": "V3_0",
    }

    cognito.update_user_pool(
        UserPoolId=pool_id,
        LambdaConfig=lambda_config,
    )
    print("   ✅ Pre-Token-Generation V3_0 trigger attached")
    print(f"   Lambda: {lambda_arn}")


def update_claims_only(session, role, sub_value):
    """Fast path: just update Lambda env vars without recreating anything."""
    region = os.getenv("aws_default_region", "us-east-1")
    lambda_client = session.client("lambda", region_name=region)

    print(f"\n🔄 Updating claims: role={role}, sub={sub_value}")
    lambda_client.update_function_configuration(
        FunctionName=LAMBDA_FUNCTION_NAME,
        Environment={
            "Variables": {
                "CLAIM_ROLE": role,
                "CLAIM_SUB": sub_value,
            }
        },
    )
    print("   ✅ Claims updated — next token will have new values")


def verify_token(session):
    """Get a fresh token and decode it to verify claims."""
    import requests as req

    client_id = os.getenv("cognito_client_id")
    token_url = os.getenv("cognito_token_url")
    scope = os.getenv("cognito_auth_scope", "")

    # Demo: retrieves client secret from Amazon Cognito API.
    # Production: store client secrets in AWS Secrets Manager.
    secret = get_client_secret(session)

    data = "grant_type=client_credentials"
    if scope:
        data += f"&scope={scope}"

    resp = req.post(
        token_url,
        data=data,
        auth=(client_id, secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"   ❌ Token request failed: {resp.text}")
        return

    import base64

    token = resp.json()["access_token"]
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))

    print("\n🔍 JWT Token Claims:")
    for k, v in sorted(claims.items()):
        print(f"   {k}: {v}")

    # Check for our custom claims
    has_role = "role" in claims or "custom:role" in claims
    has_pid = "patient_id" in claims
    print(f"\n   Custom claims present: role={'✅' if has_role else '❌'}, patient_id={'✅' if has_pid else '❌'}")


def main():
    parser = argparse.ArgumentParser(
        description="Setup Amazon Cognito claims for Cedar policy evaluation",
        epilog="""
Examples:
  # First-time setup with patient role
  python policy/setup_cognito_claims.py --role patient --sub adult-patient-001

  # Switch to doctor role (fast — just updates env vars)
  python policy/setup_cognito_claims.py --role doctor --sub doctor-001 --update-only

  # Verify current token claims
  python policy/setup_cognito_claims.py --verify
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--role", default="patient", help="Role claim value (default: patient)")
    parser.add_argument(
        "--sub",
        default="adult-patient-001",
        help="Sub claim value (default: adult-patient-001)",
    )
    parser.add_argument(
        "--update-only",
        action="store_true",
        help="Only update Lambda env vars (skip setup)",
    )
    parser.add_argument("--verify", action="store_true", help="Get a token and show decoded claims")

    args = parser.parse_args()
    session = get_session()

    if args.verify:
        verify_token(session)
        return

    if args.update_only:
        update_claims_only(session, args.role, args.sub)
        print("\n⏳ Waiting 5s for Lambda update to propagate...")
        time.sleep(5)
        verify_token(session)
        return

    # Full setup
    print("=" * 70)
    print("🚀 Setting up Cognito Claims for Cedar Policy Evaluation")
    print("=" * 70)
    print(f"   Role: {args.role}")
    print(f"   Sub:  {args.sub}")

    ensure_essentials_tier(session)
    lambda_arn = create_or_update_lambda(session, args.role, args.sub)
    add_cognito_permission(session, lambda_arn)
    attach_trigger(session, lambda_arn)

    print("\n⏳ Waiting 10s for trigger to propagate...")
    time.sleep(10)

    verify_token(session)

    print("\n" + "=" * 70)
    print("✅ COGNITO CLAIMS SETUP COMPLETE")
    print("=" * 70)
    print(f"   Lambda: {LAMBDA_FUNCTION_NAME}")
    print(f"   Claims: role={args.role}, sub={args.sub}")
    print("\n   To switch roles:")
    print("     python policy/setup_cognito_claims.py --role doctor --sub doctor-001 --update-only")
    print("   To verify:")
    print("     python policy/setup_cognito_claims.py --verify")
    print("=" * 70)


if __name__ == "__main__":
    main()
