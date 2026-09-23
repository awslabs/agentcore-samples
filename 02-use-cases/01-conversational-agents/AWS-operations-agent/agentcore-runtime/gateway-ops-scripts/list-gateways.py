#!/usr/bin/env python3
"""
List Bedrock AgentCore Gateways
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
    parser = argparse.ArgumentParser(description="List Bedrock AgentCore Gateways")
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


def list_gateways(config_manager, environment):
    """List all gateways using configuration"""

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
    print("   Endpoint Type: default")

    # Create AWS session
    session = boto3.Session(region_name=aws_config["region"])

    # Use bedrock-agentcore-control client
    bedrock_agentcore_client = session.client("bedrock-agentcore-control", region_name=aws_config["region"])

    print("\nFetching live gateways from AWS Bedrock AgentCore API...")

    try:
        # List gateways
        response = bedrock_agentcore_client.list_gateways()

        print_response("LIST GATEWAYS RESPONSE (LIVE DATA)", response)

        gateways = response.get("items", [])

        print("\nLive Data Summary:")
        print(f"   Total Gateways: {len(gateways)}")

        if gateways:
            print("\nLive Gateways from AWS:")
            print("=" * 60)

            for i, gateway in enumerate(gateways, 1):
                gateway_id = gateway.get("gatewayId", "Unknown")
                gateway_name = gateway.get("name", "Unknown")
                status = gateway.get("status", "Unknown")
                protocol = gateway.get("protocolType", "Unknown")
                authorizer = gateway.get("authorizerType", "Unknown")
                description = gateway.get("description", "Unknown")
                created_at = gateway.get("createdAt", "Unknown")
                updated_at = gateway.get("updatedAt", "Unknown")

                # Try to construct MCP endpoint URL
                mcp_endpoint = (
                    f"https://{gateway_id}.gateway.bedrock-agentcore.{aws_config['region']}.amazonaws.com/mcp"
                    if gateway_id != "Unknown"
                    else "Unknown"
                )

                print(f"\n{i}. Gateway ID: {gateway_id}")
                print(f"   Name: {gateway_name}")
                print(f"   Status: {status}")
                print(f"   Protocol: {protocol}")
                print(f"   Authorizer: {authorizer}")
                print("   Role ARN: Unknown")  # Not returned in list response
                print(f"   MCP Endpoint: {mcp_endpoint}")
                print(f"   Description: {description}")
                print(f"   Created: {created_at}")
                print(f"   Updated: {updated_at}")

            print(f"\nListed {len(gateways)} gateways from live AWS data")
        else:
            print("\nNo gateways found")

        return gateways

    except Exception as e:
        logger.error(f"Failed to list gateways: {str(e)}")
        print(f"\nFailed to list gateways: {str(e)}")
        return []


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("List Bedrock AgentCore Gateways (Live Data)")
    print("=" * 45)
    print(f"Environment: {environment}")
    print("Endpoint: default")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("Data Source: AWS Bedrock AgentCore API (Live)")

    try:
        # List gateways
        gateways = list_gateways(config_manager, environment)

        if not gateways:
            print("\n⚠️  No gateways found")
            sys.exit(0)

    except Exception as e:
        logger.error(f"Gateway listing failed: {str(e)}")
        print(f"\n❌ Gateway listing failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
