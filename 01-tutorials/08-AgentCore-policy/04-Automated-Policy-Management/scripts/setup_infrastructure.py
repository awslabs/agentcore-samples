"""
Setup script for Automated Policy Management demo.

Deploys two Lambda functions (FinancialReportTool, TradeExecutionTool),
creates an AgentCore Gateway, attaches the Lambda targets with their tool schemas,
and saves a config.json that includes the RBAC permission manifest emitted
for each tool.

Usage:
    python setup_infrastructure.py
"""

import boto3
import io
import json
import logging
import os
import time
import zipfile
from pathlib import Path

from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

# ---------------------------------------------------------------------------
# RBAC manifest emitted when tools are attached to the Gateway.
# In a real system this would be part of the tool registration contract —
# the team that owns each tool declares the access-control requirements here.
# The policy orchestrator reads this manifest to auto-generate Cedar policies.
# ---------------------------------------------------------------------------
TOOL_RBAC_MANIFEST = {
    "FinancialReportTarget___get_financial_report": {
        "target_name": "FinancialReportTarget",
        "function_name": "get_financial_report",
        "description": (
            "Retrieve financial reports from the data platform. "
            "Restricted to internal data classification and approved geographic regions."
        ),
        "rbac": {
            "allowed_roles": ["analyst", "senior-analyst", "manager"],
            "data_classification": "internal",
            "requires_approval": False,
            "constraints": {
                "classification_level": {
                    "type": "string",
                    "enum": ["internal"],
                    "description": "Only internal classification is permitted",
                },
                "region": {
                    "type": "string",
                    "enum": ["US", "EU", "APAC"],
                    "description": "Allowed geographic regions",
                },
            },
        },
    },
    "TradeExecutionTarget___execute_trade": {
        "target_name": "TradeExecutionTarget",
        "function_name": "execute_trade",
        "description": (
            "Execute financial trades on the platform. "
            "Restricted to authorised traders; trade amount capped at $500K."
        ),
        "rbac": {
            "allowed_roles": ["trader", "portfolio-manager"],
            "data_classification": "restricted",
            "requires_approval": True,
            "approval_threshold": 100000,
            "constraints": {
                "amount": {
                    "type": "integer",
                    "max": 500000,
                    "description": "Maximum single-trade amount in USD",
                },
            },
        },
    },
}

# Tool schemas for the Gateway targets
FINANCIAL_REPORT_SCHEMA = [
    {
        "name": "get_financial_report",
        "description": (
            "Retrieve financial reports from the data platform. "
            "Requires analyst, senior-analyst, or manager role. "
            "Only internal data classification is permitted. "
            "Available regions: US, EU, APAC."
        ),
        "inputSchema": {
            "type": "object",
            "description": "Input parameters for financial report retrieval",
            "properties": {
                "report_type": {
                    "type": "string",
                    "description": "Type of report: quarterly, annual, or monthly",
                },
                "region": {
                    "type": "string",
                    "description": "Geographic region: US, EU, or APAC",
                },
                "classification_level": {
                    "type": "string",
                    "description": "Data classification level: internal or public",
                },
            },
            "required": ["report_type", "region", "classification_level"],
        },
    }
]

TRADE_EXECUTION_SCHEMA = [
    {
        "name": "execute_trade",
        "description": (
            "Execute financial trades on the platform. "
            "Requires trader or portfolio-manager role. "
            "Trade amount must not exceed $500,000."
        ),
        "inputSchema": {
            "type": "object",
            "description": "Input parameters for trade execution",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol (e.g. AMZN, MSFT)",
                },
                "amount": {
                    "type": "integer",
                    "description": "Trade amount in USD (max 500000)",
                },
                "trade_type": {
                    "type": "string",
                    "description": "Type of trade: buy or sell",
                },
            },
            "required": ["ticker", "amount", "trade_type"],
        },
    }
]


def get_or_create_lambda_role(iam_client: boto3.client) -> str:
    role_name = "AgentCoreAutoPolicyLambdaRole"
    try:
        resp = iam_client.get_role(RoleName=role_name)
        print(f"   Using existing IAM role: {role_name}")
        return resp["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        print(f"   Creating IAM role: {role_name}")
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
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Lambda execution role for AgentCore Auto Policy Management demo",
        )
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        print("   Waiting 15s for IAM propagation...")
        time.sleep(15)
        return resp["Role"]["Arn"]


def deploy_lambda(
    lambda_client: boto3.client,
    function_name: str,
    js_filename: str,
    role_arn: str,
    region: str,
) -> str:
    """Deploy or update a Lambda function from a JS file in lambda-tools/."""
    print(f"   Deploying {function_name}...")

    js_path = Path(__file__).parent / "lambda-tools" / js_filename
    with open(js_path, "r") as f:
        code_content = f.read()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.mjs", code_content)
    buf.seek(0)
    zip_bytes = buf.read()

    try:
        resp = lambda_client.create_function(
            FunctionName=function_name,
            Runtime="nodejs20.x",
            Role=role_arn,
            Handler="index.handler",
            Code={"ZipFile": zip_bytes},
            Description=f"AgentCore Auto Policy Management - {function_name}",
            Timeout=30,
            MemorySize=256,
        )
        print(f"   Created: {resp['FunctionArn']}")
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        resp = lambda_client.update_function_code(
            FunctionName=function_name, ZipFile=zip_bytes
        )
        print(f"   Updated: {resp['FunctionArn']}")
        return resp["FunctionArn"]


