#!/usr/bin/env python3
"""
Create Bedrock AgentCore Gateway
Uses unified AgentCore configuration system
"""

import json
import boto3
import logging
import argparse
import sys
import subprocess
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
    parser = argparse.ArgumentParser(description="Create Bedrock AgentCore Gateway")
    parser.add_argument("--name", help="Gateway name (optional)")
    parser.add_argument("--description", help="Gateway description (optional)")
    parser.add_argument(
        "--environment",
        type=str,
        default="production",
        help="Environment to use (for CloudFormation tags only)",
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


def update_dynamic_config_with_yq(gateway_id, gateway_arn, gateway_url):
    """Update dynamic configuration using yq"""
    try:
        config_file = project_root / "config" / "dynamic-config.yaml"

        # Update using yq commands
        subprocess.run(
            ["yq", "eval", f'.gateway.id = "{gateway_id}"', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["yq", "eval", f'.gateway.arn = "{gateway_arn}"', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["yq", "eval", f'.gateway.url = "{gateway_url}"', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        print("✅ Dynamic configuration updated successfully")
        return True

    except subprocess.CalledProcessError as e:
        print(f"⚠️  Failed to update dynamic configuration: {e}")
        return False
    except Exception as e:
        print(f"⚠️  Error updating configuration: {e}")
        return False


def create_bedrock_agentcore_gateway(config_manager, environment, gateway_name=None, description=None):
    """Create Bedrock AgentCore Gateway using configuration"""

    # Get configuration from config manager
    base_settings = config_manager.get_base_settings()
    oauth_settings = config_manager.get_oauth_settings()
    config_manager.get_dynamic_config()

    # Extract AWS configuration
    aws_config = {
        "region": base_settings["aws"]["region"],
        "account_id": base_settings["aws"]["account_id"],
        "profile": None,  # Use default credentials
    }

    # Get gateway execution role from static config (bac-execution-role)
    gateway_execution_role_arn = base_settings["runtime"]["role_arn"]

    if not gateway_execution_role_arn:
        raise ValueError(
            "Gateway execution role ARN not found in static configuration. Please run 01-prerequisites.sh first."
        )

    # Create authorization configuration
    auth_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": oauth_settings["jwt"]["discovery_url"],
            "allowedAudience": [oauth_settings["jwt"]["audience"]],
        }
    }

    print("Using Configuration:")
    print(f"   Environment: {environment}")
    print(f"   AWS Region: {aws_config['region']}")
    print(f"   AWS Account: {aws_config['account_id']}")
    print(f"   Gateway Execution Role (bac-execution-role): {gateway_execution_role_arn}")

    # Use default gateway name if not provided
    if not gateway_name:
        gateway_name = f"{environment}-agentcore-gateway"

    # Use default description if not provided
    if not description:
        description = f"AgentCore Gateway for {environment} environment"

    # Create AWS session
    session = boto3.Session(region_name=aws_config["region"])

    # Use bedrock-agentcore-control client
    bedrock_agentcore_client = session.client("bedrock-agentcore-control", region_name=aws_config["region"])

    # Prepare request
    request_data = {
        "name": gateway_name,
        "protocolType": "MCP",
        "roleArn": gateway_execution_role_arn,
        "description": description,
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": auth_config,
    }

    print_request("CREATE GATEWAY REQUEST", request_data)

    try:
        # Create gateway
        response = bedrock_agentcore_client.create_gateway(**request_data)

        print_response("CREATE GATEWAY RESPONSE", response)

        gateway_id = response["gatewayId"]
        gateway_status = response.get("status", "Unknown")
        gateway_url = response.get("gatewayUrl", "Unknown")
        gateway_arn = response.get("gatewayArn", "")

        # Update the dynamic config with the gateway information
        update_dynamic_config_with_yq(gateway_id, gateway_arn, gateway_url)

        print("\nGateway Created Successfully!")
        print(f"   Gateway ID: {gateway_id}")
        print(f"   Gateway URL: {gateway_url}")
        print(f"   Status: {gateway_status}")
        print(f"   Environment: {environment}")

        return gateway_id, response

    except Exception as e:
        logger.error(f"Gateway creation failed: {str(e)}")
        print(f"\nGateway creation failed: {str(e)}")
        raise


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("🚀 Create Bedrock AgentCore Gateway")
    print("=" * 40)
    print(f"Environment: {environment}")
    print(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # Create gateway
        gateway_id, response = create_bedrock_agentcore_gateway(
            config_manager, environment, args.name, args.description
        )

        print("\n✅ Gateway creation completed successfully!")
        print(f"   Gateway ID: {gateway_id}")
        print("   Use 'python list-gateways.py' to see all gateways")
        print(f"   Use 'python get-gateway.py --gateway-id {gateway_id}' for details")

    except Exception as e:
        logger.error(f"Gateway creation failed: {str(e)}")
        print(f"\n❌ Gateway creation failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
