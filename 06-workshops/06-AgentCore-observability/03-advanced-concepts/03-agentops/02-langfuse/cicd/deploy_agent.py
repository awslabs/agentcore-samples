#!/usr/bin/env python3
"""
Deploy agent script for CI/CD pipeline.
This script reads agent hyperparameters from hp_config.json and deploys an agent
using the deploy_agent function from utils.agent with specified environment (TST or PRD).
"""

import json
import sys
import argparse
from pathlib import Path

# Add the parent directory to the Python path to import utils
sys.path.append(str(Path(__file__).parent.parent))

from utils.agent import deploy_agent


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


def main():
    """Main function to deploy the agent."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Deploy agent to specified environment")
    parser.add_argument(
        "--environment",
        choices=["TST", "PRD"],
        help="Environment to deploy to (TST or PRD)",
        default="TST",
    )
    args = parser.parse_args()

    environment = args.environment

    print("Loading agent hyperparameters...")
    config = load_hp_config()

    # Extract the model and system prompt from the config
    if not config.get("model") or not config.get("system_prompt"):
        print("Error: Configuration must contain 'model' and 'system_prompt' objects.")
        sys.exit(1)

    model = config["model"]
    system_prompt = config["system_prompt"]

    print("Deploying agent with:")
    print(f"  Model: {model['name']} ({model['model_id']})")
    print(f"  System Prompt: {system_prompt['name']}")
    print(f"  Environment: {environment}")

    try:
        # Deploy the agent with specified environment
        result = deploy_agent(
            model=model,
            system_prompt=system_prompt,
            force_redeploy=False,
            environment=environment,
        )

        print("Agent deployment successful!")
        print(f"Agent Name: {result['agent_name']}")
        print(f"Agent ARN: {result['launch_result'].agent_arn}")
        print(f"Agent ID: {result['launch_result'].agent_id}")

        # Add agent ARN to the existing hp_config.json for use by subsequent pipeline steps
        # Use environment-specific keys to avoid conflicts between TST and PRD deployments
        # Create the environment key if it doesn't exist
        if environment.lower() not in config:
            config[environment.lower()] = {}

        config[environment.lower()]["agent_arn"] = result["launch_result"].agent_arn
        config[environment.lower()]["agent_name"] = result["agent_name"]
        config[environment.lower()]["agent_id"] = result["launch_result"].agent_id

        with open("cicd/hp_config.json", "w") as f:
            json.dump(config, f, indent=2)
        print(f"Agent ARN added to hp_config.json with {environment} environment key")

        # Wait for agent to be ready
        print("Waiting for agent to be ready...")
        import time

        time.sleep(60)
        # status_response = result['launch_result'].status()
        # status = status_response.endpoint['status']
        # end_status = ['READY', 'CREATE_FAILED', 'DELETE_FAILED', 'UPDATE_FAILED']
        # while status not in end_status:
        #     time.sleep(10)
        #     status_response = result['launch_result'].status()
        #     status = status_response.endpoint['status']
        #     print(f"Agent status: {status}")

        # if status == 'READY':
        #     print("Agent is ready!")
        # else:
        #     print(f"Agent deployment failed with status: {status}")
        #     sys.exit(1)

        return result

    except Exception as e:
        print(f"Error deploying agent: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
