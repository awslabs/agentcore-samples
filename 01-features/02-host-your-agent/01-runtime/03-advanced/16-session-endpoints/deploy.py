"""
Deploy the web preview sample.

Builds the agent into one file, packages it with the Vite site and the site's
linux-arm64 dependencies, uploads the zip to Amazon S3, and creates a Node.js runtime
whose Session endpoint can reach the site (8090) and the agent's status port (8081).

Usage:
    python deploy.py
    python deploy.py --name my_preview --config my_runtime_config.json
"""

import argparse
import json
import os
import shutil
import subprocess
import time
from functools import cache

import boto3
from bedrock_agentcore.runtime import AgentCoreRuntimeClient, WaitConfig
from boto3.session import Session

AGENT_NAME = "remote_access_sample"
SERVING_PORT = 8080
SERVING_PATH = "/invocations"
PREVIEW_PORT = 8090  # the Vite dev server inside the session
STATUS_PORT = 8081  # the agent's own record of what it changed
ALLOWED_PORTS = [PREVIEW_PORT, STATUS_PORT]
IDLE_SESSION_TIMEOUT = 1800
MAX_SESSION_LIFETIME = 7200

session = Session()
REGION = session.region_name or "us-west-2"
S3_PREFIX = f"{AGENT_NAME}/code.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = "site"  # the website folder to package; --site changes it
CONFIG_FILE = "runtime_config.json"
MODEL_ID = None  # --model sets the in-session agent's model


@cache
def account_id() -> str:
    return session.client("sts").get_caller_identity()["Account"]


def code_bucket() -> str:
    return f"agentcore-code-{account_id()}-{REGION}"


def create_execution_role() -> str:
    """Create the least privilege role the runtime assumes."""
    if os.environ.get("AGENTCORE_ROLE_ARN"):
        return os.environ["AGENTCORE_ROLE_ARN"]

    iam = boto3.client("iam", region_name=REGION)
    role_name = f"agentcore-{AGENT_NAME}-role"
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id()}},
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
             "Resource": "*"},
            {"Effect": "Allow",
             "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
             "Resource": "arn:aws:logs:*:*:*"},
        ],
    }
    try:
        role_arn = iam.create_role(
            RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
            Description=f"Execution role for {AGENT_NAME}",
        )["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{account_id()}:role/{role_name}"
    iam.put_role_policy(RoleName=role_name, PolicyName=f"{AGENT_NAME}-policy",
                        PolicyDocument=json.dumps(policy))
    print(f"IAM role: {role_arn}")
    time.sleep(10)
    return role_arn


def build_package() -> str:
    """Bundle the agent and add the site with dependencies built for linux-arm64."""
    build = os.path.join(HERE, "build")
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)

    print("Bundling the agent...")
    subprocess.run(["npm", "install", "--silent"], cwd=os.path.join(HERE, "agent"), check=True)
    subprocess.run(["npm", "run", "build", "--silent"], cwd=os.path.join(HERE, "agent"), check=True)
    shutil.copyfile(os.path.join(HERE, "agent", "dist", "app.js"), os.path.join(build, "app.js"))

    print("Installing the site's dependencies for linux-arm64...")
    shutil.copytree(os.path.join(HERE, SITE_DIR), os.path.join(build, "site"),
                    ignore=shutil.ignore_patterns("node_modules"))
    subprocess.run(["npm", "install", "--silent", "--os=linux", "--cpu=arm64", "--libc=glibc"],
                   cwd=os.path.join(build, "site"), check=True)

    archive = shutil.make_archive(os.path.join(HERE, "code"), "zip", build)
    print(f"Package: {os.path.getsize(archive) / (1024 * 1024):.1f} MB")
    return archive


def upload(archive: str) -> None:
    s3 = boto3.client("s3", region_name=REGION)
    bucket = code_bucket()
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION})
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass
    s3.upload_file(archive, bucket, S3_PREFIX)
    print(f"Uploaded to s3://{bucket}/{S3_PREFIX}")


def create_runtime(role_arn: str) -> dict:
    """Create the runtime. allowedPorts is what lets the Session endpoint reach 8090 and 8081."""
    client = AgentCoreRuntimeClient(region=REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    existing = [runtime for runtime in control.list_agent_runtimes()["agentRuntimes"]
                if runtime["agentRuntimeName"] == AGENT_NAME]
    if existing:
        raise SystemExit(
            f"A runtime named {AGENT_NAME} already exists ({existing[0]['agentRuntimeId']}). "
            f"Run cleanup.py --config {CONFIG_FILE} to delete it first, or pass --name to use another name."
        )
    print("Creating the runtime and waiting for READY...")
    runtime = client.create_agent_runtime_and_wait(
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={"codeConfiguration": {
            "code": {"s3": {"bucket": code_bucket(), "prefix": S3_PREFIX}},
            "runtime": "NODE_22",
            "entryPoint": ["app.js"],
        }},
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP", "port": SERVING_PORT, "path": SERVING_PATH},
        allowedPorts=[{"port": port} for port in ALLOWED_PORTS],
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT,
            "maxLifetime": MAX_SESSION_LIFETIME,
        },
        **({"environmentVariables": {"MODEL_ID": MODEL_ID}} if MODEL_ID else {}),
        description="Web preview sample for Session endpoints",
        wait_config=WaitConfig(max_wait=600, poll_interval=15),
    )
    print(f"Runtime {runtime['agentRuntimeId']} is {runtime['status']}")
    return {"runtime_id": runtime["agentRuntimeId"], "runtime_arn": runtime["agentRuntimeArn"]}


def main():
    global AGENT_NAME, S3_PREFIX, SITE_DIR, CONFIG_FILE, MODEL_ID
    parser = argparse.ArgumentParser(description="Deploy the web preview sample.")
    parser.add_argument("--site", default=SITE_DIR, help="the website folder to package")
    parser.add_argument("--name", default=AGENT_NAME, help="the runtime name")
    parser.add_argument("--config", default=CONFIG_FILE, help="where to write the runtime details")
    parser.add_argument("--model", default=None, help="Bedrock model ID for the in-session agent")
    args = parser.parse_args()
    SITE_DIR, AGENT_NAME, CONFIG_FILE, MODEL_ID = args.site, args.name, args.config, args.model
    S3_PREFIX = f"{AGENT_NAME}/code.zip"
    os.chdir(HERE)
    print(f"Deploying {AGENT_NAME} to {REGION}\n")
    role_arn = create_execution_role()
    archive = build_package()
    upload(archive)
    runtime = create_runtime(role_arn)
    with open(CONFIG_FILE, "w") as handle:
        json.dump({"agent_name": AGENT_NAME, **runtime, "region": REGION,
                   "preview_port": PREVIEW_PORT, "status_port": STATUS_PORT,
                   "allowed_ports": ALLOWED_PORTS}, handle, indent=2)
    print("\nDeployment complete.")


if __name__ == "__main__":
    main()
