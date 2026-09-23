"""Client for querying observability data from CloudWatch Logs."""

import logging
import time
from typing import List

import boto3

from datetime import datetime, timezone
from typing import Optional

from .models import RuntimeLog, SessionInfo, Span, TraceData


class CloudWatchQueryBuilder:
    """Builder for CloudWatch Logs Insights queries."""

    @staticmethod
    def build_spans_by_session_query(session_id: str, agent_id: str = None) -> str:
        """Build query to get all spans for a session from aws/spans log group.

        Args:
            session_id: The session ID to filter by
            agent_id: Optional agent ID to filter by

        Returns:
            CloudWatch Logs Insights query string
        """
        base_filter = f"attributes.session.id = '{session_id}'"

        return f"""fields @timestamp,
               @message,
               traceId,
               spanId,
               name as spanName,
               kind,
               status.code as statusCode,
               status.message as statusMessage,
               durationNano/1000000 as durationMs,
               attributes.session.id as sessionId,
               startTimeUnixNano,
               endTimeUnixNano,
               parentSpanId,
               events,
               resource.attributes.service.name as serviceName,
               resource.attributes.cloud.resource_id as resourceId,
               attributes.aws.remote.service as serviceType
        | filter {base_filter}
        | sort startTimeUnixNano asc"""

    @staticmethod
    def build_runtime_logs_by_traces_batch(trace_ids: List[str]) -> str:
        """Build optimized query to get runtime logs for multiple traces in one query.

        Args:
            trace_ids: List of trace IDs to filter by

        Returns:
            CloudWatch Logs Insights query string
        """
        if not trace_ids:
            return ""

        trace_ids_quoted = ", ".join([f"'{tid}'" for tid in trace_ids])

        return f"""fields @timestamp, @message, spanId, traceId, @logStream
        | filter traceId in [{trace_ids_quoted}]
        | sort @timestamp asc"""

    @staticmethod
    def build_runtime_logs_by_trace_direct(trace_id: str) -> str:
        """Build query to get runtime logs for a trace.

        Args:
            trace_id: The trace ID to filter by

        Returns:
            CloudWatch Logs Insights query string
        """
        return f"""fields @timestamp, @message, spanId, traceId, @logStream
        | filter traceId = '{trace_id}'
        | sort @timestamp asc"""

    @staticmethod
    def build_discover_sessions_query() -> str:
        """Build query to discover unique session IDs within a time window.

        Returns:
            CloudWatch Logs Insights query string that returns unique session IDs
            with span counts and time ranges.
        """
        return """fields @timestamp, attributes.session.id as sessionId, traceId
        | filter ispresent(attributes.session.id)
        | stats count(*) as spanCount,
                min(@timestamp) as firstSeen,
                max(@timestamp) as lastSeen,
                count_distinct(traceId) as traceCount
          by sessionId
        | sort lastSeen desc"""

    @staticmethod
    def build_sessions_by_score_query(
        evaluator_name: str,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
    ) -> str:
        """Build query to find sessions by evaluation score from results log group.

        Args:
            evaluator_name: Name of the evaluator (e.g., "Custom.StrandsEvalOfflineTravelEvaluator")
            min_score: Minimum score threshold (inclusive)
            max_score: Maximum score threshold (inclusive)

        Returns:
            CloudWatch Logs Insights query string
        """
        # Build score filter conditions
        score_filters = []
        if min_score is not None:
            score_filters.append(f"`{evaluator_name}` >= {min_score}")
        if max_score is not None:
            score_filters.append(f"`{evaluator_name}` <= {max_score}")

        score_filter_clause = ""
        if score_filters:
            score_filter_clause = "| filter " + " and ".join(score_filters)

        return f"""fields @timestamp,
               attributes.session.id as sessionId,
               attributes.gen_ai.response.id as traceId,
               `{evaluator_name}` as score,
               label
        | filter ispresent(`{evaluator_name}`)
        {score_filter_clause}
        | stats count(*) as evalCount,
                avg(score) as avgScore,
                min(score) as minScore,
                max(score) as maxScore,
                min(@timestamp) as firstEval,
                max(@timestamp) as lastEval
          by sessionId
        | sort avgScore asc"""