def setup_infrastructure(region: str = "us-east-1") -> dict:
    """
    Full setup: Lambda functions + Gateway + targets.
    Returns the complete config dict (also written to config.json).
    """
    print("=" * 70)
    print("AgentCore Automated Policy Management - Infrastructure Setup")
    print("=" * 70)
    print(f"Region: {region}\n")

    lambda_client = boto3.client("lambda", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    # -----------------------------------------------------------------------
    # Step 1: IAM role
    # -----------------------------------------------------------------------
    print("Step 1: IAM role")
    role_arn = get_or_create_lambda_role(iam_client)
    print(f"   Role ARN: {role_arn}\n")

    # -----------------------------------------------------------------------
    # Step 2: Deploy Lambda functions
    # -----------------------------------------------------------------------
    print("Step 2: Deploy Lambda functions")
    financial_arn = deploy_lambda(
        lambda_client,
        "FinancialReportTool",
        "financial_report_tool.js",
        role_arn,
        region,
    )
    time.sleep(1)
    trade_arn = deploy_lambda(
        lambda_client,
        "TradeExecutionTool",
        "trade_execution_tool.js",
        role_arn,
        region,
    )
    print()

    # -----------------------------------------------------------------------
    # Step 3: Create Gateway
    # -----------------------------------------------------------------------
    print("Step 3: Create AgentCore Gateway")
    gw_client = GatewayClient(region_name=region)
    gw_client.logger.setLevel(logging.WARNING)

    cognito_resp = gw_client.create_oauth_authorizer_with_cognito(
        "FinancialDataPlatformGateway"
    )
    print("   OAuth authorizer created")

    gateway = gw_client.create_mcp_gateway(
        name="GW-FinancialDataPlatform",
        role_arn=None,
        authorizer_config=cognito_resp["authorizer_config"],
        enable_semantic_search=True,
    )
    print(f"   Gateway created: {gateway['gatewayUrl']}")

    gw_client.fix_iam_permissions(gateway)
    print("   Waiting 30s for IAM propagation...")
    time.sleep(30)

    # -----------------------------------------------------------------------
    # Step 4: Attach Lambda targets  (this "emits" the RBAC permissions)
    # -----------------------------------------------------------------------
    print("\nStep 4: Attach Lambda targets (RBAC permissions emitted)")
    gateway_arn = None

    financial_target = gw_client.create_mcp_gateway_target(
        gateway=gateway,
        name="FinancialReportTarget",
        target_type="lambda",
        target_payload={
            "lambdaArn": financial_arn,
            "toolSchema": {"inlinePayload": FINANCIAL_REPORT_SCHEMA},
        },
        credentials=None,
    )
    gateway_arn = financial_target.get("gatewayArn")
    print("   FinancialReportTarget attached")

    gw_client.create_mcp_gateway_target(
        gateway=gateway,
        name="TradeExecutionTarget",
        target_type="lambda",
        target_payload={
            "lambdaArn": trade_arn,
            "toolSchema": {"inlinePayload": TRADE_EXECUTION_SCHEMA},
        },
        credentials=None,
    )
    print("   TradeExecutionTarget attached")

    # -----------------------------------------------------------------------
    # Step 5: Save config
    # -----------------------------------------------------------------------
    config = {
        "region": region,
        "lambdas": {
            "FinancialReportTool": financial_arn,
            "TradeExecutionTool": trade_arn,
        },
        "gateway": {
            "gateway_url": gateway["gatewayUrl"],
            "gateway_id": gateway["gatewayId"],
            "gateway_arn": gateway_arn or gateway.get("gatewayArn"),
            "gateway_name": "GW-FinancialDataPlatform",
            "client_info": cognito_resp["client_info"],
        },
        # RBAC manifest emitted when the tools were registered on the Gateway.
        # The policy orchestrator reads this to auto-generate Cedar policies.
        "rbac_manifest": TOOL_RBAC_MANIFEST,
    }

    config_path = Path(__file__).parent.parent / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 70)
    print("Infrastructure setup complete!")
    print("=" * 70)
    print(f"Gateway URL : {gateway['gatewayUrl']}")
    print(f"Gateway ID  : {gateway['gatewayId']}")
    print(f"Gateway ARN : {config['gateway']['gateway_arn']}")
    print(f"Config saved: {config_path}")
    print("=" * 70)

    return config


if __name__ == "__main__":
    session = boto3.Session()
    region = session.region_name or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    setup_infrastructure(region)
