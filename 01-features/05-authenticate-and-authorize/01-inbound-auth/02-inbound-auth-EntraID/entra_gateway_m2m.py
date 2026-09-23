"""
Entra ID M2M Authentication for AgentCore Gateway.

Demonstrates how to configure an Amazon Bedrock AgentCore Gateway with
Microsoft Entra ID machine-to-machine (M2M) authentication using the
OAuth 2.0 client credentials flow.

Architecture:
    Client app → Entra ID (client_credentials) → access_token
    Client + access_token → AgentCore Gateway → validate JWT → Lambda tools
    Strands Agent + MCPClient → Gateway → Lambda → response

Usage:
    python entra_gateway_m2m.py
    python entra_gateway_m2m.py --cleanup

Prerequisites:
    - AWS CLI configured
    - Microsoft Entra ID tenant with:
        - API app registration (exposes weather/directions roles)
        - Client app registration (assigned API permissions, admin consent granted)
    - pip install -r requirements.txt
    - Set environment variables:
        ENTRA_TENANT_ID       - Directory (tenant) ID
        ENTRA_CLIENT_ID       - Client application (client) ID
        ENTRA_CLIENT_SECRET   - Client application secret
        ENTRA_APP_ID_URI      - API application URI (e.g. api://3dXX...885f25)

Entra ID Setup:
    1. Register an API app (weather_service):
       - Manage > Expose an API > set Application ID URI
       - App Roles > add mcp_invoke role
    2. Register a client app (weather_service_client):
       - Certificates & Secrets > New client secret
       - API Permissions > APIs my org uses > weather_service > grant admin consent
    3. Collect Tenant ID, Client ID, Client Secret, App ID URI
"""

import argparse
import json
import os
import time
import zipfile
from io import BytesIO

import boto3
import requests
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────

GATEWAY_NAME = f"entra-m2m-gateway-{int(time.time()) % 100000}"
LAMBDA_NAME = f"entra-m2m-lambda-{int(time.time()) % 100000}"
LAMBDA_ROLE_NAME = f"entra-m2m-lambda-role-{int(time.time()) % 100000}"
GATEWAY_ROLE_NAME = f"entra-m2m-gateway-role-{int(time.time()) % 100000}"
CONFIG_FILE = "gateway_m2m_config.json"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name or "us-west-2"
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

print(f"Region:  {REGION}")
print(f"Account: {ACCOUNT_ID}")


# ── Lambda Function Code ───────────────────────────────────────────────────────

LAMBDA_CODE = """
def lambda_handler(event, context):
    print(f"Event: {event}")
    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    resource = extended_tool_name.split("___")[1]
    city = event.get("city", "Unknown")

    if resource == "weather_check":
        return f"Weather in {city} is bright and sunny!"
    elif resource == "directions":
        return f"Take I-5 south all the way to {city} downtown."
    return "Unknown tool"
"""


# ── Step 1: Create Lambda Function ────────────────────────────────────────────


def create_lambda(iam: object) -> str:
    """Create IAM role and Lambda function for Gateway target."""
    lam = boto3.client("lambda", region_name=REGION)

    # Lambda execution role
    trust = json.dumps(
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
    )
    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": "arn:aws:logs:*:*:*",
                }
            ],
        }
    )

    try:
        role_resp = iam.create_role(
            RoleName=LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=trust,
        )
        lambda_role_arn = role_resp["Role"]["Arn"]
        iam.put_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyName="logs", PolicyDocument=policy)
        time.sleep(10)
        print(f"  Created Lambda role: {LAMBDA_ROLE_NAME}")
    except iam.exceptions.EntityAlreadyExistsException:
        lambda_role_arn = iam.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]
        print(f"  Reusing Lambda role: {LAMBDA_ROLE_NAME}")

    # Create zip
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lambda_function.py", LAMBDA_CODE)
    buf.seek(0)

    try:
        resp = lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.12",
            Role=lambda_role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": buf.read()},
        )
        lambda_arn = resp["FunctionArn"]
        print(f"  Created Lambda: {LAMBDA_NAME}")
    except lam.exceptions.ResourceConflictException:
        lambda_arn = lam.get_function(FunctionName=LAMBDA_NAME)["Configuration"]["FunctionArn"]
        print(f"  Reusing Lambda: {LAMBDA_NAME}")

    return lambda_arn, lambda_role_arn


# ── Step 2: Create Gateway IAM Role ───────────────────────────────────────────


