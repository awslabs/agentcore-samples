#!/usr/bin/env python3
"""
Delete Bedrock AgentCore Gateway Target
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
    parser = argparse.ArgumentParser(description="Delete Bedrock AgentCore Gateway Target")
    parser.add_argument("--gateway-id", required=True, help="Gateway ID")
    parser.add_argument("--target-id", required=True, help="Target ID to delete")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--environment",
        type=str,
        default="dev",
        help="Environment to use (dev, gamma, prod)",
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


def confirm_deletion(target_info, gateway_info):
    """Confirm target deletion with user"""
    print("\nTarget Deletion Confirmation")
    print("=" * 40)
    print(f"Gateway ID: {gateway_info.get('gatewayId', 'Unknown')}")
    print(f"Gateway Name: {gateway_info.get('name', 'Unknown')}")
    print(f"Target ID: {target_info.get('targetId', 'Unknown')}")
    print(f"Target Name: {target_info.get('name', 'Unknown')}")
    print(f"Target Status: {target_info.get('status', 'Unknown')}")
    print(f"Description: {target_info.get('description', 'Unknown')}")

    # Show Lambda and tool information if available
    target_config = target_info.get("targetConfiguration", {})
    if "mcp" in target_config and "lambda" in target_config["mcp"]:
        lambda_config = target_config["mcp"]["lambda"]
        lambda_arn = lambda_config.get("lambdaArn", "Unknown")
        print(f"Lambda ARN: {lambda_arn}")

        tool_schema = lambda_config.get("toolSchema", {})
        if "inlinePayload" in tool_schema:
            tools = tool_schema["inlinePayload"]
            tool_names = [tool.get("name", "unknown") for tool in tools]
            print(f"Tool Count: {len(tools)}")
            print(f"Tools: {', '.join(tool_names)}")

    print()
    print("This action cannot be undone!")
    print()

    confirmation = input("Type 'DELETE' to confirm target deletion: ").strip()

    if confirmation != "DELETE":
        print("Deletion cancelled")
        return False

    return True


def delete_gateway_target(config_manager, environment, gateway_id, target_id, force=False):
    """Delete Gateway Target using configuration"""

    # Get configuration from config manager
    base_settings = config_manager.get_base_settings()

    # Extract AWS configuration
    aws_config = {
        "region": base_settings["aws"]["region"],
        "account_id": base_settings["aws"]["account_id"],
        "profile": None,  # Use default credentials
    }

    print("Using Configuration:")
    print(f"   Environment: {environment}")
    print(f"   AWS Region: {aws_config['region']}")
    print(f"   AWS Account: {aws_config['account_id']}")

    # Create AWS session
    session = boto3.Session(region_name=aws_config["region"])

    # Use bedrock-agentcore-control client
    bedrock_agentcore_client = session.client("bedrock-agentcore-control", region_name=aws_config["region"])

    print("\nRetrieving target and gateway information...")

    # Get target info
    try:
        target_response = bedrock_agentcore_client.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        target_info = target_response
    except Exception as e:
        print(f"Target {target_id} not found: {str(e)}")
        return False

    # Get gateway info
    try:
        gateway_response = bedrock_agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_info = gateway_response
    except Exception as e:
        print(f"Gateway {gateway_id} not found: {str(e)}")
        return False

    # Confirm deletion unless forced
    if not force:
        if not confirm_deletion(target_info, gateway_info):
            return False

    # Prepare request
    request_data = {"gatewayIdentifier": gateway_id, "targetId": target_id}

    print_request("DELETE TARGET REQUEST", request_data)

    try:
        # Delete target
        response = bedrock_agentcore_client.delete_gateway_target(**request_data)

        print_response("DELETE TARGET RESPONSE", response)

        response.get("status", "Unknown")

        print("\nTarget Deleted Successfully!")
        print(f"   Target ID: {target_id}")
        print(f"   Gateway ID: {gateway_id}")
        print(f"   Environment: {environment}")

        return True

    except Exception as e:
        logger.error(f"Target deletion failed: {str(e)}")
        print(f"\nTarget deletion failed: {str(e)}")
        return False


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("Delete Bedrock AgentCore Gateway Target")
    print("=" * 45)
    print(f"Environment: {environment}")
    print(f"Gateway ID: {args.gateway_id}")
    print(f"Target ID: {args.target_id}")
    print(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # Delete target
        success = delete_gateway_target(config_manager, environment, args.gateway_id, args.target_id, args.force)

        if success:
            print("\nTarget deletion completed successfully!")
            print(f"   Use 'python list-targets.py --gateway-id {args.gateway_id}' to see remaining targets")
        else:
            print("\n❌ Target deletion failed!")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Target deletion failed: {str(e)}")
        print(f"\n❌ Target deletion failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
