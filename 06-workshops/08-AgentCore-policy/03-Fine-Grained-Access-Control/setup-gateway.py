"""
Setup script to create Gateway with Lambda target and save configuration.

Usage:
    python setup-gateway.py [--region REGION] [--role-arn ROLE_ARN]

Options:
    --region REGION      AWS region (defaults to current session region)
    --role-arn ROLE_ARN  IAM role ARN with trust relationship (creates one if not provided)

This script will:
1. Create a sample Refund Lambda function (if not provided)
2. Create an Amazon Bedrock AgentCore Gateway with OAuth authorization
3. Attach the Lambda as a target to the Gateway
4. Save the configuration to gateway_config.json

If a Gateway already exists (from gateway_config.json), it will be reused.
"""

import argparse
import json
import logging
import time
import zipfile
import tempfile
import os
from pathlib import Path
import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient


# Refund Lambda function code (Node.js)
REFUND_LAMBDA_CODE = """
console.log('Loading function');

export const handler = async (event, context) => {
    console.log('event =', JSON.stringify(event));
    console.log('context =', JSON.stringify(context));
    
    var response = undefined;
    
    if (event.body !== undefined) {
        console.log('event.body =', event.body);
        const body = JSON.parse(event.body);
        response = {"status": "Done", "amount": body.amount, "orderId": body.orderId};
    } else {
        // For Gateway direct invocation
        response = {"status": "Done", "amount": event.amount, "orderId": event.orderId};
        return response;
    }
    
    console.log('response =', JSON.stringify(response));
    return {"statusCode": 200, "body": JSON.stringify(response)};
};
"""

# Refund tool schema for the Gateway target
REFUND_TOOL_SCHEMA = [
    {
        "name": "refund",
        "description": (
            "Processes customer refunds by validating the refund amount, "
            "customer ID, and reason. Returns a refund ID and confirmation "
            "details upon successful processing."
        ),
        "inputSchema": {
            "type": "object",
            "description": "Input parameters for processing a customer refund",
            "properties": {
                "amount": {
                    "type": "integer",
                    "description": "The refund amount in USD (must be positive)",
                },
                "orderId": {
                    "type": "string",
                    "description": "Unique identifier for the customer requesting the refund",
                },
            },
            "required": ["amount", "orderId"],
        },
    }
]


def load_existing_config() -> dict | None:
    """Load existing gateway_config.json if it exists and has valid gateway info."""
    config_path = Path("gateway_config.json")
    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # Check if config has required gateway fields (not placeholders)
        if config.get("gateway_id") and "<" not in config.get("gateway_id", "<"):
            return config
    except (json.JSONDecodeError, IOError):
        pass

    return None


def get_existing_gateway(region: str, gateway_id: str = None, gateway_name: str = None) -> dict | None:
    """Check if gateway exists by ID or name and return its details."""
    boto_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # Try by ID first
    if gateway_id:
        try:
            gateway = boto_client.get_gateway(gatewayIdentifier=gateway_id)
            if gateway and gateway.get("status") in ["READY", "ACTIVE"]:
                return gateway
        except Exception as exc:
            print(f"  Could not retrieve gateway by ID {gateway_id}: {exc}")

    # Try to find by name
    if gateway_name:
        try:
            response = boto_client.list_gateways()
            for gw in response.get("items", []):
                if gw.get("name") == gateway_name and gw.get("status") in [
                    "READY",
                    "ACTIVE",
                ]:
                    # Get full gateway details
                    full_gw = boto_client.get_gateway(gatewayIdentifier=gw["gatewayId"])
                    return full_gw
        except Exception as exc:
            print(f"  Could not search for gateway by name: {exc}")

    return None


def get_existing_target(region: str, gateway_id: str, target_name: str) -> dict | None:
    """Check if a target with the given name exists on the gateway."""
    boto_client = boto3.client("bedrock-agentcore-control", region_name=region)

    try:
        response = boto_client.list_gateway_targets(gatewayIdentifier=gateway_id)
        targets = response.get("items", [])
        print(f"  Found {len(targets)} existing target(s) on gateway")
        for target in targets:
            print(f"    - {target.get('name')} (ID: {target.get('targetId')})")
            if target.get("name") == target_name:
                return target
    except Exception as exc:
        print(f"  Could not list gateway targets: {exc}")

    return None


