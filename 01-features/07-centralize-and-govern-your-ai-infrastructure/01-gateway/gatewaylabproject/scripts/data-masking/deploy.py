"""Create gateway target for the data-masking tutorial.

Assumes the CloudFormation stack (Guardrail + Lambdas) and gateway are
already deployed. Registers the employee-data-tool Lambda as a target.

Requires GATEWAY_ID, LAMBDA_STACK_NAME in environment or .env.

Usage:
    uv run python scripts/data-masking/deploy.py
"""

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

LAMBDA_STACK_NAME_DEFAULT = "agentcore-data-masking-lambdas"

TOOL_DEFINITION = {
    "name": "employee_data_tool",
    "description": "Get employee information including contact details, address, and financial data.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "employee_id": {
                "type": "string",
                "description": "Employee ID to look up",
            }
        },
        "required": ["employee_id"],
    },
}


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def get_required_env(key):
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Export it or add to .env")
        sys.exit(1)
    return val


def save_env(env_vars: dict[str, str]):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    existing: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    existing[key] = value
    existing.update(env_vars)
    with open(env_path, "w") as f:
        for key, value in existing.items():
            f.write(f"{key}={value}\n")
    print("  Saved state to .env")


def main():
    load_env()

    gateway_id = get_required_env("GATEWAY_ID")
    lambda_stack = os.environ.get("LAMBDA_STACK_NAME", LAMBDA_STACK_NAME_DEFAULT)

    region = boto3.Session().region_name
    cfn = boto3.client("cloudformation", region_name=region)
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # --- Read CloudFormation outputs ---
    print("--- Reading CloudFormation Stack Outputs ---")
    try:
        outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cfn.describe_stacks(StackName=lambda_stack)["Stacks"][0]["Outputs"]
        }
    except Exception as e:
        print(f"ERROR: Could not read stack {lambda_stack}: {e}")
        print("  Deploy the CloudFormation stack first (see README Step 2).")
        sys.exit(1)

    tool_arn = outputs["ToolFunctionArn"]
    print(f"  Tool ARN: {tool_arn}")

    # --- Register tool as gateway target ---
    print("\n--- Registering Employee Data Tool as Gateway Target ---")
    target_name = "employee-data-tool-target"
    try:
        target_resp = control_client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": tool_arn,
                        "toolSchema": {"inlinePayload": [TOOL_DEFINITION]},
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        target_id = target_resp["targetId"]
        print(f"  Target created: {target_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            targets = control_client.list_gateway_targets(
                gatewayIdentifier=gateway_id, maxResults=20
            )
            target_id = next(
                t["targetId"]
                for t in targets.get("items", [])
                if t.get("name") == target_name
            )
            print(f"  Target already exists: {target_id}")
        else:
            raise

    print("  Waiting for target to be READY...")
    for _ in range(18):
        tgt = control_client.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt.get("status")
        if status == "READY":
            print("  Target is READY")
            break
        elif status == "FAILED":
            print("  ERROR: Target FAILED")
            break
        time.sleep(10)

    save_env({"TARGET_ID": target_id, "TOOL_ARN": tool_arn})

    print("\n--- Done ---")
    print(f"  Target: {target_id}")
    print("\n  Run: uv run python scripts/data-masking/invoke.py")


if __name__ == "__main__":
    main()
