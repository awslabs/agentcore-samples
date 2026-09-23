"""Utilities for CloudWatch to Strands Eval conversion with multi-session support.

This module provides utilities for:
- Querying CloudWatch Logs for OTEL traces (ObservabilityClient)
- Discovering sessions from CloudWatch log groups (time-based and score-based)
- Mapping CloudWatch spans to Strands Eval Session format (CloudWatchSessionMapper)
- Data models for spans, sessions, and evaluation results
- Custom CloudWatch logging with original trace IDs (send_evaluation_to_cloudwatch)

Note: Configuration is in config.py (same directory as notebooks).
"""

from .cloudwatch_client import CloudWatchQueryBuilder, ObservabilityClient
from .evaluation_cloudwatch_logger import (
    EvaluationLogConfig,
    log_evaluation_batch,
    send_evaluation_to_cloudwatch,
)
from .models import (
    EvaluationRequest,
    EvaluationResult,
    EvaluationResults,
    RuntimeLog,
    SessionDiscoveryResult,
    SessionInfo,
    Span,
    TraceData,
)
from .session_mapper import CloudWatchSessionMapper

__all__ = [
    # CloudWatch client
    "ObservabilityClient",
    "CloudWatchQueryBuilder",
    # Session mapper
    "CloudWatchSessionMapper",
    # Custom CloudWatch logger
    "send_evaluation_to_cloudwatch",
    "log_evaluation_batch",
    "EvaluationLogConfig",
    # Models
    "Span",
    "RuntimeLog",
    "TraceData",
    "SessionInfo",
    "SessionDiscoveryResult",
    "EvaluationRequest",
    "EvaluationResult",
    "EvaluationResults",
]
