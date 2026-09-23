"""
Entra ID 3-Legged OAuth (Authorization Code Flow) with AgentCore Gateway.

Demonstrates how to configure a Strands agent on AgentCore Runtime with
Microsoft Entra ID USER_FEDERATION (3LO) authentication to access Microsoft
OneNote on behalf of an authenticated user.

Flow:
    1. Create MicrosoftOauth2 credential provider in AgentCore Identity
    2. Deploy strands_entraid_onenote.py to AgentCore Runtime
    3. Start oauth2_callback_server.py to handle OAuth redirect
    4. Invoke agent — agent returns authorization URL on first call
    5. User visits URL, grants consent to OneNote access
    6. oauth2_callback_server binds the session
    7. On re-invocation, agent retrieves OneNote token and creates notebook

Usage:
    python entra_gateway_auth_code.py
    python entra_gateway_auth_code.py --cleanup

Prerequisites:
    - AWS CLI configured
    - Microsoft Entra ID App Registration with OneNote permissions:
        - API Permissions: Microsoft Graph > Notes.ReadWrite.All, Notes.Create (delegated)
        - Redirect URI: AgentCore callback URL (from Step 1 output)
    - pip install -r requirements.txt
    - Set environment variables:
        ENTRA_TENANT_ID       - Directory (tenant) ID
        ENTRA_CLIENT_ID       - Application (client) ID
        ENTRA_CLIENT_SECRET   - Client secret
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
import zipfile

import boto3
from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

PROVIDER_NAME = "microsoft_entra_oauth_provider"
AGENT_NAME = f"entra_3lo_onenote_{int(time.time()) % 100000}"
AGENT_FILE = "strands_entraid_onenote.py"
CONFIG_FILE = "runtime_config_entra_3lo.json"

# Scopes for Microsoft OneNote delegated access
SCOPES = "openid profile https://graph.microsoft.com/Notes.ReadWrite.All https://graph.microsoft.com/Notes.Create"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-west-2"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Step 1: Create MicrosoftOauth2 Credential Provider ────────────────────────


def create_credential_provider() -> dict:
    """Create a MicrosoftOauth2 credential provider for Entra ID 3LO."""
    client_id = os.environ.get("ENTRA_CLIENT_ID")
    client_secret = os.environ.get("ENTRA_CLIENT_SECRET")
    tenant_id = os.environ.get("ENTRA_TENANT_ID")

    if not all([client_id, client_secret, tenant_id]):
        raise ValueError("Set ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, ENTRA_TENANT_ID environment variables.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_oauth2_credential_provider(
            name=PROVIDER_NAME,
            credentialProviderVendor="MicrosoftOauth2",
            oauth2ProviderConfigInput={
                "microsoftOauth2ProviderConfig": {
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "tenantId": tenant_id,
                }
            },
        )
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]
        print(f"  Created credential provider: {PROVIDER_NAME}")
    except (control.exceptions.ConflictException, ClientError) as e:
        # The control plane returns ValidationException("...already exists")
        # instead of ConflictException for duplicate names; treat both as
        # "exists, reuse it".
        if isinstance(e, ClientError):
            code = e.response.get("Error", {}).get("Code", "")
            msg = str(e).lower()
            if not (code == "ValidationException" and "already exists" in msg):
                raise
        resp = control.get_oauth2_credential_provider(name=PROVIDER_NAME)
        provider_arn = resp["credentialProviderArn"]
        callback_url = resp["callbackUrl"]
        print(f"  Reusing credential provider: {PROVIDER_NAME}")

    print(f"  Provider ARN: {provider_arn}")
    print(f"  Callback URL: {callback_url}")
    print(
        "\n  IMPORTANT: Add this callback URL as a Redirect URI in your Entra ID App Registration:\n"
        f"  Azure Portal > App Registrations > your app > Authentication > Redirect URIs\n"
        f"  Add: {callback_url}\n"
        "  Also select: Access tokens + ID tokens under Implicit grant and hybrid flows"
    )
    return {"provider_arn": provider_arn, "callback_url": callback_url}


# ── Step 2: Create IAM Execution Role ─────────────────────────────────────────


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
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
        )
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
                    "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": "arn:aws:secretsmanager:*:*:secret:bedrock-agentcore*",
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
            PolicyName="agentcore-onenote-policy",
            PolicyDocument=policy,
        )
    except Exception:
        pass

    time.sleep(5)
    return role_arn


# ── Step 3: Upload Agent Code to S3 ───────────────────────────────────────────


def upload_agent_to_s3() -> dict:
    """Build agent deployment zip with uv, upload to S3.

    AgentCore Runtime executes containers on ARM64 (Graviton) and enforces a
    30s container init budget. We pre-install dependencies as ARM64 wheels with
    uv and bundle them with the agent code so the runtime can boot without
    running pip at start. See:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
    """
    s3 = boto3.client("s3", region_name=REGION)
    bucket_name = f"agentcore-entra-3lo-{ACCOUNT_ID}-{REGION}"

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
    agent_path = os.path.join(sample_dir, AGENT_FILE)
    callback_path = os.path.join(sample_dir, "oauth2_callback_server.py")
    requirements = os.path.join(sample_dir, "requirements.txt")

    for p in (agent_path, callback_path, requirements):
        if not os.path.exists(p):
            raise FileNotFoundError(f"Required file not found: {p}")

    build_dir = tempfile.mkdtemp(prefix="agentcore-build-")
    pkg_dir = os.path.join(build_dir, "deployment_package")
    zip_path = os.path.join(build_dir, "agent.zip")
    os.makedirs(pkg_dir)

    try:
        # Bundle source files (agent + callback helper imported by agent).
        shutil.copy(agent_path, os.path.join(pkg_dir, AGENT_FILE))
        shutil.copy(callback_path, os.path.join(pkg_dir, "oauth2_callback_server.py"))

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


# ── Step 4: Create AgentCore Runtime ──────────────────────────────────────────


def create_runtime(role_arn: str, s3_info: dict) -> dict:
    """Deploy strands_entraid_onenote.py to AgentCore Runtime."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    response = control.create_agent_runtime(
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
        environmentVariables={
            "scopes": SCOPES,
        },
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  Created runtime: {AGENT_NAME} (ID: {runtime_id})")

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
        "region": REGION,
        "role_arn": role_arn,
        "s3_bucket": s3_info["bucket"],
        "provider_name": PROVIDER_NAME,
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    return config


