"""Deploy resources for the Semantic Search tutorial.

Creates:
  1. Two Lambda functions (calc, restaurant) from zip files
  2. Cognito User Pool with test user for inbound OAuth
  3. IAM role for the gateway (with lambda:InvokeFunction)
  4. AgentCore Gateway with semantic search enabled
  5. Five Lambda targets (FoodTools, CalcTools, Calc2, Calc3, Calc4) to
     demonstrate 300+ tools

Saves all resource identifiers to a local .env so invoke.py and cleanup.py
can reference them.

Requires:
  - The calc/ and restaurant/ directories with lambda_function_code.zip and
    *-api.json at the tutorial root (04-advanced-concepts/semantic-search-tool/).

Usage:
    uv run python scripts/semantic-search/deploy.py
"""

import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GATEWAY_NAME = "gateway-search-tutorial"
GATEWAY_DESCRIPTION = "AgentCore Gateway Tutorial"

# Paths are relative to gatewaylabproject/app/semantic-search/
APP_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "app", "semantic-search")
)

CALC_SOURCE = os.path.join(APP_DIR, "calc_lambda.py")
CALC_API_SPEC = os.path.join(APP_DIR, "calc-api.json")
RESTAURANT_SOURCE = os.path.join(APP_DIR, "restaurant_lambda.py")
RESTAURANT_API_SPEC = os.path.join(APP_DIR, "restaurant-api.json")

CALC_LAMBDA_NAME = "calc_lambda_gateway"
RESTAURANT_LAMBDA_NAME = "restaurant_lambda_gateway"

COGNITO_POOL_NAME = "MCPServerPool"
COGNITO_CLIENT_NAME = "MCPServerPoolClient"
COGNITO_USERNAME = "testuser"
COGNITO_TEMP_PASSWORD = "Temp123!"  # pragma: allowlist secret
COGNITO_PASSWORD = "MyPassword123!"  # pragma: allowlist secret

LAMBDA_RUNTIME = "python3.12"
LAMBDA_HANDLER = "lambda_function_code.lambda_handler"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)


def save_env(env_vars: dict):
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "w") as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    print(f"\n  State saved to {env_path}")


def read_apispec(json_path: str) -> list:
    with open(json_path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Lambda creation
# ---------------------------------------------------------------------------


def create_lambda_function(
    lambda_client, iam_client, function_name: str, source_path: str
) -> str:
    """Create a Lambda function from source file (or reuse existing). Returns the function ARN."""
    role_name = f"{function_name}_lambda_iamrole"
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

    # Create or get IAM role
    try:
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="IAM role for Lambda function",
        )
        role_arn = resp["Role"]["Arn"]
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print(f"  Created IAM role: {role_name}")
        time.sleep(20)  # Wait for IAM propagation
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = iam_client.get_role(RoleName=role_name)["Role"]["Arn"]
            print(f"  IAM role already exists: {role_name}")
        else:
            raise

    # Create or get Lambda function — build zip from source
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source_path, "lambda_function.py")
    code = buf.getvalue()

    try:
        resp = lambda_client.create_function(
            FunctionName=function_name,
            Role=role_arn,
            Runtime=LAMBDA_RUNTIME,
            Handler=LAMBDA_HANDLER,
            Code={"ZipFile": code},
            Description="Lambda function for AgentCore Gateway semantic search tutorial",
            PackageType="Zip",
        )
        arn = resp["FunctionArn"]
        print(f"  Created Lambda: {function_name} ({arn})")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceConflictException":
            arn = lambda_client.get_function(FunctionName=function_name)[
                "Configuration"
            ]["FunctionArn"]
            print(f"  Lambda already exists: {function_name} ({arn})")
        else:
            raise

    return arn


# ---------------------------------------------------------------------------
# Cognito setup
# ---------------------------------------------------------------------------


def setup_cognito(cognito_client, region: str) -> dict:
    """Create Cognito User Pool, app client, and test user. Returns client_id and discovery_url."""
    print("\n--- Setting up Cognito User Pool ---")

    resp = cognito_client.create_user_pool(
        PoolName=COGNITO_POOL_NAME,
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = resp["UserPool"]["Id"]
    print(f"  User Pool ID: {pool_id}")

    resp = cognito_client.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=COGNITO_CLIENT_NAME,
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = resp["UserPoolClient"]["ClientId"]
    print(f"  Client ID: {client_id}")

    cognito_client.admin_create_user(
        UserPoolId=pool_id,
        Username=COGNITO_USERNAME,
        TemporaryPassword=COGNITO_TEMP_PASSWORD,
        MessageAction="SUPPRESS",
    )
    cognito_client.admin_set_user_password(
        UserPoolId=pool_id,
        Username=COGNITO_USERNAME,
        Password=COGNITO_PASSWORD,
        Permanent=True,
    )
    print(f"  Created test user: {COGNITO_USERNAME}")

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    return {
        "pool_id": pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
    }


# ---------------------------------------------------------------------------
# Gateway + Targets
# ---------------------------------------------------------------------------


def create_gateway_with_search(admin: GatewayBoto3Client, cognito_info: dict) -> dict:
    """Create the gateway with semantic search enabled."""
    print("\n--- Creating Gateway with Semantic Search ---")

    role_arn = admin.create_gateway_role(GATEWAY_NAME, lambda_targets=True)

    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [cognito_info["client_id"]],
            "discoveryUrl": cognito_info["discovery_url"],
        }
    }
    search_config = {
        "mcp": {"searchType": "SEMANTIC", "supportedVersions": ["2025-03-26"]}
    }

    response = admin.client.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        authorizerType="CUSTOM_JWT",
        description=GATEWAY_DESCRIPTION,
        protocolType="MCP",
        authorizerConfiguration=auth_config,
        protocolConfiguration=search_config,
        exceptionLevel="DEBUG",
    )

    gateway_id = response["gatewayId"]
    gateway_url = response["gatewayUrl"]
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway URL: {gateway_url}")
    return {"gateway_id": gateway_id, "gateway_url": gateway_url}


