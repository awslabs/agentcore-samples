"""Custom CloudWatch logger for evaluation results with original trace IDs.

This module provides CloudWatch logging that uses the exact EMF format expected
by AgentCore Observability Dashboard, but with trace IDs from the original
AgentCore trace dataset instead of generating new ones.

Based on strands_evals.telemetry._cloudwatch_logger but modified to accept
trace_id as a parameter from the case metadata.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

# Module-level CloudWatch client (lazy initialization)
_cloudwatch_client = None


def _get_cloudwatch_client():
    """Get or create the CloudWatch Logs client (singleton pattern)."""
    global _cloudwatch_client
    if _cloudwatch_client is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        _cloudwatch_client = boto3.client("logs", region_name=region)
    return _cloudwatch_client


@dataclass
class EvaluationLogConfig:
    """Configuration for evaluation logging."""

    destination_log_group: str
    log_stream: str
    service_name: str
    resource_log_group: Optional[str] = None

    @classmethod
    def from_environment(cls) -> "EvaluationLogConfig":
        """Parse log configuration from environment variables.

        Environment variables:
        - EVALUATION_RESULTS_LOG_GROUP: Base name for results log group
        - LOG_STREAM_NAME: Explicit log stream name (takes priority)
        - OTEL_RESOURCE_ATTRIBUTES: Contains service.name and optionally aws.log.group.names
        - OTEL_EXPORTER_OTLP_LOGS_HEADERS: Contains x-aws-log-stream (fallback)
        """
        # Destination log group from EVALUATION_RESULTS_LOG_GROUP
        base_log_group = os.environ.get("EVALUATION_RESULTS_LOG_GROUP", "default_strands_evals_results")
        destination_log_group = f"/aws/bedrock-agentcore/evaluations/results/{base_log_group}"

        # Log stream: First check LOG_STREAM_NAME env var (explicit override)
        log_stream = os.environ.get("LOG_STREAM_NAME", "")

        # Fallback: Parse log stream from OTEL_EXPORTER_OTLP_LOGS_HEADERS
        if not log_stream:
            logs_headers = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_HEADERS", "")
            if logs_headers:
                for header in logs_headers.split(","):
                    if "=" in header:
                        key, value = header.split("=", 1)
                        if key.strip() == "x-aws-log-stream":
                            log_stream = value.strip()
                            break

        # Final fallback: use "default"
        if not log_stream:
            log_stream = "default"

        # Parse OTEL_RESOURCE_ATTRIBUTES for service.name and aws.log.group.names
        resource_attrs = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")
        service_name = None
        resource_log_group = None

        for attr in resource_attrs.split(","):
            if "=" in attr:
                key, value = attr.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key == "service.name":
                    service_name = value
                elif key == "aws.log.group.names":
                    resource_log_group = value

        if not service_name:
            raise ValueError("service.name must be set in OTEL_RESOURCE_ATTRIBUTES environment variable")

        return cls(
            destination_log_group=destination_log_group,
            log_stream=log_stream,
            service_name=service_name,
            resource_log_group=resource_log_group,
        )


def send_evaluation_to_cloudwatch(
    trace_id: str,
    session_id: str,
    evaluator_name: str,
    score: float,
    explanation: str,
    evaluation_level: str = "Trace",
    label: Optional[str] = None,
    config_id: str = "strands-offline-evaluation",
) -> bool:
    """Send evaluation result to CloudWatch in EMF format.

    This function uses the exact EMF format expected by AgentCore Observability Dashboard,
    but with the trace_id from the original AgentCore trace dataset.

    Args:
        trace_id: The original trace ID from AgentCore Observability (passed through from case metadata)
        session_id: The session ID from the original trace dataset
        evaluator_name: Full evaluator name (e.g., "Custom.StrandsEvalOfflineTravelEvaluator")
        score: Evaluation score (0.0 to 1.0)
        explanation: Explanation for the score
        evaluation_level: "Trace" or "Span" (default: "Trace")
        label: Score label ("YES", "NO", or custom). If None, derived from score.
        config_id: Configuration ID for ARN construction (default: "strands-offline-evaluation")

    Returns:
        True if logging succeeded, False otherwise
    """
    try:
        config = EvaluationLogConfig.from_environment()

        if not config.destination_log_group:
            logger.warning("No destination log group configured, skipping CloudWatch logging")
            return False

        cloudwatch_client = _get_cloudwatch_client()

        # Ensure log group exists
        try:
            cloudwatch_client.create_log_group(logGroupName=config.destination_log_group)
            logger.info(f"Created log group: {config.destination_log_group}")
        except cloudwatch_client.exceptions.ResourceAlreadyExistsException:
            pass
        except Exception as e:
            logger.warning(f"Failed to create log group: {str(e)}")

        # Ensure log stream exists
        try:
            cloudwatch_client.create_log_stream(
                logGroupName=config.destination_log_group,
                logStreamName=config.log_stream,
            )
            logger.info(f"Created log stream: {config.log_stream}")
        except cloudwatch_client.exceptions.ResourceAlreadyExistsException:
            pass
        except Exception as e:
            logger.warning(f"Failed to create log stream: {str(e)}")

        # Get sequence token for the log stream
        sequence_token = None
        try:
            response = cloudwatch_client.describe_log_streams(
                logGroupName=config.destination_log_group,
                logStreamNamePrefix=config.log_stream,
            )
            if response["logStreams"]:
                sequence_token = response["logStreams"][0].get("uploadSequenceToken")
        except Exception as e:
            logger.warning(f"Failed to get sequence token: {str(e)}")

        # Derive label from score if not provided
        if label is None:
            label = "YES" if score >= 0.5 else "NO"

        # Build ARNs (using bedrock-agentcore format)
        region = os.environ.get("AWS_REGION", "us-east-1")
        account_id = os.environ.get("AWS_ACCOUNT_ID", "")
        config_arn = f"arn:aws:bedrock-agentcore:{region}:{account_id}:online-evaluation-config/{config_id}"
        evaluator_arn = f"arn:aws:bedrock-agentcore:::evaluator/{evaluator_name}"

        # Derive config_name from config_id (e.g., "EKS_Agent_Evaluation" from "EKS_Agent_Evaluation-5MB8aF5rLE")
        config_name = config_id.rsplit("-", 1)[0] if "-" in config_id else config_id

        # Get current timestamp
        current_time_ns = time.time_ns()
        current_time_ms = int(current_time_ns / 1_000_000)

        # Build log_data (attributes that go inside EMF)
        log_data = {
            "gen_ai.evaluation.name": evaluator_name,
            "session.id": session_id,
            "gen_ai.response.id": trace_id,
            "gen_ai.evaluation.score.value": score,
            "gen_ai.evaluation.explanation": explanation or "",
            "gen_ai.evaluation.score.label": label,
            "aws.bedrock_agentcore.online_evaluation_config.arn": config_arn,
            "aws.bedrock_agentcore.online_evaluation_config.name": config_name,
            "aws.bedrock_agentcore.evaluator.arn": evaluator_arn,
            "aws.bedrock_agentcore.evaluator.rating_scale": "Numerical",
            "aws.bedrock_agentcore.evaluation_level": evaluation_level,
        }

        # Build EMF log structure (exact format from strands_evals)
        emf_log = {
            "resource": {
                "attributes": {
                    "aws.service.type": "gen_ai_agent",
                    "aws.local.service": config.service_name,
                    "service.name": config.service_name,
                }
            },
            "traceId": trace_id,
            "timeUnixNano": current_time_ns,
            "observedTimeUnixNano": current_time_ns,
            "severityNumber": 9,
            "name": "gen_ai.evaluation.result",
            "attributes": {
                **log_data,
            },
            "onlineEvaluationConfigId": config_id,
            evaluator_name: score,  # Dynamic key for metric
            "label": label,
            "service.name": config.service_name,
            "_aws": {
                "Timestamp": current_time_ms,
                "CloudWatchMetrics": [
                    {
                        "Namespace": "Bedrock-AgentCore/Evaluations",
                        "Dimensions": [
                            ["service.name"],
                            ["label", "service.name"],
                            ["service.name", "onlineEvaluationConfigId"],
                            ["label", "service.name", "onlineEvaluationConfigId"],
                        ],
                        "Metrics": [{"Name": evaluator_name, "Unit": "None"}],
                    }
                ],
            },
        }

        # Send to CloudWatch
        log_event = {"timestamp": current_time_ms, "message": json.dumps(emf_log)}

        put_log_params = {
            "logGroupName": config.destination_log_group,
            "logStreamName": config.log_stream,
            "logEvents": [log_event],
        }

        if sequence_token:
            put_log_params["sequenceToken"] = sequence_token

        cloudwatch_client.put_log_events(**put_log_params)

        logger.info(
            f"Sent evaluation to CloudWatch: trace_id={trace_id[:16]}..., "
            f"evaluator={evaluator_name}, score={score}, label={label}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send evaluation to CloudWatch: {str(e)}")
        return False


def log_evaluation_batch(
    results: list[dict],
    evaluator_name: str,
    config_id: str = "strands-offline-evaluation",
) -> int:
    """Send multiple evaluation results to CloudWatch.

    Args:
        results: List of dicts with keys: trace_id, session_id, score, explanation, label (optional)
        evaluator_name: Full evaluator name
        config_id: Configuration ID

    Returns:
        Number of successfully logged results
    """
    success_count = 0
    for result in results:
        success = send_evaluation_to_cloudwatch(
            trace_id=result["trace_id"],
            session_id=result["session_id"],
            evaluator_name=evaluator_name,
            score=result["score"],
            explanation=result.get("explanation", ""),
            label=result.get("label"),
            config_id=config_id,
        )
        if success:
            success_count += 1

    logger.info(f"Logged {success_count}/{len(results)} evaluation results to CloudWatch")
    return success_count
