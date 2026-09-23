"""
Setup script to create Gateway with Lambda targets for Insurance Underwriting
Run this after deploying Lambda functions with deploy_lambdas.py
"""

import json
import logging
import sys
import time
import boto3
from pathlib import Path
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

GATEWAY_NAME = "GW-Insurance-Underwriting"


def _find_gateway_by_name(region: str) -> str | None:
    """Return the gateway ID if a gateway with GATEWAY_NAME already exists."""
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        resp = client.list_gateways()
        for gw in resp.get("items", []):
            if gw.get("name") == GATEWAY_NAME and gw.get("status") in [
                "READY",
                "ACTIVE",
            ]:
                return gw["gatewayId"]
    except Exception:
        pass
    return None


def _delete_gateway(region: str, gateway_id: str) -> None:
    """Delete all targets then the gateway itself, waiting for targets to clear."""
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        targets = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        for t in targets:
            client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
        # Wait until all targets are gone (deletion is asynchronous)
        for _ in range(30):
            remaining = client.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            if not remaining:
                break
            time.sleep(3)
        client.delete_gateway(gatewayIdentifier=gateway_id)
        print(f"   Deleted stale gateway and {len(targets)} target(s): {gateway_id}")
        time.sleep(5)
    except Exception as exc:
        print(f"   Warning: could not delete gateway {gateway_id}: {exc}")


def load_config():
    """Load existing config.json"""
    config_file = Path(__file__).parent.parent / "config.json"

    if not config_file.exists():
        print("❌ Error: config.json not found!")
        print(f"   Expected location: {config_file}")
        print("\n   Please run deploy_lambdas.py first to create Lambda functions")
        sys.exit(1)

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f), config_file
    except Exception as exc:
        print(f"❌ Error reading config.json: {exc}")
        sys.exit(1)


