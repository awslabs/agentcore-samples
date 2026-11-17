"""Deploy and clean up the samples in this folder (client side; not copied into the zips).

Every sample deploys the same way: a code zip with an arm64 Python 3.13 build of the server's
requirements, an IAM execution role, and an agent runtime with serverProtocol=MCP and
platformVersion=V2. Inbound auth is IAM (SigV4) unless a sample passes a JWT authorizer.
"""

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

HELPERS = Path(__file__).resolve().parent
RUNTIME_HELPERS = ["runtime_utils.py", "dynamo_utils.py"]  # copied into every zip
PYTHON = "PYTHON_3_13"
CONFIG_FILE = "runtime_config.json"

session = boto3.Session()
REGION = session.region_name
if not REGION:
    sys.exit("Set AWS_REGION (for example, export AWS_REGION=us-west-2). AgentCore Runtime is regional.")
ACCOUNT = session.client("sts").get_caller_identity()["Account"]
BUCKET = f"agentcore-code-{ACCOUNT}-{REGION}"
control = session.client("bedrock-agentcore-control")


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------- shared resources
def ensure_bucket() -> None:
    s3 = session.client("s3")
    for attempt in range(6):
        try:
            # Look before creating: concurrent CreateBucket calls fail with OperationAborted, even on an existing bucket.
            s3.head_bucket(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)
            break
        except ClientError as error:
            if error.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                sys.exit(f"✗ Bucket {BUCKET} belongs to another account. Change BUCKET in helpers/deploy_utils.py.")
        try:
            if REGION == "us-east-1":
                s3.create_bucket(Bucket=BUCKET)
            else:
                s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
            break
        except s3.exceptions.BucketAlreadyOwnedByYou:
            break
        except s3.exceptions.BucketAlreadyExists:
            sys.exit(f"✗ Bucket {BUCKET} belongs to another account. Change BUCKET in helpers/deploy_utils.py.")
        except ClientError as error:
            # Another deploy is creating the same bucket right now: wait, then look again.
            if error.response["Error"]["Code"] != "OperationAborted" or attempt == 5:
                raise
            time.sleep(5)
    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def ensure_table(name: str) -> str:
    dynamodb = session.client("dynamodb")
    try:
        arn = dynamodb.describe_table(TableName=name)["Table"]["TableArn"]
    except dynamodb.exceptions.ResourceNotFoundException:
        arn = dynamodb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )["TableDescription"]["TableArn"]
        dynamodb.get_waiter("table_exists").wait(TableName=name)
    log(f"✓ DynamoDB table {name}")
    return arn


def ensure_secret(name: str) -> str:
    """The key that seals MRTR requestState. Every microVM of the runtime must read the same key."""
    secrets = session.client("secretsmanager")
    try:
        arn = secrets.describe_secret(SecretId=name)["ARN"]
    except secrets.exceptions.ResourceNotFoundException:
        password = secrets.get_random_password(PasswordLength=64, ExcludePunctuation=True)["RandomPassword"]
        arn = secrets.create_secret(
            Name=name, SecretString=password, Description="Seals MCP requestState (multi round-trip requests)"
        )["ARN"]
    log(f"✓ Secret {name}")
    return arn


