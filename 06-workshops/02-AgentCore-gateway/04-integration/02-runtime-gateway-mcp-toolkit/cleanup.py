#!/usr/bin/env python3
"""
AgentCore Cleanup Script
Removes all resources created by main.py including gateways, runtimes, Cognito resources, and IAM roles.
"""

import os
import boto3
import json
import argparse
from pathlib import Path
from bedrock_agentcore_starter_toolkit.operations.runtime import (
    destroy_bedrock_agentcore,
)
import agentcore_toolkit.utils as utils


def main():
    parser = argparse.ArgumentParser(description="AgentCore Cleanup Script")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--gateway-name", required=True, help="Gateway name to clean up")
    parser.add_argument(
        "--runtime-names",
        required=True,
        help='JSON array of runtime names to clean up: ["runtime1", "runtime2"]',
    )
    parser.add_argument("--confirm", action="store_true", help="Confirm deletion without prompting")

    args = parser.parse_args()

    # Parse runtime names from JSON
    try:
        runtime_names = json.loads(args.runtime_names)
    except json.JSONDecodeError:
        print("Error: Invalid JSON format for --runtime-names")
        return 1

    # Confirmation prompt
    if not args.confirm:
        print("This will delete the following resources:")
        print(f"  Gateway: {args.gateway_name}")
        print(f"  Runtimes: {', '.join(runtime_names)}")
        print(f"  Region: {args.region}")
        print("\nThis action cannot be undone!")

        confirm = input("Are you sure you want to proceed? (yes/no): ")
        if confirm.lower() != "yes":
            print("Cleanup cancelled.")
            return 0

    if os.path.exists(".bedrock_agentcore.yaml"):
        for runtime_agent in runtime_names:
            destroy_bedrock_agentcore(
                config_path=Path(".bedrock_agentcore.yaml"),
                agent_name=runtime_agent,
                delete_ecr_repo=True,
            )
    gateway_client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    utils.delete_gateway(gateway_client, args.gateway_name)


if __name__ == "__main__":
    main()