def create_refund_lambda(region: str, function_name: str = "RefundLambda") -> str:
    """
    Create or update the Refund Lambda function.

    Args:
        region: AWS region
        function_name: Name for the Lambda function

    Returns:
        Lambda function ARN
    """
    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)
    sts_client = boto3.client("sts", region_name=region)

    account_id = sts_client.get_caller_identity()["Account"]

    print(f"\n📦 Setting up Refund Lambda function: {function_name}")
    print("-" * 60)

    # Create deployment package (zip file with index.mjs)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
        zip_path = tmp_file.name
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Use .mjs extension for ES module support
            zipf.writestr("index.mjs", REFUND_LAMBDA_CODE.strip())

    try:
        with open(zip_path, "rb") as f:
            zip_content = f.read()

        # Try to update existing function first
        try:
            lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_content)
            print(f"✓ Updated existing Lambda function: {function_name}")

            # Wait for update to complete
            waiter = lambda_client.get_waiter("function_updated_v2")
            waiter.wait(FunctionName=function_name)

            response = lambda_client.get_function(FunctionName=function_name)
            return response["Configuration"]["FunctionArn"]

        except lambda_client.exceptions.ResourceNotFoundException:
            # Create new function with IAM role
            role_name = f"{function_name}-execution-role"
            role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

            # Create IAM role if needed
            try:
                iam_client.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Principal": {"Service": "lambda.amazonaws.com"},
                                    "Action": "sts:AssumeRole",
                                }
                            ],
                        }
                    ),
                    Description="Execution role for RefundLambda function",
                )
                iam_client.attach_role_policy(
                    RoleName=role_name,
                    PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                )
                print(f"✓ Created IAM role: {role_name}")
                print("  ⏳ Waiting for IAM role propagation (10s)...")
                time.sleep(10)
            except iam_client.exceptions.EntityAlreadyExistsException:
                print(f"  IAM role already exists: {role_name}")

            # Create Lambda function with Node.js 20.x runtime
            response = lambda_client.create_function(
                FunctionName=function_name,
                Runtime="nodejs20.x",
                Role=role_arn,
                Handler="index.handler",
                Code={"ZipFile": zip_content},
                Description="Sample refund processing Lambda for AgentCore Policy tutorial",
                Timeout=30,
                MemorySize=128,
            )
            print(f"✓ Created Lambda function: {function_name}")

            # Wait for function to be active
            waiter = lambda_client.get_waiter("function_active_v2")
            waiter.wait(FunctionName=function_name)

            return response["FunctionArn"]

    finally:
        os.remove(zip_path)


def get_default_region() -> str:
    """Get the default AWS region from the current session or environment."""
    session = boto3.Session()
    region = session.region_name or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise ValueError("AWS region not configured. Pass --region or set AWS_DEFAULT_REGION.")
    return region