def create_gateway_role(iam: object, lambda_arn: str) -> str:
    """Create IAM role that allows Gateway to invoke Lambda."""
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )
    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": lambda_arn,
                }
            ],
        }
    )

    try:
        resp = iam.create_role(RoleName=GATEWAY_ROLE_NAME, AssumeRolePolicyDocument=trust)
        gateway_role_arn = resp["Role"]["Arn"]
        iam.put_role_policy(
            RoleName=GATEWAY_ROLE_NAME,
            PolicyName="invoke-lambda",
            PolicyDocument=policy,
        )
        time.sleep(5)
        print(f"  Created Gateway role: {GATEWAY_ROLE_NAME}")
    except iam.exceptions.EntityAlreadyExistsException:
        gateway_role_arn = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]["Arn"]
        print(f"  Reusing Gateway role: {GATEWAY_ROLE_NAME}")

    return gateway_role_arn


# ── Step 3: Create Gateway with Entra M2M Auth ────────────────────────────────


def create_gateway(gateway_role_arn: str) -> dict:
    """Create AgentCore Gateway with Entra ID customJWTAuthorizer."""
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    app_id_uri = os.environ.get("ENTRA_APP_ID_URI")

    if not tenant_id or not app_id_uri:
        raise ValueError("Set ENTRA_TENANT_ID and ENTRA_APP_ID_URI environment variables.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    try:
        resp = control.create_gateway(
            name=GATEWAY_NAME,
            roleArn=gateway_role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "allowedAudience": [app_id_uri],
                    "discoveryUrl": (f"https://login.microsoftonline.com/{tenant_id}/.well-known/openid-configuration"),
                }
            },
        )
        gateway_id = resp["gatewayId"]
        gateway_url = resp["gatewayUrl"]
        print(f"  Created Gateway: {GATEWAY_NAME}")
        print(f"  Gateway URL: {gateway_url}")
    except control.exceptions.ConflictException:
        existing = control.list_gateways()
        for gw in existing.get("gateways", []):
            if gw["name"] == GATEWAY_NAME:
                gateway_id = gw["gatewayId"]
                gateway_url = gw["gatewayUrl"]
                break
        print(f"  Reusing Gateway: {GATEWAY_NAME}")

    # Wait for the gateway to reach READY before subsequent operations
    # (e.g. create_gateway_target, which fails with
    # "Cannot perform operation ... when gateway is in CREATING status").
    print("  Waiting for Gateway to become READY...")
    end_states = {"READY", "FAILED", "DELETE_FAILED"}
    while True:
        status_resp = control.get_gateway(gatewayIdentifier=gateway_id)
        status = status_resp.get("status", "")
        if status in end_states:
            break
        time.sleep(5)
    if status != "READY":
        raise RuntimeError(f"Gateway creation failed: {status}")
    print(f"  Gateway READY: {gateway_id}")

    return {"gateway_id": gateway_id, "gateway_url": gateway_url}


# ── Step 4: Create Lambda Target ──────────────────────────────────────────────


