"""Clean up all resources including DynamoDB table. Usage: python cleanup.py"""

import json
import os
import sys
import time
import boto3
from boto3.session import Session


def main():
    try:
        with open("runtime_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: runtime_config.json not found.")
        sys.exit(1)

    agent_name, runtime_id, region = (
        config["agent_name"],
        config["runtime_id"],
        config["region"],
    )
    dynamo_table = config.get("dynamo_table", "finance_tracker")
    account_id = Session(region_name=region).client("sts").get_caller_identity()["Account"]
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    print(f"Cleaning up: {agent_name}\n")
    try:
        for ep in control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id).get("runtimeEndpoints", []):
            control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName=ep["name"])
        time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")
    try:
        control.delete_agent_runtime(agentRuntimeId=runtime_id)
        time.sleep(30)
    except Exception as e:
        print(f"  Warning: {e}")
    try:
        boto3.client("s3", region_name=region).delete_object(
            Bucket=f"agentcore-code-{account_id}-{region}", Key=f"{agent_name}/code.zip"
        )
    except Exception:
        pass
    role_name = f"agentcore-{agent_name}-role"
    iam = boto3.client("iam", region_name=region)
    try:
        for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
        iam.delete_role(RoleName=role_name)
    except Exception:
        pass

    # Delete DynamoDB table
    try:
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(dynamo_table)
        table.delete()
        table.wait_until_not_exists()
        print(f"✓ DynamoDB table '{dynamo_table}' deleted")
    except Exception as e:
        print(f"  Warning (DynamoDB): {e}")

    if os.path.exists("runtime_config.json"):
        os.remove("runtime_config.json")
    print("✓ Cleanup complete")


if __name__ == "__main__":
    main()
