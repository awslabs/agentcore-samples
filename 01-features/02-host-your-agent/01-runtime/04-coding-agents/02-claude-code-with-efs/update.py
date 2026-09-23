"""
Update an existing AgentCore Runtime (e.g. after changing access point, image, or config).

Reads runtime_id from runtime_config.json and current settings from envvars.config.

Usage:
    python update.py
"""

import json
import os
import sys
import time

import boto3

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
                    cfg[key] = value.strip('"').strip("'")
    return cfg


file_cfg = load_dotconfig()


def cfg(key, default=None):
    return file_cfg.get(key) or os.environ.get(key) or default


# ── Configuration ────────────────────────────────────────────────────────────

REGION = cfg("AGENTCORE_REGION", boto3.session.Session().region_name or "us-west-2")

session = boto3.Session(region_name=REGION)
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

ECR_URI = cfg("AGENTCORE_ECR_URI")
SUBNET_1 = cfg("AGENTCORE_SUBNET_1")
SUBNET_2 = cfg("AGENTCORE_SUBNET_2")
SECURITY_GROUP = cfg("AGENTCORE_SECURITY_GROUP")
EFS_AP_ARN = cfg("AGENTCORE_EFS_AP_ARN")

PROTOCOL = "HTTP"
EFS_MOUNT_PATH = "/mnt/efs"


def load_runtime_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found. Run deploy.py first.")
        sys.exit(1)


def update_runtime(runtime_id: str, role_arn: str) -> dict:
    control = session.client("bedrock-agentcore-control", region_name=REGION)

    update_params = dict(
        agentRuntimeId=runtime_id,
        agentRuntimeArtifact={
            "containerConfiguration": {
                "containerUri": ECR_URI,
            }
        },
        roleArn=role_arn,
        networkConfiguration={
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": [SUBNET_1, SUBNET_2],
                "securityGroups": [SECURITY_GROUP],
            },
        },
        protocolConfiguration={"serverProtocol": PROTOCOL},
        description="Claude Code agent on AgentCore Runtime with EFS",
    )

    if EFS_AP_ARN:
        update_params["filesystemConfigurations"] = [
            {
                "efsAccessPoint": {
                    "accessPointArn": EFS_AP_ARN,
                    "mountPath": EFS_MOUNT_PATH,
                }
            }
        ]

    print(f"\nUpdating AgentCore Runtime '{runtime_id}'...")
    response = control.update_agent_runtime(**update_params)

    runtime_arn = response["agentRuntimeArn"]
    print(f"Update initiated: {runtime_id}")

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


def main():
    existing = load_runtime_config()
    runtime_id = existing["runtime_id"]
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/agentcore-{existing['agent_name']}-role"

    print("=" * 60)
    print(f"Updating runtime: {runtime_id}")
    print(f"  Image:      {ECR_URI}")
    print(f"  Role:       {role_arn}")
    if EFS_AP_ARN:
        print(f"  EFS AP:     {EFS_AP_ARN}")
        print(f"  Mount:      {EFS_MOUNT_PATH}")
    print("=" * 60)

    runtime = update_runtime(runtime_id, role_arn)

    existing.update(
        {
            "runtime_id": runtime["runtime_id"],
            "runtime_arn": runtime["runtime_arn"],
            "ecr_uri": ECR_URI,
        }
    )
    if EFS_AP_ARN:
        existing["efs_access_point_arn"] = EFS_AP_ARN
        existing["efs_mount_path"] = EFS_MOUNT_PATH

    config_path = os.path.join(os.path.dirname(__file__), "runtime_config.json")
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)

    print("\n" + "=" * 60)
    print("Update complete!")
    print(f"  Runtime ARN: {runtime['runtime_arn']}")
    print("  Config saved to: runtime_config.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
