"""Deploy SQL injection prevention gateway with Lambda interceptor.

Deploys:
1. CloudFormation stack with interceptor Lambda + tool Lambda
2. AgentCore Gateway with REQUEST interceptor (using shared Cognito)
3. Gateway target for the customer query tool

Requires COGNITO_STACK_NAME in environment.

Usage:
    uv run python scripts/prevent-sql-injection/deploy.py
"""

import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from gateway_admin import GatewayBoto3Client

LAMBDA_STACK_NAME = "agentcore-sql-injection-lambdas"
GATEWAY_NAME = "sql-injection-prevention-gateway"
TOOL_DEFINITION = {
    "name": "customer_query_tool",
    "description": "Query customer database. Accepts query string parameter.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Query string to search customers",
            }
        },
        "required": ["query"],
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

    cognito_stack = get_required_env("COGNITO_STACK_NAME")
    region = boto3.Session().region_name

    cfn = boto3.client("cloudformation", region_name=region)
    admin = GatewayBoto3Client(region=region)
    control = admin.client

    # --- Step 1: Deploy Lambda stack ---
    print("=" * 60)
    print("Step 1: Deploy Lambda Functions (CloudFormation)")
    print("=" * 60)

    template_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "cloudformation",
        "sql-injection",
        "sql-injection-stack.yaml",
    )
    with open(template_path) as f:
        template_body = f.read()

    try:
        cfn.create_stack(
            StackName=LAMBDA_STACK_NAME,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_NAMED_IAM"],
        )
        print(f"  Creating stack: {LAMBDA_STACK_NAME}")
        waiter = cfn.get_waiter("stack_create_complete")
        waiter.wait(
            StackName=LAMBDA_STACK_NAME,
            WaiterConfig={"Delay": 10, "MaxAttempts": 60},
        )
        print("  Stack CREATE_COMPLETE")
    except cfn.exceptions.AlreadyExistsException:
        print(f"  Stack already exists: {LAMBDA_STACK_NAME}")

    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=LAMBDA_STACK_NAME)["Stacks"][0][
            "Outputs"
        ]
    }
    interceptor_arn = outputs["InterceptorFunctionArn"]
    tool_arn = outputs["ToolFunctionArn"]
    print(f"  Interceptor ARN: {interceptor_arn}")
    print(f"  Tool ARN: {tool_arn}")

    # --- Step 2: Get Cognito outputs ---
    print("\n" + "=" * 60)
    print("Step 2: Get Cognito Configuration (shared stack)")
    print("=" * 60)

    cognito_outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=cognito_stack)["Stacks"][0]["Outputs"]
    }
    discovery_url = cognito_outputs["DiscoveryUrl"]
    gw_client_id = cognito_outputs["GatewayClientId"]
    print(f"  Discovery URL: {discovery_url}")
    print(f"  Gateway Client ID: {gw_client_id}")

    # --- Step 3: Create Gateway with REQUEST interceptor ---
    print("\n" + "=" * 60)
    print("Step 3: Create Gateway with REQUEST Interceptor")
    print("=" * 60)

    role_arn = admin.create_gateway_role(GATEWAY_NAME, lambda_targets=True)

    gw_resp = control.create_gateway(
        name=GATEWAY_NAME,
        roleArn=role_arn,
        protocolType="MCP",
        protocolConfiguration={
            "mcp": {"supportedVersions": ["2025-03-26", "2025-11-25"]}
        },
        interceptorConfigurations=[
            {
                "interceptor": {"lambda": {"arn": interceptor_arn}},
                "interceptionPoints": ["REQUEST"],
                "inputConfiguration": {"passRequestHeaders": True},
            }
        ],
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [gw_client_id],
            }
        },
        exceptionLevel="DEBUG",
    )

    gateway_id = gw_resp["gatewayId"]
    print(f"  Gateway ID: {gateway_id}")

    print("  Waiting for Gateway to become READY...")
    while True:
        time.sleep(10)
        gw = control.get_gateway(gatewayIdentifier=gateway_id)
        status = gw["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "CREATE_FAILED"]:
            break

    gateway_url = gw["gatewayUrl"]
    print(f"  Gateway URL: {gateway_url}")

    # --- Step 4: Create Gateway target ---
    print("\n" + "=" * 60)
    print("Step 4: Register Customer Query Tool as Gateway Target")
    print("=" * 60)

    target_resp = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name="customer-query-tool-target",
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
    print(f"  Target ID: {target_id}")

    print("  Waiting for Target to become READY...")
    while True:
        time.sleep(10)
        tgt = control.get_gateway_target(
            gatewayIdentifier=gateway_id, targetId=target_id
        )
        status = tgt["status"]
        print(f"    Status: {status}")
        if status in ["READY", "FAILED", "UPDATE_UNSUCCESSFUL"]:
            break

    # --- Save state ---
    save_env(
        {
            "LAMBDA_STACK_NAME": LAMBDA_STACK_NAME,
            "GATEWAY_NAME": GATEWAY_NAME,
            "GATEWAY_ID": gateway_id,
            "GATEWAY_URL": gateway_url,
            "TARGET_ID": target_id,
            "INTERCEPTOR_ARN": interceptor_arn,
            "TOOL_ARN": tool_arn,
        }
    )

    print("\n" + "=" * 60)
    print("Deployment complete")
    print("=" * 60)
    print(f"\n  Gateway URL: {gateway_url}")
    print(f"  Interceptor: {interceptor_arn}")
    print(f"  Tool: {tool_arn}")
    print("\n  Run: uv run python scripts/prevent-sql-injection/invoke.py")


if __name__ == "__main__":
    main()