def setup_gateway(region: str = None, role_arn: str = None):
    """
    Setup AgentCore Gateway with Lambda target and policy engine.

    Args:
        region: AWS region (defaults to current session region)
        role_arn: IAM role ARN with trust relationship (creates one if not provided)
    """
    # Use provided region or get default
    if not region:
        region = get_default_region()

    print("\n🚀 Setting up AgentCore Gateway...")
    print(f"Region: {region}\n")

    # Initialize client
    client = GatewayClient(region_name=region)
    client.logger.setLevel(logging.INFO)

    # Gateway and target names used for this tutorial
    gateway_name = "TestGWforPolicyEngine"
    target_name = "RefundToolTarget"
    lambda_function_name = "RefundLambda"

    # Check for existing configuration or gateway
    existing_config = load_existing_config()
    gateway = None
    cognito_response = None
    lambda_arn = None

    if existing_config:
        print("📋 Found existing gateway_config.json")
        gateway_id = existing_config.get("gateway_id")

        # Try to retrieve the existing gateway
        print(f"  Checking if gateway '{gateway_id}' exists...")
        gateway = get_existing_gateway(region, gateway_id=gateway_id)

        if gateway:
            print(f"✓ Reusing existing gateway: {gateway.get('gatewayUrl', gateway_id)}\n")
            # Reuse existing client_info if available
            if existing_config.get("client_info"):
                cognito_response = {"client_info": existing_config["client_info"]}

            # Check if Lambda ARN is stored in config
            lambda_arn = existing_config.get("lambda_arn")
        else:
            print(f"  Gateway '{gateway_id}' not found or not ready.\n")

    # If no gateway yet, check if one exists by name
    if not gateway:
        print(f"🔍 Checking for existing gateway named '{gateway_name}'...")
        gateway = get_existing_gateway(region, gateway_name=gateway_name)
        if gateway:
            print(f"✓ Found existing gateway: {gateway.get('gatewayUrl')}\n")

    # Create or get Lambda function
    if not lambda_arn:
        print("\n" + "=" * 60)
        print("Step 1: Setting up Refund Lambda function")
        print("=" * 60)
        lambda_arn = create_refund_lambda(region, lambda_function_name)
        print(f"✓ Lambda ARN: {lambda_arn}\n")
    else:
        print(f"\n✓ Using existing Lambda ARN: {lambda_arn}\n")

    # Create OAuth authorizer if we don't have existing client_info
    if not cognito_response:
        print("=" * 60)
        print("Step 2: Creating OAuth authorization server")
        print("=" * 60)
        cognito_response = client.create_oauth_authorizer_with_cognito("TestGateway")
        print("✓ Authorization server created\n")

    # Create Gateway if we don't have an existing one
    if not gateway:
        print("=" * 60)
        print("Step 3: Creating Gateway")
        print("=" * 60)
        gateway = client.create_mcp_gateway(
            name=gateway_name,
            role_arn=role_arn,
            authorizer_config=cognito_response.get("authorizer_config"),
            enable_semantic_search=True,
        )
        print(f"✓ Gateway created: {gateway['gatewayUrl']}\n")
    else:
        print("=" * 60)
        print("Step 3: Skipping gateway creation (reusing existing)")
        print("=" * 60 + "\n")

    # Check if target already exists and add if not
    print("=" * 60)
    print("Step 4: Adding Lambda target")
    print("=" * 60)

    gateway_id = gateway.get("gatewayId")
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Target name: {target_name}")
    print(f"  Lambda ARN: {lambda_arn}")

    existing_target = get_existing_target(region, gateway_id, target_name)

    if existing_target:
        print(f"✓ Lambda target '{target_name}' already exists, reusing")
        print(f"  Target ID: {existing_target.get('targetId')}")
        lambda_target = {"gatewayArn": gateway.get("gatewayArn")}
    else:
        print(f"  Target '{target_name}' not found, creating...")
        try:
            lambda_target = client.create_mcp_gateway_target(
                gateway=gateway,
                name=target_name,
                target_type="lambda",
                target_payload={
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": REFUND_TOOL_SCHEMA},
                },
                credentials=None,
            )
            print(f"✓ Lambda target '{target_name}' created and attached to gateway\n")
        except Exception as exc:
            error_str = str(exc)
            if "ConflictException" in str(type(exc).__name__) or "already exists" in error_str:
                print(f"✓ Lambda target '{target_name}' already exists, reusing\n")
                lambda_target = {"gatewayArn": gateway.get("gatewayArn")}
            else:
                print(f"✗ Error creating target: {exc}")
                raise

    # Save configuration
    config = {
        "gateway_url": gateway.get("gatewayUrl"),
        "gateway_id": gateway.get("gatewayId"),
        "gateway_arn": lambda_target.get("gatewayArn") or gateway.get("gatewayArn"),
        "region": region,
        "client_info": cognito_response.get("client_info"),
        "lambda_arn": lambda_arn,
    }

    with open("gateway_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 60)
    print("✅ Gateway setup complete!")
    print("=" * 60)
    print(f"Gateway URL: {config['gateway_url']}")
    print(f"Gateway ID: {config['gateway_id']}")
    print(f"Gateway ARN: {config['gateway_arn']}")
    print(f"Lambda ARN: {config['lambda_arn']}")
    print("\nConfiguration saved to: gateway_config.json")
    print("=" * 60)

    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup AgentCore Gateway with Lambda target for Policy tutorial")
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="AWS region (defaults to current session region; required if not configured)",
    )
    parser.add_argument(
        "--role-arn",
        type=str,
        default=None,
        help="IAM role ARN with trust relationship (creates one if not provided)",
    )

    args = parser.parse_args()
    setup_gateway(region=args.region, role_arn=args.role_arn)
