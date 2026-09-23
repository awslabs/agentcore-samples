#!/usr/bin/env python3
"""
deploy_runtime.py — Deploy the Text-to-Python IDE agent to AWS Bedrock AgentCore Runtime.

What this script does:
  1. Creates an ECR repository (if it doesn't exist)
  2. Builds the Docker image from Dockerfile
  3. Pushes the image to ECR
  4. Creates (or updates) the AgentCore Runtime
  5. Creates the DEFAULT endpoint and waits for READY
  6. Prints the runtime ARN and invocation instructions

Usage:
    python deploy_runtime.py              # deploy
    python deploy_runtime.py --teardown   # delete runtime + endpoint
"""

import argparse
import json
import os
import subprocess
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Config ────────────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT_ID = boto3.client("sts").get_caller_identity()["Account"]
RUNTIME_NAME = "text_to_python_ide"
ECR_REPO_NAME = "bedrock-agentcore-text-to-python-ide"
IMAGE_TAG = "latest"
ECR_URI = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO_NAME}"
IMAGE_URI = f"{ECR_URI}:{IMAGE_TAG}"
ENDPOINT_NAME = "DEFAULT"

EXECUTION_ROLE_NAME = "AgentCoreTextToPythonIDERole"
EXECUTION_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{EXECUTION_ROLE_NAME}"

# Memory ID — read from memory_info.json if available
_memory_info_path = os.path.join(os.path.dirname(__file__), "memory_info.json")
MEMORY_ID = ""
if os.path.exists(_memory_info_path):
    with open(_memory_info_path) as _f:
        MEMORY_ID = json.load(_f).get("memory_id", "")

# Guardrail ID — read from guardrail_info.json if available
_guardrail_info_path = os.path.join(os.path.dirname(__file__), "guardrail_info.json")
GUARDRAIL_ID = ""
GUARDRAIL_VERSION = ""
if os.path.exists(_guardrail_info_path):
    with open(_guardrail_info_path) as _f:
        _gi = json.load(_f)
        GUARDRAIL_ID = _gi.get("guardrail_id", "")
        GUARDRAIL_VERSION = _gi.get("guardrail_version", "")


# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd: list[str], **kwargs):
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True, **kwargs)
    return result


