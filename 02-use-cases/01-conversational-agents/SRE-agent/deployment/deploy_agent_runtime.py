#!/usr/bin/env python3

import argparse
import json
import logging
import os
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# Configuration constants
DELETION_WAIT_TIME = 150  # seconds to wait after runtime deletion before recreating

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)


def _write_agent_arn_to_file(agent_arn: str, output_dir: str = None) -> None:
    """Write agent ARN to .agent_arn file."""
    if output_dir is None:
        output_dir = Path(__file__).parent
    else:
        output_dir = Path(output_dir)

    arn_file = output_dir / ".agent_arn"

    try:
        with open(arn_file, "w") as f:
            f.write(agent_arn)
        logging.info(f"💾 Agent Runtime ARN saved to {arn_file}")
    except Exception as e:
        logging.error(f"Failed to write agent ARN to file: {e}")


def _get_agent_runtime_id_by_name(client: boto3.client, runtime_name: str) -> str:
    """Get agent runtime ID by name."""
    try:
        response = client.list_agent_runtimes()
        agent_runtimes = response.get("agentRuntimes", [])

        for runtime in agent_runtimes:
            if runtime["agentRuntimeName"] == runtime_name:
                return runtime["agentRuntimeId"]

        return None

    except ClientError as e:
        logging.error(f"Failed to get agent runtime ID: {e}")
        return None


def _delete_agent_runtime(client: boto3.client, runtime_id: str) -> bool:
    """Delete an agent runtime by ID."""
    try:
        logging.info(f"Deleting agent runtime with ID: {runtime_id}")
        client.delete_agent_runtime(agentRuntimeId=runtime_id)
        logging.info("Agent runtime deleted successfully")
        return True

    except ClientError as e:
        logging.error(f"Failed to delete agent runtime: {e}")
        return False


def _list_existing_agent_runtimes(client: boto3.client) -> None:
    """List all existing agent runtimes."""
    try:
        response = client.list_agent_runtimes()
        agent_runtimes = response.get("agentRuntimes", [])

        if not agent_runtimes:
            logging.info("No existing agent runtimes found.")
            return

        logging.info("Existing agent runtimes:")
        for runtime in agent_runtimes:
            logging.info(json.dumps(runtime, indent=2, default=str))

    except ClientError as e:
        logging.error(f"Failed to list agent runtimes: {e}")


