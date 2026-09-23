"""Client for querying observability data from CloudWatch Logs."""

import logging
import time
from typing import List

import boto3

from .models import RuntimeLog, Span, TraceData


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

        if agent_id:
            parse_and_filter = f"""| parse resource.attributes.cloud.resource_id "runtime/*/" as parsedAgentId
        | filter parsedAgentId = '{agent_id}'"""
        else:
            parse_and_filter = ""

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
        {parse_and_filter}
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


class ObservabilityClient:
    """Client for querying spans and runtime logs from CloudWatch Logs."""

    SPANS_LOG_GROUP = "aws/spans"
    QUERY_TIMEOUT_SECONDS = 60
    POLL_INTERVAL_SECONDS = 2

    def __init__(
        self,
        region_name: str,
        agent_id: str,
        runtime_suffix: str = "DEFAULT",
    ):
        """Initialize the ObservabilityClient.

        Args:
            region_name: AWS region name
            agent_id: Agent ID for querying agent-specific logs
            runtime_suffix: Runtime suffix for log group (default: DEFAULT)
        """
        self.region = region_name
        self.agent_id = agent_id
        self.runtime_suffix = runtime_suffix
        self.runtime_log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id}-{runtime_suffix}"

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
        self.logger.info("Querying spans for session: %s (agent: %s)", session_id, self.agent_id)

        query_string = self.query_builder.build_spans_by_session_query(session_id, agent_id=self.agent_id)

        results = self._execute_cloudwatch_query(
            query_string=query_string,
            log_group_name=self.SPANS_LOG_GROUP,
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
                log_group_name=self.runtime_log_group,
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
