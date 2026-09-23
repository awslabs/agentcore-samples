import sys
import os
import json

from langfuse.experiment import create_evaluator_from_autoevals
from autoevals.llm import Factuality
from openai import OpenAI

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.langfuse import get_langfuse_client
from utils.agent import invoke_agent
from utils.aws import get_ssm_parameter

# Add this at the top of your script
# logging.basicConfig(level=logging.DEBUG)
# logger = logging.getLogger("autoevals")
# logger.setLevel(logging.DEBUG)


# Load hyperparameters and agent configuration from hp_config.json
def load_hp_config(config_path="cicd/hp_config.json"):
    """Load hyperparameters and agent configuration from the JSON file."""
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        return config["tst"]
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found.")
        print("Make sure to run the deployment step first.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}")
        sys.exit(1)


# Load configuration
print("Loading agent configuration from hp_config.json...")
config = load_hp_config()

# Check if agent_arn exists in the config
if not config.get("agent_arn"):
    print("Error: agent_arn not found in hp_config.json.")
    print("Make sure to run the deployment step first.")
    sys.exit(1)

agent_arn = config["agent_arn"]
print(f"Using agent ARN from deployment: {agent_arn}")
print(f"Agent Name: {config.get('agent_name', 'N/A')}")
print(f"Agent ID: {config.get('agent_id', 'N/A')}")

# Initialize Langfuse client
langfuse_client = get_langfuse_client()

# Define Bedrock model as LLMaaJ model
# Set environment variables to point to Bedrock
os.environ["OPENAI_API_KEY"] = get_ssm_parameter("/autoevals/OPENAI_API_KEY")
os.environ["OPENAI_BASE_URL"] = get_ssm_parameter("/autoevals/OPENAI_BASE_URL")


# Get the dataset
dataset_name = "strands-ai-mcp-agent-evaluation"
dataset = langfuse_client.get_dataset(dataset_name)

# Print first 3 items of the original dataset
print(f"\n{'=' * 80}\nFirst 3 ORIGINAL items from dataset '{dataset_name}':\n{'=' * 80}")
for i, item in enumerate(dataset.items[:3]):
    print(f"\nItem {i + 1}:")
    print(f"  ID: {item.id}")
    print(f"  Input: {item.input}")
    print(f"  Expected Output: {item.expected_output}")
    print(f"  Metadata: {item.metadata}")
print(f"{'=' * 80}\n")

# Transform dataset items: expand response_facts into separate items
expanded_items = []
for item in dataset.items:
    # Extract response_facts from expected_output
    response_facts = item.expected_output.get("response_facts", [])

    # Create a new item for each response_fact
    for idx, fact in enumerate(response_facts):
        # Create a dictionary with the transformed data
        # Extract the question string from the input dictionary
        expanded_item = {"input": item.input["question"], "expected_output": fact}
        expanded_items.append(expanded_item)

# Print first 3 items of the transformed dataset
print(f"\n{'=' * 80}\nFirst 3 EXPANDED items from dataset '{dataset_name}':\n{'=' * 80}")
for i, item in enumerate(expanded_items[:3]):
    print(f"\nItem {i + 1}:")
    print(f"  Input: {item['input']}")
    print(f"  Expected Output: {item['expected_output']}")
print(f"{'=' * 80}\n")


# Define the task function that wraps invoke_agent
def agent_task(*, item, **kwargs):
    """
    Task function that invokes the agent with the dataset item input.

    Parameters:
    - item: Dictionary containing 'input' and 'expected_output'

    Returns:
    - str: The agent's response
    """
    # Extract the prompt from the dataset item
    # item is now a dictionary, input contains the question directly
    prompt = item["input"]

    # Invoke the agent
    result = invoke_agent(agent_arn, prompt)

    # Check for errors
    if "error" in result:
        raise Exception(f"Agent invocation error: {result['error']}")

    # Extract the response based on content type
    if result.get("content_type") == "application/json":
        response = result["response"]
    else:
        response = result.get("response", "")

    return response


# Define autoevals evaluator
evaluator = create_evaluator_from_autoevals(Factuality(client=OpenAI(), model="qwen.qwen3-235b-a22b-2507-v1:0"))

result = langfuse_client.run_experiment(
    name="Autoevals Integration Test",
    data=expanded_items,
    task=agent_task,
    evaluators=[evaluator],
)

print(result.format(include_item_results=True))

# Extract Factuality scores and save to file

factuality_scores = []
# Access item results from the experiment result
for item_result in result.item_results:
    for evaluation in item_result.evaluations:
        if evaluation.name == "Factuality":
            evaluation_dict = {
                "name": evaluation.name,
                "value": evaluation.value,
                "comment": evaluation.comment,
            }
            factuality_scores.append(evaluation_dict)
            print(evaluation_dict)

# Calculate average
avg_score = sum(s["value"] for s in factuality_scores) / len(factuality_scores) if factuality_scores else 0

# Save results
results = {
    "experiment_name": result.name,
    "total_items": len(factuality_scores),
    "average_factuality_score": avg_score,
    "scores": factuality_scores,
}

with open("factuality_results.json", "w") as f:
    json.dump(results, f, indent=2)

print(f"\n{'=' * 80}")
print("Factuality Results Summary:")
print(f"  Average Score: {avg_score:.3f} ({avg_score * 100:.1f}%)")
print(f"  Total Items: {len(factuality_scores)}")
print("  Results saved to: factuality_results.json")
print(f"{'=' * 80}\n")
