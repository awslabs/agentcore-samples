"""
Okta Inbound Authentication for AgentCore Runtime.

Demonstrates how to configure an Amazon Bedrock AgentCore Runtime agent with
Okta as the inbound identity provider, using the customJWTAuthorizer.

Only callers presenting a valid Okta JWT bearer token can invoke the agent.
Unauthenticated requests are automatically rejected with AccessDeniedException.

Architecture:
    ┌──────────┐  1. client_credentials  ┌──────────┐  2. JWT Token  ┌──────────┐
    │  Client  │ ──────────────────────► │   Okta   │ ─────────────► │  Client  │
    └──────────┘                         └──────────┘                └──────────┘
                                                                            │
                                                                            │ 3. Bearer JWT
                                                                            ▼
    ┌──────────┐  6. Response   ┌──────────────────┐  4. Invoke    ┌──────────┐
    │  Client  │ ◄──────────── │  AgentCore       │ ────────────► │  Agent   │
    └──────────┘                │  Runtime         │               └──────────┘
                                │ (JWT validated)  │
                                └──────────────────┘

Usage:
    python okta_inbound_auth.py
    python okta_inbound_auth.py --cleanup
    python okta_inbound_auth.py --test-only

Prerequisites:
    - AWS CLI configured
    - Okta developer account (https://developer.okta.com/signup/)
    - pip install -r requirements.txt
    - Set environment variables:
        OKTA_CLIENT_ID       - Application Client ID
        OKTA_CLIENT_SECRET   - Application Client Secret
        OKTA_AUDIENCE        - Audience (e.g. testagentcore)
        OKTA_TOKEN_URL       - https://{your-domain}/oauth2/default/v1/token
        OKTA_DISCOVERY_URL   - https://{your-domain}/oauth2/default/.well-known/openid-configuration

Okta Setup (one-time):
    1. Sign up at https://developer.okta.com/signup/ (Integrator Free Plan)
    2. Directory > People > Add person (create a test user)
    3. Applications > Create App Integration > OIDC > Web Application
       - Grant type: Authorization Code
       - Redirect URL: https://bedrock-agentcore.{region}.amazonaws.com/identities/oauth2/callback
    4. Security > API > Authorization server
       - Copy Audience
       - Scopes > Add Scope: name=agentcore, User Consent=implicit
       - Claims > Add Claims: client_id, scope
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import uuid
import zipfile

import boto3
import jwt
import requests
from boto3.session import Session
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

AGENT_NAME = f"okta_inbound_auth_{int(time.time()) % 100000}"
AGENT_FILE = "simple_agent.py"
RUNTIME_CONFIG_FILE = "runtime_config_okta_inbound.json"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-west-2"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Agent Code ─────────────────────────────────────────────────────────────────

AGENT_CODE = '''from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
agent = Agent()

@app.entrypoint
def invoke(payload):
    """Simple agent with session awareness for inbound auth demo."""
    user_message = payload.get("prompt", "Hello! How can I help you today?")
    session_id = payload.get("session_id", "no-session")
    response = f"Authenticated session {session_id[:8]}. You asked: {user_message}"
    result = agent(response)
    return {"result": result.message}

if __name__ == "__main__":
    app.run()
'''


# ── Step 1: Create IAM Execution Role ─────────────────────────────────────────


def create_execution_role(role_name: str) -> str:
    """Create IAM execution role for AgentCore Runtime."""
    iam = boto3.client("iam", region_name=REGION)

    trust_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )

    try:
        role = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust_policy)
        role_arn = role["Role"]["Arn"]
        print(f"  Created IAM role: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  Reusing IAM role: {role_name}")

    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                        "bedrock:Converse",
                        "bedrock:ConverseStream",
                    ],
                    "Resource": [
                        "arn:aws:bedrock:*::foundation-model/*",
                        f"arn:aws:bedrock:*:{ACCOUNT_ID}:inference-profile/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "arn:aws:logs:*:*:*",
                },
            ],
        }
    )

    try:
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="agentcore-okta-inbound-policy",
            PolicyDocument=policy,
        )
    except Exception:
        pass

    time.sleep(15)  # IAM cross-service propagation
    return role_arn


# ── Step 2: Upload Agent Code to S3 ───────────────────────────────────────────


def upload_agent_to_s3() -> dict:
    """Build agent deployment zip with uv, upload to S3.

    AgentCore Runtime mounts the zip at /var/task and does not run pip at
    boot. We pre-install ARM64 wheels with uv and bundle them with the agent
    code so the runtime can boot without ModuleNotFoundError. See:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
    """
    s3 = boto3.client("s3", region_name=REGION)
    bucket_name = f"agentcore-okta-inbound-{ACCOUNT_ID}-{REGION}"

    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        print(f"  Created S3 bucket: {bucket_name}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  Reusing S3 bucket: {bucket_name}")

    sample_dir = os.path.dirname(os.path.abspath(__file__))
    requirements = os.path.join(sample_dir, "requirements.txt")
    if not os.path.exists(requirements):
        raise FileNotFoundError(f"requirements.txt not found: {requirements}")

    build_dir = tempfile.mkdtemp(prefix="agentcore-build-")
    pkg_dir = os.path.join(build_dir, "deployment_package")
    zip_path = os.path.join(build_dir, "agent.zip")
    os.makedirs(pkg_dir)

    try:
        # Write the agent entry point.
        with open(os.path.join(pkg_dir, AGENT_FILE), "w") as f:
            f.write(AGENT_CODE)

        # Pre-install ARM64 wheels.
        print("  Installing arm64 dependencies with uv...")
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python-platform",
                "aarch64-manylinux2014",
                "--python-version",
                "3.13",
                "--target",
                pkg_dir,
                "--only-binary",
                ":all:",
                "-r",
                requirements,
            ],
            check=True,
        )

        # Zip the package directory at the archive root.
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(pkg_dir):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    arc_name = os.path.relpath(abs_path, pkg_dir)
                    zf.write(abs_path, arc_name)

        s3_key = f"agents/{AGENT_NAME}/agent.zip"
        s3.upload_file(zip_path, bucket_name, s3_key)
        print(f"  Uploaded to s3://{bucket_name}/{s3_key}")

        return {"bucket": bucket_name, "key": s3_key}

    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


# ── Step 3: Create AgentCore Runtime with Okta JWT Authorizer ─────────────────


def _create_runtime_with_retry(control, **kwargs):
    """Retry create_agent_runtime to absorb the IAM role propagation race.

    The control plane briefly returns ValidationException("Role validation
    failed... please verify that the role exists") before the role is fully
    propagated across services. Backoff: 4, 8, 16, 32, 64 seconds.
    """
    last_exc = None
    for attempt in range(5):
        try:
            return control.create_agent_runtime(**kwargs)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = str(e).lower()
            if code in ("ValidationException", "AccessDeniedException") and "role" in msg:
                last_exc = e
                wait = 2**attempt * 4
                print(f"    Role not yet assumable; retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
    raise last_exc


def create_runtime(role_arn: str, s3_info: dict) -> dict:
    """Create AgentCore Runtime configured with Okta customJWTAuthorizer."""
    discovery_url = os.environ.get("OKTA_DISCOVERY_URL")
    client_id = os.environ.get("OKTA_CLIENT_ID")
    audience = os.environ.get("OKTA_AUDIENCE")

    if not all([discovery_url, client_id, audience]):
        raise ValueError("Set OKTA_DISCOVERY_URL, OKTA_CLIENT_ID, OKTA_AUDIENCE environment variables.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    response = _create_runtime_with_retry(
        control,
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": s3_info["bucket"], "prefix": s3_info["key"]}},
                "runtime": "PYTHON_3_13",
                "entryPoint": [AGENT_FILE],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [client_id],
                "allowedAudience": [audience],
            }
        },
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  Created runtime: {AGENT_NAME}")
    print(f"  Runtime ID: {runtime_id}")
    print(f"  Discovery URL: {discovery_url}")
    print(f"  Allowed Audience: {audience}")

    print("  Waiting for READY...")
    while True:
        s = control.get_agent_runtime(agentRuntimeId=runtime_id).get("status", "UNKNOWN")
        print(f"    Status: {s}")
        if s == "READY":
            break
        if s in ("CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Runtime failed: {s}")
        time.sleep(15)

    endpoint_url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com"
        f"/runtimes/{urllib.parse.quote(runtime_arn, safe='')}/invocations"
        "?qualifier=DEFAULT"
    )

    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "endpoint_url": endpoint_url,
        "discovery_url": discovery_url,
        "audience": audience,
        "region": REGION,
        "role_arn": role_arn,
        "s3_bucket": s3_info["bucket"],
    }
    with open(RUNTIME_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  Config saved to {RUNTIME_CONFIG_FILE}")
    return config


# ── Step 4: Get Okta Access Token ─────────────────────────────────────────────


def get_okta_token(scope: str = "agentcore") -> str:
    """Get OAuth access token from Okta using client_credentials grant."""
    client_id = os.environ.get("OKTA_CLIENT_ID")
    client_secret = os.environ.get("OKTA_CLIENT_SECRET")
    token_url = os.environ.get("OKTA_TOKEN_URL")

    if not all([client_id, client_secret, token_url]):
        raise ValueError("Set OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, OKTA_TOKEN_URL.")

    resp = requests.post(  # nosec B113
        token_url,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token endpoint returned {resp.status_code}:\n  {resp.text}\n"
            "Verify OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, OKTA_TOKEN_URL."
        )
    token = resp.json()["access_token"]
    print("  Okta token acquired")
    return token


# ── Step 5: Test Agent Invocation ─────────────────────────────────────────────


def test_agent(endpoint_url: str):
    """Run a series of tests against the agent."""
    session_id = str(uuid.uuid4())  # AgentCore requires a session id of >= 33 chars

    # Test 1: No auth — should fail
    print("\n  TEST 1: Unauthenticated request (should fail)...")
    try:
        resp = requests.post(
            endpoint_url,
            headers={"Content-Type": "application/json"},
            json={"prompt": "hello without auth"},
            timeout=30,
        )
        if resp.status_code in (401, 403):
            print(f"  Expected: HTTP {resp.status_code} - authentication required ✓")
        else:
            print(f"  Unexpected status: {resp.status_code}")
    except requests.exceptions.HTTPError as e:
        print(f"  Expected auth failure: {e.response.status_code} ✓")
    except Exception as e:
        print(f"  Request error: {e}")

    # Get valid token
    access_token = get_okta_token()

    # Test 2: Decode and inspect token
    print("\n  TEST 2: Decoding Okta JWT token...")
    try:
        decoded = jwt.decode(access_token, options={"verify_signature": False})  # nosec: test-only claim inspection
        print(f"    Audience: {decoded.get('aud')}")
        print(f"    Issuer: {decoded.get('iss')}")
        print(f"    Scopes: {decoded.get('scp')}")
        print(f"    Client ID claim: {decoded.get('client_id')}")
    except Exception as e:
        print(f"    Could not decode token: {e}")

    # Test 3: Scope validation — negative (wrong scope)
    print("\n  TEST 3: Scope validation - negative (checking for 'admin' scope)...")
    decoded = jwt.decode(access_token, options={"verify_signature": False})  # nosec: test-only claim inspection
    token_scopes = decoded.get("scp", [])
    if "admin" in token_scopes:
        print("  Token has 'admin' scope (unexpected)")
    else:
        print(f"  Token does NOT have 'admin' scope ✓ (has: {token_scopes})")

    # Test 4: Scope validation — positive (correct scope)
    print("\n  TEST 4: Scope validation - positive (checking for 'agentcore' scope)...")
    if "agentcore" in token_scopes:
        print("  Token has 'agentcore' scope ✓ — proceeding with agent call")
    else:
        print(f"  Token scopes: {token_scopes}. Proceeding anyway for demonstration.")

    # Test 5: Authenticated invocation
    print("\n  TEST 5: Authenticated invocation...")
    resp = requests.post(
        endpoint_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        json={
            "prompt": "Hello! Tell me about AWS security best practices for authentication.",
            "session_id": session_id,
        },
        timeout=120,
    )
    resp.raise_for_status()
    print(f"  Response: {resp.text[:400]}")

    # Test 6: Session continuity — same session ID
    print("\n  TEST 6: Session continuity (same session ID)...")
    resp2 = requests.post(
        endpoint_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        json={"prompt": "What was my previous question?", "session_id": session_id},
        timeout=120,
    )
    resp2.raise_for_status()
    print(f"  Response: {resp2.text[:400]}")


# ── Step 6: Cleanup ────────────────────────────────────────────────────────────


def cleanup():
    """Delete all created resources."""
    try:
        with open(RUNTIME_CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        print("  No config file found.")
        return

    control = boto3.client("bedrock-agentcore-control", region_name=config["region"])
    iam = boto3.client("iam", region_name=config["region"])
    s3 = boto3.client("s3", region_name=config["region"])

    try:
        control.delete_agent_runtime(agentRuntimeId=config["runtime_id"])
        print(f"  Deleted runtime: {config['agent_name']} ✓")
    except Exception as e:
        print(f"  Runtime delete: {e}")

    role_name = config["role_arn"].split("/")[-1]
    try:
        for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted role: {role_name} ✓")
    except Exception as e:
        print(f"  Role delete: {e}")

    try:
        bucket = config["s3_bucket"]
        for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket)
        print(f"  Deleted S3 bucket: {bucket} ✓")
    except Exception as e:
        print(f"  S3 cleanup: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AgentCore Runtime with Okta inbound auth")
    parser.add_argument("--cleanup", action="store_true", help="Delete created resources")
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Only run tests using existing runtime_config.json",
    )
    args = parser.parse_args()

    if args.cleanup:
        print("\n=== Cleaning Up ===")
        cleanup()
        return

    if args.test_only:
        with open(RUNTIME_CONFIG_FILE) as f:
            config = json.load(f)
        test_agent(config["endpoint_url"])
        return

    print("=== AgentCore Runtime: Okta Inbound Auth ===\n")

    role_name = f"agentcore-okta-inbound-{ACCOUNT_ID}-role"

    print("=== Step 1: Creating IAM Execution Role ===")
    role_arn = create_execution_role(role_name)

    print("\n=== Step 2: Uploading Agent Code to S3 ===")
    s3_info = upload_agent_to_s3()

    print("\n=== Step 3: Creating AgentCore Runtime with Okta JWT Authorizer ===")
    config = create_runtime(role_arn, s3_info)

    print("\n=== Step 4: Testing Agent Invocation ===")
    test_agent(config["endpoint_url"])

    print("\n=== Summary ===")
    print(f"  Runtime: {config['agent_name']}")
    print(f"  Discovery URL: {config['discovery_url']}")
    print(f"  Audience: {config['audience']}")
    print("\n  To clean up: python okta_inbound_auth.py --cleanup")


if __name__ == "__main__":
    main()