def ensure_role(name: str, table_arn: str | None = None, secret_arn: str | None = None) -> str:
    iam = session.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}},
            }
        ],
    }
    logs = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/*"
    statements: list[dict[str, Any]] = [
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:DescribeLogStreams"], "Resource": logs},
        {
            "Effect": "Allow",
            "Action": "logs:DescribeLogGroups",
            "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:*",
        },
        {
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"{logs}:log-stream:*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": "cloudwatch:PutMetricData",
            "Resource": "*",
            "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        },
    ]
    if table_arn:
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"],
                "Resource": table_arn,
            }
        )
    if secret_arn:
        statements.append({"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn})
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        new = True
    except iam.exceptions.EntityAlreadyExistsException:
        arn, new = iam.get_role(RoleName=name)["Role"]["Arn"], False
    iam.put_role_policy(
        RoleName=name,
        PolicyName="runtime",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}),
    )
    if new:
        time.sleep(10)  # IAM propagation
    log(f"✓ IAM role {name}")
    return arn


def find_user_pool_id(name: str) -> str | None:
    cognito = session.client("cognito-idp")
    for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == name:
                return pool["Id"]
    return None


def ensure_cognito(name: str, username: str = "demo-user") -> dict[str, str]:
    """A Cognito user pool, an app client, and one demo user with a generated password, for JWT inbound auth."""
    cognito = session.client("cognito-idp")
    pool_id = find_user_pool_id(name)
    if pool_id is None:
        pool_id = cognito.create_user_pool(
            PoolName=name,
            Policies={"PasswordPolicy": {"MinimumLength": 16}},
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )["UserPool"]["Id"]
    clients = cognito.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]
    client_id = next((c["ClientId"] for c in clients if c["ClientName"] == f"{name}-client"), None)
    if client_id is None:
        client_id = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=f"{name}-client",
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            GenerateSecret=False,
        )["UserPoolClient"]["ClientId"]
    try:
        cognito.admin_create_user(UserPoolId=pool_id, Username=username, MessageAction="SUPPRESS")
    except cognito.exceptions.UsernameExistsException:
        pass
    password = secrets.token_urlsafe(24) + "aA1!"  # a new one on every deploy; deploy.py saves it locally
    cognito.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=True)
    log(f"✓ Cognito user pool {name} with user {username}")
    return {
        "user_pool_id": pool_id,
        "client_id": client_id,
        "username": username,
        "password": password,
        "discovery_url": f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
    }


def jwt_authorizer(cognito: dict[str, str]) -> dict[str, Any]:
    """AgentCore Runtime's inbound JWT authorizer, accepting tokens issued to the Cognito app client."""
    return {"customJWTAuthorizer": {"discoveryUrl": cognito["discovery_url"], "allowedClients": [cognito["client_id"]]}}


# ---------------------------------------------------------------- code zip
def _uv() -> list[str]:
    if importlib.util.find_spec("uv"):
        return [sys.executable, "-m", "uv"]
    if shutil.which("uv"):
        return ["uv"]
    sys.exit("uv is required to build arm64 wheels: pip install uv")


def upload_code(
    sample_dir: Path,
    key: str,
    agents: str = "agents",
    requirements: str = "requirements.txt",
    helpers: list[str] | None = None,
) -> None:
    """Zip a server's *.py, the runtime helpers it needs, and an arm64 build of its requirements; upload to S3.

    05-compatibility-e2e passes agents_v1 and requirements-v1.txt, with helpers=[], to package an MCP
    Python SDK v1 server: the shared runtime helpers import MCPServer, which only SDK v2 has.
    """
    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp) / "package"
        subprocess.run(
            [
                *_uv(),
                "pip",
                "install",
                "--quiet",
                "--python-platform",
                "aarch64-manylinux2014",
                "--python-version",
                "3.13",
                "--only-binary",
                ":all:",
                "--target",
                str(package),
                "-r",
                str(sample_dir / requirements),
            ],
            check=True,
        )
        copied = [*sorted((sample_dir / agents).glob("*.py"))]
        copied += [HELPERS / f for f in (RUNTIME_HELPERS if helpers is None else helpers)]
        for source in copied:
            shutil.copy(source, package / source.name)
        archive = Path(tmp) / "code.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(package.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    zf.write(path, path.relative_to(package))
        session.client("s3").upload_file(str(archive), BUCKET, key)
        log(f"✓ Code s3://{BUCKET}/{key} ({archive.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- runtime
def find_runtime_id(name: str) -> str | None:
    args: dict[str, Any] = {"maxResults": 100}
    while True:
        page = control.list_agent_runtimes(**args)
        for runtime in page["agentRuntimes"]:
            if runtime["agentRuntimeName"] == name:
                return runtime["agentRuntimeId"]
        if not page.get("nextToken"):
            return None
        args["nextToken"] = page["nextToken"]


def deploy_runtime(
    name: str,
    role_arn: str,
    code_key: str,
    entry_point: str,
    env: dict[str, str] | None = None,
    authorizer: dict[str, Any] | None = None,
    idle_timeout: int | None = None,
) -> dict[str, str]:
    """Create the runtime, or update it if it exists, and wait for READY."""
    args: dict[str, Any] = {
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": BUCKET, "prefix": code_key}},
                "runtime": PYTHON,
                "entryPoint": [entry_point],
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "MCP"},
        "platformVersion": "V2",
        # 0.0.0.0 inside the microVM, as AgentCore Runtime requires; the runtime authenticates every request.
        "environmentVariables": {"MCP_HOST": "0.0.0.0", **(env or {})},  # nosec B104
    }
    if authorizer:
        args["authorizerConfiguration"] = authorizer
    if idle_timeout:  # seconds without requests before AgentCore ends a session (unless /ping says HealthyBusy)
        args["lifecycleConfiguration"] = {"idleRuntimeSessionTimeout": idle_timeout}
    runtime_id = find_runtime_id(name)
    started = time.time()
    if runtime_id:
        control.update_agent_runtime(agentRuntimeId=runtime_id, **args)
        log(f"  Updating {name} …")
    else:
        for attempt in range(6):  # a new role can take a few seconds to become assumable
            try:
                runtime_id = control.create_agent_runtime(agentRuntimeName=name, **args)["agentRuntimeId"]
                break
            except control.exceptions.ValidationException as error:
                if "role" not in str(error).lower() or attempt == 5:
                    raise
                time.sleep(10)
        log(f"  Creating {name} (Runtime V2 prepares a snapshot; this takes a few minutes) …")
    while True:
        runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
        if runtime["status"] == "READY":
            break
        if "FAILED" in runtime["status"]:
            sys.exit(f"✗ {name} {runtime['status']}: {runtime.get('failureReason')}")
        time.sleep(15)
    log(f"✓ Runtime {name} READY in {time.time() - started:.0f}s (platformVersion={runtime.get('platformVersion')})")
    return {"name": name, "runtime_id": runtime_id, "runtime_arn": runtime["agentRuntimeArn"]}


def smoke_test(runtime_arn: str, client: Any = None, version: str | None = None) -> None:
    """AgentCore doesn't run the entry point at create time, so READY doesn't mean the server starts.

    version="2025-11-25" asks as an earlier client, for a server that doesn't speak 2026-07-28.
    """
    sys.path.insert(0, str(HELPERS))
    from mcp_client import LATEST, McpClient

    client = client or McpClient(runtime_arn, REGION, session_id=new_session_id_for_smoke())
    reply = client.request("tools/list", version=version or LATEST)
    if reply.error:
        sys.exit(f"✗ Smoke test: tools/list failed: {reply.error}")
    log(f"✓ Smoke test: {len(reply.result.get('tools', []))} tools")


def new_session_id_for_smoke() -> str:
    sys.path.insert(0, str(HELPERS))
    from mcp_client import new_session_id

    return new_session_id()


def save_config(sample_dir: Path, config: dict[str, Any]) -> None:
    """Write runtime_config.json readable only by you (04 keeps its demo user's password there).

    Each deploy.py saves the resource names first, so cleanup.py can delete whatever exists even when a
    later step fails, and saves again once the runtime is READY.
    """
    path = sample_dir / CONFIG_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"region": REGION, **config}, indent=2))
    path.chmod(0o600)  # also when the file existed with wider permissions


def load_config(sample_dir: Path) -> dict[str, Any]:
    path = sample_dir / CONFIG_FILE
    if not path.exists():
        sys.exit(f"{path} not found. Run python deploy.py first.")
    return json.loads(path.read_text())


# ---------------------------------------------------------------- cleanup
def delete_runtime(name: str) -> None:
    """Delete the runtime with this name, if it exists, and its log groups."""
    runtime_id = find_runtime_id(name)
    if runtime_id is None:
        return
    try:
        control.delete_agent_runtime(agentRuntimeId=runtime_id)  # the DEFAULT endpoint goes with it
    except control.exceptions.ResourceNotFoundException:
        return
    while True:
        try:
            control.get_agent_runtime(agentRuntimeId=runtime_id)
        except control.exceptions.ResourceNotFoundException:
            break
        time.sleep(10)
    logs = session.client("logs")
    for group in logs.describe_log_groups(logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{runtime_id}")[
        "logGroups"
    ]:
        logs.delete_log_group(logGroupName=group["logGroupName"])
    log(f"✓ Deleted runtime {name} ({runtime_id}) and its log groups")


def delete_role(name: str) -> None:
    iam = session.client("iam")
    try:
        for policy in iam.list_role_policies(RoleName=name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=name, PolicyName=policy)
        iam.delete_role(RoleName=name)
        log(f"✓ Deleted IAM role {name}")
    except iam.exceptions.NoSuchEntityException:
        pass


def delete_table(name: str) -> None:
    dynamodb = session.client("dynamodb")
    try:
        dynamodb.delete_table(TableName=name)
        dynamodb.get_waiter("table_not_exists").wait(TableName=name)
        log(f"✓ Deleted DynamoDB table {name}")
    except dynamodb.exceptions.ResourceNotFoundException:
        pass


def delete_secret(name: str) -> None:
    secrets = session.client("secretsmanager")
    try:
        secrets.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        log(f"✓ Deleted secret {name}")
    except secrets.exceptions.ResourceNotFoundException:
        pass


def delete_cognito(name: str) -> None:
    cognito = session.client("cognito-idp")
    user_pool_id = find_user_pool_id(name)
    if user_pool_id is None:
        return
    try:
        cognito.delete_user_pool(UserPoolId=user_pool_id)  # its app clients and users go with it
        log(f"✓ Deleted Cognito user pool {name} ({user_pool_id})")
    except cognito.exceptions.ResourceNotFoundException:
        pass


def delete_code(key: str) -> None:
    s3 = session.client("s3")
    try:
        s3.delete_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchBucket:
        return
    if not s3.list_objects_v2(Bucket=BUCKET, MaxKeys=1).get("KeyCount"):
        s3.delete_bucket(Bucket=BUCKET)
        log(f"✓ Deleted empty bucket {BUCKET}")