def create_lambda_target(gateway_id: str, lambda_arn: str) -> str:
    """Add Lambda function as MCP tool target on the Gateway."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    api_spec = [
        {
            "name": "weather_check",
            "description": "Check the weather for a given city",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "The city"}},
                "required": ["city"],
            },
        },
        {
            "name": "directions",
            "description": "Get driving directions to a city",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "The city"}},
                "required": ["city"],
            },
        },
    ]

    resp = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="LambdaUsingSDK",
        description="Lambda target for weather and directions tools",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": api_spec},
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    target_id = resp["targetId"]
    print(f"  Created Lambda target: LambdaUsingSDK (ID: {target_id})")
    return target_id


# ── Step 5: Get Entra Access Token (client_credentials) ───────────────────────


def get_entra_m2m_token() -> str:
    """Fetch access token via Entra client_credentials grant."""
    tenant_id = os.environ.get("ENTRA_TENANT_ID")
    client_id = os.environ.get("ENTRA_CLIENT_ID")
    client_secret = os.environ.get("ENTRA_CLIENT_SECRET")
    app_id_uri = os.environ.get("ENTRA_APP_ID_URI")

    if not all([tenant_id, client_id, client_secret, app_id_uri]):
        raise ValueError("Set ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, ENTRA_APP_ID_URI.")

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": f"{app_id_uri}/.default",
    }
    resp = requests.post(  # nosec B113
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not resp.ok:
        raise RuntimeError(
            f"Token endpoint returned {resp.status_code}:\n  {resp.text}\n"
            "Verify ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET, ENTRA_APP_ID_URI."
        )
    token = resp.json()["access_token"]
    print("  Access token acquired from Entra ID")
    return token


# ── Step 6: List Tools and Invoke Agent ───────────────────────────────────────


def invoke_with_agent(gateway_url: str, access_token: str):
    """Use MCPClient + Strands agent to call Gateway tools."""
    from mcp.client.streamable_http import streamablehttp_client
    from strands import Agent
    from strands.tools.mcp import MCPClient

    mcp_client = MCPClient(
        lambda: streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    )

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        print(f"  Available tools: {[t.tool_name for t in tools]}")

        agent = Agent(tools=tools)
        result = agent("What is the weather in San Diego?")
        print(f"\n  Agent response: {result.message}")

        result2 = agent("Give me directions to Seattle.")
        print(f"\n  Agent response: {result2.message}")


# ── Step 7: Cleanup ────────────────────────────────────────────────────────────


def cleanup():
    """Delete all created resources."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        print("  No config file found.")
        return

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)

    for target_id in config.get("target_ids", []):
        try:
            control.delete_gateway_target(gatewayIdentifier=config["gateway_id"], targetId=target_id)
            print(f"  Deleted target: {target_id} ✓")
        except Exception as e:
            print(f"  Target delete error: {e}")

    # Wait for targets to disappear before deleting the gateway. Target
    # deletion is async; the gateway DeleteGateway call rejects with
    # ValidationException("...has targets associated with it") until the
    # targets are fully gone.
    if config.get("target_ids"):
        print("  Waiting for targets to be removed...")
        for _ in range(24):  # up to ~2 minutes
            remaining = control.list_gateway_targets(gatewayIdentifier=config["gateway_id"]).get("items", [])
            if not remaining:
                break
            time.sleep(5)

    try:
        control.delete_gateway(gatewayIdentifier=config["gateway_id"])
        print(f"  Deleted Gateway: {config['gateway_name']} ✓")
    except Exception as e:
        print(f"  Gateway delete error: {e}")

    try:
        lam.delete_function(FunctionName=config["lambda_name"])
        print(f"  Deleted Lambda: {config['lambda_name']} ✓")
    except Exception as e:
        print(f"  Lambda delete error: {e}")

    for role_name in [config.get("lambda_role_name"), config.get("gateway_role_name")]:
        if not role_name:
            continue
        try:
            for p in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            iam.delete_role(RoleName=role_name)
            print(f"  Deleted role: {role_name} ✓")
        except Exception as e:
            print(f"  Role delete error: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AgentCore Gateway with Entra ID M2M auth")
    parser.add_argument("--cleanup", action="store_true", help="Delete created resources")
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Only run tests using existing gateway_m2m_config.json",
    )
    args = parser.parse_args()

    if args.cleanup:
        print("\n=== Cleaning Up ===")
        cleanup()
        return

    iam = boto3.client("iam", region_name=REGION)

    if args.test_only:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        access_token = get_entra_m2m_token()
        invoke_with_agent(config["gateway_url"], access_token)
        return

    print("=== AgentCore Gateway: Entra ID M2M Auth ===\n")

    print("=== Step 1: Creating Lambda Function ===")
    lambda_arn, lambda_role_arn = create_lambda(iam)

    print("\n=== Step 2: Creating Gateway IAM Role ===")
    gateway_role_arn = create_gateway_role(iam, lambda_arn)

    print("\n=== Step 3: Creating AgentCore Gateway with Entra JWT Authorizer ===")
    gateway_info = create_gateway(gateway_role_arn)

    print("\n=== Step 4: Creating Lambda Target ===")
    target_id = create_lambda_target(gateway_info["gateway_id"], lambda_arn)

    print("\n=== Step 5: Getting Entra ID Access Token ===")
    access_token = get_entra_m2m_token()

    print("\n=== Step 6: Invoking Agent via Gateway ===")
    invoke_with_agent(gateway_info["gateway_url"], access_token)

    # Save config
    config = {
        "gateway_name": GATEWAY_NAME,
        "gateway_id": gateway_info["gateway_id"],
        "gateway_url": gateway_info["gateway_url"],
        "lambda_name": LAMBDA_NAME,
        "lambda_arn": lambda_arn,
        "lambda_role_name": LAMBDA_ROLE_NAME,
        "gateway_role_name": GATEWAY_ROLE_NAME,
        "target_ids": [target_id],
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print("\n=== Summary ===")
    print(f"  Gateway: {GATEWAY_NAME}")
    print(f"  Gateway URL: {gateway_info['gateway_url']}")
    print(f"  Lambda: {LAMBDA_NAME}")
    print("\n  To clean up: python entra_gateway_m2m.py --cleanup")


if __name__ == "__main__":
    main()
