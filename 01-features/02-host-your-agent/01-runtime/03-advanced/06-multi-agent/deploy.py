"""
Deploy a multi-agent system: tech agent, HR agent, and orchestrator.

Deploys three separate AgentCore Runtimes:
1. Tech agent — handles programming and technical questions
2. HR agent — handles benefits and policy questions
3. Orchestrator — routes questions to the appropriate specialist

The orchestrator receives the specialist ARNs as environment variables
so it can invoke them via invoke_agent_runtime.

Usage:
    python deploy.py
"""

import json
import os
import sys
import time

import boto3
from boto3.session import Session

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"
PYTHON_RUNTIME = "PYTHON_3_12"

# Agent definitions
AGENTS = [
    {
        "name": "multi_tech_agent",
        "entry_point": "tech_agent.py",
        "code_files": ["tech_agent.py", "requirements.txt"],
        "description": "Tech support specialist — programming and debugging",
    },
    {
        "name": "multi_hr_agent",
        "entry_point": "hr_agent.py",
        "code_files": ["hr_agent.py", "requirements.txt"],
        "description": "HR specialist — benefits and policies",
    },
]


def create_execution_role(agent_name: str) -> str:
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{agent_name}-role"
    trust = {
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
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
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

    # Orchestrator also needs permission to invoke other runtimes
    if "orchestrator" in agent_name:
        policy["Statement"].append(
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": "*",
            }
        )

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=f"Execution role for {agent_name}",
        )
        role_arn = resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{agent_name}-policy",
        PolicyDocument=json.dumps(policy),
    )
    return role_arn


def zip_and_upload(agent_name: str, code_files: list[str]):
    import shutil
    import subprocess

    s3 = boto3.client("s3", region_name=REGION)
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            s3.create_bucket(
                Bucket=S3_BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass

    pkg_dir = f"deployment_package_{agent_name}"
    zip_file = f"deployment_package_{agent_name}.zip"
    py_files = [f for f in code_files if f.endswith(".py")]
    req_files = [f for f in code_files if f == "requirements.txt"]
    req_file = req_files[0] if req_files else "requirements.txt"

    if os.path.isdir(pkg_dir):
        shutil.rmtree(pkg_dir)
    if os.path.exists(zip_file):
        os.remove(zip_file)

    python_version = PYTHON_RUNTIME.replace("PYTHON_", "").replace("_", ".").lower()
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            python_version,
            "--target",
            pkg_dir,
            "--only-binary",
            ":all:",
            "-r",
            req_file,
        ],
        check=True,
    )
    for f in py_files:
        if os.path.exists(f):
            shutil.copy(f, os.path.join(pkg_dir, os.path.basename(f)))

    subprocess.run(["zip", "-r", zip_file, pkg_dir], check=True, capture_output=True)
    # Re-zip flattened so imports work at root
    import zipfile as zfmod

    with zfmod.ZipFile(zip_file, "w", zfmod.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(pkg_dir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, pkg_dir)
                zf.write(full, arcname)

    s3_prefix = f"{agent_name}/code.zip"
    s3.upload_file(zip_file, S3_BUCKET, s3_prefix)
    print(f"  ✓ Uploaded s3://{S3_BUCKET}/{s3_prefix}")

    shutil.rmtree(pkg_dir)
    os.remove(zip_file)
    return s3_prefix


def deploy_runtime(
    agent_name: str,
    role_arn: str,
    s3_prefix: str,
    entry_point: str,
    description: str,
    env_vars: dict | None = None,
) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    params = {
        "agentRuntimeName": agent_name,
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": s3_prefix}},
                "runtime": PYTHON_RUNTIME,
                "entryPoint": [entry_point],
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "description": description,
    }
    if env_vars:
        params["environmentVariables"] = env_vars

    response = control.create_agent_runtime(**params)
    runtime_id, runtime_arn = response["agentRuntimeId"], response["agentRuntimeArn"]

    # Wait for READY
    while True:
        s = control.get_agent_runtime(agentRuntimeId=runtime_id)
        print(f"    {agent_name}: {s['status']}")
        if s["status"] == "READY":
            break
        if "FAILED" in s["status"]:
            print(f"    ✗ Failed: {s.get('failureReason')}")
            sys.exit(1)
        time.sleep(15)

    # Create endpoint
    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        if eps.get("runtimeEndpoints") and eps["runtimeEndpoints"][0]["status"] == "READY":
            break
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("═══ Deploying Multi-Agent System ═══\n")

    deployed = {}

    # Step 1: Deploy specialist agents
    for agent_def in AGENTS:
        name = agent_def["name"]
        print(f"\n── Deploying {name} ──")
        role_arn = create_execution_role(name)
        print("  ✓ IAM role ready")
        time.sleep(10)

        s3_prefix = zip_and_upload(name, agent_def["code_files"])
        print("  ✓ Code uploaded")

        result = deploy_runtime(
            name,
            role_arn,
            s3_prefix,
            agent_def["entry_point"],
            agent_def["description"],
        )
        deployed[name] = result
        print(f"  ✓ {name} deployed: {result['runtime_arn']}")

    # Step 2: Deploy orchestrator with specialist ARNs as env vars
    orch_name = "multi_orchestrator"
    print(f"\n── Deploying {orch_name} ──")
    print("  Passing specialist ARNs as environment variables:")
    print(f"    TECH_AGENT_ARN = {deployed['multi_tech_agent']['runtime_arn']}")
    print(f"    HR_AGENT_ARN   = {deployed['multi_hr_agent']['runtime_arn']}")

    role_arn = create_execution_role(orch_name)
    print("  ✓ IAM role ready (includes InvokeAgentRuntime permission)")
    time.sleep(10)

    s3_prefix = zip_and_upload(orch_name, ["orchestrator_agent.py", "requirements.txt"])
    print("  ✓ Code uploaded")

    orch_result = deploy_runtime(
        orch_name,
        role_arn,
        s3_prefix,
        "orchestrator_agent.py",
        "Orchestrator — routes to tech and HR specialists",
        env_vars={
            "TECH_AGENT_ARN": deployed["multi_tech_agent"]["runtime_arn"],
            "HR_AGENT_ARN": deployed["multi_hr_agent"]["runtime_arn"],
        },
    )
    deployed[orch_name] = orch_result
    print(f"  ✓ {orch_name} deployed: {orch_result['runtime_arn']}")

    # Save config
    config = {
        "agents": {name: info for name, info in deployed.items()},
        "orchestrator_arn": orch_result["runtime_arn"],
        "region": REGION,
    }
    with open("runtime_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n{'=' * 60}")
    print("✓ All 3 agents deployed!")
    print(f"  Orchestrator ARN: {orch_result['runtime_arn']}")
    print("  Config saved to: runtime_config.json")
    print("\n  Test with: python invoke.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
