"""Delete all resources created by deploy.py. Reads runtime_config.json."""

import json
import time
import boto3

with open("runtime_config.json") as f:
    config = json.load(f)

region = config["region"]
runtime_id = config["runtime_id"]

control = boto3.client("bedrock-agentcore-control", region_name=region)
iam = boto3.client("iam", region_name=region)
s3 = boto3.client("s3", region_name=region)

# Delete endpoint
print("Deleting endpoint 'default'...")
try:
    control.delete_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
    time.sleep(5)
except Exception as e:
    print(f"  {e}")

# Delete runtime
print(f"Deleting runtime {runtime_id}...")
try:
    control.delete_agent_runtime(agentRuntimeId=runtime_id)
except Exception as e:
    print(f"  {e}")

# Delete IAM role
role_name = config.get("role_name")
if role_name:
    print(f"Deleting IAM role {role_name}...")
    try:
        for policy in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy)
        iam.delete_role(RoleName=role_name)
    except Exception as e:
        print(f"  {e}")

# Delete S3 objects
s3_bucket = config.get("s3_bucket")
s3_prefix = config.get("s3_prefix")
if s3_bucket and s3_prefix:
    print(f"Deleting s3://{s3_bucket}/{s3_prefix}...")
    try:
        s3.delete_object(Bucket=s3_bucket, Key=s3_prefix)
    except Exception as e:
        print(f"  {e}")

print("Cleanup complete.")
