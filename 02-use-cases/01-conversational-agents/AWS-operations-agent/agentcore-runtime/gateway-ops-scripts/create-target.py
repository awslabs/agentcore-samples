#!/usr/bin/env python3
"""
Create Bedrock AgentCore Gateway Target
Uses unified AgentCore configuration system
"""

import json
import boto3
import logging
import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path for shared config manager
project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root))
from shared.config_manager import AgentCoreConfigManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="Create Bedrock AgentCore Gateway Target")
    parser.add_argument("--gateway-id", help="Gateway ID (uses live gateway discovery if not specified)")
    parser.add_argument("--lambda-arn", help="Lambda ARN (uses config default if not specified)")
    parser.add_argument("--name", help="Target name (optional)")
    parser.add_argument("--description", help="Target description (optional)")
    parser.add_argument(
        "--environment",
        type=str,
        default="production",
        help="Environment to use (for naming only)",
    )
    return parser.parse_args()


def print_request(title, request_data):
    """Print formatted request"""
    print(f"\n{title}")
    print("=" * 60)
    print(json.dumps(request_data, indent=2, default=str))
    print("=" * 60)


def print_response(title, response_data):
    """Print formatted response"""
    print(f"\n{title}")
    print("=" * 60)
    print(json.dumps(response_data, indent=2, default=str))
    print("=" * 60)


def get_live_gateways(bedrock_agentcore_client):
    """Get live gateways from AWS"""
    try:
        response = bedrock_agentcore_client.list_gateways()
        return response.get("items", [])
    except Exception as e:
        logger.error(f"Failed to get live gateways: {str(e)}")
        return []


def select_gateway(bedrock_agentcore_client, config_manager, gateway_id=None):
    """Select gateway to use for target creation"""

    if gateway_id:
        # Verify specified gateway exists
        try:
            response = bedrock_agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
            gateway_info = {
                "gatewayId": gateway_id,
                "name": response.get("name", "Unknown"),
                "status": response.get("status", "Unknown"),
            }
            print(f"Using specified gateway: {gateway_id}")
            return gateway_id, gateway_info
        except Exception as e:
            print(f"Gateway {gateway_id} not found: {str(e)}")
            return None, None

    # First, try to get gateway from dynamic config
    try:
        dynamic_config = config_manager.get_dynamic_config()
        config_gateway_id = dynamic_config["gateway"]["id"]

        if config_gateway_id:
            print(f"Found gateway in config: {config_gateway_id}")

            # Verify the gateway exists in AWS
            try:
                response = bedrock_agentcore_client.get_gateway(gatewayIdentifier=config_gateway_id)
                gateway_info = {
                    "gatewayId": config_gateway_id,
                    "name": response.get("name", "Unknown"),
                    "status": response.get("status", "Unknown"),
                }
                print(
                    f"✅ Using gateway from config: {config_gateway_id} ({gateway_info['name']}) - Status: {gateway_info['status']}"
                )
                return config_gateway_id, gateway_info
            except Exception as e:
                print(f"⚠️  Gateway from config not found in AWS: {str(e)}")
                print("   Falling back to gateway discovery...")
        else:
            print("No gateway found in config, discovering available gateways...")
    except Exception as e:
        print(f"Error reading gateway from config: {str(e)}")

    # Fallback: discover gateways from AWS
    gateways = get_live_gateways(bedrock_agentcore_client)

    if not gateways:
        print("No gateways found")
        return None, None

    # Use the first available gateway
    gateway = gateways[0]
    gateway_id = gateway["gatewayId"]
    print(f"✅ Using first gateway: {gateway_id}")
    return gateway_id, gateway


def create_gateway_target(
    config_manager,
    environment,
    gateway_id,
    lambda_arn,
    target_name=None,
    description=None,
):
    """Create Gateway Target using configuration"""

    # Get configuration from config manager
    base_settings = config_manager.get_base_settings()
    tools_schema = config_manager.get_tools_schema()

    # Extract AWS configuration
    aws_config = {
        "region": base_settings["aws"]["region"],
        "account_id": base_settings["aws"]["account_id"],
        "profile": None,  # Use default credentials
    }

    # Create Lambda target configuration
    lambda_target_config = {
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": {"inlinePayload": tools_schema},
            }
        }
    }

    # Create credential provider configuration
    credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    print("Using Configuration:")
    print(f"   Environment: {environment}")
    print(f"   AWS Region: {aws_config['region']}")
    print(f"   AWS Account: {aws_config['account_id']}")
    print(f"   Available Tools: {len(tools_schema)}")

    # Use default target name if not provided
    if not target_name:
        target_name = f"{environment}-mcp-target"

    # Use default description if not provided
    if not description:
        description = f"MCP Target for {environment} environment - {len(tools_schema)} tools: hello_world, get_time, EC2, S3, Lambda, CloudFormation"

    # Create AWS session
    session = boto3.Session(region_name=aws_config["region"])

    # Use bedrock-agentcore-control client
    bedrock_agentcore_client = session.client("bedrock-agentcore-control", region_name=aws_config["region"])

    # Select gateway (now uses config first)
    gateway_id, gateway_info = select_gateway(bedrock_agentcore_client, config_manager, gateway_id)
    if not gateway_id:
        sys.exit(1)

    # Determine Lambda ARN
    if not lambda_arn:
        dynamic_config = config_manager.get_dynamic_config()
        lambda_arn = dynamic_config["mcp_lambda"]["function_arn"]

    print("\nTarget Configuration:")
    print(f"  Gateway ID: {gateway_id}")
    print(f"  Gateway Name: {gateway_info.get('name', 'Unknown')}")
    print(f"  Lambda ARN: {lambda_arn}")

    # Prepare request
    request_data = {
        "gatewayIdentifier": gateway_id,
        "name": target_name,
        "description": description,
        "targetConfiguration": lambda_target_config,
        "credentialProviderConfigurations": credential_config,
    }

    print_request("CREATE TARGET REQUEST", request_data)

    try:
        # Create target
        response = bedrock_agentcore_client.create_gateway_target(**request_data)

        print_response("CREATE TARGET RESPONSE", response)

        target_id = response["targetId"]
        target_status = response.get("status", "Unknown")

        print("\nTarget Created Successfully!")
        print(f"   Target ID: {target_id}")
        print(f"   Status: {target_status}")
        print(f"   Gateway ID: {gateway_id}")
        print(f"   Lambda ARN: {lambda_arn}")
        print(f"   Tool Count: {len(tools_schema)}")
        print(f"   Environment: {environment}")

        return target_id, response

    except Exception as e:
        logger.error(f"Target creation failed: {str(e)}")
        print(f"\nTarget creation failed: {str(e)}")
        raise


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("🚀 Create Bedrock AgentCore Gateway Target")
    print("=" * 45)
    print(f"Environment: {environment}")
    print("Endpoint: default")
    print(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # Create target
        target_id, response = create_gateway_target(
            config_manager,
            environment,
            args.gateway_id,
            args.lambda_arn,
            args.name,
            args.description,
        )

        print("\n✅ Target creation completed successfully!")
        print(f"   Use 'python list-targets.py --gateway-id {args.gateway_id or 'GATEWAY_ID'}' to see targets")
        print(
            f"   Use 'python get-target.py --gateway-id {args.gateway_id or 'GATEWAY_ID'} --target-id {target_id}' for details"
        )

    except Exception as e:
        logger.error(f"Target creation failed: {str(e)}")
        print(f"\n❌ Target creation failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
