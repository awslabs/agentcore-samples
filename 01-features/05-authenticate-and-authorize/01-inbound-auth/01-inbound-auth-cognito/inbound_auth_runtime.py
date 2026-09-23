"""
Inbound Auth with Amazon Cognito and AgentCore Runtime.

Demonstrates how to configure a Strands agent on AgentCore Runtime to require
JWT bearer token authentication using Amazon Cognito as the identity provider.

Key concepts:
- Cognito User Pool as the OAuth2/OIDC identity provider
- customJWTAuthorizer on AgentCore Runtime for inbound auth
- Invoking the runtime without auth shows AccessDeniedException
- Invoking with a valid Cognito access token grants access

Usage:
    python inbound_auth_runtime.py [--skip-deploy] [--cleanup]

Prerequisites:
    - AWS CLI configured with credentials
    - AgentCore Runtime access
    - pip install -r requirements.txt
    - uv installed (https://docs.astral.sh/uv/getting-started/installation/)
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile

import boto3
from boto3.session import Session
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

AGENT_NAME = f"inbound_auth_cognito_{int(time.time()) % 100000}"
PYTHON_RUNTIME = "PYTHON_3_13"
ENTRY_POINT = "strands_agent.py"
STATE_FILE = "runtime_config.json"
TEST_PROMPT = "How is the weather now?"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-east-1"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
S3_PREFIX = f"{AGENT_NAME}/code.zip"

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")
print(f"Agent:   {AGENT_NAME}")


# ── Agent Code ─────────────────────────────────────────────────────────────────

AGENT_CODE = '''"""
Strands agent with calculator and weather tools, hosted on AgentCore Runtime.
Requires a valid inbound JWT bearer token (Cognito) for authentication.
"""
from strands import Agent, tool
from strands_tools import calculator
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@tool
def weather():
    """Get current weather."""
    return "sunny"


model = BedrockModel(
    model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
)
agent = Agent(
    model=model,
    tools=[calculator, weather],
    system_prompt=(
        "You're a helpful assistant. "
        "You can do simple math calculations and tell the weather."
    ),
)


@app.entrypoint
def strands_agent(payload):
    user_input = payload.get("prompt")
    print(f"User input: {user_input}")
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
'''


# ── Step 1: Set up Amazon Cognito User Pool ────────────────────────────────────


def setup_cognito_user_pool(pool_name: str = None) -> dict:
    """Create a Cognito User Pool with an app client and a test user.

    Returns a dict with: user_pool_id, client_id, discovery_url,
    domain, username, password.
    """
    if pool_name is None:
        pool_name = f"agentcore-inbound-{int(time.time()) % 100000}"

    cognito = boto3.client("cognito-idp", region_name=REGION)

    # Create user pool
    pool_resp = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={
            "PasswordPolicy": {
                "MinimumLength": 8,
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": False,
            }
        },
        Schema=[
            {
                "Name": "email",
                "AttributeDataType": "String",
                "Required": True,
                "Mutable": True,
            }
        ],
    )
    user_pool_id = pool_resp["UserPool"]["Id"]
    print(f"  Created Cognito User Pool: {user_pool_id}")

    # Create domain
    domain = f"agentcore-{pool_name.lower().replace('_', '-')[:30]}"
    try:
        cognito.create_user_pool_domain(UserPoolId=user_pool_id, Domain=domain)
        print(f"  Cognito domain: {domain}")
    except cognito.exceptions.InvalidParameterException:
        # Domain already exists - use a shorter random one
        domain = f"ac-{uuid.uuid4().hex[:10]}"
        cognito.create_user_pool_domain(UserPoolId=user_pool_id, Domain=domain)

    # Create app client
    client_resp = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=f"{pool_name}-client",
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        GenerateSecret=False,
    )
    client_id = client_resp["UserPoolClient"]["ClientId"]
    print(f"  App client ID: {client_id}")

    # Create test user
    username = "testuser"
    password = "MyPassword123!"  # pragma: allowlist secret
    email = "testuser@example.com"

    cognito.admin_create_user(
        UserPoolId=user_pool_id,
        Username=username,
        TemporaryPassword=password,
        UserAttributes=[{"Name": "email", "Value": email}],
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    print(f"  Test user created: {username} / {password}")  # codeql[py/clear-text-logging-sensitive-data]

    discovery_url = f"https://cognito-idp.{REGION}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

    return {
        "user_pool_id": user_pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
        "domain": domain,
        "username": username,
        "password": password,
    }


def reauthenticate_user(cognito_config: dict) -> str:
    """Authenticate the test user and return a fresh access token."""
    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth_resp = cognito.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={
            "USERNAME": cognito_config["username"],
            "PASSWORD": cognito_config["password"],
        },
        ClientId=cognito_config["client_id"],
    )
    token = auth_resp["AuthenticationResult"]["AccessToken"]
    print(f"  Access token obtained: {token[:40]}...")
    return token


# ── Step 2: Create IAM Execution Role ──────────────────────────────────────────


def create_execution_role() -> str:
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{AGENT_NAME}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT_ID}},
            }
        ],
    }
    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogStreams", "logs:CreateLogGroup"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:DescribeLogGroups"],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:*"],
            },
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
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
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        role_arn = resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="agentcore-execution",
        PolicyDocument=json.dumps(inline_policy),
    )
    print(f"  Execution role: {role_arn}")
    time.sleep(15)  # IAM cross-service propagation
    return role_arn


# ── Step 3: Build and upload code zip ─────────────────────────────────────────


def build_and_upload_zip() -> tuple[str, str]:
    """Build arm64 deployment zip with uv, upload to S3 under (bucket, prefix).

    AgentCore Runtime executes containers on ARM64 (Graviton). uv downloads
    the matching wheels and we bundle them with the agent code; the runtime
    extracts the zip into the managed image at start.

    See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
    """
    sample_dir = os.path.dirname(os.path.abspath(__file__))
    requirements = os.path.join(sample_dir, "requirements.txt")

    build_dir = tempfile.mkdtemp(prefix="agentcore-build-")
    pkg_dir = os.path.join(build_dir, "deployment_package")
    zip_path = os.path.join(build_dir, "code.zip")
    os.makedirs(pkg_dir)

    try:
        # Write the agent code into the package dir.
        agent_file = os.path.join(pkg_dir, ENTRY_POINT)
        with open(agent_file, "w") as f:
            f.write(AGENT_CODE)

        # Download arm64-compatible wheels.
        # --python-platform aarch64-manylinux2014 : targets ARM64 Linux (Graviton)
        # --python-version 3.13                   : matches PYTHON_RUNTIME
        # --only-binary :all:                     : prebuilt wheels only, no source builds
        # --target pkg_dir                        : install into package dir, not site-packages
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

        # Zip everything (deps + agent code) at the root of the archive.
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(pkg_dir):
                for fname in files:
                    abs_path = os.path.join(root, fname)
                    arc_name = os.path.relpath(abs_path, pkg_dir)
                    zf.write(abs_path, arc_name)

        # Upload to S3.
        s3 = boto3.client("s3", region_name=REGION)
        try:
            if REGION == "us-east-1":
                s3.create_bucket(Bucket=S3_BUCKET)
            else:
                s3.create_bucket(
                    Bucket=S3_BUCKET,
                    CreateBucketConfiguration={"LocationConstraint": REGION},
                )
        except (
            s3.exceptions.BucketAlreadyOwnedByYou,
            s3.exceptions.BucketAlreadyExists,
        ):
            pass

        s3.upload_file(zip_path, S3_BUCKET, S3_PREFIX)
        print(f"  Uploaded code to s3://{S3_BUCKET}/{S3_PREFIX}")
        return (S3_BUCKET, S3_PREFIX)

    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


# ── Step 4: Create AgentCore Runtime with inbound auth ─────────────────────────


def _create_runtime_with_retry(control, **kwargs):
    """Retry create_agent_runtime to absorb IAM role propagation race.

    The control plane briefly returns ValidationException ("role cannot be
    assumed by ...") before the role is fully propagated across services.
    Backoff: 4, 8, 16, 32, 64 seconds.
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


def create_runtime_with_inbound_auth(role_arn: str, s3_bucket: str, s3_prefix: str, cognito_config: dict) -> dict:
    """Create AgentCore Runtime configured for Cognito JWT inbound auth."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    response = _create_runtime_with_retry(
        control,
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": s3_bucket, "prefix": s3_prefix}},
                "runtime": PYTHON_RUNTIME,
                "entryPoint": [ENTRY_POINT],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": cognito_config["discovery_url"],
                "allowedClients": [cognito_config["client_id"]],
            }
        },
    )

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"  Runtime created: {runtime_id}")

    # Wait for READY
    end_states = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"  Status: {status}")
        if status in end_states:
            break
        time.sleep(15)

    if status != "READY":
        raise RuntimeError(f"Runtime creation failed with status: {status}")

    print(f"  Runtime READY: {runtime_arn}")
    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


# ── Step 5: Invoke runtime ─────────────────────────────────────────────────────


def invoke_runtime(runtime_arn: str, prompt: str, bearer_token: str = None) -> str:
    """Invoke the AgentCore Runtime, optionally with a Cognito bearer token.

    For runtimes configured with `customJWTAuthorizer`, AgentCore expects the
    JWT in an `Authorization: Bearer <jwt>` header instead of SigV4. boto3's
    `invoke_agent_runtime` doesn't accept the token as a parameter, so we hook
    `before-send` to inject the header on the wire (and skip SigV4 signing).
    Pattern matches `03-m2m-3lo/invoke.py`.
    """
    data_plane = boto3.client("bedrock-agentcore", region_name=REGION)

    if bearer_token:

        def _inject_bearer(request, **_):
            request.headers["Authorization"] = f"Bearer {bearer_token}"

        data_plane.meta.events.register("before-send.bedrock-agentcore.InvokeAgentRuntime", _inject_bearer)

    response = data_plane.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}),
        runtimeSessionId=str(uuid.uuid4()),
    )

    chunks = []
    for event in response.get("response", []):
        if isinstance(event, (bytes, bytearray)):
            chunks.append(event.decode("utf-8"))
        elif isinstance(event, str):
            chunks.append(event)
        elif isinstance(event, dict) and "chunk" in event:
            chunks.append(event["chunk"].get("bytes", b"").decode("utf-8"))

    return "".join(chunks)


# ── Cleanup ────────────────────────────────────────────────────────────────────


def cleanup(state: dict):
    """Delete all resources created during the demo."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    cognito = boto3.client("cognito-idp", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)  # noqa: F841

    if state.get("runtime_id"):
        try:
            control.delete_agent_runtime(agentRuntimeId=state["runtime_id"])
            print(f"  Deleted runtime: {state['runtime_id']}")
        except Exception as e:
            print(f"  Runtime delete error: {e}")

    cognito_config = state.get("cognito_config", {})
    user_pool_id = cognito_config.get("user_pool_id")
    if user_pool_id:
        try:
            pool_info = cognito.describe_user_pool(UserPoolId=user_pool_id)
            domain = pool_info["UserPool"].get("Domain", "")
            if domain:
                cognito.delete_user_pool_domain(UserPoolId=user_pool_id, Domain=domain)
            cognito.delete_user_pool(UserPoolId=user_pool_id)
            print(f"  Deleted Cognito pool: {user_pool_id}")
        except Exception as e:
            print(f"  Cognito delete error: {e}")

    role_name = f"agentcore-{state.get('agent_name', AGENT_NAME)}-role"
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="agentcore-execution")
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM role: {role_name}")
    except Exception as e:
        print(f"  IAM delete error: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Inbound Auth with Cognito demo")
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Skip deployment and use existing state.json",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete all resources from a previous run",
    )
    args = parser.parse_args()

    state = {}

    if args.cleanup:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
        print("\n=== Cleaning up resources ===")
        cleanup(state)
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        print("Cleanup complete.")
        return

    if args.skip_deploy and os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        cognito_config = state["cognito_config"]
        runtime_arn = state["runtime_arn"]
        print(f"Using existing runtime: {runtime_arn}")
    else:
        # ── 1. Cognito setup ─────────────────────────────────────────────────
        print("\n=== Step 1: Setting up Amazon Cognito User Pool ===")
        cognito_config = setup_cognito_user_pool()

        # ── 2. IAM role ──────────────────────────────────────────────────────
        print("\n=== Step 2: Creating IAM Execution Role ===")
        role_arn = create_execution_role()

        # ── 3. Build & upload ────────────────────────────────────────────────
        print("\n=== Step 3: Building and Uploading Code ===")
        s3_bucket, s3_prefix = build_and_upload_zip()

        # ── 4. Create runtime with inbound auth ──────────────────────────────
        print("\n=== Step 4: Creating AgentCore Runtime with Inbound Auth ===")
        runtime_info = create_runtime_with_inbound_auth(role_arn, s3_bucket, s3_prefix, cognito_config)
        runtime_arn = runtime_info["runtime_arn"]

        state = {
            "agent_name": AGENT_NAME,
            "runtime_id": runtime_info["runtime_id"],
            "runtime_arn": runtime_arn,
            "role_arn": role_arn,
            "s3_bucket": s3_bucket,
            "s3_prefix": s3_prefix,
            "cognito_config": cognito_config,
        }
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        print(f"  State saved to {STATE_FILE}")

    # ── 5. Test: invoke WITHOUT auth ─────────────────────────────────────────
    print("\n=== Step 5: Testing — Invoke WITHOUT authorization ===")
    print("Expected: AccessDeniedException")
    try:
        result = invoke_runtime(runtime_arn, TEST_PROMPT)
        print(f"  Unexpected success: {result}")
    except Exception as e:
        print(f"  Got expected error: {type(e).__name__}: {e}")

    # ── 6. Get Cognito access token ──────────────────────────────────────────
    print("\n=== Step 6: Obtaining Cognito Access Token ===")
    bearer_token = reauthenticate_user(cognito_config)

    # ── 7. Test: invoke WITH auth ────────────────────────────────────────────
    print("\n=== Step 7: Testing — Invoke WITH authorization ===")
    result = invoke_runtime(runtime_arn, TEST_PROMPT, bearer_token=bearer_token)
    print(f"  Agent response: {result}")

    print("\n=== Demo Complete ===")
    print(f"Runtime ARN: {runtime_arn}")
    print(f"State saved to: {STATE_FILE}")
    print(f"\nTo clean up: python {os.path.basename(__file__)} --cleanup")


if __name__ == "__main__":
    main()
