#!/usr/bin/env python3
"""
Delete agent script for CI/CD pipeline cleanup.
This script deletes the deployed agent using the delete_agent function from utils.agent.
"""

import json
import sys
from pathlib import Path

# Add the parent directory to the Python path to import utils
sys.path.append(str(Path(__file__).parent.parent))

from utils.agent import delete_agent


def load_hp_config(config_path="cicd/hp_config.json"):
    """Load hyperparameters from the configuration file."""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}")
        sys.exit(1)


def get_agent_info_from_deploy_result():
    """
    Get agent information from the deployment result.
    In a real CI/CD scenario, this would read from a file or environment variable
    that was set during the deployment step.
    """
    # For now, we'll construct the agent name from the config and try to find it
    # In a production scenario, you might want to store the agent info in a file
    # or pass it as an environment variable between workflow steps

    config = load_hp_config()
    model = config["model"]
    system_prompt = config["system_prompt"]
    environment = "TST"

    agent_name = f"strands_{model['name']}_{system_prompt['name']}_{environment}"

    # Import boto3 to find the agent
    import boto3
    from boto3.session import Session

    boto_session = Session()
    region = boto_session.region_name

    try:
        agentcore_control_client = boto3.client("bedrock-agentcore-control", region_name=region)

        # List all agent runtimes to find our agent
        list_response = agentcore_control_client.list_agent_runtimes()
        existing_agents = list_response.get("agentRuntimes", [])

        # Find the agent with our name
        target_agent = None
        for agent_summary in existing_agents:
            if agent_summary.get("agentRuntimeName") == agent_name:
                target_agent = agent_summary
                break

        if not target_agent:
            print(f"Warning: Agent '{agent_name}' not found. It may have already been deleted.")
            return None

        # Get full agent runtime details to extract ECR URI
        agent_runtime_id = target_agent.get("agentRuntimeId")

        print(f"Agent Runtime ID: {agent_runtime_id}")

        try:
            get_response = agentcore_control_client.get_agent_runtime(agentRuntimeId=agent_runtime_id)

            print(f"Get Response: {get_response}")

            ecr_uri = get_response["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"]

            print(f"ECR URI: {ecr_uri}")
        except Exception as e:
            print(f"Warning: Could not retrieve ECR URI: {str(e)}")
            ecr_uri = ""

        return {
            "agent_runtime_id": agent_runtime_id,
            "ecr_uri": ecr_uri,
            "agent_name": agent_name,
        }

    except Exception as e:
        print(f"Error finding agent: {str(e)}")
        return None


def main():
    """Main function to delete the agent."""
    print("Finding deployed agent...")
    agent_info = get_agent_info_from_deploy_result()

    if not agent_info:
        print("No agent found to delete. Exiting.")
        return

    print("Deleting agent:")
    print(f"  Agent Name: {agent_info['agent_name']}")
    print(f"  Agent Runtime ID: {agent_info['agent_runtime_id']}")
    print(f"  ECR URI: {agent_info['ecr_uri']}")

    try:
        # Delete the agent
        result = delete_agent(
            agent_runtime_id=agent_info["agent_runtime_id"],
            ecr_uri=agent_info["ecr_uri"],
        )

        if result["status"] == "success":
            print("Agent deletion successful!")
            print(f"Runtime deletion response: {result.get('runtime_delete_response', {})}")
            print(f"ECR deletion response: {result.get('ecr_delete_response', {})}")
        else:
            print(f"Agent deletion failed: {result.get('error', 'Unknown error')}")
            sys.exit(1)

    except Exception as e:
        print(f"Error deleting agent: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