def _create_agent_runtime(
    client: boto3.client,
    runtime_name: str,
    container_uri: str,
    role_arn: str,
    anthropic_api_key: str,
    gateway_access_token: str,
    llm_provider: str = "bedrock",
    force_recreate: bool = False,
) -> None:
    """Create an agent runtime with error handling for conflicts."""
    # Build environment variables
    env_vars = {
        "GATEWAY_ACCESS_TOKEN": gateway_access_token,
        "LLM_PROVIDER": llm_provider,
    }

    # Only add ANTHROPIC_API_KEY if it exists
    if anthropic_api_key:
        env_vars["ANTHROPIC_API_KEY"] = anthropic_api_key

    # Check for DEBUG environment variable
    debug_mode = os.getenv("DEBUG", "false")
    if debug_mode.lower() in ("true", "1", "yes"):
        env_vars["DEBUG"] = "true"
        logging.info("Debug mode enabled for agent runtime")

    # Log environment variables being passed to AgentCore (mask sensitive data)
    logging.info("🚀 Environment variables being passed to AgentCore Runtime:")
    for key, value in env_vars.items():
        if key in ["ANTHROPIC_API_KEY", "GATEWAY_ACCESS_TOKEN"]:
            masked_value = f"{'*' * 20}...{value[-8:] if len(value) > 8 else '***'}"
            logging.info(f"   {key}: {masked_value}")
        else:
            logging.info(f"   {key}: {value}")
    try:
        response = client.create_agent_runtime(
            agentRuntimeName=runtime_name,
            agentRuntimeArtifact={
                "containerConfiguration": {"containerUri": container_uri}
            },
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=role_arn,
            environmentVariables=env_vars,
        )

        logging.info("Agent Runtime created successfully!")
        logging.info(f"Agent Runtime ARN: {response['agentRuntimeArn']}")
        logging.info(f"Status: {response['status']}")
        _write_agent_arn_to_file(response["agentRuntimeArn"])

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")

        # Handle non-conflict errors immediately
        if error_code != "ConflictException":
            logging.error(f"Failed to create agent runtime: {e}")
            raise

        # Handle conflict - runtime already exists
        logging.error(f"Agent runtime '{runtime_name}' already exists.")
        logging.info("Listing existing agent runtimes:")
        _list_existing_agent_runtimes(client)

        # If not forcing recreate, provide guidance and exit
        if not force_recreate:
            logging.info(
                "Please retry with a new agent name using the --runtime-name parameter, or use --force-recreate to delete and recreate."
            )
            return

        # Handle force recreate scenario
        logging.info(
            "Force recreate requested, attempting to delete existing runtime..."
        )
        runtime_id = _get_agent_runtime_id_by_name(client, runtime_name)

        if not runtime_id:
            logging.error(f"Could not find runtime ID for '{runtime_name}'")
            return

        if not _delete_agent_runtime(client, runtime_id):
            logging.error("Failed to delete existing runtime")
            return

        # Wait for deletion to complete
        logging.info(
            f"Waiting {DELETION_WAIT_TIME} seconds for deletion to complete..."
        )
        time.sleep(DELETION_WAIT_TIME)

        # Recreate the runtime after successful deletion
        logging.info("Attempting to recreate agent runtime...")
        try:
            response = client.create_agent_runtime(
                agentRuntimeName=runtime_name,
                agentRuntimeArtifact={
                    "containerConfiguration": {"containerUri": container_uri}
                },
                networkConfiguration={"networkMode": "PUBLIC"},
                roleArn=role_arn,
                environmentVariables=env_vars,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                logging.error("\n" + "=" * 70)
                logging.error("⚠️  AGENT NAME CONFLICT - AWS CLEANUP STILL IN PROGRESS")
                logging.error("=" * 70)
                logging.error(
                    f"Even after waiting {DELETION_WAIT_TIME} seconds, the agent name"
                )
                logging.error(f"'{runtime_name}' is still not available.")
                logging.error("")
                logging.error(
                    "This is an AWS internal cleanup delay. Please try one of:"
                )
                logging.error("1. Wait 1-2 more minutes and run the script again")
                logging.error("2. Use a different agent name (e.g., add a timestamp)")
                logging.error(f"   ./deployment/build_and_deploy.sh {runtime_name}_v2")
                logging.error("=" * 70)
                print(
                    "\n⚠️  Please wait 1-2 minutes for AWS to complete agent deletion,"
                )
                print("   then try running the deployment script again.")
            raise

        logging.info("Agent Runtime recreated successfully!")
        logging.info(f"Agent Runtime ARN: {response['agentRuntimeArn']}")
        logging.info(f"Status: {response['status']}")
        _write_agent_arn_to_file(response["agentRuntimeArn"])


def main():
    parser = argparse.ArgumentParser(
        description="Deploy SRE Agent to AgentCore Runtime"
    )
    parser.add_argument(
        "--runtime-name",
        default="sre-agent",
        help="Name for the agent runtime (default: sre-agent)",
    )
    parser.add_argument(
        "--container-uri",
        required=True,
        help="Container URI (e.g., account-id.dkr.ecr.us-west-2.amazonaws.com/my-agent:latest)",
    )
    parser.add_argument(
        "--role-arn", required=True, help="IAM role ARN for the agent runtime"
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region (default: AWS_REGION env var or us-east-1)",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Delete existing runtime if it exists and recreate it",
    )

    args = parser.parse_args()

    # Load environment variables from .env file
    script_dir = Path(__file__).parent
    env_file = script_dir / ".env"

    if env_file.exists():
        load_dotenv(env_file)
        logging.info(f"Loaded environment variables from {env_file}")
    else:
        logging.error(f".env file not found at {env_file}")
        raise FileNotFoundError(
            f"Please create a .env file at {env_file} with GATEWAY_ACCESS_TOKEN and optionally ANTHROPIC_API_KEY"
        )

    # Get environment variables
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    gateway_access_token = os.getenv("GATEWAY_ACCESS_TOKEN")
    llm_provider = os.getenv("LLM_PROVIDER", "bedrock")

    # Log environment variable values (mask sensitive data)
    logging.info("📋 Environment variables loaded:")
    logging.info(f"   LLM_PROVIDER: {llm_provider}")
    if anthropic_api_key:
        logging.info(
            f"   ANTHROPIC_API_KEY: {'*' * 20}...{anthropic_api_key[-8:] if len(anthropic_api_key) > 8 else '***'}"
        )
    else:
        logging.info(
            "   ANTHROPIC_API_KEY: Not set - Amazon Bedrock will be used as the provider"
        )

    if gateway_access_token:
        logging.info(
            f"   GATEWAY_ACCESS_TOKEN: {'*' * 20}...{gateway_access_token[-8:] if len(gateway_access_token) > 8 else '***'}"
        )

    if not gateway_access_token:
        logging.error("GATEWAY_ACCESS_TOKEN not found in .env")
        raise ValueError("GATEWAY_ACCESS_TOKEN must be set in .env")

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)

    _create_agent_runtime(
        client=client,
        runtime_name=args.runtime_name,
        container_uri=args.container_uri,
        role_arn=args.role_arn,
        anthropic_api_key=anthropic_api_key,
        gateway_access_token=gateway_access_token,
        llm_provider=llm_provider,
        force_recreate=args.force_recreate,
    )


if __name__ == "__main__":
    main()
