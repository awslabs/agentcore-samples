"""
Deploy Lambda functions and save their ARNs to config.json

Usage:
    python deploy_lambdas.py --region REGION [--role-arn ROLE_ARN]

Examples:
    # Use existing role
    python deploy_lambdas.py --region us-west-2 --role-arn arn:aws:iam::123456789012:role/MyLambdaRole

    # Create new role automatically
    python deploy_lambdas.py --region us-west-2
"""

import argparse
import boto3
import zipfile
import io
import os
import json
import sys
import time


def get_or_create_lambda_role(iam_client):
    """Get or create IAM role for Lambda execution"""
    role_name = "AgentCoreLambdaExecutionRole"

    try:
        response = iam_client.get_role(RoleName=role_name)
        print(f"   ✅ Using existing IAM role: {role_name}")
        return response["Role"]["Arn"], False
    except iam_client.exceptions.NoSuchEntityException:
        print(f"   📝 Creating IAM role: {role_name}")

        # Trust policy for Lambda
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

        # Create role
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore Lambda functions",
        )

        # Attach basic Lambda execution policy
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )

        print(f"   ✅ IAM role created: {role_name}")
        print("   ⏳ Waiting 10 seconds for IAM propagation...")
        return response["Role"]["Arn"], True


def deploy_lambda(lambda_client, function_name, js_file, role_arn):
    """Deploy a Lambda function from a JS file"""

    print(f"📦 Deploying {function_name}...")

    # Read the JS file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    js_path = os.path.join(script_dir, js_file)

    with open(js_path, "r") as f:
        code_content = f.read()

    # Create a zip file in memory with the code as index.mjs (ES module)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("index.mjs", code_content)

    zip_buffer.seek(0)
    zip_content = zip_buffer.read()

    try:
        # Try to create the function
        response = lambda_client.create_function(
            FunctionName=function_name,
            Runtime="nodejs20.x",
            Role=role_arn,
            Handler="index.handler",
            Code={"ZipFile": zip_content},
            Description=f"AgentCore {function_name}",
            Timeout=30,
            MemorySize=256,
        )

        print("   ✅ Lambda created")
        print(f"   ARN: {response['FunctionArn']}")
        return response["FunctionArn"]

    except lambda_client.exceptions.ResourceConflictException:
        # Function already exists, update it
        print("   ℹ️  Function exists, updating code...")

        response = lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_content)

        print("   ✅ Code updated")
        print(f"   ARN: {response['FunctionArn']}")
        return response["FunctionArn"]

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


def save_config(lambda_arns, region, output_file="config.json"):
    """Save Lambda ARNs to config.json in the Getting-Started directory"""

    # Get the script directory (lambda-target-setup)
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Navigate up to Getting-Started directory: lambda-target-setup -> scripts -> Getting-Started
    getting_started_dir = os.path.dirname(os.path.dirname(script_dir))
    config_path = os.path.join(getting_started_dir, output_file)

    config = {"lambdas": lambda_arns, "region": region}

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n💾 Configuration saved to: {config_path}")


def main():
    print("🚀 Deploying Lambda Functions\n")
    print("=" * 70)

    # Parse arguments
    parser = argparse.ArgumentParser(description="Deploy Lambda functions for AgentCore Policy demo")
    parser.add_argument("--region", type=str, default=None, help="AWS region to deploy into")
    parser.add_argument("--role-arn", type=str, default=None, help="IAM role ARN for Lambda execution")
    args = parser.parse_args()

    # Resolve region
    region = args.region
    if not region:
        session = boto3.Session()
        region = session.region_name
    if not region:
        region = input("Enter AWS region (e.g., us-east-1, us-west-2): ").strip()
        if not region:
            print("❌ Error: AWS region is required")
            sys.exit(1)

    print(f"\nRegion: {region}")

    # Initialize AWS clients
    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    if args.role_arn:
        role_arn = args.role_arn

        # Validate role ARN format
        if not role_arn.startswith("arn:aws:iam::"):
            print(f"\n❌ Error: Invalid role ARN format: {role_arn}")
            print("Expected format: arn:aws:iam::ACCOUNT_ID:role/ROLE_NAME")
            print("\n" + "=" * 70)
            sys.exit(1)

        print(f"\n🔐 Using provided IAM role: {role_arn}")
        print()
        newly_created = False
    else:
        # No role provided, create one
        print("\n🔐 No role provided, setting up IAM role...")
        role_arn, newly_created = get_or_create_lambda_role(iam_client)
        print()

        # Wait for IAM propagation if role was just created
        if newly_created:
            time.sleep(10)

    # Deploy each function
    functions = [
        ("ApplicationTool", "application_tool.js"),
        ("ApprovalTool", "approval_tool.js"),
        ("RiskModelTool", "risk_model_tool.js"),
    ]

    lambda_arns = {}

    for function_name, js_file in functions:
        arn = deploy_lambda(lambda_client, function_name, js_file, role_arn)
        if arn:
            lambda_arns[function_name] = arn
        print()
        # Small delay between deployments
        time.sleep(1)

    # Save configuration
    if lambda_arns:
        save_config(lambda_arns, region)

    print("=" * 70)
    print(f"\n✅ Deployment complete! {len(lambda_arns)}/3 functions deployed.")
    print("\nLambda ARNs have been saved to config.json")
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