def ensure_execution_role(iam_client):
    """Create the AgentCore runtime execution role if it doesn't exist, and ensure all policies are attached."""
    role_exists = False
    try:
        iam_client.get_role(RoleName=EXECUTION_ROLE_NAME)
        role_exists = True
        print(f"✅ Execution role already exists: {EXECUTION_ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    if not role_exists:
        print(f"🔧 Creating execution role: {EXECUTION_ROLE_NAME}...")

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

        iam_client.create_role(
            RoleName=EXECUTION_ROLE_NAME,
            AssumeRolePolicyDocument=trust_policy,
            Description="Execution role for AgentCore Text-to-Python IDE runtime",
        )

    # Always ensure all required policies are attached
    required_policies = [
        "arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
        "arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    ]

    attached = iam_client.list_attached_role_policies(RoleName=EXECUTION_ROLE_NAME)
    attached_arns = {p["PolicyArn"] for p in attached.get("AttachedPolicies", [])}

    for policy_arn in required_policies:
        if policy_arn not in attached_arns:
            iam_client.attach_role_policy(RoleName=EXECUTION_ROLE_NAME, PolicyArn=policy_arn)
            print(f"   Attached: {policy_arn.split('/')[-1]}")

    if not role_exists:
        import time

        print("   Waiting 10s for IAM propagation...")
        time.sleep(10)

    print("✅ Execution role ready with all required permissions")


def ensure_ecr_repo(ecr_client):
    try:
        ecr_client.describe_repositories(repositoryNames=[ECR_REPO_NAME])
        print(f"✅ ECR repo already exists: {ECR_REPO_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            ecr_client.create_repository(repositoryName=ECR_REPO_NAME)
            print(f"✅ Created ECR repo: {ECR_REPO_NAME}")
        else:
            raise


CONTAINER_ENGINE = os.getenv("CONTAINER_ENGINE", "docker")


def docker_login(ecr_client):
    token = ecr_client.get_authorization_token()
    auth = token["authorizationData"][0]
    import base64

    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    registry = auth["proxyEndpoint"]
    run(
        [CONTAINER_ENGINE, "login", "--username", user, "--password-stdin", registry],
        input=password.encode(),
        capture_output=True,
    )
    print(f"✅ {CONTAINER_ENGINE} login successful")


def build_and_push():
    print(f"\n📦 Building image with {CONTAINER_ENGINE}...")
    run([CONTAINER_ENGINE, "build", "--platform", "linux/arm64", "-t", f"{RUNTIME_NAME}:latest", "."])

    print("\n🏷️  Tagging image...")
    run([CONTAINER_ENGINE, "tag", f"{RUNTIME_NAME}:latest", IMAGE_URI])

    print("\n⬆️  Pushing to ECR...")
    run([CONTAINER_ENGINE, "push", IMAGE_URI])
    print(f"✅ Image pushed: {IMAGE_URI}")


def get_existing_runtime(cp_client):
    response = cp_client.list_agent_runtimes()
    for rt in response.get("agentRuntimes", []):
        if rt.get("agentRuntimeName") == RUNTIME_NAME:
            return rt
    return None


def deploy(cp_client):
    existing = get_existing_runtime(cp_client)

    if existing:
        runtime_id = existing["agentRuntimeId"]
        print(f"\n🔄 Runtime '{RUNTIME_NAME}' already exists (id={runtime_id}), updating image...")
        env_vars = {"AWS_REGION": REGION}
        if MEMORY_ID:
            env_vars["AGENTCORE_MEMORY_ID"] = MEMORY_ID
        if GUARDRAIL_ID:
            env_vars["BEDROCK_GUARDRAIL_ID"] = GUARDRAIL_ID
            env_vars["BEDROCK_GUARDRAIL_VERSION"] = GUARDRAIL_VERSION
        cp_client.update_agent_runtime(
            agentRuntimeId=runtime_id,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": IMAGE_URI}},
            roleArn=EXECUTION_ROLE_ARN,
            networkConfiguration={"networkMode": "PUBLIC"},
            environmentVariables=env_vars,
        )
        print("✅ Runtime updated")
    else:
        print(f"\n🚀 Creating AgentCore Runtime '{RUNTIME_NAME}'...")
        env_vars = {"AWS_REGION": REGION}
        if MEMORY_ID:
            env_vars["AGENTCORE_MEMORY_ID"] = MEMORY_ID
            print(f"   Memory ID : {MEMORY_ID}")
        if GUARDRAIL_ID:
            env_vars["BEDROCK_GUARDRAIL_ID"] = GUARDRAIL_ID
            env_vars["BEDROCK_GUARDRAIL_VERSION"] = GUARDRAIL_VERSION
            print(f"   Guardrail : {GUARDRAIL_ID} (v{GUARDRAIL_VERSION})")
        response = cp_client.create_agent_runtime(
            agentRuntimeName=RUNTIME_NAME,
            description="Text-to-Python IDE agent — generates and executes Python code via AgentCore",
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": IMAGE_URI}},
            roleArn=EXECUTION_ROLE_ARN,
            networkConfiguration={"networkMode": "PUBLIC"},
            environmentVariables=env_vars,
        )
        runtime_id = response["agentRuntimeId"]
        print(f"✅ Runtime created: {runtime_id}")

    # Wait for READY
    print("⏳ Waiting for runtime to reach READY status...")
    import time

    for _ in range(60):
        rt = cp_client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = rt.get("status")
        print(f"   Status: {status}")
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            print(f"❌ Runtime failed: {rt.get('failureReason')}")
            sys.exit(1)
        time.sleep(10)
    else:
        print("❌ Timed out waiting for runtime READY")
        sys.exit(1)

    runtime_arn = rt["agentRuntimeArn"]

    # Create DEFAULT endpoint if it doesn't exist
    try:
        ep = cp_client.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=ENDPOINT_NAME)
        print(f"✅ Endpoint '{ENDPOINT_NAME}' already exists")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"\n🔌 Creating endpoint '{ENDPOINT_NAME}'...")
            cp_client.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name=ENDPOINT_NAME)
        else:
            raise

    # Wait for endpoint READY
    print("⏳ Waiting for endpoint to reach READY status...")
    for _ in range(60):
        ep = cp_client.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=ENDPOINT_NAME)
        ep_status = ep.get("status")
        print(f"   Endpoint status: {ep_status}")
        if ep_status == "READY":
            break
        if ep_status in ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            print(f"❌ Endpoint failed: {ep.get('failureReason')}")
            sys.exit(1)
        time.sleep(10)
    else:
        print("❌ Timed out waiting for endpoint READY")
        sys.exit(1)

    return runtime_id, runtime_arn


