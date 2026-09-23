"""
Deploy Cursor (PTY/WebSocket) runtime to AgentCore.

Prerequisites:
  - infra.config exists (run ../infra/setup.sh)
  - Image built (run ./setup.sh)

Usage:
    python deploy.py
"""

import json
import os
import sys
import time

import boto3


def load_dotconfig(path):
    cfg = {}
    if not os.path.exists(path):
        return cfg
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                cfg[key] = value.strip('"').strip("'")
    return cfg


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
INFRA_CONFIG = os.path.join(ROOT_DIR, "infra.config")
LOCAL_CONFIG = os.path.join(SCRIPT_DIR, "agent.config")

infra = load_dotconfig(INFRA_CONFIG)
local = load_dotconfig(LOCAL_CONFIG)

if not infra:
    print("Error: infra.config not found. Run ../infra/setup.sh first.")
    sys.exit(1)

REGION = os.environ.get("AWS_REGION", infra.get("INFRA_REGION", "us-west-2"))
ACCOUNT_ID = infra["INFRA_ACCOUNT_ID"]
SUBNET_1 = infra["INFRA_SUBNET_1"]
SUBNET_2 = infra["INFRA_SUBNET_2"]
SECURITY_GROUP = infra["INFRA_SECURITY_GROUP"]
S3FILES_AP_ARN = infra["INFRA_S3FILES_AP_ARN"]
S3FILES_BUCKET = infra["INFRA_BUCKET"]
ECR_URI = local.get("ECR_URI") or os.environ.get("ECR_URI")

if not ECR_URI:
    print("Error: ECR_URI not found. Run ./setup.sh first.")
    sys.exit(1)

AGENT_NAME = local.get("AGENT_NAME", "cursor")
S3FILES_MOUNT_PATH = "/mnt/s3files"

# Load GATEWAY_URL from gateway_mcp deployed state
GATEWAY_MCP_STATE = os.path.join(ROOT_DIR, "..", "gateway_mcp", ".deployed-state.json")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
if not GATEWAY_URL and os.path.exists(GATEWAY_MCP_STATE):
    with open(GATEWAY_MCP_STATE) as f:
        GATEWAY_URL = json.load(f).get("gateway_url", "")

if not GATEWAY_URL:
    print("Warning: GATEWAY_URL not found. Deploy will continue without gateway support.")
    print("  Either export GATEWAY_URL or deploy the gateway first (gateway_mcp/).")

session = boto3.Session(region_name=REGION)

print("=" * 60)
print(f"Deploying {AGENT_NAME} to AgentCore Runtime")
print(f"  Region:      {REGION}")
print(f"  Image:       {ECR_URI}")
print(f"  S3 Files:    {S3FILES_AP_ARN}")
if GATEWAY_URL:
    print(f"  Gateway URL: {GATEWAY_URL}")
print("=" * 60)


