"""
Delete all AWS resources created by deploy.py.

Reads runtime_config.json to find resource IDs.

Usage:
    python cleanup.py
"""

import json
import os

import boto3


def main():
    try:
        with open("runtime_config.json") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("runtime_config.json not found — nothing to clean up.")
        return

    region = config["region"]
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    lambda_client = boto3.client("lambda", region_name=region)
    iam = boto3.client("iam", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    # Delete Lambda function
    fn_name = config.get("lambda_function_name", "")
    if fn_name:
        try:
            lambda_client.delete_function(FunctionName=fn_name)
            print(f"Deleted Lambda function: {fn_name}")
        except Exception as e:
            print(f"Lambda delete: {e}")

    # Delete AgentCore Runtime endpoint
    runtime_id = config.get("runtime_id", "")
    if runtime_id:
        try:
            eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
            for ep in eps.get("runtimeEndpoints", []):
                control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, name=ep["name"])
                print(f"Deleted endpoint: {ep['name']}")
        except Exception as e:
            print(f"Endpoint delete: {e}")

        # Delete AgentCore Runtime
        try:
            control.delete_agent_runtime(agentRuntimeId=runtime_id)
            print(f"Deleted AgentCore Runtime: {runtime_id}")
        except Exception as e:
            print(f"Runtime delete: {e}")

    # Delete IAM roles
    for role_key in ("runtime_role_name", "lambda_role_name"):
        role_name = config.get(role_key, "")
        if not role_name:
            continue
        try:
            # Detach managed policies
            attached = iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
            for p in attached:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])

            # Delete inline policies
            inline = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])
            for pname in inline:
                iam.delete_role_policy(RoleName=role_name, PolicyName=pname)

            iam.delete_role(RoleName=role_name)
            print(f"Deleted IAM role: {role_name}")
        except Exception as e:
            print(f"IAM role delete ({role_name}): {e}")

    # Delete S3 objects
    bucket = config.get("s3_bucket", "")
    prefix = config.get("s3_prefix", "")
    if bucket and prefix:
        try:
            s3.delete_object(Bucket=bucket, Key=prefix)
            print(f"Deleted S3 object: s3://{bucket}/{prefix}")
        except Exception as e:
            print(f"S3 delete: {e}")

    # Remove config file
    os.remove("runtime_config.json")
    print("Removed runtime_config.json")
    print("Cleanup complete.")


if __name__ == "__main__":
    main()
