"""Deploy the sample Lambda function for the observability tutorial.

Creates:
  1. IAM execution role for the Lambda
  2. Lambda function (observability-gateway-lambda) with get_order and update_order tools

The Lambda function name and ARN are saved to the local .env for use by
invoke.py and cleanup.py.

Usage:
    uv run python scripts/observability/deploy.py
"""

import json
import os
import sys
import time

import boto3


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
        print(f"ERROR: {key} not set. Export it or add to the script .env")
        sys.exit(1)
    return val


LAMBDA_FUNCTION_NAME = "observability-gateway-lambda"
LAMBDA_ROLE_NAME = "observability-gateway-lambda-role"

LAMBDA_CODE = """\
import json

def lambda_handler(event, context):
    toolName = context.client_context.custom['bedrockAgentCoreToolName']
    delimiter = "___"
    if delimiter in toolName:
        toolName = toolName[toolName.index(delimiter) + len(delimiter):]
    if toolName == 'get_order_tool':
        return {'statusCode': 200, 'body': 'Order Id 123 is in shipped status'}
    else:
        return {'statusCode': 200, 'body': 'Updated the order details successfully'}
"""


def create_lambda_role(iam):
    """Create the IAM execution role for the Lambda function."""
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        resp = iam.create_role(
            RoleName=LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        iam.attach_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"  Created IAM role: {LAMBDA_ROLE_NAME}")
        # Wait for role propagation
        time.sleep(10)
        return resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"  IAM role already exists: {LAMBDA_ROLE_NAME}")
        return iam.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]


def create_lambda_function(lambda_client, role_arn):
    """Create the Lambda function with inline code."""
    import zipfile
    import io

    # Package the code into a zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.py", LAMBDA_CODE)
    zip_buffer.seek(0)

    try:
        resp = lambda_client.create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="index.lambda_handler",
            Code={"ZipFile": zip_buffer.read()},
            Timeout=30,
        )
        print(f"  Created Lambda function: {LAMBDA_FUNCTION_NAME}")
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        print(f"  Lambda function already exists: {LAMBDA_FUNCTION_NAME}")
        resp = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        return resp["Configuration"]["FunctionArn"]


def save_env(**kwargs):
    """Save key=value pairs to the local .env file."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    env_vars: dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_vars[key] = value
    env_vars.update(kwargs)
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print(f"\n  Saved to .env: {', '.join(kwargs.keys())}")


def main():
    load_env()

    region = boto3.Session().region_name
    iam = boto3.client("iam")
    lambda_client = boto3.client("lambda", region_name=region)

    print("=" * 60)
    print("Deploy Lambda for Observability Tutorial")
    print("=" * 60)

    print("\n--- Step 1: Create IAM execution role ---")
    role_arn = create_lambda_role(iam)

    print("\n--- Step 2: Create Lambda function ---")
    lambda_arn = create_lambda_function(lambda_client, role_arn)

    print(f"\n  Lambda ARN: {lambda_arn}")

    save_env(
        LAMBDA_FUNCTION_NAME=LAMBDA_FUNCTION_NAME,
        LAMBDA_ARN=lambda_arn,
    )

    print("\nDone. Export the Lambda ARN for subsequent steps:")
    print(f'  export LAMBDA_ARN="{lambda_arn}"')


if __name__ == "__main__":
    main()
