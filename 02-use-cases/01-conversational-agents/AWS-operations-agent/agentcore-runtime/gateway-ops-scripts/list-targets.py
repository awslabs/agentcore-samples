#!/usr/bin/env python3
"""
List Bedrock AgentCore Gateway Targets
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
    parser = argparse.ArgumentParser(description="List Bedrock AgentCore Gateway Targets")
    parser.add_argument("--gateway-id", help="Gateway ID (uses config default if not specified)")
    parser.add_argument(
        "--environment",
        type=str,
        default="dev",
        help="Environment to use (dev, gamma, prod)",
    )
    return parser.parse_args()


def print_response(title, response_data):
    """Print formatted response"""
    print(f"\n{title}")
    print("=" * 60)
    print(json.dumps(response_data, indent=2, default=str))
    print("=" * 60)


def get_gateway_info(bedrock_agentcore_client, gateway_id):
    """Get gateway information"""
    try:
        response = bedrock_agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        return response
    except Exception as e:
        logger.error(f"Failed to get gateway info: {str(e)}")
        return None


def list_targets(config_manager, environment, gateway_id=None):
    """List all targets for a gateway using configuration"""

    # Get configuration from config manager
    base_settings = config_manager.get_base_settings()
    dynamic_config = config_manager.get_dynamic_config()

    # Extract AWS configuration
    aws_config = {
        "region": base_settings["aws"]["region"],
        "account_id": base_settings["aws"]["account_id"],
        "profile": None,  # Use default credentials
    }

    # Use gateway from config if not provided
    if not gateway_id:
        gateway_id = dynamic_config["gateway"]["id"]
        if not gateway_id:
            print("❌ No gateway ID provided and none found in config")
            return []

    print(f"Fetching live targets for gateway {gateway_id}...")

    # Create AWS session
    session = boto3.Session(region_name=aws_config["region"])

    # Use bedrock-agentcore-control client
    bedrock_agentcore_client = session.client("bedrock-agentcore-control", region_name=aws_config["region"])

    # Get gateway info
    gateway_info = get_gateway_info(bedrock_agentcore_client, gateway_id)
    gateway_name = gateway_info.get("name", "Unknown") if gateway_info else "Unknown"

    try:
        # List targets
        response = bedrock_agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)

        print_response(f"LIST TARGETS RESPONSE (Gateway: {gateway_id})", response)

        targets = response.get("items", [])

        print(f"\nLive Data Summary for Gateway {gateway_id}:")
        print(f"   Total Targets: {len(targets)}")

        if targets:
            print(f"\nLive Targets for Gateway {gateway_id}:")
            print("=" * 60)
            print(f"Gateway Name: {gateway_name}")
            print(
                f"MCP Endpoint: https://{gateway_id}.gateway.bedrock-agentcore.{aws_config['region']}.amazonaws.com/mcp"
            )

            for i, target in enumerate(targets, 1):
                target_id = target.get("targetId", "Unknown")
                target_name = target.get("name", "Unknown")
                status = target.get("status", "Unknown")
                description = target.get("description", "Unknown")
                created_at = target.get("createdAt", "Unknown")
                updated_at = target.get("updatedAt", "Unknown")

                print(f"\n  {i}. Target ID: {target_id}")
                print(f"     Name: {target_name}")
                print(f"     Status: {status}")
                print(f"     Description: {description}")
                print(f"     Created: {created_at}")
                print(f"     Updated: {updated_at}")

            print("\nListed targets from live AWS data")
        else:
            print(f"\nNo targets found for gateway {gateway_name}")

        return targets

    except Exception as e:
        logger.error(f"Failed to list targets: {str(e)}")
        print(f"\nFailed to list targets: {str(e)}")
        return []


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("List Bedrock AgentCore Gateway Targets (Live Data)")
    print("=" * 50)
    print(f"Environment: {environment}")
    print("Endpoint: default")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("Data Source: AWS Bedrock AgentCore API (Live)")

    try:
        # List targets
        targets = list_targets(config_manager, environment, args.gateway_id)

        if not targets:
            print("\n⚠️  No targets found")
            sys.exit(0)

    except Exception as e:
        logger.error(f"Target listing failed: {str(e)}")
        print(f"\n❌ Target listing failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
