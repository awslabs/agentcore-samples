import os
from datetime import datetime
from langfuse import get_client
from utils.agent import invoke_agent
from utils.aws import get_ssm_parameter


def get_langfuse_client():
    """
    Initialize and return a Langfuse client with the proper configuration.

    Returns:
    - Langfuse client instance
    """

    os.environ["LANGFUSE_HOST"] = get_ssm_parameter("/langfuse/LANGFUSE_HOST")
    os.environ["LANGFUSE_SECRET_KEY"] = get_ssm_parameter("/langfuse/LANGFUSE_SECRET_KEY")
    os.environ["LANGFUSE_PUBLIC_KEY"] = get_ssm_parameter("/langfuse/LANGFUSE_PUBLIC_KEY")
    os.environ["LANGFUSE_PROJECT_NAME"] = get_ssm_parameter("/langfuse/LANGFUSE_PROJECT_NAME")
    # Initialize Langfuse client
    client = get_client()

    return client


def run_experiment(
    agent_arn,
    dataset_name="strands-ai-mcp-agent-evaluation",
    experiment_name=None,
    experiment_description=None,
    evaluators=None,
    run_evaluators=None,
    max_concurrency=1,
    metadata=None,
):
    """
    Run an experiment on a Langfuse dataset using invoke_agent as the task function.

    Parameters:
    - agent_arn (str): The ARN of the deployed agent runtime
    - dataset_name (str): Name of the dataset in Langfuse (default: "strands-ai-mcp-agent-evaluation")
    - experiment_name (str): Name for this experiment run (default: "{timestamp}_strands_langfuse_mcp_experimentation")
    - experiment_description (str, optional): Description of the experiment
    - evaluators (list, optional): List of evaluator functions for item-level evaluation
    - run_evaluators (list, optional): List of evaluator functions for run-level evaluation
    - max_concurrency (int): Maximum number of concurrent task executions (default: 1)

    Returns:
    - dict: Experiment result containing traces, scores, and metadata
    """

    # Extend experiment name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    experiment_name_ts = f"{timestamp}_{experiment_name}"

    # Initialize Langfuse client
    langfuse = get_langfuse_client()

    # Get the dataset
    dataset = langfuse.get_dataset(dataset_name)

    # Define the task function that wraps invoke_agent
    def agent_task(*, item, **kwargs):
        """
        Task function that invokes the agent with the dataset item input.

        Parameters:
        - item: DatasetItemClient object containing input and optionally expected_output

        Returns:
        - str: The agent's response
        """
        # Extract the prompt from the dataset item
        # Use dot notation to access DatasetItemClient properties
        prompt = item.input["question"]

        # Invoke the agent
        result = invoke_agent(agent_arn, prompt, environment="DEV")

        # Check for errors
        if "error" in result:
            raise Exception(f"Agent invocation error: {result['error']}")

        # Extract the response based on content type
        if result.get("content_type") == "application/json":
            response = result["response"]
        else:
            response = result.get("response", "")

        return response

    # Run the experiment on the dataset
    result = dataset.run_experiment(
        name=experiment_name_ts,
        description=experiment_description or f"Evaluation of agent {agent_arn}",
        task=agent_task,
        metadata=metadata,
        # evaluators=evaluators or [],
        # run_evaluators=run_evaluators or [],
        # max_concurrency=max_concurrency
    )

    # Print formatted results
    print("\n" + "=" * 80)
    print("EXPERIMENT RESULTS")
    print("=" * 80)
    print(result.format())
    print("=" * 80 + "\n")

    return result


def run_experiment_with_evaluators(
    agent_arn,
    dataset_name="strands-ai-mcp-agent-evaluation",
    experiment_name="Agent Evaluation with Scoring",
    experiment_description=None,
    max_concurrency=1,
):
    """
    Run an experiment with example evaluators for response quality assessment.

    Parameters:
    - agent_arn (str): The ARN of the deployed agent runtime
    - dataset_name (str): Name of the dataset in Langfuse
    - experiment_name (str): Name for this experiment run
    - experiment_description (str, optional): Description of the experiment
    - max_concurrency (int): Maximum number of concurrent task executions

    Returns:
    - dict: Experiment result with evaluations
    """
    from langfuse import Evaluation

    # Define item-level evaluator
    def response_length_evaluator(*, input, output, expected_output, metadata, **kwargs):
        """
        Evaluates if the response has a reasonable length (not too short).
        """
        if isinstance(output, str):
            response_text = output
        else:
            response_text = str(output)

        # Check if response is at least 10 characters
        is_adequate = len(response_text) >= 10

        return Evaluation(
            name="response_length",
            value=1.0 if is_adequate else 0.0,
            comment=f"Response length: {len(response_text)} characters",
        )

    def response_quality_evaluator(*, input, output, expected_output, metadata, **kwargs):
        """
        Basic quality check - ensures response doesn't contain error indicators.
        """
        if isinstance(output, str):
            response_text = output.lower()
        else:
            response_text = str(output).lower()

        # Check for common error patterns
        error_indicators = ["error", "failed", "unable", "cannot", "invalid"]
        has_errors = any(indicator in response_text for indicator in error_indicators)

        return Evaluation(
            name="response_quality",
            value=0.0 if has_errors else 1.0,
            comment="Response contains error indicators" if has_errors else "Response appears valid",
        )

    # Define run-level evaluator
    def average_score_evaluator(*, run_evaluations, **kwargs):
        """
        Calculates average score across all item evaluations.
        """
        if not run_evaluations:
            return Evaluation(name="avg_score", value=0.0, comment="No evaluations to average")

        # Calculate average of response_quality scores
        quality_scores = [eval.value for eval in run_evaluations if eval.name == "response_quality"]

        if quality_scores:
            avg = sum(quality_scores) / len(quality_scores)
            return Evaluation(
                name="avg_response_quality",
                value=avg,
                comment=f"Average response quality: {avg:.2%}",
            )

        return Evaluation(name="avg_response_quality", value=0.0, comment="No quality scores found")

    # Run experiment with evaluators
    return run_experiment(
        agent_arn=agent_arn,
        dataset_name=dataset_name,
        experiment_name=experiment_name,
        experiment_description=experiment_description,
        evaluators=[response_length_evaluator, response_quality_evaluator],
        run_evaluators=[average_score_evaluator],
        max_concurrency=max_concurrency,
    )
