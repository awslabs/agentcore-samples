"""
Deploy the Codex agent to AgentCore Runtime using a container image from ECR.

Run setup.sh first, then:
    python deploy.py

Reads configuration from envvars.config (created by setup.sh).
"""

import json
import os
import sys
import time

import boto3
import botocore.exceptions

# ── Load config ──────────────────────────────────────────────────────────────


def load_dotconfig():
    config_path = os.path.join(os.path.dirname(__file__), "envvars.config")
    cfg = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    cfg[key] = value
    return cfg


file_cfg = load_dotconfig()


def cfg(key, default=None):
    return file_cfg.get(key) or os.environ.get(key) or default


# ── Configuration ────────────────────────────────────────────────────────────

REGION = cfg("AGENTCORE_REGION", boto3.session.Session().region_name or "us-west-2")

session = boto3.Session(region_name=REGION)
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

AGENT_NAME = cfg("AGENTCORE_AGENT_NAME", f"codex_{int(time.time()) % 100000}")
ECR_URI = cfg("AGENTCORE_ECR_URI")
SUBNET_1 = cfg("AGENTCORE_SUBNET_1")
SUBNET_2 = cfg("AGENTCORE_SUBNET_2")
SECURITY_GROUP = cfg("AGENTCORE_SECURITY_GROUP")
EFS_AP_ARN = cfg("AGENTCORE_EFS_AP_ARN")
ECR_REPO = cfg("AGENTCORE_ECR_REPO", "agentcore-codex")

# Codex model served through Bedrock. Override with CODEX_MODEL.
CODEX_MODEL = cfg("CODEX_MODEL", "openai.gpt-5.6-terra")
CODEX_REASONING_EFFORT = cfg("CODEX_REASONING_EFFORT", "medium")

if not ECR_URI:
    print("Error: AGENTCORE_ECR_URI not found. Run setup.sh first.")
    sys.exit(1)

if not all([SUBNET_1, SUBNET_2, SECURITY_GROUP]):
    print("Error: VPC config (subnets, security group) not found. Run setup.sh first.")
    sys.exit(1)

PROTOCOL = "HTTP"
EFS_MOUNT_PATH = "/mnt/efs"

# Bedrock inference region. Defaults to the runtime's region, but can be
# overridden — see the model availability table in the README, since not every
# GPT-5.6 model is served in every region.
BEDROCK_REGION = cfg("BEDROCK_REGION", REGION)

ENVIRONMENT_VARIABLES = {
    "CODEX_HOME": f"{EFS_MOUNT_PATH}/.codex",
    "WORKSPACE_DIR": f"{EFS_MOUNT_PATH}/workspace",
    "SKILLS_DIR": f"{EFS_MOUNT_PATH}/.codex/skills",
    "CODEX_MODEL": CODEX_MODEL,
    "CODEX_REASONING_EFFORT": CODEX_REASONING_EFFORT,
    "BEDROCK_REGION": BEDROCK_REGION,
}

print(f"Region:     {REGION}")
print(f"Account:    {ACCOUNT_ID}")
print(f"Agent:      {AGENT_NAME}")
print(f"Image:      {ECR_URI}")
print(f"Model:      {CODEX_MODEL}")
print(f"Subnets:    {SUBNET_1}, {SUBNET_2}")
print(f"SG:         {SECURITY_GROUP}")
if EFS_AP_ARN:
    print(f"EFS AP:     {EFS_AP_ARN}")
    print(f"Mount:      {EFS_MOUNT_PATH}")


# ── Step 1: Create IAM Execution Role ────────────────────────────────────────