def setup_gateway():
    """Setup AgentCore Gateway with Insurance Underwriting Lambda targets"""

    print("🚀 Setting up AgentCore Gateway for Insurance Underwriting...\n")

    # Load existing configuration (contains the region set by deploy_lambdas.py)
    print("📦 Loading configuration...")
    existing_config, config_file = load_config()

    region = existing_config.get("region")
    if not region:
        raise ValueError("Region not found in config.json. Please run deploy_lambdas.py first.")

    print(f"Region: {region}\n")

    # --- Idempotency: reuse existing gateway if config is complete --------
    saved_gateway = existing_config.get("gateway", {})
    saved_gw_id = saved_gateway.get("gateway_id")
    if saved_gw_id:
        boto_ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
        try:
            gw_status = boto_ctrl.get_gateway(gatewayIdentifier=saved_gw_id).get("status")
            if gw_status in ("READY", "ACTIVE"):
                print(f"✅ Reusing existing gateway from config: {saved_gw_id}")
                print(f"   Gateway URL: {saved_gateway.get('gateway_url')}")
                print("=" * 70)
                return existing_config
        except Exception:
            print(f"   Gateway {saved_gw_id} not found in AWS — will create fresh.")

    # --- No config: detect and remove stale gateway by name ---------------
    stale_id = _find_gateway_by_name(region)
    if stale_id:
        print(f"⚠️  Found stale gateway '{GATEWAY_NAME}' ({stale_id}) with no saved config.")
        print("   Deleting it so a fresh one can be created...")
        _delete_gateway(region, stale_id)
    # -----------------------------------------------------------------------

    lambda_config = existing_config.get("lambdas", {})

    if not lambda_config:
        print("❌ No Lambda functions found in config.json")
        sys.exit(1)

    print("✅ Found Lambda functions:")
    for name, arn in lambda_config.items():
        print(f"   • {name}: {arn}")
    print()

    # Initialize client
    print("� Iniutializing AgentCore client...")
    client = GatewayClient(region_name=region)
    client.logger.setLevel(logging.INFO)

    # Step 1: Create OAuth authorizer
    print("\n📝 Step 1: Creating OAuth authorization server...")
    cognito_response = client.create_oauth_authorizer_with_cognito("InsuranceUnderwritingGateway")
    print("✅ Authorization server created")

    # Step 2: Create Gateway (role will be auto-created)
    print("\n📝 Step 2: Creating AgentCore Gateway...")
    gateway = client.create_mcp_gateway(
        name=GATEWAY_NAME,
        role_arn=None,  # Let the toolkit create the role
        authorizer_config=cognito_response["authorizer_config"],
        enable_semantic_search=True,
    )
    print(f"✅ Gateway created: {gateway['gatewayUrl']}")

    # Fix IAM permissions for the auto-created role
    print("\n📝 Step 2.1: Configuring IAM permissions...")
    client.fix_iam_permissions(gateway)
    print("⏳ Waiting 30s for IAM propagation...")
    time.sleep(30)
    print("✅ IAM permissions configured")

    # Step 3: Add Lambda targets
    print("\n📝 Step 3: Adding Lambda targets...")

    # Define Lambda functions with their schemas
    lambda_functions = []

    # ApplicationTool - Stage 1: Application Submission
    if "ApplicationTool" in lambda_config:
        lambda_functions.append(
            {
                "name": "ApplicationTool",
                "arn": lambda_config["ApplicationTool"],
                "schema": [
                    {
                        "name": "create_application",
                        "description": "Create insurance application with geographic and eligibility validation",
                        "inputSchema": {
                            "type": "object",
                            "description": "Input parameters for insurance application creation",
                            "properties": {
                                "applicant_region": {
                                    "type": "string",
                                    "description": "Customer's geographic region (US, CA, UK, EU, APAC, etc.)",
                                },
                                "coverage_amount": {
                                    "type": "integer",
                                    "description": "Requested insurance coverage amount",
                                },
                            },
                            "required": ["applicant_region", "coverage_amount"],
                        },
                    }
                ],
            }
        )

    # RiskModelTool - Stage 3: External Scoring Integration
    if "RiskModelTool" in lambda_config:
        lambda_functions.append(
            {
                "name": "RiskModelTool",
                "arn": lambda_config["RiskModelTool"],
                "schema": [
                    {
                        "name": "invoke_risk_model",
                        "description": "Invoke external risk scoring model with governance controls",
                        "inputSchema": {
                            "type": "object",
                            "description": "Input parameters for risk model invocation",
                            "properties": {
                                "API_classification": {
                                    "type": "string",
                                    "description": "API classification (public, internal, restricted)",
                                },
                                "data_governance_approval": {
                                    "type": "boolean",
                                    "description": "Whether data governance has approved model usage",
                                },
                            },
                            "required": [
                                "API_classification",
                                "data_governance_approval",
                            ],
                        },
                    }
                ],
            }
        )

    # ApprovalTool - Stage 7: Senior Approval
    if "ApprovalTool" in lambda_config:
        lambda_functions.append(
            {
                "name": "ApprovalTool",
                "arn": lambda_config["ApprovalTool"],
                "schema": [
                    {
                        "name": "approve_underwriting",
                        "description": "Approve high-value or high-risk underwriting decisions",
                        "inputSchema": {
                            "type": "object",
                            "description": "Input parameters for underwriting approval",
                            "properties": {
                                "claim_amount": {
                                    "type": "integer",
                                    "description": "Insurance claim/coverage amount",
                                },
                                "risk_level": {
                                    "type": "string",
                                    "description": "Risk level assessment (low, medium, high, critical)",
                                },
                            },
                            "required": ["claim_amount", "risk_level"],
                        },
                    }
                ],
            }
        )

    # Add each Lambda target to the gateway
    gateway_arn = None
    for lambda_func in lambda_functions:
        print(f"\n   🔧 Adding {lambda_func['name']} target...")

        try:
            target = client.create_mcp_gateway_target(
                gateway=gateway,
                name=f"{lambda_func['name']}Target",
                target_type="lambda",
                target_payload={
                    "lambdaArn": lambda_func["arn"],
                    "toolSchema": {"inlinePayload": lambda_func["schema"]},
                },
                credentials=None,
            )

            if gateway_arn is None:
                gateway_arn = target.get("gatewayArn")

            print(f"   ✅ Successfully added {lambda_func['name']} target")

        except Exception as e:
            print(f"   ❌ Error adding {lambda_func['name']} target: {e}")

    # Step 4: Update existing config.json with gateway information
    print("\n📝 Step 4: Updating config.json with gateway information...")

    # Add gateway configuration to existing config
    existing_config["gateway"] = {
        "gateway_url": gateway["gatewayUrl"],
        "gateway_id": gateway["gatewayId"],
        "gateway_arn": gateway_arn or gateway.get("gatewayArn"),
        "gateway_name": GATEWAY_NAME,
        "client_info": cognito_response["client_info"],
    }

    # Write updated config back to config.json
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(existing_config, f, indent=2)

    print("\n" + "=" * 70)
    print("✅ GATEWAY SETUP COMPLETE!")
    print("=" * 70)
    print("Gateway Name: GW-Insurance-Underwriting")
    print(f"Gateway URL: {gateway['gatewayUrl']}")
    print(f"Gateway ID: {gateway['gatewayId']}")
    print(f"Gateway ARN: {existing_config['gateway']['gateway_arn']}")
    print(f"\nTargets Added: {len(lambda_functions)}")
    for func in lambda_functions:
        print(f"   • {func['name']}")
    print(f"\nConfiguration updated in: {config_file}")
    print("=" * 70)

    return existing_config


if __name__ == "__main__":
    setup_gateway()
