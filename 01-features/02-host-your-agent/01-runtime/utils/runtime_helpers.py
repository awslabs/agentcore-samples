"""
Shared helpers for deploying, invoking, and cleaning up AgentCore Runtimes.

Uses boto3 with the `bedrock-agentcore` (data plane) and
`bedrock-agentcore-control` (control plane) clients directly.

Deployment uses direct code upload (zip to S3) — no Docker required.
"""

import io
import json
import os
import time
import zipfile

import boto3
from boto3.session import Session


def get_account_info(region: str | None = None) -> tuple[str, str]:
    """Return (account_id, region) for the current AWS session."""
    session = Session(region_name=region)
    region = session.region_name
    account_id = session.client("sts").get_caller_identity()["Account"]
    return account_id, region


def create_execution_role(
    agent_name: str,
    region: str,
    additional_policy_statements: list[dict] | None = None,
) -> str:
    """Create an IAM execution role for an AgentCore Runtime agent.

    Returns the role ARN. If the role already exists, returns the existing ARN.
    """
    iam = boto3.client("iam", region_name=region)
    role_name = f"agentcore-{agent_name}-role"
    account_id, _ = get_account_info(region)

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }

    # Base permissions for Bedrock model invocation and logging
    policy_statements = [
        {
            "Sid": "BedrockPermissions",
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
            ],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": "arn:aws:logs:*:*:*",
        },
    ]

    if additional_policy_statements:
        policy_statements.extend(additional_policy_statements)

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": policy_statements,
    }

    try:
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Execution role for AgentCore Runtime agent: {agent_name}",
        )
        role_arn = response["Role"]["Arn"]
        print(f"Created IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        print(f"IAM role already exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{agent_name}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    # Allow time for IAM propagation
    print("Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return role_arn


def zip_and_upload_code(
    agent_name: str,
    source_files: list[str],
    region: str,
) -> tuple[str, str]:
    """Zip source files and upload to S3 for direct code deployment.

    Creates an S3 bucket if needed, zips the specified files, and uploads.

    Args:
        agent_name: Used to name the S3 bucket and key prefix.
        source_files: List of local file paths to include in the zip.
        region: AWS region.

    Returns:
        Tuple of (bucket_name, s3_prefix) for use in codeConfiguration.
    """
    account_id, _ = get_account_info(region)
    s3 = boto3.client("s3", region_name=region)

    bucket_name = f"agentcore-code-{account_id}-{region}"
    s3_prefix = f"{agent_name}/code.zip"

    # Create bucket if it doesn't exist
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        print(f"Created S3 bucket: {bucket_name}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"S3 bucket exists: {bucket_name}")
    except s3.exceptions.BucketAlreadyExists:
        print(f"S3 bucket exists: {bucket_name}")

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in source_files:
            arcname = os.path.basename(file_path)
            zf.write(file_path, arcname)
    zip_buffer.seek(0)

    # Upload to S3
    s3.put_object(Bucket=bucket_name, Key=s3_prefix, Body=zip_buffer.getvalue())
    print(f"Uploaded code to s3://{bucket_name}/{s3_prefix}")

    return bucket_name, s3_prefix


def create_agent_runtime(
    agent_name: str,
    role_arn: str,
    region: str,
    s3_bucket: str,
    s3_prefix: str,
    entry_point: str,
    python_runtime: str = "PYTHON_3_12",
    protocol: str = "HTTP",
    environment_variables: dict | None = None,
    description: str = "",
) -> dict:
    """Create an AgentCore Runtime using direct code deployment.

    Args:
        agent_name: Name for the runtime.
        role_arn: IAM execution role ARN.
        region: AWS region.
        s3_bucket: S3 bucket containing the code zip.
        s3_prefix: S3 key for the code zip.
        entry_point: Python file to run (e.g., "agent.py").
        python_runtime: Runtime version — PYTHON_3_12, PYTHON_3_13, etc.
        protocol: Server protocol — HTTP, MCP, A2A, or AGUI.
        environment_variables: Optional env vars for the runtime.
        description: Optional description.

    Returns:
        The create_agent_runtime API response.
    """
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    params = {
        "agentRuntimeName": agent_name,
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {
                    "s3": {
                        "bucket": s3_bucket,
                        "prefix": s3_prefix,
                    }
                },
                "runtime": python_runtime,
                "entryPoint": [entry_point],
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": protocol},
    }

    if description:
        params["description"] = description
    if environment_variables:
        params["environmentVariables"] = environment_variables

    response = control.create_agent_runtime(**params)
    print(f"Creating runtime '{agent_name}' — status: {response['status']}")
    print(f"  ARN: {response['agentRuntimeArn']}")
    return response


def wait_for_runtime_ready(
    agent_runtime_id: str,
    region: str,
    timeout: int = 600,
    poll_interval: int = 15,
) -> dict:
    """Poll until the runtime reaches READY status or times out."""
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    elapsed = 0

    while elapsed < timeout:
        response = control.get_agent_runtime(agentRuntimeId=agent_runtime_id)
        status = response["status"]
        print(f"  Runtime status: {status} ({elapsed}s elapsed)")

        if status == "READY":
            return response
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            reason = response.get("failureReason", "Unknown")
            raise RuntimeError(f"Runtime failed: {reason}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Runtime did not become READY within {timeout}s")


def create_runtime_endpoint(
    agent_runtime_id: str,
    endpoint_name: str,
    region: str,
) -> dict:
    """Create an endpoint for an AgentCore Runtime."""
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    response = control.create_agent_runtime_endpoint(
        agentRuntimeId=agent_runtime_id,
        name=endpoint_name,
    )
    print(f"Creating endpoint '{endpoint_name}' — status: {response['status']}")
    print(f"  Endpoint ARN: {response['agentRuntimeEndpointArn']}")
    return response


def wait_for_endpoint_ready(
    agent_runtime_id: str,
    region: str,
    timeout: int = 600,
    poll_interval: int = 15,
) -> dict:
    """Poll until the endpoint reaches READY status."""
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    elapsed = 0

    while elapsed < timeout:
        endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=agent_runtime_id)
        if endpoints.get("runtimeEndpoints"):
            ep = endpoints["runtimeEndpoints"][0]
            status = ep["status"]
            print(f"  Endpoint status: {status} ({elapsed}s elapsed)")
            if status == "READY":
                return ep
            if status in ("CREATE_FAILED", "UPDATE_FAILED"):
                raise RuntimeError(f"Endpoint failed with status: {status}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Endpoint did not become READY within {timeout}s")


def invoke_agent(
    agent_runtime_arn: str,
    payload: dict,
    region: str,
    session_id: str | None = None,
    content_type: str = "application/json",
    accept: str = "application/json",
) -> dict:
    """Invoke an agent hosted on AgentCore Runtime.

    Returns the parsed JSON response.
    """
    client = boto3.client("bedrock-agentcore", region_name=region)

    params = {
        "agentRuntimeArn": agent_runtime_arn,
        "payload": json.dumps(payload).encode("utf-8"),
        "contentType": content_type,
        "accept": accept,
    }
    if session_id:
        params["runtimeSessionId"] = session_id

    response = client.invoke_agent_runtime(**params)

    body = response["response"].read().decode("utf-8")
    return {
        "session_id": response.get("runtimeSessionId"),
        "status_code": response.get("statusCode"),
        "body": json.loads(body) if body.startswith("{") or body.startswith("[") else body,
    }


def invoke_agent_streaming(
    agent_runtime_arn: str,
    payload: dict,
    region: str,
    session_id: str | None = None,
):
    """Invoke an agent and stream the response using SSE.

    Yields decoded chunks as they arrive.
    """
    client = boto3.client("bedrock-agentcore", region_name=region)

    params = {
        "agentRuntimeArn": agent_runtime_arn,
        "payload": json.dumps(payload).encode("utf-8"),
        "contentType": "application/json",
        "accept": "text/event-stream",
    }
    if session_id:
        params["runtimeSessionId"] = session_id

    response = client.invoke_agent_runtime(**params)

    for line in response["response"].iter_lines():
        decoded = line.decode("utf-8") if isinstance(line, bytes) else line
        if decoded.startswith("data:"):
            yield decoded[5:].strip()


def delete_agent_runtime(agent_runtime_id: str, region: str) -> None:
    """Delete an AgentCore Runtime and its endpoints."""
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    # Delete endpoints first
    try:
        endpoints = control.list_agent_runtime_endpoints(agentRuntimeId=agent_runtime_id)
        for ep in endpoints.get("runtimeEndpoints", []):
            ep_name = ep["name"]
            print(f"Deleting endpoint: {ep_name}")
            control.delete_agent_runtime_endpoint(
                agentRuntimeId=agent_runtime_id,
                endpointName=ep_name,
            )
    except Exception as e:
        print(f"Warning: could not delete endpoints: {e}")

    # Delete the runtime
    print(f"Deleting runtime: {agent_runtime_id}")
    control.delete_agent_runtime(agentRuntimeId=agent_runtime_id)
    print("Runtime deletion initiated.")


def delete_execution_role(agent_name: str, region: str) -> None:
    """Delete the IAM execution role created for an agent."""
    iam = boto3.client("iam", region_name=region)
    role_name = f"agentcore-{agent_name}-role"

    try:
        # Remove inline policies
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        iam.delete_role(RoleName=role_name)
        print(f"Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"IAM role not found: {role_name}")


def delete_s3_code(agent_name: str, region: str) -> None:
    """Delete the S3 code artifact for an agent."""
    account_id, _ = get_account_info(region)
    s3 = boto3.client("s3", region_name=region)
    bucket_name = f"agentcore-code-{account_id}-{region}"
    s3_prefix = f"{agent_name}/code.zip"

    try:
        s3.delete_object(Bucket=bucket_name, Key=s3_prefix)
        print(f"Deleted s3://{bucket_name}/{s3_prefix}")
    except Exception as e:
        print(f"Warning: could not delete S3 object: {e}")