class ObservabilityClient:
    """Client for querying spans and runtime logs from CloudWatch Logs."""

    QUERY_TIMEOUT_SECONDS = 60
    POLL_INTERVAL_SECONDS = 2

    def __init__(
        self,
        region_name: str,
        log_group: str,
        agent_id: str = None,
        runtime_suffix: str = "DEFAULT",
    ):
        """Initialize the ObservabilityClient.

        Args:
            region_name: AWS region name
            log_group: CloudWatch log group name for spans/traces
            agent_id: Optional agent ID (not used for filtering currently)
            runtime_suffix: Runtime suffix for log group (default: DEFAULT)
        """
        self.region = region_name
        self.log_group = log_group
        self.agent_id = agent_id
        self.runtime_suffix = runtime_suffix

        self.logs_client = boto3.client("logs", region_name=region_name)
        self.query_builder = CloudWatchQueryBuilder()

        self.logger = logging.getLogger("cloudwatch_client")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def query_spans_by_session(
        self,
        session_id: str,
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[Span]:
        """Query all spans for a session from aws/spans log group.

        Args:
            session_id: The session ID to query
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch

        Returns:
            List of Span objects
        """
        self.logger.info(
            "Querying spans for session: %s from log group: %s",
            session_id,
            self.log_group,
        )

        query_string = self.query_builder.build_spans_by_session_query(session_id)

        results = self._execute_cloudwatch_query(
            query_string=query_string,
            log_group_name=self.log_group,
            start_time=start_time_ms,
            end_time=end_time_ms,
        )

        spans = [Span.from_cloudwatch_result(result) for result in results]
        self.logger.info("Found %d spans for session %s", len(spans), session_id)

        return spans

    def query_runtime_logs_by_traces(
        self,
        trace_ids: List[str],
        start_time_ms: int,
        end_time_ms: int,
    ) -> List[RuntimeLog]:
        """Query runtime logs for multiple traces from agent-specific log group.

        Args:
            trace_ids: List of trace IDs to query
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch

        Returns:
            List of RuntimeLog objects
        """
        if not trace_ids:
            return []

        self.logger.info("Querying runtime logs for %d traces", len(trace_ids))

        query_string = self.query_builder.build_runtime_logs_by_traces_batch(trace_ids)

        try:
            results = self._execute_cloudwatch_query(
                query_string=query_string,
                log_group_name=self.log_group,
                start_time=start_time_ms,
                end_time=end_time_ms,
            )

            logs = [RuntimeLog.from_cloudwatch_result(result) for result in results]
            self.logger.info("Found %d runtime logs across %d traces", len(logs), len(trace_ids))
            return logs

        except Exception as e:
            self.logger.error("Failed to query runtime logs: %s", str(e))
            return []

    def get_session_data(
        self,
        session_id: str,
        start_time_ms: int,
        end_time_ms: int,
        include_runtime_logs: bool = True,
    ) -> TraceData:
        """Get complete session data including spans and optionally runtime logs.

        Args:
            session_id: The session ID to query
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch
            include_runtime_logs: Whether to fetch runtime logs (default: True)

        Returns:
            TraceData object with spans and runtime logs
        """
        self.logger.info("Fetching session data for: %s", session_id)

        spans = self.query_spans_by_session(session_id, start_time_ms, end_time_ms)

        session_data = TraceData(
            session_id=session_id,
            spans=spans,
        )

        if include_runtime_logs:
            trace_ids = session_data.get_trace_ids()
            if trace_ids:
                runtime_logs = self.query_runtime_logs_by_traces(trace_ids, start_time_ms, end_time_ms)
                session_data.runtime_logs = runtime_logs

        self.logger.info(
            "Session data retrieved: %d spans, %d traces, %d runtime logs",
            len(session_data.spans),
            len(session_data.get_trace_ids()),
            len(session_data.runtime_logs),
        )

        return session_data

    def discover_sessions(
        self,
        start_time_ms: int,
        end_time_ms: int,
        limit: int = 100,
    ) -> List[SessionInfo]:
        """Discover unique session IDs within a time window.

        Args:
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch
            limit: Maximum number of sessions to return (default: 100)

        Returns:
            List of SessionInfo objects with session metadata
        """
        self.logger.info(
            "Discovering sessions in log group: %s from %s to %s",
            self.log_group,
            datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc),
            datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc),
        )

        query_string = self.query_builder.build_discover_sessions_query()

        results = self._execute_cloudwatch_query(
            query_string=query_string,
            log_group_name=self.log_group,
            start_time=start_time_ms,
            end_time=end_time_ms,
        )

        sessions = []
        for result in results[:limit]:
            session_info = self._parse_session_discovery_result(result)
            if session_info:
                session_info.discovery_method = "time_based"
                sessions.append(session_info)

        self.logger.info("Discovered %d sessions", len(sessions))
        return sessions

    def discover_sessions_by_score(
        self,
        evaluation_log_group: str,
        evaluator_name: str,
        start_time_ms: int,
        end_time_ms: int,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
        limit: int = 100,
    ) -> List[SessionInfo]:
        """Discover sessions by evaluation score from evaluation results log group.

        Args:
            evaluation_log_group: Log group containing evaluation results
            evaluator_name: Name of the evaluator to filter by
            start_time_ms: Start time in milliseconds since epoch
            end_time_ms: End time in milliseconds since epoch
            min_score: Minimum score threshold (inclusive)
            max_score: Maximum score threshold (inclusive)
            limit: Maximum number of sessions to return (default: 100)

        Returns:
            List of SessionInfo objects with session metadata and score info
        """
        self.logger.info(
            "Discovering sessions by score in log group: %s (evaluator: %s, score range: %s-%s)",
            evaluation_log_group,
            evaluator_name,
            min_score,
            max_score,
        )

        query_string = self.query_builder.build_sessions_by_score_query(
            evaluator_name=evaluator_name,
            min_score=min_score,
            max_score=max_score,
        )

        results = self._execute_cloudwatch_query(
            query_string=query_string,
            log_group_name=evaluation_log_group,
            start_time=start_time_ms,
            end_time=end_time_ms,
        )

        sessions = []
        for result in results[:limit]:
            session_info = self._parse_score_discovery_result(result)
            if session_info:
                session_info.discovery_method = "score_based"
                sessions.append(session_info)

        self.logger.info("Discovered %d sessions by score", len(sessions))
        return sessions

    def _parse_session_discovery_result(self, result) -> Optional[SessionInfo]:
        """Parse CloudWatch result into SessionInfo for time-based discovery."""
        fields = result if isinstance(result, list) else result.get("fields", [])

        def get_field(field_name: str, default=None):
            for field_item in fields:
                if field_item.get("field") == field_name:
                    return field_item.get("value", default)
            return default

        session_id = get_field("sessionId")
        if not session_id:
            return None

        span_count_str = get_field("spanCount", "0")
        trace_count_str = get_field("traceCount")
        first_seen_str = get_field("firstSeen")
        last_seen_str = get_field("lastSeen")

        # Parse counts
        try:
            span_count = int(float(span_count_str))
        except (ValueError, TypeError):
            span_count = 0

        trace_count = None
        if trace_count_str:
            try:
                trace_count = int(float(trace_count_str))
            except (ValueError, TypeError):
                pass

        # Parse timestamps - require valid timestamps, don't fallback to now()
        first_seen = None
        last_seen = None
        if first_seen_str:
            try:
                first_seen = datetime.fromisoformat(first_seen_str.replace("Z", "+00:00"))
                # Ensure timezone-aware
                if first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Failed to parse first_seen '{first_seen_str}': {e}")
        if last_seen_str:
            try:
                last_seen = datetime.fromisoformat(last_seen_str.replace("Z", "+00:00"))
                # Ensure timezone-aware
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Failed to parse last_seen '{last_seen_str}': {e}")

        # Skip sessions with missing timestamps
        if first_seen is None or last_seen is None:
            self.logger.warning(f"Session {session_id} missing timestamps, skipping")
            return None

        return SessionInfo(
            session_id=session_id,
            span_count=span_count,
            first_seen=first_seen,
            last_seen=last_seen,
            trace_count=trace_count,
        )

    def _parse_score_discovery_result(self, result) -> Optional[SessionInfo]:
        """Parse CloudWatch result into SessionInfo for score-based discovery.

        Note: For score-based discovery, span_count represents the evaluation count
        (number of evaluations found for this session), not the span count from traces.
        The actual eval_count is also stored in metadata for clarity.
        """
        fields = result if isinstance(result, list) else result.get("fields", [])

        def get_field(field_name: str, default=None):
            for field_item in fields:
                if field_item.get("field") == field_name:
                    return field_item.get("value", default)
            return default

        session_id = get_field("sessionId")
        if not session_id:
            return None

        eval_count_str = get_field("evalCount", "0")
        avg_score_str = get_field("avgScore", "0")
        min_score_str = get_field("minScore", "0")
        max_score_str = get_field("maxScore", "0")
        first_eval_str = get_field("firstEval")
        last_eval_str = get_field("lastEval")

        # Parse counts and scores
        try:
            eval_count = int(float(eval_count_str))
        except (ValueError, TypeError):
            eval_count = 0

        try:
            avg_score = float(avg_score_str)
        except (ValueError, TypeError):
            avg_score = 0.0

        try:
            min_score = float(min_score_str)
        except (ValueError, TypeError):
            min_score = 0.0

        try:
            max_score = float(max_score_str)
        except (ValueError, TypeError):
            max_score = 0.0

        # Parse timestamps - require valid timestamps, don't fallback to now()
        first_seen = None
        last_seen = None
        if first_eval_str:
            try:
                first_seen = datetime.fromisoformat(first_eval_str.replace("Z", "+00:00"))
                # Ensure timezone-aware
                if first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Failed to parse first_eval '{first_eval_str}': {e}")
        if last_eval_str:
            try:
                last_seen = datetime.fromisoformat(last_eval_str.replace("Z", "+00:00"))
                # Ensure timezone-aware
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Failed to parse last_eval '{last_eval_str}': {e}")

        # Skip sessions with missing timestamps
        if first_seen is None or last_seen is None:
            self.logger.warning(f"Session {session_id} missing timestamps, skipping")
            return None

        return SessionInfo(
            session_id=session_id,
            span_count=eval_count,  # For score-based: eval_count (see docstring)
            first_seen=first_seen,
            last_seen=last_seen,
            metadata={
                "avg_score": avg_score,
                "min_score": min_score,
                "max_score": max_score,
                "eval_count": eval_count,
            },
        )

    def _execute_cloudwatch_query(
        self,
        query_string: str,
        log_group_name: str,
        start_time: int,
        end_time: int,
    ) -> list:
        """Execute a CloudWatch Logs Insights query and wait for results.

        Args:
            query_string: The CloudWatch Logs Insights query
            log_group_name: The log group to query
            start_time: Start time in milliseconds since epoch
            end_time: End time in milliseconds since epoch

        Returns:
            List of result dictionaries

        Raises:
            TimeoutError: If query doesn't complete within timeout
            Exception: If query fails
        """
        self.logger.debug("Starting CloudWatch query on log group: %s", log_group_name)

        try:
            response = self.logs_client.start_query(
                logGroupName=log_group_name,
                startTime=start_time // 1000,
                endTime=end_time // 1000,
                queryString=query_string,
            )
        except self.logs_client.exceptions.ResourceNotFoundException as e:
            self.logger.error("Log group not found: %s", log_group_name)
            raise Exception(f"Log group not found: {log_group_name}") from e

        query_id = response["queryId"]
        self.logger.debug("Query started with ID: %s", query_id)

        start_poll_time = time.time()
        while True:
            elapsed = time.time() - start_poll_time
            if elapsed > self.QUERY_TIMEOUT_SECONDS:
                raise TimeoutError(f"Query {query_id} timed out after {self.QUERY_TIMEOUT_SECONDS} seconds")

            result = self.logs_client.get_query_results(queryId=query_id)
            status = result["status"]

            if status == "Complete":
                results = result.get("results", [])
                self.logger.debug("Query completed with %d results", len(results))
                return results
            elif status == "Failed" or status == "Cancelled":
                raise Exception(f"Query {query_id} failed with status: {status}")

            time.sleep(self.POLL_INTERVAL_SECONDS)
