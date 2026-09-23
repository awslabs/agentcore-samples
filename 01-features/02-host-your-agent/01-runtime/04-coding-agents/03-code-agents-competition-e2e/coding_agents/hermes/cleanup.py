"""
Delete the Hermes AgentCore runtime and its IAM role.
Does NOT delete shared infra (VPC, S3 Files) — use ../infra/cleanup.sh for that.

Usage:
    python cleanup.py
"""

import json
import os
import sys
import time

import boto3


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    config_path = os.path.join(SCRIPT_DIR, "runtime_config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def main():
    config = load_config()
    if not config:
        print("No runtime_config.json found. Nothing to clean up.")
        sys.exit(0)

    agent_name = config["agent_name"]
    runtime_id = config["runtime_id"]
    region = config["region"]

    session = boto3.Session(region_name=region)
    control = session.client("bedrock-agentcore-control", region_name=region)
    iam = session.client("iam")

    print(f"Cleaning up: {agent_name}\n")

    try:
        print(f"  Deleting runtime: {runtime_id}")
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print("  Waiting for deletion...")
        time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")

    role_name = f"agentcore-{agent_name}-{region}-role"
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
        print(f"  Deleted IAM role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  IAM role not found: {role_name}")
    except Exception as e:
        print(f"  Warning: {e}")

    for f in ["runtime_config.json", "agent.config"]:
        path = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(path):
            os.remove(path)

    print("\nDone. Shared infra (VPC, S3 Files) was kept.")


if __name__ == "__main__":
    main()