def create_execution_role() -> str:
    iam = session.client("iam")
    role_name = f"agentcore-{AGENT_NAME}-{REGION}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            },
            {
                "Effect": "Allow",
                "Principal": {"Service": "elasticfilesystem.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:s3files:{REGION}:{ACCOUNT_ID}:file-system/*"},
                },
            },
        ],
    }

    ecr_repo = ECR_URI.split("/")[1].split(":")[0] if "/" in ECR_URI else "coding-agents-cursor"

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/*"],
            },
            {
                "Sid": "BedrockInvoke",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListInferenceProfiles",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:*",
                ],
            },
            {
                "Sid": "ECRAuth",
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": ["*"],
            },
            {
                "Sid": "ECRPull",
                "Effect": "Allow",
                "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": [f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/{ecr_repo}"],
            },
            {
                "Sid": "S3Files",
                "Effect": "Allow",
                "Action": [
                    "s3files:GetAccessPoint",
                    "s3files:GetFileSystem",
                    "s3files:GetMountTarget",
                    "s3files:DescribeMountTargets",
                    "s3files:ListMountTargets",
                    "s3files:ClientMount",
                    "s3files:ClientWrite",
                    "s3files:ClientRootAccess",
                ],
                "Resource": [
                    S3FILES_AP_ARN,
                    S3FILES_AP_ARN.rsplit("/access-point/", 1)[0],
                ],
            },
            {
                "Sid": "EFS",
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:ClientMount",
                    "elasticfilesystem:ClientWrite",
                    "elasticfilesystem:DescribeAccessPoints",
                    "elasticfilesystem:DescribeMountTargets",
                ],
                "Resource": [
                    f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:file-system/*",
                    f"arn:aws:elasticfilesystem:{REGION}:{ACCOUNT_ID}:access-point/*",
                ],
            },
            {
                "Sid": "S3Bucket",
                "Effect": "Allow",
                "Action": [
                    "s3:ListBucket",
                    "s3:ListBucketVersions",
                    "s3:GetObject*",
                    "s3:PutObject*",
                    "s3:DeleteObject*",
                    "s3:AbortMultipartUpload",
                ],
                "Resource": [
                    f"arn:aws:s3:::{S3FILES_BUCKET}",
                    f"arn:aws:s3:::{S3FILES_BUCKET}/*",
                ],
            },
            {
                "Sid": "AgentCoreIdentity",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetResourceApiKey",
                ],
                "Resource": ["*"],
            },
            {
                "Sid": "SecretsManager",
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                ],
                "Resource": [f"arn:aws:secretsmanager:{REGION}:{ACCOUNT_ID}:secret:*"],
            },
            {
                "Sid": "AgentCoreGateway",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeGateway"],
                "Resource": [f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:gateway/*"],
            },
            {
                "Sid": "EventBridge",
                "Effect": "Allow",
                "Action": [
                    "events:DeleteRule",
                    "events:DisableRule",
                    "events:EnableRule",
                    "events:PutRule",
                    "events:PutTargets",
                    "events:RemoveTargets",
                    "events:DescribeRule",
                    "events:ListRules",
                    "events:ListTargetsByRule",
                ],
                "Resource": ["arn:aws:events:*:*:rule/*"],
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Execution role for {AGENT_NAME} on AgentCore",
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


def deploy_runtime(role_arn: str) -> dict:
    control = session.client("bedrock-agentcore-control", region_name=REGION)

    artifact = {"containerConfiguration": {"containerUri": ECR_URI}}
    network = {
        "networkMode": "VPC",
        "networkModeConfig": {
            "subnets": [SUBNET_1, SUBNET_2],
            "securityGroups": [SECURITY_GROUP],
        },
    }
    filesystem = [
        {
            "s3FilesAccessPoint": {
                "accessPointArn": S3FILES_AP_ARN,
                "mountPath": S3FILES_MOUNT_PATH,
            }
        }
    ]
    env_vars = {
        "AWS_REGION": REGION,
    }
    if GATEWAY_URL:
        env_vars["GATEWAY_URL"] = GATEWAY_URL

    existing_id = None
    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            existing_id = json.load(f).get("runtime_id")

    if existing_id:
        try:
            control.get_agent_runtime(agentRuntimeId=existing_id)
            print(f"\nUpdating existing runtime '{existing_id}'...")
            control.update_agent_runtime(
                agentRuntimeId=existing_id,
                agentRuntimeArtifact=artifact,
                roleArn=role_arn,
                networkConfiguration=network,
                filesystemConfigurations=filesystem,
                environmentVariables=env_vars,
                description="Cursor PTY agent",
            )
            runtime_id = existing_id
            runtime_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{existing_id}"
        except control.exceptions.ResourceNotFoundException:
            existing_id = None

    if not existing_id:
        print(f"\nCreating runtime '{AGENT_NAME}'...")
        response = control.create_agent_runtime(
            agentRuntimeName=AGENT_NAME,
            agentRuntimeArtifact=artifact,
            roleArn=role_arn,
            networkConfiguration=network,
            protocolConfiguration={"serverProtocol": "HTTP"},
            filesystemConfigurations=filesystem,
            environmentVariables=env_vars,
            description="Cursor PTY agent",
        )
        runtime_id = response["agentRuntimeId"]
        runtime_arn = response["agentRuntimeArn"]

    print(f"Runtime ID: {runtime_id}")
    print("Waiting for READY...")
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


def main():
    role_arn = create_execution_role()
    runtime = deploy_runtime(role_arn)

    config = {
        "agent_name": AGENT_NAME,
        "runtime_id": runtime["runtime_id"],
        "runtime_arn": runtime["runtime_arn"],
        "region": REGION,
        "ecr_uri": ECR_URI,
        "s3files_access_point_arn": S3FILES_AP_ARN,
        "s3files_mount_path": S3FILES_MOUNT_PATH,
    }

    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    print(f"  S3 Files:    {S3FILES_MOUNT_PATH}")
    print("  Config:      cursor/runtime_config.json")
    print("\n  Connect: python cursor/connect.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
