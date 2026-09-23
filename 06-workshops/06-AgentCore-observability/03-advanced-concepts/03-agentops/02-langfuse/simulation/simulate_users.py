import json
import os
import sys

# Add parent directory to path to import utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.agent import invoke_agent

# Configuration
AGENT_ARN = "arn:aws:bedrock-agentcore:us-west-2:308819823671:runtime/strands_claude45sonnet_prompt1_PRD-86HGVK6oub"  # Replace with your actual agent ARN
CONFIG_FILE = "load_config.json"


def load_config(config_file):
    """
    Load the configuration file containing prompts.

    Parameters:
    - config_file (str): Path to the config JSON file

    Returns:
    - dict: The loaded configuration
    """
    config_path = os.path.join(os.path.dirname(__file__), config_file)

    with open(config_path, "r") as f:
        config = json.load(f)

    return config


def simulate_user_interactions(agent_arn, prompts):
    """
    Simulate user interactions by invoking the agent with each prompt.

    Parameters:
    - agent_arn (str): The ARN of the deployed agent runtime
    - prompts (list): List of prompt dictionaries with 'name' and 'prompt' keys

    Returns:
    - list: List of results from each agent invocation
    """
    results = []

    for idx, prompt_item in enumerate(prompts):
        prompt_name = prompt_item.get("name", f"prompt_{idx}")
        prompt = prompt_item.get("prompt", "")

        print(f"\n{'=' * 80}")
        print(f"Processing: {prompt_name}")
        print(f"Prompt: {prompt}")
        print(f"{'=' * 80}")

        # Invoke the agent
        result = invoke_agent(agent_arn, prompt)

        # Check for errors
        if "error" in result:
            print(f"❌ Error invoking agent: {result['error']}")
            results.append(
                {
                    "prompt_name": prompt_name,
                    "prompt": prompt,
                    "status": "error",
                    "error": result["error"],
                }
            )
            continue

        # Extract the response based on content type
        if result.get("content_type") == "application/json":
            response = result["response"]
        else:
            response = result.get("response", "")

        print("\n✅ Response received:")
        print(f"{response}\n")

        results.append(
            {
                "prompt_name": prompt_name,
                "prompt": prompt,
                "status": "success",
                "response": response,
                "session_id": result.get("session_id"),
                "content_type": result.get("content_type"),
            }
        )

    return results


def main():
    """
    Main function to load config and simulate user interactions.
    """
    print(f"Loading configuration from {CONFIG_FILE}...")

    try:
        config = load_config(CONFIG_FILE)
        prompts = config.get("prompts", [])

        if not prompts:
            print("⚠️  No prompts found in configuration file.")
            return

        print(f"Found {len(prompts)} prompt(s) to process.")
        print(f"Using Agent ARN: {AGENT_ARN}")

        # Simulate user interactions
        results = simulate_user_interactions(AGENT_ARN, prompts)

        # Print summary
        print(f"\n{'=' * 80}")
        print("SIMULATION SUMMARY")
        print(f"{'=' * 80}")
        print(f"Total prompts processed: {len(results)}")
        success_count = sum(1 for r in results if r["status"] == "success")
        error_count = sum(1 for r in results if r["status"] == "error")
        print(f"✅ Successful: {success_count}")
        print(f"❌ Failed: {error_count}")
        print(f"{'=' * 80}\n")

    except FileNotFoundError:
        print(f"❌ Error: Config file '{CONFIG_FILE}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON config file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