# ── Step 5: Invoke Agent with OAuth Callback Server ───────────────────────────


def invoke_agent_with_oauth(runtime_arn: str):
    """
    Start the OAuth callback server, invoke the agent.
    On first call the agent returns an authorization URL for user consent.
    After consent, re-invoke to get the OneNote access token and create the notebook.

    The runtime is created without `authorizerConfiguration`, so the data plane
    expects SigV4-signed requests. boto3's `invoke_agent_runtime` signs
    automatically; matches the pattern in 03-m2m-3lo/invoke.py.
    """
    from oauth2_callback_server import wait_for_oauth2_server_to_be_ready

    session_id = str(uuid.uuid4())
    user_id = "entra-3lo-user"

    prompt = (
        "Put these notes into a OneNote notebook named 'Bedrock Agents'. "
        "Amazon Bedrock AgentCore enables you to deploy and operate highly capable "
        "AI agents securely, at scale. Provide a link to the created OneNote Notebook."
    )

    # Start oauth2 callback server
    oauth_proc = subprocess.Popen([sys.executable, "oauth2_callback_server.py", "--region", REGION])

    try:
        ok = wait_for_oauth2_server_to_be_ready()
        if not ok:
            print("  Failed to start OAuth2 callback server.")
            return

        # The agent calls @requires_access_token under the hood, which polls
        # AgentCore Identity for token completion after returning the auth
        # URL. The polling extends the streaming response well beyond the
        # 60s boto3 read default; bump it so the user has time to complete
        # OAuth in a browser.
        client = boto3.client(
            "bedrock-agentcore",
            region_name=REGION,
            config=Config(read_timeout=300, connect_timeout=10),
        )
        print("  Invoking agent (first call — will return authorization URL)...")
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier="DEFAULT",
            runtimeSessionId=session_id,
            runtimeUserId=user_id,
            payload=json.dumps({"prompt": prompt, "user_id": user_id}),
        )

        # Stream chunks to stdout as they arrive. The agent's first useful
        # message is "Authorization URL: ...", emitted ~4s into the request.
        # We iterate the raw stream (not the buffered .read()) and flush each
        # decoded chunk immediately so the user can copy the URL into a
        # browser while the agent's polling loop continues server-side.
        print("  Streaming response (Ctrl+C to abort):", flush=True)
        print("  ── stream begin ──", flush=True)
        body = resp.get("response")
        if hasattr(body, "iter_lines"):
            iterator = body.iter_lines(chunk_size=1)
        else:
            iterator = body
        for event in iterator:
            if isinstance(event, (bytes, bytearray)):
                text = event.decode("utf-8", errors="replace")
            elif isinstance(event, str):
                text = event
            elif isinstance(event, dict) and "chunk" in event:
                text = event["chunk"].get("bytes", b"").decode("utf-8", errors="replace")
            else:
                continue
            if text.strip():
                print(f"  {text}", flush=True)
        print("  ── stream end ──", flush=True)
        print(
            "\n  If an authorization URL was returned, copy it into your browser, "
            "grant consent, then re-run with --test-only to continue."
        )
    finally:
        oauth_proc.terminate()


