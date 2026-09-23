"""Online evaluation helper functions for agent invocation and evaluation workflows."""

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .evaluation_client import EvaluationClient


def generate_session_id() -> str:
    """Generate a valid session ID in UUID format.

    Returns:
        UUID v4 string (e.g., 'de45c51c-27c3-4670-aa72-c8b302b23890')
    """
    return str(uuid.uuid4())


def invoke_agent(
    agentcore_client: Any,
    agent_arn: str,
    prompt: str,
    session_id: str = "",
    qualifier: str = "DEFAULT",
) -> Tuple[str, List[str]]:
    """Invoke agent runtime and return session ID with response content.

    Args:
        agentcore_client: Boto3 agentcore client
        agent_arn: Agent runtime ARN
        prompt: User input prompt
        session_id: Optional session ID for multi-turn conversations (UUID format)
                   - Empty string '' = create new session
                   - Valid UUID = continue existing session or use specific session ID
        qualifier: Agent runtime qualifier (default: DEFAULT)

    Returns:
        Tuple of (session_id, content_list)
    """
    api_params = {
        "agentRuntimeArn": agent_arn,
        "qualifier": qualifier,
        "payload": json.dumps({"prompt": prompt}),
    }

    if session_id:
        api_params["runtimeSessionId"] = session_id

    boto3_response = agentcore_client.invoke_agent_runtime(**api_params)

    returned_session_id = (
        boto3_response["ResponseMetadata"]["HTTPHeaders"].get("x-amzn-bedrock-agentcore-runtime-session-id")
        or boto3_response.get("runtimeSessionId")
        or session_id
    )

    content = []
    if "text/event-stream" in boto3_response.get("contentType", ""):
        for line in boto3_response["response"].iter_lines(chunk_size=1):
            if line:
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    content.append(line[6:])
    else:
        try:
            events = [event for event in boto3_response.get("response", [])]
            if events:
                content = [json.loads(events[0].decode("utf-8"))]
        except Exception as e:
            content = [f"Error reading EventStream: {e}"]

    return returned_session_id, content


def evaluate_session(
    eval_client: EvaluationClient,
    session_id: str,
    evaluators: List[str],
    scope: str,
    agent_id: str,
    region: str,
    experiment_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Any:
    """Evaluate a session with specified evaluators.

    Args:
        eval_client: EvaluationClient instance
        session_id: Session ID to evaluate
        evaluators: List of evaluator IDs
        scope: Evaluation scope (session, trace, or span)
        agent_id: Agent ID
        region: AWS region
        experiment_name: Experiment identifier for tracking
        metadata: Optional metadata dictionary

    Returns:
        EvaluationResults object
    """
    eval_metadata = {"experiment": experiment_name}
    if metadata:
        eval_metadata.update(metadata)

    results = eval_client.evaluate_session(
        session_id=session_id,
        evaluator_ids=evaluators,
        agent_id=agent_id,
        region=region,
        scope=scope,
        auto_save_input=True,
        auto_save_output=True,
        auto_create_dashboard=True,
        metadata=eval_metadata,
    )

    return results


def evaluate_session_comprehensive(
    eval_client: EvaluationClient,
    session_id: str,
    agent_id: str,
    region: str,
    experiment_name: str,
    flexible_evaluators: List[str],
    session_only_evaluators: List[str],
    span_only_evaluators: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Run all evaluators across appropriate scopes.

    Args:
        eval_client: EvaluationClient instance
        session_id: Session ID to evaluate
        agent_id: Agent ID
        region: AWS region
        experiment_name: Experiment identifier
        flexible_evaluators: List of flexible scope evaluators
        session_only_evaluators: List of session-only evaluators
        span_only_evaluators: List of span-only evaluators
        metadata: Optional metadata dictionary

    Returns:
        List of combined evaluation results
    """
    all_results = []

    evaluation_configs = [
        {"evaluators": flexible_evaluators, "scope": "session"},
        {"evaluators": session_only_evaluators, "scope": "session"},
        {"evaluators": span_only_evaluators, "scope": "span"},
    ]

    for config in evaluation_configs:
        if config["evaluators"]:
            try:
                results = evaluate_session(
                    eval_client=eval_client,
                    session_id=session_id,
                    evaluators=config["evaluators"],
                    scope=config["scope"],
                    agent_id=agent_id,
                    region=region,
                    experiment_name=experiment_name,
                    metadata=metadata,
                )
                all_results.extend(results.results)
            except Exception as e:
                print(f"Error in {config['scope']} evaluation: {e}")

    return all_results


def invoke_and_evaluate(
    agentcore_client: Any,
    eval_client: EvaluationClient,
    agent_arn: str,
    agent_id: str,
    region: str,
    prompt: str,
    experiment_name: str,
    session_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    evaluators: Optional[List[str]] = None,
    scope: str = "session",
    delay: int = 90,
    flexible_evaluators: Optional[List[str]] = None,
    session_only_evaluators: Optional[List[str]] = None,
    span_only_evaluators: Optional[List[str]] = None,
) -> Tuple[str, List[Any]]:
    """Complete workflow: invoke agent, wait for log propagation, then evaluate.

    Args:
        agentcore_client: Boto3 agentcore client
        eval_client: EvaluationClient instance
        agent_arn: Agent runtime ARN
        agent_id: Agent ID
        region: AWS region
        prompt: User input prompt
        experiment_name: Experiment identifier
        session_id: Optional session ID (empty = new session, UUID = continue/specify session)
        metadata: Optional metadata dictionary
        evaluators: List of evaluator IDs (None = use comprehensive evaluation)
        scope: Evaluation scope (session, trace, span)
        delay: Seconds to wait for CloudWatch propagation
        flexible_evaluators: Required if evaluators is None
        session_only_evaluators: Required if evaluators is None
        span_only_evaluators: Required if evaluators is None

    Returns:
        Tuple of (session_id, results_list)
    """
    returned_session_id, content = invoke_agent(
        agentcore_client=agentcore_client,
        agent_arn=agent_arn,
        prompt=prompt,
        session_id=session_id,
    )

    time.sleep(delay)

    if evaluators is None:
        if not all([flexible_evaluators, session_only_evaluators, span_only_evaluators]):
            raise ValueError("Must provide evaluator lists for comprehensive evaluation")

        results = evaluate_session_comprehensive(
            eval_client=eval_client,
            session_id=returned_session_id,
            agent_id=agent_id,
            region=region,
            experiment_name=experiment_name,
            flexible_evaluators=flexible_evaluators,
            session_only_evaluators=session_only_evaluators,
            span_only_evaluators=span_only_evaluators,
            metadata=metadata,
        )
    else:
        eval_results = evaluate_session(
            eval_client=eval_client,
            session_id=returned_session_id,
            evaluators=evaluators,
            scope=scope,
            agent_id=agent_id,
            region=region,
            experiment_name=experiment_name,
            metadata=metadata,
        )
        results = eval_results.results

    return returned_session_id, content, results
