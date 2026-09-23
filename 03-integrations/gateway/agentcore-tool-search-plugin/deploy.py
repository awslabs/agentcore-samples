"""
Deploy Lambda function, create AgentCore Gateway, and register tools.

Usage:
    python deploy.py
"""

import io
import json
import time
import zipfile

import boto3

from config import (
    AWS_REGION,
    GATEWAY_NAME,
    GATEWAY_ROLE_NAME,
    LAMBDA_DIR,
    LAMBDA_FUNCTION_NAME,
    LAMBDA_ROLE_NAME,
    STATE_FILE,
)


def deploy():
    print("=" * 60)
    print("STEP 1: Verify AWS Credentials")
    print("=" * 60)

    sts = boto3.client("sts")
    identity = sts.get_caller_identity()
    print(f"  Account: {identity['Account']}")
    print(f"  ARN:     {identity['Arn']}")
    print(f"  Region:  {AWS_REGION}")
    print()

    iam = boto3.client("iam", region_name=AWS_REGION)
    lambda_client = boto3.client("lambda", region_name=AWS_REGION)
    agentcore_control = boto3.client("bedrock-agentcore-control", region_name=AWS_REGION)

    # --- Create Lambda execution role ---
    print("=" * 60)
    print("STEP 2: Create Lambda Execution Role")
    print("=" * 60)

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
        role_response = iam.create_role(
            RoleName=LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore travel tools Lambda",
        )
        role_arn = role_response["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"  [OK] Created IAM role: {LAMBDA_ROLE_NAME}")
        print("  Waiting 10s for IAM propagation...")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{identity['Account']}:role/{LAMBDA_ROLE_NAME}"
        print(f"  [INFO] IAM role already exists: {LAMBDA_ROLE_NAME}")
    print()

    # --- Deploy Lambda function ---
    print("=" * 60)
    print("STEP 3: Deploy Lambda Function")
    print("=" * 60)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(f"{LAMBDA_DIR}/travel_tools.py", "travel_tools.py")
    zip_buffer.seek(0)

    try:
        response = lambda_client.create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="travel_tools.lambda_handler",
            Code={"ZipFile": zip_buffer.read()},
            Description="Travel domain tools for AgentCore Gateway",
            Timeout=30,
            MemorySize=256,
        )
        lambda_arn = response["FunctionArn"]
        print(f"  [OK] Created Lambda: {LAMBDA_FUNCTION_NAME}")
    except lambda_client.exceptions.ResourceConflictException:
        zip_buffer.seek(0)
        lambda_client.update_function_code(
            FunctionName=LAMBDA_FUNCTION_NAME, ZipFile=zip_buffer.read()
        )
        func_info = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION_NAME)
        lambda_arn = func_info["Configuration"]["FunctionArn"]
        print(f"  [INFO] Updated existing Lambda: {LAMBDA_FUNCTION_NAME}")
    print(f"  ARN: {lambda_arn}")

    # Wait for Lambda to become Active
    print("  Waiting for Lambda to become Active...")
    waiter = lambda_client.get_waiter("function_active_v2")
    waiter.wait(FunctionName=LAMBDA_FUNCTION_NAME)
    print("  [OK] Lambda is Active")

    # Verify deployment
    test_event = {"tool_name": "get_supported_currencies"}
    resp = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION_NAME, Payload=json.dumps(test_event)
    )
    payload = json.loads(resp["Payload"].read())
    print(f"  [OK] Verified: {payload['total']} currencies available")
    print()

    # --- Create Gateway ---
    print("=" * 60)
    print("STEP 4: Create AgentCore Gateway")
    print("=" * 60)

    gateway_trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        gw_role_response = iam.create_role(
            RoleName=GATEWAY_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(gateway_trust_policy),
            Description="Role for AgentCore Gateway to invoke Lambda targets",
        )
        gateway_role_arn = gw_role_response["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=GATEWAY_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaRole",
        )
        print(f"  [OK] Created gateway role: {GATEWAY_ROLE_NAME}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        gateway_role_arn = f"arn:aws:iam::{identity['Account']}:role/{GATEWAY_ROLE_NAME}"
        print(f"  [INFO] Gateway role already exists: {GATEWAY_ROLE_NAME}")

    try:
        gateway_response = agentcore_control.create_gateway(
            name=GATEWAY_NAME,
            roleArn=gateway_role_arn,
            authorizerType="AWS_IAM",
            protocolType="MCP",
            protocolConfiguration={"mcp": {"searchType": "SEMANTIC"}},
            description="Travel domain gateway for semantic tool search demo",
        )
        gateway_id = gateway_response["gatewayId"]
        gateway_endpoint = gateway_response["gatewayUrl"]
        print(f"  [OK] Created gateway: {GATEWAY_NAME}")
    except Exception as e:
        if "already exists" in str(e).lower() or "conflict" in str(e).lower():
            gateways = agentcore_control.list_gateways()
            for gw in gateways.get("items", []):
                if gw.get("name") == GATEWAY_NAME:
                    gateway_id = gw["gatewayId"]
                    gw_detail = agentcore_control.get_gateway(gatewayIdentifier=gateway_id)
                    gateway_endpoint = gw_detail["gatewayUrl"]
                    break
            print(f"  [INFO] Gateway already exists: {GATEWAY_NAME}")
        else:
            raise

    print(f"  Gateway ID: {gateway_id}")
    print(f"  Endpoint:   {gateway_endpoint}")

    # Wait for gateway to become ACTIVE
    print("  Waiting for gateway to become ACTIVE...")
    for _ in range(60):
        gw_detail = agentcore_control.get_gateway(gatewayIdentifier=gateway_id)
        status = gw_detail.get("status", "UNKNOWN")
        if status == "ACTIVE" or status == "READY":
            break
        time.sleep(5)
    else:
        print(f"  [WARN] Gateway still in {status} state after timeout")
    print(f"  [OK] Gateway is {status}")
    print()

    # --- Register tools ---
    print("=" * 60)
    print("STEP 5: Register Lambda as Tool Target")
    print("=" * 60)

    with open(f"{LAMBDA_DIR}/tool_schemas.json", "r") as f:
        tool_schemas = json.load(f)

    print(f"  Loaded {len(tool_schemas)} tool schemas")

    try:
        agentcore_control.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=LAMBDA_FUNCTION_NAME,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_arn,
                        "toolSchema": {"inlinePayload": tool_schemas},
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
            description="Travel domain tools - flights, hotels, car rentals, restaurants, currency, loyalty, weather, activities, trip planning",
        )
        print(f"  [OK] Registered {len(tool_schemas)} tools with gateway")
    except Exception as e:
        if "already exists" in str(e).lower() or "conflict" in str(e).lower():
            print("  [INFO] Tool target already registered")
        else:
            raise

    # Verify registration
    time.sleep(5)
    targets = agentcore_control.list_gateway_targets(gatewayIdentifier=gateway_id)
    print(f"  [OK] Gateway has {len(targets.get('items', []))} registered target(s)")
    print()

    # Save state for invoke/cleanup
    state = {
        "gateway_id": gateway_id,
        "gateway_endpoint": gateway_endpoint,
        "lambda_arn": lambda_arn,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

    print("=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"  Gateway endpoint: {gateway_endpoint}")
    print(f"  Tools registered: {len(tool_schemas)}")
    print()
    print("  Run 'python invoke.py' to test the agent")


if __name__ == "__main__":
    deploy()