def create_execution_role() -> str:
    iam = session.client("iam")
    role_name = f"agentcore-{AGENT_NAME}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT_ID}},
            },
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
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": ["*"],
            },
            {
                "Effect": "Allow",
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
            },
            {
                # Classic bedrock-runtime invocation. Codex's amazon-bedrock
                # provider does not use this path (see BedrockMantleInference
                # below); it is granted only so the sample keeps working if you
                # point Codex at a bedrock-runtime model via a custom
                # model_providers entry in config.toml. Drop this statement if
                # you stay on the default GPT-5.6 models.
                "Sid": "BedrockModelInvocation",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:inference-profile/*",
                ],
            },
            {
                # Required for the GPT-5.6 family (sol/terra/luna), which is
                # served through the bedrock-mantle endpoint rather than the
                # classic bedrock-runtime InvokeModel API. These are the two
                # actions Codex actually calls; the AmazonBedrockMantleInferenceAccess
                # managed policy is a broader alternative if you prefer a
                # managed grant.
                "Sid": "BedrockMantleInference",
                "Effect": "Allow",
                "Action": [
                    "bedrock-mantle:CreateInference",
                    "bedrock-mantle:CallWithBearerToken",
                ],
                "Resource": "*",
            },
            {
                "Sid": "ECRPull",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": ["*"],
            },
            {
                "Sid": "ECRImage",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": [f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/{ECR_REPO}"],
            },
            {
                "Sid": "EFSClientAccess",
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:ClientWrite",
                ],
                "Resource": f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:file-system/*",
                # Scoped to this sample's access point, not every access point
                # in the account.
                "Condition": {"StringEquals": {"elasticfilesystem:AccessPointArn": EFS_AP_ARN}}
                if EFS_AP_ARN
                else {
                    "ArnLike": {
                        "elasticfilesystem:AccessPointArn": f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:access-point/*",
                    }
                },
            },
            {
                "Sid": "EFSDescribe",
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:DescribeAccessPoints",
                    "elasticfilesystem:DescribeMountTargets",
                ],
                "Resource": [
                    f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:file-system/*",
                    f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:access-point/*",
                ],
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Execution role for {AGENT_NAME}",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"\nCreated IAM role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"\nIAM role exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{AGENT_NAME}-policy",
        PolicyDocument=json.dumps(inline_policy),
    )

    print("Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


# ── Step 2: Create AgentCore Runtime (VPC + container + EFS) ─────────────────


def create_runtime(role_arn: str) -> dict:
    control = session.client("bedrock-agentcore-control", region_name=REGION)

    create_params = {
        "agentRuntimeName": AGENT_NAME,
        "agentRuntimeArtifact": {
            "containerConfiguration": {
                "containerUri": ECR_URI,
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": [SUBNET_1, SUBNET_2],
                "securityGroups": [SECURITY_GROUP],
            },
        },
        "protocolConfiguration": {"serverProtocol": PROTOCOL},
        "environmentVariables": ENVIRONMENT_VARIABLES,
        "description": "Codex agent on AgentCore Runtime with EFS",
    }

    if EFS_AP_ARN:
        create_params["filesystemConfigurations"] = [
            {
                "efsAccessPoint": {
                    "accessPointArn": EFS_AP_ARN,
                    "mountPath": EFS_MOUNT_PATH,
                }
            }
        ]

    print(f"\nCreating AgentCore Runtime '{AGENT_NAME}'...")
    try:
        response = control.create_agent_runtime(**create_params)
    except botocore.exceptions.ParamValidationError as e:
        if "filesystemConfigurations" in str(e):
            print(
                "\nError: this boto3 does not know 'filesystemConfigurations', so it cannot"
                f"\nmount EFS (installed: {boto3.__version__}). Install a current boto3 in a"
                "\nvirtualenv as described in the README, then rerun:"
                "\n    uv venv --python 3.13 .venv && source .venv/bin/activate"
                "\n    uv pip install boto3 awscli --force-reinstall --no-cache-dir"
            )
            sys.exit(1)
        raise

    runtime_id = response["agentRuntimeId"]
    runtime_arn = response["agentRuntimeArn"]
    print(f"Runtime created: {runtime_id}")

    print("Waiting for runtime to be ready...")
    while True:
        status_resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = status_resp["status"]
        print(f"  Status: {status}")
        if status == "READY":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            print(f"Failed: {status_resp.get('failureReason', 'Unknown')}")
            sys.exit(1)
        time.sleep(15)

    return {"runtime_id": runtime_id, "runtime_arn": runtime_arn}


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print(f"Deploying {AGENT_NAME} to AgentCore Runtime")
    print("  (VPC mode + container + EFS)")
    print("=" * 60)

    role_arn = create_execution_role()
    runtime = create_runtime(role_arn)

    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime["runtime_id"],
        "runtime_arn": runtime["runtime_arn"],
        "region": REGION,
        "ecr_uri": ECR_URI,
        "codex_model": CODEX_MODEL,
    }
    if EFS_AP_ARN:
        config["efs_access_point_arn"] = EFS_AP_ARN
        config["efs_mount_path"] = EFS_MOUNT_PATH

    config_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    if EFS_AP_ARN:
        print(f"  EFS mounted at: {EFS_MOUNT_PATH}")
    print("  Config saved to: runtime_config.json")
    print("\n  Test with: python invoke.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
