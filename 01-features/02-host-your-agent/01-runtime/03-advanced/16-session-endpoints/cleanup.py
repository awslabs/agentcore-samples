"""
Remove everything the sample created.

Deletes the runtime, the uploaded code object, and the execution role.

Usage:
    python cleanup.py
"""

import argparse
import json
import os
import time

import boto3
from bedrock_agentcore.runtime import AgentCoreRuntimeClient
from botocore.exceptions import ClientError


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Remove what deploy.py created.")
    parser.add_argument("--config", default="runtime_config.json", help="the file deploy.py wrote")
    config_file = parser.parse_args().config
    try:
        with open(config_file) as handle:
            config = json.load(handle)
    except FileNotFoundError:
        print(f"{config_file} not found. Nothing to remove.")
        return

    region = config["region"]
    agent_name = config["agent_name"]
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    client = AgentCoreRuntimeClient(region=region)
    try:
        client.delete_agent_runtime(agentRuntimeId=config["runtime_id"])
        # Wait for the delete to finish, so deploying again with the same name works.
        control = boto3.client("bedrock-agentcore-control", region_name=region)
        for _ in range(60):
            try:
                control.get_agent_runtime(agentRuntimeId=config["runtime_id"])
            except control.exceptions.ResourceNotFoundException:
                break
            time.sleep(5)
        print(f"Deleted runtime {config['runtime_id']}")
    except ClientError as error:
        print(f"Runtime: {error}")

    bucket = f"agentcore-code-{account_id}-{region}"
    try:
        boto3.client("s3", region_name=region).delete_object(
            Bucket=bucket, Key=f"{agent_name}/code.zip")
        print(f"Deleted s3://{bucket}/{agent_name}/code.zip")
    except ClientError as error:
        print(f"Code object: {error}")

    if os.environ.get("AGENTCORE_ROLE_ARN"):
        print("Role: supplied through AGENTCORE_ROLE_ARN, left in place")
    else:
        role_name = f"agentcore-{agent_name}-role"
        iam = boto3.client("iam", region_name=region)
        try:
            iam.delete_role_policy(RoleName=role_name,
                                   PolicyName=f"{agent_name}-policy")
            iam.delete_role(RoleName=role_name)
            print(f"Deleted role {role_name}")
        except ClientError as error:
            print(f"Role: {error}")

    os.remove(config_file)
    print("Cleanup complete.")


if __name__ == "__main__":
    main()