def create_target(
    admin: GatewayBoto3Client,
    gateway_id: str,
    target_name: str,
    target_desc: str,
    lambda_arn: str,
    api_spec: list,
) -> str:
    """Create a Lambda-backed gateway target. Returns target ID."""
    response = admin.client.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description=target_desc,
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": api_spec},
                }
            }
        },
        credentialProviderConfigurations=[
            {"credentialProviderType": "GATEWAY_IAM_ROLE"}
        ],
    )
    target_id = response["targetId"]
    print(f"  Created target: {target_name} (ID: {target_id})")
    return target_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_env()

    cognito_stack = os.environ.get("COGNITO_STACK_NAME", "agentcore-gateway-lab")

    session = boto3.Session()
    region = session.region_name
    cfn = boto3.client("cloudformation", region_name=region)

    admin = GatewayBoto3Client(region=region)

    # 1. Read Lambda ARNs from CloudFormation stack
    lambda_stack = os.environ.get(
        "LAMBDA_STACK_NAME", "agentcore-semantic-search-lambdas"
    )
    print("--- Reading Lambda ARNs from CloudFormation ---")
    try:
        stack_outputs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cfn.describe_stacks(StackName=lambda_stack)["Stacks"][0]["Outputs"]
        }
        calc_arn = stack_outputs["CalcFunctionArn"]
        restaurant_arn = stack_outputs["RestaurantFunctionArn"]
        print(f"  Calc ARN: {calc_arn}")
        print(f"  Restaurant ARN: {restaurant_arn}")
    except Exception as e:
        print(f"ERROR: Could not read stack {lambda_stack}: {e}")
        print("  Deploy the CloudFormation stack first (see README Step 1).")
        sys.exit(1)

    # 2. Read shared Cognito outputs
    print("\n--- Reading Cognito Configuration (shared stack) ---")
    cognito_outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = cognito_outputs["DiscoveryUrl"]
    gw_client_id = cognito_outputs["GatewayClientId"]
    print(f"  Discovery URL: {discovery_url}")
    print(f"  Gateway Client ID: {gw_client_id}")

    # 3. Create Gateway with semantic search
    print("\n--- Creating Gateway with Semantic Search ---")
    role_arn = admin.create_gateway_role(GATEWAY_NAME, lambda_targets=True)

    try:
        response = admin.client.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            authorizerType="CUSTOM_JWT",
            description=GATEWAY_DESCRIPTION,
            protocolType="MCP",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "allowedClients": [gw_client_id],
                    "discoveryUrl": discovery_url,
                }
            },
            protocolConfiguration={
                "mcp": {
                    "searchType": "SEMANTIC",
                    "supportedVersions": ["2025-11-25"],
                }
            },
            exceptionLevel="DEBUG",
        )
        gateway_id = response["gatewayId"]
        gateway_url = response["gatewayUrl"]
        print(f"  Gateway ID: {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")

        print("  Waiting for gateway to become READY...")
        while True:
            time.sleep(10)
            gw = admin.client.get_gateway(gatewayIdentifier=gateway_id)
            status = gw["status"]
            print(f"    Status: {status}")
            if status in ["READY", "FAILED", "CREATE_FAILED"]:
                break
    except admin.client.exceptions.ConflictException:
        print(f"  Gateway already exists: {GATEWAY_NAME}")
        gateways = admin.client.list_gateways(maxResults=50)
        gw_item = next(
            (g for g in gateways.get("items", []) if g["name"] == GATEWAY_NAME),
            None,
        )
        if not gw_item:
            print("ERROR: Gateway exists but could not find it in list.")
            sys.exit(1)
        gateway_id = gw_item["gatewayId"]
        gateway_url = (
            f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"
        )
        print(f"  Gateway ID: {gateway_id}")
        print(f"  Gateway URL: {gateway_url}")

    # 4. Create Targets
    print("\n--- Creating Gateway Targets ---")
    restaurant_api = read_apispec(RESTAURANT_API_SPEC)
    calc_api = read_apispec(CALC_API_SPEC)

    target_ids = []

    target_ids.append(
        create_target(
            admin,
            gateway_id,
            "FoodTools",
            "Restaurant Tools",
            restaurant_arn,
            restaurant_api,
        )
    )
    time.sleep(5)

    target_ids.append(
        create_target(
            admin, gateway_id, "CalcTools", "Calculation Tools", calc_arn, calc_api
        )
    )
    time.sleep(10)

    for name, desc in [
        ("Calc2", "Calculation 2 Tools"),
        ("Calc3", "Calculation 3 Tools"),
        ("Calc4", "Calculation 4 Tools"),
    ]:
        target_ids.append(
            create_target(admin, gateway_id, name, desc, calc_arn, calc_api)
        )
        time.sleep(10)

    print(f"\n  Total targets created: {len(target_ids)}")
    print("  Estimated tool count: 300+")

    # 5. Save state
    state = {
        "GATEWAY_ID": gateway_id,
        "GATEWAY_URL": gateway_url,
        "GATEWAY_NAME": GATEWAY_NAME,
    }
    for i, tid in enumerate(target_ids):
        state[f"TARGET_{i}_ID"] = tid

    save_env(state)
    print("\nDeploy complete.")


if __name__ == "__main__":
    main()