def teardown(cp_client):
    existing = get_existing_runtime(cp_client)
    if not existing:
        print(f"ℹ️  No runtime named '{RUNTIME_NAME}' found, nothing to tear down")
        return

    runtime_id = existing["agentRuntimeId"]

    # Delete the runtime directly — default endpoints are removed automatically
    print(f"🗑️  Deleting runtime '{RUNTIME_NAME}' (id={runtime_id})...")
    cp_client.delete_agent_runtime(agentRuntimeId=runtime_id)

    import time

    print("⏳ Waiting for deletion to complete...")
    for _ in range(60):
        try:
            rt = cp_client.get_agent_runtime(agentRuntimeId=runtime_id)
            status = rt.get("status")
            print(f"   Status: {status}")
            if status in ("DELETE_FAILED",):
                print(f"❌ Deletion failed: {rt.get('failureReason')}")
                sys.exit(1)
            time.sleep(5)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                break
            raise

    print("✅ Teardown complete")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teardown", action="store_true", help="Delete the runtime and endpoint")
    parser.add_argument("--skip-build", action="store_true", help="Skip Docker build/push (use existing image)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=os.getenv("AWS_PROFILE", "default"), region_name=REGION)
    ecr_client = session.client("ecr")
    cp_client = session.client("bedrock-agentcore-control")

    if args.teardown:
        teardown(cp_client)
        return

    print(f"🚀 Deploying {RUNTIME_NAME} to AgentCore Runtime")
    print(f"   Account : {ACCOUNT_ID}")
    print(f"   Region  : {REGION}")
    print(f"   Image   : {IMAGE_URI}")
    print(f"   Role    : {EXECUTION_ROLE_ARN}")

    # Ensure the execution role exists (creates it if not)
    iam_client = session.client("iam")
    ensure_execution_role(iam_client)

    if not args.skip_build:
        ensure_ecr_repo(ecr_client)
        docker_login(ecr_client)
        build_and_push()

    runtime_id, runtime_arn = deploy(cp_client)

    # Save runtime info for invoke_runtime.py
    info = {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "endpoint_name": ENDPOINT_NAME,
        "region": REGION,
        "account_id": ACCOUNT_ID,
    }
    with open("runtime_info.json", "w") as f:
        json.dump(info, f, indent=2)

    print("\n" + "=" * 60)
    print("✅ Deployment complete!")
    print(f"   Runtime ARN : {runtime_arn}")
    print(f"   Runtime ID  : {runtime_id}")
    print(f"   Endpoint    : {ENDPOINT_NAME}")
    print("\nTo invoke the runtime:")
    print("   python invoke_runtime.py --action generate_code --prompt 'write a fibonacci function'")
    print("   python invoke_runtime.py --action execute_code  --code 'print(2+2)'")
    print("\nTo tear down:")
    print("   python deploy_runtime.py --teardown")
    print("=" * 60)


if __name__ == "__main__":
    main()
