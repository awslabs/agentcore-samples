#!/usr/bin/env python3
"""
Delete Bedrock AgentCore Gateway
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
    parser = argparse.ArgumentParser(description="Delete Bedrock AgentCore Gateway")
    parser.add_argument("--gateway-id", required=True, help="Gateway ID to delete")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--delete-targets", action="store_true", help="Delete all targets first")
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


def clear_dynamic_config_with_yq():
    """Clear gateway configuration using yq"""
    try:
        config_file = project_root / "config" / "dynamic-config.yaml"

        # Clear using yq commands
        subprocess.run(
            ["yq", "eval", '.gateway.id = ""', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["yq", "eval", '.gateway.arn = ""', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["yq", "eval", '.gateway.url = ""', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["yq", "eval", '.gateway.status = ""', "-i", str(config_file)],
            check=True,
            capture_output=True,
        )

        print("✅ Dynamic configuration cleared successfully")
        return True

    except subprocess.CalledProcessError as e:
        print(f"⚠️  Failed to clear dynamic configuration: {e}")
        return False
    except Exception as e:
        print(f"⚠️  Error clearing configuration: {e}")
        return False


def get_gateway_targets(bedrock_agentcore_client, gateway_id):
    """Get all targets for a gateway"""
    try:
        response = bedrock_agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)
        return response.get("items", [])
    except Exception as e:
        logger.error(f"Failed to get gateway targets: {str(e)}")
        return []


def delete_all_targets(bedrock_agentcore_client, gateway_id, targets):
    """Delete all targets for a gateway"""
    success = True
    for target in targets:
        target_id = target["targetId"]
        try:
            print(f"   Deleting target: {target_id}")
            bedrock_agentcore_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        except Exception as e:
            print(f"   ⚠️  Failed to delete target {target_id}: {str(e)}")
            success = False
    return success


def confirm_deletion(gateway_info, targets):
    """Confirm gateway deletion with user"""
    print("\nGateway Deletion Confirmation")
    print("=" * 40)
    print(f"Gateway ID: {gateway_info.get('gatewayId', 'Unknown')}")
    print(f"Gateway Name: {gateway_info.get('name', 'Unknown')}")
    print(f"Status: {gateway_info.get('status', 'Unknown')}")
    print(f"Description: {gateway_info.get('description', 'Unknown')}")
    print(f"Targets: {len(targets)}")
    print(f"Created: {gateway_info.get('createdAt', 'Unknown')}")
    print(f"Updated: {gateway_info.get('updatedAt', 'Unknown')}")
    print()
    print("This action cannot be undone!")
    print("All targets and tools will become inaccessible!")
    print()

    confirmation = input("Type 'DELETE' to confirm gateway deletion: ").strip()

    if confirmation != "DELETE":
        print("Deletion cancelled")
        return False

    return True


def delete_bedrock_agentcore_gateway(config_manager, environment, gateway_id, force=False, delete_targets=False):
    """Delete Bedrock AgentCore Gateway using configuration"""

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

    print("\nRetrieving gateway information from AWS...")

    # Get gateway info
    try:
        gateway_response = bedrock_agentcore_client.get_gateway(gatewayIdentifier=gateway_id)
        gateway_info = gateway_response
    except Exception as e:
        print(f"Gateway {gateway_id} not found: {str(e)}")
        return False

    # Get targets from AWS
    targets = get_gateway_targets(bedrock_agentcore_client, gateway_id)

    # Confirm deletion unless forced
    if not force:
        if not confirm_deletion(gateway_info, targets):
            return False

    # Delete targets first if requested or if they exist
    if targets and (delete_targets or not force):
        if not delete_all_targets(bedrock_agentcore_client, gateway_id, targets):
            print("Cannot proceed with gateway deletion due to target deletion failures")
            return False

    # Prepare request
    request_data = {"gatewayIdentifier": gateway_id}

    print_request("DELETE GATEWAY REQUEST", request_data)

    try:
        # Delete gateway
        response = bedrock_agentcore_client.delete_gateway(**request_data)

        print_response("DELETE GATEWAY RESPONSE", response)

        response.get("status", "Unknown")

        # Clear the dynamic config
        clear_dynamic_config_with_yq()

        print("\nGateway Deleted Successfully!")
        print(f"   Gateway ID: {gateway_id}")
        print(f"   Environment: {environment}")
        print(f"   Targets Deleted: {len(targets) if delete_targets else 0}")

        return True

    except Exception as e:
        logger.error(f"Gateway deletion failed: {str(e)}")
        print(f"\nGateway deletion failed: {str(e)}")
        return False


def main():
    """Main function"""
    args = parse_arguments()

    # Initialize configuration manager
    config_manager = AgentCoreConfigManager()

    # Use environment from args
    environment = args.environment

    print("Delete Bedrock AgentCore Gateway")
    print("=" * 40)
    print(f"Environment: {environment}")
    print(f"Gateway ID: {args.gateway_id}")
    print(f"Timestamp: {datetime.now().isoformat()}")

    try:
        # Delete gateway
        success = delete_bedrock_agentcore_gateway(
            config_manager,
            environment,
            args.gateway_id,
            args.force,
            args.delete_targets,
        )

        if success:
            print("\nGateway deletion completed successfully!")
            print("   Use 'python list-gateways.py' to see remaining gateways")
        else:
            print("\n❌ Gateway deletion failed!")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Gateway deletion failed: {str(e)}")
        print(f"\n❌ Gateway deletion failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
