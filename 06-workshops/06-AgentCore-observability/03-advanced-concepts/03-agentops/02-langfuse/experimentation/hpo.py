import json
import argparse
import os
import sys
import time

# Add parent directory to path to import from top-level utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.agent import deploy_agent, delete_agent
from utils.langfuse import run_experiment


def _parse_bool(value):
    value_str = str(value).strip().lower()
    if value_str in {"true", "t", "1", "yes", "y"}:
        return True
    if value_str in {"false", "f", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("Expected a boolean value (True/False)")


def main():
    # Load the configuration file
    config_path = os.path.join(os.path.dirname(__file__), "hpo_config.json")

    parser = argparse.ArgumentParser(description="Hyperparameter optimization runner")
    parser.add_argument(
        "--force-redeploy",
        dest="force_redeploy",
        type=_parse_bool,
        default=True,
        metavar="True/False",
        help="Force agent re-deployment before running experiments (default: True)",
    )
    args = parser.parse_args()

    force_redeploy = args.force_redeploy
    environment = "DEV"

    with open(config_path, "r") as f:
        config = json.load(f)

    models = config["models"]
    system_prompts = config["system_prompts"]

    # Dictionary to store results
    results = {}
    deployed_agents = []

    # Phase 1: Deploy all agents
    print(f"\n{'=' * 80}")
    print("PHASE 1: DEPLOYING AGENTS")
    print(f"{'=' * 80}\n")

    for model in models:
        for prompt in system_prompts:
            combination_key = f"{model['name']}__{prompt['name']}"

            print(f"\n{'=' * 80}")
            print(f"Deploying combination: {combination_key}")
            print(f"Model: {model['name']} ({model['model_id']})")
            print(f"Prompt: {prompt['name']}")
            print(f"{'=' * 80}\n")

            try:
                # Execute deploy_agent function
                result = deploy_agent(model, prompt, force_redeploy, environment)

                # Extract agent_name, agent_arn, agent_id, and ecr_uri from the result
                agent_name = result["agent_name"]
                launch_result = result["launch_result"]
                # The launch_result should contain the agent runtime ARN
                agent_arn = launch_result.agent_arn
                agent_id = launch_result.agent_id
                ecr_uri = launch_result.ecr_uri

                results[combination_key] = {
                    "status": "deployed",
                    "model": model["name"],
                    "prompt": prompt["name"],
                    "deployment_result": result,
                }
                deployed_agents.append(
                    {
                        "combination_key": combination_key,
                        "agent_name": agent_name,
                        "agent_arn": agent_arn,
                        "agent_id": agent_id,
                        "ecr_uri": ecr_uri,
                        "model_id": model["model_id"],
                        "system_prompt_id": prompt["name"],
                    }
                )
                print(f"✓ Successfully deployed: {combination_key}\n")

            except Exception as e:
                results[combination_key] = {
                    "status": "error",
                    "model": model["name"],
                    "prompt": prompt["name"],
                    "error": str(e),
                }
                print(f"✗ Error deploying {combination_key}: {str(e)}\n")

    # Phase 2: Run experiments on all deployed agents
    print(f"\n{'=' * 80}")
    print("PHASE 2: RUNNING EXPERIMENTS")
    print(f"{'=' * 80}\n")

    for agent_info in deployed_agents:
        combination_key = agent_info["combination_key"]
        agent_name = agent_info["agent_name"]
        agent_arn = agent_info["agent_arn"]
        model_id = agent_info["model_id"]
        system_prompt_id = agent_info["system_prompt_id"]

        print(f"\n{'=' * 80}")
        print(f"Running experiment for agent: {combination_key}")
        print(f"Agent ARN: {agent_arn}")
        print(f"{'=' * 80}\n")

        try:
            # Run experiment using Langfuse dataset
            experiment_result = run_experiment(
                agent_arn=agent_arn,
                experiment_name=f"hpo_experiment_{combination_key}",
                experiment_description=f"Hyperparameter optimization experiment for {combination_key}",
                metadata={"model_id": model_id, "system_prompt_id": system_prompt_id},
            )

            # Update the results with experiment data
            results[combination_key]["experiment_result"] = str(experiment_result)
            results[combination_key]["status"] = "success"
            print(f"✓ Successfully ran experiment: {combination_key}\n")

        except Exception as e:
            results[combination_key]["experiment_error"] = str(e)
            results[combination_key]["status"] = "experiment_error"
            print(f"✗ Error running experiment for {combination_key}: {str(e)}\n")

    # Wait for 2 minutes for the evaluations to complete in Langfuse
    time.sleep(120)

    # # Phase 3: Delete all deployed agents
    print(f"\n{'=' * 80}")
    print("PHASE 3: DELETING AGENTS")
    print(f"{'=' * 80}\n")

    for agent_info in deployed_agents:
        combination_key = agent_info["combination_key"]
        agent_name = agent_info["agent_name"]
        agent_id = agent_info["agent_id"]
        ecr_uri = agent_info["ecr_uri"]

        print(f"\n{'=' * 80}")
        print(f"Deleting agent: {combination_key}")
        print(f"Agent name: {agent_name}")
        print(f"Agent ID: {agent_id}")
        print(f"{'=' * 80}\n")

        try:
            deletion_result = delete_agent(agent_id, ecr_uri)

            # Update the results with deletion data
            results[combination_key]["deletion_result"] = deletion_result
            print(f"✓ Successfully deleted: {combination_key}\n")

        except Exception as e:
            results[combination_key]["deletion_error"] = str(e)
            print(f"✗ Error deleting {combination_key}: {str(e)}\n")

    # Print final results summary
    print(f"\n{'=' * 80}")
    print("FINAL RESULTS SUMMARY")
    print(f"{'=' * 80}\n")

    print(json.dumps(results, indent=2, default=str))

    # Print statistics
    successful = sum(1 for r in results.values() if r["status"] == "success")
    failed = sum(1 for r in results.values() if r["status"] == "error")

    print(f"\n{'=' * 80}")
    print(f"Total combinations: {len(results)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"{'=' * 80}\n")

    return results


if __name__ == "__main__":
    main()