# ── Step 6: Cleanup ────────────────────────────────────────────────────────────


def cleanup():
    """Delete all created resources."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        print("  No config file found.")
        return

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    try:
        control.delete_agent_runtime(agentRuntimeId=config["runtime_id"])
        print(f"  Deleted runtime: {config['agent_name']} ✓")
    except Exception as e:
        print(f"  Runtime delete: {e}")

    try:
        control.delete_oauth2_credential_provider(name=config["provider_name"])
        print(f"  Deleted credential provider: {config['provider_name']} ✓")
    except Exception as e:
        print(f"  Provider delete: {e}")

    role_name = config["role_arn"].split("/")[-1]
    try:
        for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM role: {role_name} ✓")
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
    parser = argparse.ArgumentParser(description="AgentCore Runtime: Entra ID 3LO with OneNote")
    parser.add_argument("--cleanup", action="store_true", help="Delete created resources")
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Only invoke agent using existing config",
    )
    args = parser.parse_args()

    if args.cleanup:
        print("\n=== Cleaning Up ===")
        cleanup()
        return

    if args.test_only:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        invoke_agent_with_oauth(config["runtime_arn"])
        return

    print("=== AgentCore Runtime: Entra ID 3LO Auth Code Flow (OneNote) ===\n")

    print("=== Step 1: Creating MicrosoftOauth2 Credential Provider ===")
    provider_info = create_credential_provider()  # noqa: F841

    print("\n=== Step 2: Creating IAM Execution Role ===")
    role_name = f"agentcore-entra-3lo-{ACCOUNT_ID}-role"
    role_arn = create_execution_role(role_name)

    print("\n=== Step 3: Uploading Agent Code to S3 ===")
    s3_info = upload_agent_to_s3()

    print("\n=== Step 4: Creating AgentCore Runtime ===")
    config = create_runtime(role_arn, s3_info)

    print("\n=== Step 5: Invoking Agent (with OAuth callback server) ===")
    invoke_agent_with_oauth(config["runtime_arn"])

    print("\n=== Summary ===")
    print(f"  Credential provider: {PROVIDER_NAME}")
    print(f"  Runtime: {AGENT_NAME}")
    print(f"  Config saved to: {CONFIG_FILE}")
    print("\n  To clean up: python entra_gateway_auth_code.py --cleanup")


if __name__ == "__main__":
    main()
