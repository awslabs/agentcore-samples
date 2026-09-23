"""Client for AgentCore Evaluation DataPlane API."""

import json
import os
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from .cloudwatch_client import ObservabilityClient
from .constants import (
    DASHBOARD_DATA_FILE,
    DASHBOARD_HTML_FILE,
    DEFAULT_FILE_ENCODING,
    DEFAULT_MAX_EVALUATION_ITEMS,
    DEFAULT_RUNTIME_SUFFIX,
    EVALUATION_OUTPUT_DIR,
    EVALUATION_OUTPUT_PATTERN,
    SESSION_SCOPED_EVALUATORS,
    SPAN_SCOPED_EVALUATORS,
)
from .models import EvaluationRequest, EvaluationResult, EvaluationResults, TraceData


class EvaluationClient:
    """Client for AgentCore Evaluation Data Plane API."""

    DEFAULT_REGION = "us-east-1"

    def __init__(self, region: Optional[str] = None, boto_client: Optional[Any] = None):
        """Initialize evaluation client.

        Args:
            region: AWS region (defaults to env var or us-east-1)
            boto_client: Optional pre-configured boto3 client for testing
        """
        self.region = region or os.getenv("AGENTCORE_EVAL_REGION", self.DEFAULT_REGION)

        if boto_client:
            self.client = boto_client
        else:
            self.client = boto3.client("agentcore-evaluation-dataplane", region_name=self.region)

    def _validate_scope_compatibility(self, evaluator_id: str, scope: str) -> None:
        """Validate that the evaluator is compatible with the requested scope.

        Args:
            evaluator_id: The evaluator identifier
            scope: The evaluation scope ("session", "trace", or "span")

        Raises:
            ValueError: If the evaluator-scope combination is invalid
        """
        if scope == "span":
            if evaluator_id not in SPAN_SCOPED_EVALUATORS:
                raise ValueError(
                    f"{evaluator_id} cannot use span scope. "
                    f"Only {SPAN_SCOPED_EVALUATORS} support span-level evaluation."
                )

        elif scope == "trace":
            if evaluator_id in SESSION_SCOPED_EVALUATORS:
                raise ValueError(f"{evaluator_id} requires session scope (cannot use trace scope)")
            if evaluator_id in SPAN_SCOPED_EVALUATORS:
                raise ValueError(f"{evaluator_id} requires span scope (cannot use trace scope)")

        elif scope == "session":
            if evaluator_id in SPAN_SCOPED_EVALUATORS:
                raise ValueError(f"{evaluator_id} requires span scope (cannot use session scope)")

        else:
            raise ValueError(f"Invalid scope: {scope}. Must be 'session', 'trace', or 'span'")

    def _build_evaluation_target(
        self,
        scope: str,
        trace_id: Optional[str] = None,
        span_ids: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build evaluationTarget based on scope.

        Args:
            scope: The evaluation scope ("session", "trace", or "span")
            trace_id: Trace ID for trace scope
            span_ids: List of span IDs for span scope

        Returns:
            evaluationTarget dict or None for session scope

        Raises:
            ValueError: If required IDs are missing for the scope
        """
        if scope == "session":
            return None

        elif scope == "trace":
            if not trace_id:
                raise ValueError("trace_id is required when scope='trace'")
            return {"traceIds": [trace_id]}

        elif scope == "span":
            if not span_ids:
                raise ValueError("span_ids are required when scope='span'")
            return {"spanIds": span_ids}

        else:
            raise ValueError(f"Invalid scope: {scope}. Must be 'session', 'trace', or 'span'")

    def _extract_raw_spans(self, trace_data: TraceData) -> List[Dict[str, Any]]:
        """Extract raw span documents from TraceData.

        Args:
            trace_data: TraceData containing spans and runtime logs

        Returns:
            List of raw span documents
        """
        raw_spans = []

        for span in trace_data.spans:
            if span.raw_message:
                raw_spans.append(span.raw_message)

        for log in trace_data.runtime_logs:
            if log.raw_message:
                raw_spans.append(log.raw_message)

        return raw_spans

    def _filter_relevant_spans(self, raw_spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter to only high-signal spans for evaluation.

        Keeps only:
        - Spans with gen_ai.* attributes (LLM calls, agent operations)
        - Log events with conversation data (input/output messages)

        Args:
            raw_spans: List of raw span/log documents

        Returns:
            Filtered list of relevant spans
        """
        relevant_spans = []
        for span_doc in raw_spans:
            attributes = span_doc.get("attributes", {})
            if any(k.startswith("gen_ai") for k in attributes.keys()):
                relevant_spans.append(span_doc)
                continue

            body = span_doc.get("body", {})
            if isinstance(body, dict) and ("input" in body or "output" in body):
                relevant_spans.append(span_doc)

        return relevant_spans

    def _get_most_recent_session_spans(
        self, trace_data: TraceData, max_items: int = DEFAULT_MAX_EVALUATION_ITEMS
    ) -> List[Dict[str, Any]]:
        """Get most recent relevant spans across all traces in session.

        Args:
            trace_data: TraceData containing all session data
            max_items: Maximum number of items to return

        Returns:
            List of raw span documents, most recent first
        """
        raw_spans = self._extract_raw_spans(trace_data)

        if not raw_spans:
            return []

        relevant_spans = self._filter_relevant_spans(raw_spans)

        def get_timestamp(span_doc):
            return span_doc.get("startTimeUnixNano") or span_doc.get("timeUnixNano") or 0

        relevant_spans.sort(key=get_timestamp, reverse=True)

        return relevant_spans[:max_items]

    def _fetch_session_data(self, session_id: str, agent_id: str, region: str) -> TraceData:
        """Fetch session data from CloudWatch.

        Args:
            session_id: Session ID to fetch
            agent_id: Agent ID for filtering
            region: AWS region

        Returns:
            TraceData with session spans and logs

        Raises:
            RuntimeError: If session data cannot be fetched
        """
        obs_client = ObservabilityClient(region_name=region, agent_id=agent_id, runtime_suffix=DEFAULT_RUNTIME_SUFFIX)

        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)
        start_time_ms = int(start_time.timestamp() * 1000)
        end_time_ms = int(end_time.timestamp() * 1000)

        try:
            trace_data = obs_client.get_session_data(
                session_id=session_id,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                include_runtime_logs=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to fetch session data: {e}") from e

        if not trace_data or not trace_data.spans:
            raise RuntimeError(f"No trace data found for session {session_id}")

        return trace_data

    def _count_span_types(self, raw_spans: List[Dict[str, Any]]) -> tuple:
        """Count spans, logs, and gen_ai spans.

        Args:
            raw_spans: List of raw span documents

        Returns:
            Tuple of (spans_count, logs_count, genai_spans_count)
        """
        spans_count = sum(1 for item in raw_spans if "spanId" in item and "startTimeUnixNano" in item)
        logs_count = sum(1 for item in raw_spans if "body" in item and "timeUnixNano" in item)
        genai_spans = sum(
            1
            for span in raw_spans
            if "spanId" in span and any(k.startswith("gen_ai") for k in span.get("attributes", {}).keys())
        )
        return spans_count, logs_count, genai_spans

    def _save_input(
        self,
        session_id: str,
        otel_spans: List[Dict[str, Any]],
    ) -> str:
        """Save input data to JSON file.

        Saves only the spans that are sent to the evaluate API.

        Args:
            session_id: Session ID
            otel_spans: Spans being sent to API

        Returns:
            Path to saved file
        """
        from .constants import EVALUATION_INPUT_DIR

        os.makedirs(EVALUATION_INPUT_DIR, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_short = session_id[:16] if len(session_id) > 16 else session_id
        filename = f"{EVALUATION_INPUT_DIR}/input_{session_short}_{timestamp}.json"

        # Save only the spans (the actual API input)
        with open(filename, "w", encoding=DEFAULT_FILE_ENCODING) as f:
            json.dump(otel_spans, f, indent=2)

        print(f"Input saved to: {filename}")
        return filename

    def _save_output(self, results: EvaluationResults) -> str:
        """Save evaluation results to JSON file.

        Args:
            results: EvaluationResults object

        Returns:
            Path to saved file
        """
        os.makedirs(EVALUATION_OUTPUT_DIR, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_short = results.session_id[:16] if len(results.session_id) > 16 else results.session_id
        filename = f"{EVALUATION_OUTPUT_DIR}/output_{session_short}_{timestamp}.json"

        with open(filename, "w", encoding=DEFAULT_FILE_ENCODING) as f:
            json.dump(results.to_dict(), f, indent=2)

        print(f"Output saved to: {filename}")
        return filename

    def _scan_evaluation_outputs(self) -> List[Path]:
        """Scan evaluation output directory for JSON files.

        Returns:
            List of Path objects for JSON files found

        Raises:
            FileNotFoundError: If output directory doesn't exist
        """
        output_dir = Path.cwd() / EVALUATION_OUTPUT_DIR

        if not output_dir.exists():
            raise FileNotFoundError(f"Directory '{EVALUATION_OUTPUT_DIR}' does not exist")

        if not output_dir.is_dir():
            raise NotADirectoryError(f"'{EVALUATION_OUTPUT_DIR}' is not a directory")

        json_files = list(output_dir.glob(EVALUATION_OUTPUT_PATTERN))

        if not json_files:
            print(f"Warning: No JSON files found in '{EVALUATION_OUTPUT_DIR}'")
            return []

        return sorted(json_files)

    def _scan_evaluation_inputs(self) -> List[Path]:
        """Scan evaluation_input directory for JSON files.

        Returns:
            List of Path objects for input JSON files found
        """
        from .constants import EVALUATION_INPUT_DIR

        input_dir = Path.cwd() / EVALUATION_INPUT_DIR

        if not input_dir.exists() or not input_dir.is_dir():
            return []

        return list(input_dir.glob("input_*.json"))

    def _extract_trace_data_from_input(self, input_file: Path) -> Optional[Dict[str, Any]]:
        """Parse input file and extract trace-level information.

        Args:
            input_file: Path to input JSON file

        Returns:
            Dictionary with trace data or None if extraction fails
        """
        try:
            with open(input_file, "r", encoding=DEFAULT_FILE_ENCODING) as f:
                spans = json.load(f)

            if not isinstance(spans, list) or not spans:
                return None

            # Extract session_id and trace_id from first span
            first_span = spans[0]
            session_id = first_span.get("attributes", {}).get("session.id")
            trace_id = first_span.get("traceId")

            if not session_id or not trace_id:
                return None

            # Extract input and output messages
            input_messages = []
            output_messages = []
            tools_used = []

            for span in spans:
                body = span.get("body", {})

                # Extract input messages
                if "input" in body and isinstance(body["input"], dict):
                    messages = body["input"].get("messages", [])
                    for msg in messages:
                        if isinstance(msg, dict):
                            input_messages.append(msg)

                # Extract output messages
                if "output" in body and isinstance(body["output"], dict):
                    messages = body["output"].get("messages", [])
                    for msg in messages:
                        if isinstance(msg, dict):
                            output_messages.append(msg)

                            # Extract tools from message content
                            content = msg.get("content", {})
                            if isinstance(content, dict):
                                message_str = content.get("message", "")
                            elif isinstance(content, str):
                                message_str = content
                            else:
                                message_str = ""

                            # Try to find toolUse in content
                            if "toolUse" in message_str:
                                try:
                                    # Content might be double-encoded JSON
                                    parsed = json.loads(message_str) if message_str.startswith("[") else None
                                    if isinstance(parsed, list):
                                        for item in parsed:
                                            if isinstance(item, dict) and "toolUse" in item:
                                                tool_name = item["toolUse"].get("name")
                                                if tool_name:
                                                    tools_used.append(tool_name)
                                except (json.JSONDecodeError, TypeError):
                                    pass

            # Get unique tools with counts
            tools_with_counts = {}
            for tool in tools_used:
                tools_with_counts[tool] = tools_with_counts.get(tool, 0) + 1

            # Get timestamps for this trace
            timestamps = [span.get("timeUnixNano") for span in spans if span.get("timeUnixNano")]
            min_timestamp = min(timestamps) if timestamps else None
            max_timestamp = max(timestamps) if timestamps else None

            # Extract token usage from spans if available
            total_input_tokens = 0
            total_output_tokens = 0

            for span in spans:
                attrs = span.get("attributes", {})
                total_input_tokens += attrs.get("gen_ai.usage.input_tokens", 0)
                total_output_tokens += attrs.get("gen_ai.usage.output_tokens", 0)

            # Calculate latency in milliseconds if timestamps available
            latency_ms = None
            if min_timestamp and max_timestamp:
                latency_ms = (max_timestamp - min_timestamp) / 1_000_000  # Convert nanoseconds to milliseconds

            return {
                "session_id": session_id,
                "trace_id": trace_id,
                "input_messages": input_messages,
                "output_messages": output_messages,
                "tools_used": tools_with_counts,
                "span_count": len(spans),
                "timestamp": min_timestamp,
                "timestamp_end": max_timestamp,
                "latency_ms": latency_ms,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            }

        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse input file {input_file.name}: {e}")
            return None
        except Exception as e:
            print(f"Warning: Error extracting trace data from {input_file.name}: {e}")
            return None

    def _match_input_output_files(
        self, output_files: List[Path], input_files: List[Path]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Match output files to their input files and extract trace data.

        Args:
            output_files: List of evaluation output file paths
            input_files: List of evaluation input file paths

        Returns:
            Dictionary mapping (session_id, trace_id) tuples to trace data
        """
        # Build a map of input data by (session_id, trace_id)
        trace_data_map = {}

        for input_file in input_files:
            trace_data = self._extract_trace_data_from_input(input_file)
            if trace_data:
                key = (trace_data["session_id"], trace_data["trace_id"])
                trace_data_map[key] = trace_data

        return trace_data_map

    def _aggregate_evaluation_data(self, json_files: List[Path]) -> List[Dict[str, Any]]:
        """Aggregate evaluation data from JSON files by session_id with trace-level detail.

        Args:
            json_files: List of JSON file paths to process

        Returns:
            List of aggregated session data dictionaries with trace-level information
        """
        sessions_map = {}
        skipped_files = []

        # Scan for input files and extract trace data
        input_files = self._scan_evaluation_inputs()
        trace_data_map = self._match_input_output_files(json_files, input_files)

        print(f"Found {len(input_files)} input file(s) with trace data")

        for json_file in json_files:
            try:
                with open(json_file, "r", encoding=DEFAULT_FILE_ENCODING) as f:
                    data = json.load(f)
                    session_id = data.get("session_id")

                    if not session_id:
                        skipped_files.append((json_file.name, "No session_id found"))
                        continue

                    if session_id not in sessions_map:
                        sessions_map[session_id] = {
                            "session_id": session_id,
                            "results": [],
                            "metadata": data.get("metadata", {}),
                            "source_files": [],
                            "evaluation_runs": 0,
                            "traces": {},  # New: map of trace_id to trace data
                        }

                    # Only increment if there are actual results
                    results = data.get("results", [])
                    if results:
                        sessions_map[session_id]["results"].extend(results)
                        sessions_map[session_id]["evaluation_runs"] += 1

                        # Group results by trace_id
                        for result in results:
                            context = result.get("context", {})
                            span_context = context.get("spanContext", {})
                            trace_id = span_context.get("traceId")

                            if trace_id:
                                # Get or create trace entry
                                if trace_id not in sessions_map[session_id]["traces"]:
                                    # Try to get trace data from input files
                                    trace_key = (session_id, trace_id)
                                    trace_data = trace_data_map.get(trace_key, {})

                                    sessions_map[session_id]["traces"][trace_id] = {
                                        "trace_id": trace_id,
                                        "session_id": session_id,
                                        "results": [],
                                        "input": trace_data.get("input_messages", []),
                                        "output": trace_data.get("output_messages", []),
                                        "tools_used": trace_data.get("tools_used", {}),
                                        "span_count": trace_data.get("span_count", 0),
                                        "timestamp": trace_data.get("timestamp"),
                                        "latency_ms": trace_data.get("latency_ms"),
                                        "input_tokens": trace_data.get("input_tokens", 0),
                                        "output_tokens": trace_data.get("output_tokens", 0),
                                        "total_tokens": trace_data.get("total_tokens", 0),
                                    }

                                # Add result to this trace
                                sessions_map[session_id]["traces"][trace_id]["results"].append(result)

                    sessions_map[session_id]["source_files"].append(json_file.name)

                    # Merge metadata (later files override earlier ones)
                    if data.get("metadata"):
                        sessions_map[session_id]["metadata"].update(data.get("metadata", {}))

            except json.JSONDecodeError as e:
                skipped_files.append((json_file.name, f"JSON decode error: {e}"))
            except PermissionError as e:
                skipped_files.append((json_file.name, f"Permission denied: {e}"))
            except Exception as e:
                skipped_files.append((json_file.name, f"Error: {e}"))

        # Convert traces dict to list for each session
        for session in sessions_map.values():
            session["traces"] = list(session["traces"].values())

        # Report skipped files
        if skipped_files:
            print(f"Warning: Skipped {len(skipped_files)} file(s):")
            for filename, reason in skipped_files:
                print(f"  - {filename}: {reason}")

        return list(sessions_map.values())

    def _write_dashboard_data(self, evaluation_data: List[Dict[str, Any]]) -> Path:
        """Write aggregated evaluation data to dashboard_data.js file.

        Args:
            evaluation_data: List of aggregated session data

        Returns:
            Path to the generated dashboard_data.js file

        Raises:
            IOError: If file write fails
        """
        js_content = f"""// Auto-generated dashboard data
// Generated from {EVALUATION_OUTPUT_DIR} directory
// Sessions aggregated by session_id

const EVALUATION_DATA = {json.dumps(evaluation_data, indent=2)};

// Export for use in dashboard
if (typeof window !== 'undefined') {{
    window.EVALUATION_DATA = EVALUATION_DATA;
}}
"""

        dashboard_data_path = Path.cwd() / DASHBOARD_DATA_FILE

        try:
            with open(dashboard_data_path, "w", encoding=DEFAULT_FILE_ENCODING) as f:
                f.write(js_content)
        except PermissionError as e:
            raise IOError(f"Permission denied writing to {DASHBOARD_DATA_FILE}: {e}") from e
        except Exception as e:
            raise IOError(f"Failed to write {DASHBOARD_DATA_FILE}: {e}") from e

        return dashboard_data_path

    def _open_dashboard_in_browser(self, dashboard_html_path: Path) -> bool:
        """Open dashboard HTML file in default browser.

        Args:
            dashboard_html_path: Path to the dashboard HTML file

        Returns:
            True if browser opened successfully, False otherwise
        """
        if not dashboard_html_path.exists():
            print(f"Warning: {DASHBOARD_HTML_FILE} not found at {dashboard_html_path}")
            return False

        try:
            # Use as_uri() for proper cross-platform file:// URL handling
            dashboard_url = dashboard_html_path.as_uri()
            success = webbrowser.open(dashboard_url)

            if success:
                print(f"Opening dashboard in browser: {dashboard_html_path.name}")
                return True
            else:
                print("Warning: Could not open browser automatically.")
                print(f"Please open manually: {dashboard_url}")
                return False

        except Exception as e:
            print(f"Warning: Failed to open browser: {e}")
            print(f"Please open {DASHBOARD_HTML_FILE} manually")
            return False

    def _create_dashboard(self) -> None:
        """Generate dashboard data and open dashboard in browser.

        This method aggregates all evaluation outputs from the evaluation_output/
        directory, generates dashboard_data.js file, and opens the dashboard HTML
        in the default browser.

        Note: This aggregates ALL evaluation output files in the directory, not just
        the current session's evaluation.

        Raises:
            FileNotFoundError: If evaluation_output directory doesn't exist
            IOError: If dashboard data file cannot be written
        """
        try:
            # Step 1: Scan for JSON files
            json_files = self._scan_evaluation_outputs()

            if not json_files:
                print("No evaluation outputs to aggregate for dashboard")
                return

            print(f"Found {len(json_files)} evaluation output file(s)")

            # Step 2: Aggregate data
            evaluation_data = self._aggregate_evaluation_data(json_files)

            if not evaluation_data:
                print("No valid evaluation data found to generate dashboard")
                return

            # Step 3: Write dashboard data file
            dashboard_data_path = self._write_dashboard_data(evaluation_data)  # noqa: F841

            total_evaluations = sum(len(session.get("results", [])) for session in evaluation_data)
            print(f"Dashboard data generated: {len(evaluation_data)} session(s), {total_evaluations} evaluation(s)")

            # Step 4: Open dashboard in browser
            dashboard_html_path = Path.cwd() / DASHBOARD_HTML_FILE
            self._open_dashboard_in_browser(dashboard_html_path)

        except FileNotFoundError as e:
            print(f"Dashboard creation failed: {e}")
            print("Make sure you have run evaluations with auto_save_output=True")
        except IOError as e:
            print(f"Dashboard creation failed: {e}")
        except Exception as e:
            print(f"Unexpected error creating dashboard: {e}")

    def evaluate(
        self,
        evaluator_id: str,
        session_spans: List[Dict[str, Any]],
        evaluation_target: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call evaluation API with transformed spans.

        Args:
            evaluator_id: Single evaluator identifier
            session_spans: List of OpenTelemetry-formatted span documents
            evaluation_target: Optional dict with spanIds or traceIds to evaluate

        Returns:
            Raw API response with evaluationResults

        Raises:
            RuntimeError: If API call fails
        """
        request = EvaluationRequest(
            evaluator_id=evaluator_id,
            session_spans=session_spans,
            evaluation_target=evaluation_target,
        )

        evaluator_id_param, request_body = request.to_api_request()

        try:
            response = self.client.evaluate(evaluatorId=evaluator_id_param, **request_body)
            return response
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            raise RuntimeError(f"Evaluation API error ({error_code}): {error_msg}") from e

    def evaluate_session(
        self,
        session_id: str,
        evaluator_ids: List[str],
        agent_id: str,
        region: str,
        scope: str,
        trace_id: Optional[str] = None,
        span_filter: Optional[Dict[str, str]] = None,
        auto_save_input: bool = False,
        auto_save_output: bool = False,
        auto_create_dashboard: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResults:
        """Evaluate a session using one or more evaluators.

        Args:
            session_id: Session ID to evaluate
            evaluator_ids: List of evaluator identifiers (e.g., ["Builtin.Helpfulness"])
            agent_id: Agent ID for fetching session data
            region: AWS region for ObservabilityClient
            scope: Evaluation scope - "session", "trace", or "span"
            trace_id: Trace ID for trace scope (optional)
            span_filter: Filter for span scope (optional dict, e.g., {"tool_name": "calculate_bmi"})
            auto_save_input: If True, saves input spans to evaluation_input/ folder
            auto_save_output: If True, saves results to evaluation_output/ folder
            auto_create_dashboard: If True, aggregates all evaluation outputs, generates
                dashboard_data.js, and opens dashboard in browser. Requires auto_save_output=True.
                Note: Aggregates ALL evaluation outputs in the directory, not just current session.
            metadata: Optional metadata dict for tracking experiments, descriptions, etc.

        Returns:
            EvaluationResults containing evaluation results

        Raises:
            RuntimeError: If session data cannot be fetched or evaluation fails
            ValueError: If scope-evaluator combination is invalid or required IDs are missing
        """
        # Validate evaluator_ids is not empty
        if not evaluator_ids:
            raise ValueError("evaluator_ids cannot be empty")

        # Validate scope for all evaluators first
        for evaluator_id in evaluator_ids:
            self._validate_scope_compatibility(evaluator_id, scope)

        trace_data = self._fetch_session_data(session_id, agent_id, region)

        num_traces = len(trace_data.get_trace_ids())
        num_spans = len(trace_data.spans)
        print(f"Found {num_spans} spans across {num_traces} traces in session")

        # Auto-discover span IDs if scope is "span"
        span_ids = None
        if scope == "span":
            tool_name_filter = (span_filter or {}).get("tool_name")
            span_ids = trace_data.get_tool_execution_spans(tool_name_filter=tool_name_filter)

            if not span_ids:
                filter_msg = f" (filter: tool_name={tool_name_filter})" if tool_name_filter else ""
                raise ValueError(f"No tool execution spans found in session{filter_msg}")

            print(f"Found {len(span_ids)} tool execution spans for evaluation")

        # Build evaluation target based on scope
        evaluation_target = self._build_evaluation_target(scope=scope, trace_id=trace_id, span_ids=span_ids)

        if evaluation_target:
            target_type = "traceIds" if "traceIds" in evaluation_target else "spanIds"
            target_ids = evaluation_target[target_type]
            print(f"Evaluation target: {target_type} = {target_ids}")

        print(f"Collecting most recent {DEFAULT_MAX_EVALUATION_ITEMS} relevant items")
        otel_spans = self._get_most_recent_session_spans(trace_data, max_items=DEFAULT_MAX_EVALUATION_ITEMS)

        if not otel_spans:
            print("Warning: No relevant items found after filtering")

        spans_count, logs_count, genai_spans = self._count_span_types(otel_spans)
        print(
            f"Sending {len(otel_spans)} items "
            f"({spans_count} spans [{genai_spans} with gen_ai attrs], "
            f"{logs_count} log events) to evaluation API"
        )

        # Save input if requested (only the spans sent to API)
        if auto_save_input:
            self._save_input(session_id, otel_spans)

        results = EvaluationResults(session_id=session_id, metadata=metadata)

        for evaluator_id in evaluator_ids:
            try:
                response = self.evaluate(
                    evaluator_id=evaluator_id,
                    session_spans=otel_spans,
                    evaluation_target=evaluation_target,
                )

                api_results = response.get("evaluationResults", [])

                if not api_results:
                    print(f"Warning: Evaluator {evaluator_id} returned no results")

                for api_result in api_results:
                    result = EvaluationResult.from_api_response(api_result)
                    results.add_result(result)

            except Exception as e:
                error_result = EvaluationResult(
                    evaluator_id=evaluator_id,
                    evaluator_name=evaluator_id,
                    evaluator_arn="",
                    explanation=f"Evaluation failed: {str(e)}",
                    context={"spanContext": {"sessionId": session_id}},
                    error=str(e),
                )
                results.add_result(error_result)

        # results.input_data = {"spans": otel_spans} # commenting out, will think later if this is meaningful to add

        # Save output if requested
        if auto_save_output:
            self._save_output(results)

        # Create dashboard if requested
        if auto_create_dashboard:
            if auto_save_output:
                self._create_dashboard()
            else:
                print("Warning: auto_create_dashboard requires auto_save_output=True")
                print("Dashboard not created. Set auto_save_output=True to enable dashboard generation.")

        return results
