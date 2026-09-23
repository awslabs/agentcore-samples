"""Data models for trace data and evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from strands_evals.mappers.session_mapper import SessionMapper
    from strands_evals.types.trace import Session


@dataclass
class Span:
    """OpenTelemetry span with trace metadata."""

    trace_id: str
    span_id: str
    span_name: str
    start_time_unix_nano: Optional[int] = None
    raw_message: Optional[Dict[str, Any]] = None

    @classmethod
    def from_cloudwatch_result(cls, result: Any) -> "Span":
        """Create Span from CloudWatch Logs Insights query result."""
        fields = result if isinstance(result, list) else result.get("fields", [])

        def get_field(field_name: str, default: Any = None) -> Any:
            for field_item in fields:
                if field_item.get("field") == field_name:
                    return field_item.get("value", default)
            return default

        def parse_json_field(field_name: str) -> Any:
            value = get_field(field_name)
            if value and isinstance(value, str):
                try:
                    return json.loads(value)
                except Exception:
                    return value
            return value

        def get_int_field(field_name: str) -> Optional[int]:
            value = get_field(field_name)
            if value is not None:
                try:
                    return int(value)
                except (ValueError, TypeError):
                    return None
            return None

        return cls(
            trace_id=get_field("traceId", ""),
            span_id=get_field("spanId", ""),
            span_name=get_field("spanName", ""),
            start_time_unix_nano=get_int_field("startTimeUnixNano"),
            raw_message=parse_json_field("@message"),
        )


@dataclass
class RuntimeLog:
    """Runtime log entry from agent-specific log groups."""

    timestamp: str
    message: str
    span_id: Optional[str] = None
    trace_id: Optional[str] = None
    raw_message: Optional[Dict[str, Any]] = None

    @classmethod
    def from_cloudwatch_result(cls, result: Any) -> "RuntimeLog":
        """Create RuntimeLog from CloudWatch Logs Insights query result."""
        fields = result if isinstance(result, list) else result.get("fields", [])

        def get_field(field_name: str, default: Any = None) -> Any:
            for field_item in fields:
                if field_item.get("field") == field_name:
                    return field_item.get("value", default)
            return default

        def parse_json_field(field_name: str) -> Any:
            value = get_field(field_name)
            if value and isinstance(value, str):
                try:
                    return json.loads(value)
                except Exception:
                    return value
            return value

        return cls(
            timestamp=get_field("@timestamp", ""),
            message=get_field("@message", ""),
            span_id=get_field("spanId"),
            trace_id=get_field("traceId"),
            raw_message=parse_json_field("@message"),
        )


@dataclass
class TraceData:
    """Complete session data including spans and runtime logs."""

    session_id: Optional[str] = None
    spans: List[Span] = field(default_factory=list)
    runtime_logs: List[RuntimeLog] = field(default_factory=list)

    def get_trace_ids(self) -> List[str]:
        """Get all unique trace IDs from spans."""
        return list(set(span.trace_id for span in self.spans if span.trace_id))

    def get_tool_execution_spans(self, tool_name_filter: Optional[str] = None) -> List[str]:
        """Get span IDs for tool execution spans.

        Args:
            tool_name_filter: Optional tool name to filter by (e.g., "calculate_bmi")

        Returns:
            List of span IDs where gen_ai.operation.name == "execute_tool"
        """
        tool_span_ids = []

        for span in self.spans:
            if not span.raw_message:
                continue

            attributes = span.raw_message.get("attributes", {})

            # Check if this is a tool execution span
            operation_name = attributes.get("gen_ai.operation.name")
            if operation_name != "execute_tool":
                continue

            # Apply tool name filter if provided
            if tool_name_filter:
                tool_name = attributes.get("gen_ai.tool.name")
                if tool_name != tool_name_filter:
                    continue

            tool_span_ids.append(span.span_id)

        return tool_span_ids

    def to_session(self, mapper: SessionMapper) -> Session:
        """Convert to Strands Eval Session using the provided mapper.

        Args:
            mapper: A SessionMapper implementation (e.g., CloudWatchSessionMapper)

        Returns:
            Session object ready for evaluation
        """
        return mapper.map_to_session(self.spans, self.session_id or "")


class EvaluationRequest:
    """Request payload for evaluation API."""

    def __init__(
        self,
        evaluator_id: str,
        session_spans: List[Dict[str, Any]],
        evaluation_target: Optional[Dict[str, Any]] = None,
    ):
        self.evaluator_id = evaluator_id
        self.session_spans = session_spans
        self.evaluation_target = evaluation_target

    def to_api_request(self) -> tuple:
        """Convert to API request format.

        Returns:
            Tuple of (evaluator_id_param, request_body)
        """
        request_body = {"evaluationInput": {"sessionSpans": self.session_spans}}

        if self.evaluation_target:
            request_body["evaluationTarget"] = self.evaluation_target

        return self.evaluator_id, request_body


@dataclass
class EvaluationResult:
    """Result from evaluation API."""

    evaluator_id: str
    evaluator_name: str
    evaluator_arn: str
    explanation: str
    context: Dict[str, Any]
    value: Optional[float] = None
    label: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = None
    error: Optional[str] = None

    @classmethod
    def from_api_response(cls, api_result: Dict[str, Any]) -> "EvaluationResult":
        """Create EvaluationResult from API response."""
        return cls(
            evaluator_id=api_result.get("evaluatorId", ""),
            evaluator_name=api_result.get("evaluatorName", ""),
            evaluator_arn=api_result.get("evaluatorArn", ""),
            explanation=api_result.get("explanation", ""),
            context=api_result.get("context", {}),
            value=api_result.get("value"),  # None if not present
            label=api_result.get("label"),  # None if not present
            token_usage=api_result.get("tokenUsage"),  # None if not present
            error=None,
        )


@dataclass
class EvaluationResults:
    """Collection of evaluation results for a session."""

    session_id: str
    results: List[EvaluationResult] = field(default_factory=list)
    input_data: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def add_result(self, result: EvaluationResult) -> None:
        """Add an evaluation result."""
        self.results.append(result)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        output = {
            "session_id": self.session_id,
            "results": [
                {
                    "evaluator_id": r.evaluator_id,
                    "evaluator_name": r.evaluator_name,
                    "evaluator_arn": r.evaluator_arn,
                    "value": r.value,
                    "label": r.label,
                    "explanation": r.explanation,
                    "context": r.context,
                    "token_usage": r.token_usage,
                    "error": r.error,
                }
                for r in self.results
            ],
        }
        if self.metadata:
            output["metadata"] = self.metadata
        if self.input_data:
            output["input_data"] = self.input_data
        return output


@dataclass
class SessionInfo:
    """Information about a discovered session.

    Attributes:
        session_id: Unique identifier for the session
        span_count: Number of spans (time_based) or evaluations (score_based)
            - For time_based discovery: actual span count from traces
            - For score_based discovery: evaluation count (also in metadata.eval_count)
        first_seen: Timestamp of first activity
        last_seen: Timestamp of last activity
        trace_count: Number of unique traces (only for time_based discovery)
        discovery_method: How session was discovered ("time_based" or "score_based")
        metadata: Additional data (for score_based: avg_score, min_score, max_score, eval_count)
    """

    session_id: str
    span_count: int
    first_seen: datetime
    last_seen: datetime
    trace_count: Optional[int] = None
    discovery_method: Optional[str] = None  # "time_based" or "score_based"
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "span_count": self.span_count,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "trace_count": self.trace_count,
            "discovery_method": self.discovery_method,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionInfo":
        """Create SessionInfo from dictionary."""
        first_seen = data["first_seen"]
        last_seen = data["last_seen"]

        # Parse datetime strings if needed and ensure timezone-aware
        if isinstance(first_seen, str):
            first_seen = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)

        if isinstance(last_seen, str):
            last_seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)

        return cls(
            session_id=data["session_id"],
            span_count=data["span_count"],
            first_seen=first_seen,
            last_seen=last_seen,
            trace_count=data.get("trace_count"),
            discovery_method=data.get("discovery_method"),
            metadata=data.get("metadata"),
        )


@dataclass
class SessionDiscoveryResult:
    """Result of session discovery operation."""

    sessions: List[SessionInfo]
    discovery_time: datetime
    log_group: str
    time_range_start: datetime
    time_range_end: datetime
    discovery_method: str
    filter_criteria: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "sessions": [s.to_dict() for s in self.sessions],
            "discovery_time": self.discovery_time.isoformat(),
            "log_group": self.log_group,
            "time_range_start": self.time_range_start.isoformat(),
            "time_range_end": self.time_range_end.isoformat(),
            "discovery_method": self.discovery_method,
            "filter_criteria": self.filter_criteria,
        }

    def save_to_json(self, filepath: str) -> None:
        """Save discovery result to JSON file."""
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_json(cls, filepath: str) -> "SessionDiscoveryResult":
        """Load discovery result from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)

        return cls(
            sessions=[SessionInfo.from_dict(s) for s in data["sessions"]],
            discovery_time=datetime.fromisoformat(data["discovery_time"].replace("Z", "+00:00")),
            log_group=data["log_group"],
            time_range_start=datetime.fromisoformat(data["time_range_start"].replace("Z", "+00:00")),
            time_range_end=datetime.fromisoformat(data["time_range_end"].replace("Z", "+00:00")),
            discovery_method=data["discovery_method"],
            filter_criteria=data.get("filter_criteria"),
        )

    def get_session_ids(self) -> List[str]:
        """Get list of session IDs."""
        return [s.session_id for s in self.sessions]
