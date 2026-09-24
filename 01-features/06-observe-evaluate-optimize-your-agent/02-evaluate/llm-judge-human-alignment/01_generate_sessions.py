"""Phase 1, path B: generate stored AgentCore sessions from representative scenarios.

Invokes the deployed claims assistant once per scenario in data/scenarios.json, waits
for AgentCore Observability to ingest the spans, and records what the agent actually
did: the conversation, every tool call with its input and output, and the trace ID.

If you already have instrumented production or test traffic (path A), skip this script
and write output/sessions.json from the sessions you selected instead.

Usage:
    python 01_generate_sessions.py [--span-wait 300]

Output:
    output/sessions.json   - one record per case, used by the remaining scripts
    output/raw_spans.json  - the spans collected for each session, for debugging
"""

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from bedrock_agentcore.evaluation import CloudWatchAgentSpanCollector
from common import OUTPUT_DIR, SCENARIOS_FILE, SESSIONS_FILE, load_agent_config, read_json, write_json


def decode_agent_response(response: dict) -> str:
    """Join the server-sent event chunks returned by invoke_agent_runtime."""
    raw = response["response"].read().decode("utf-8")
    parts = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        chunk: Any = line.removeprefix("data: ")
        try:
            chunk = json.loads(chunk)
        except json.JSONDecodeError:
            pass
        parts.append(str(chunk))
    if parts:
        return "".join(parts).strip()
    try:
        return str(json.loads(raw)).strip()
    except json.JSONDecodeError:
        return raw.strip()


def parse_json(value: Any) -> Any:
    """Decode JSON strings, and unwrap Strands content blocks such as [{"text": "..."}]."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict) and set(value[0]) == {"text"}:
        return parse_json(value[0]["text"])
    return value


def extract_tool_calls(spans: list[dict]) -> list[dict]:
    """Return tool calls in execution order from Strands execute_tool spans."""
    calls = []
    for span in spans:
        attributes = span.get("attributes") or {}
        if attributes.get("gen_ai.operation.name") != "execute_tool":
            continue
        status = (span.get("status") or {}).get("code", "UNSET")
        calls.append(
            {
                "name": attributes.get("gen_ai.tool.name", "unknown_tool"),
                "input": {},
                "output": {},
                "status": "error" if status == "ERROR" else "success",
                "span_id": span.get("spanId"),
                "start_time": span.get("startTimeUnixNano", 0),
            }
        )
    return sorted(calls, key=lambda call: call["start_time"])


def attach_tool_payloads(calls: list[dict], logs: list[dict]) -> None:
    """Fill tool input and output from the OTel log records linked to each tool span."""
    by_span = {call["span_id"]: call for call in calls}
    for record in logs:
        call = by_span.get(record.get("spanId"))
        body = record.get("body") or {}
        if not call or not isinstance(body, dict):
            continue
        for message in body.get("input", {}).get("messages", []):
            content = message.get("content")
            if isinstance(content, dict):
                call["input"] = parse_json(content.get("content", content))
        for message in body.get("output", {}).get("messages", []):
            content = message.get("content")
            if isinstance(content, dict):
                call["output"] = parse_json(content.get("message", content.get("content", content)))


def collect_complete_session(collector: CloudWatchAgentSpanCollector, session_id: str, start, max_wait: int) -> list:
    """Poll until the root invoke_agent span is present and the item count stops changing.

    Spans and OTel log records are ingested independently, so the first non-empty
    result from the collector can be missing tool spans or tool payloads.
    """
    deadline = time.monotonic() + max_wait
    previous = -1
    while True:
        items = collector.collect(session_id=session_id, start_time=start, end_time=datetime.now(timezone.utc))
        has_root = any(str(item.get("name", "")).startswith("invoke_agent") for item in items)
        if has_root and len(items) == previous:
            return items
        if time.monotonic() > deadline:
            print(f"  WARNING: {session_id} may be incomplete after {max_wait}s ({len(items)} items)")
            return items
        previous = len(items)
        time.sleep(15)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--span-wait", type=int, default=300, help="Seconds to wait for spans per session")
    args = parser.parse_args()

    config = load_agent_config()
    scenarios = read_json(SCENARIOS_FILE)
    runtime = boto3.client("bedrock-agentcore", region_name=config["region"])
    started_at = datetime.now(timezone.utc)

    print(f"Invoking {config['agent_id']} for {len(scenarios)} scenarios ...")
    invocations = []
    for scenario in scenarios:
        # Runtime session IDs must be at least 33 characters.
        session_id = f"{scenario['case_id'].lower()}-{uuid.uuid4()}"
        response = runtime.invoke_agent_runtime(
            agentRuntimeArn=config["agent_arn"],
            qualifier="DEFAULT",
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": scenario["prompt"], "account_context": scenario["account_context"]}).encode(
                "utf-8"
            ),
        )
        answer = decode_agent_response(response)
        invocations.append({"scenario": scenario, "session_id": session_id, "answer": answer})
        print(f"  {scenario['case_id']}: {answer[:90]!r}")

    print("\nCollecting spans from CloudWatch Logs (ingestion can take a few minutes) ...")
    collector = CloudWatchAgentSpanCollector(
        log_group_name=config["cw_log_group"],
        region=config["region"],
        max_wait_seconds=args.span_wait,
        poll_interval_seconds=15,
    )
    sessions, raw_spans = [], {}
    for item in invocations:
        scenario, session_id = item["scenario"], item["session_id"]
        items = collect_complete_session(collector, session_id, started_at - timedelta(minutes=5), args.span_wait)
        raw_spans[session_id] = items
        spans = [entry for entry in items if "name" in entry]
        logs = [entry for entry in items if "body" in entry]
        tool_calls = extract_tool_calls(spans)
        attach_tool_payloads(tool_calls, logs)
        trace_ids = sorted({span["traceId"] for span in spans})
        sessions.append(
            {
                "case_id": scenario["case_id"],
                "session_id": session_id,
                "trace_ids": trace_ids,
                "title": scenario["title"],
                "intent": scenario["intent"],
                "risk": scenario["risk"],
                "selection_reason": scenario["selection_reason"],
                "account_context": scenario["account_context"],
                "turns": [
                    {"role": "user", "content": scenario["prompt"]},
                    {"role": "assistant", "content": item["answer"]},
                ],
                "tools": [{key: call[key] for key in ("name", "input", "output", "status")} for call in tool_calls],
            }
        )
        print(f"  {scenario['case_id']}: {len(spans)} spans, tools={[call['name'] for call in tool_calls]}")

    write_json(
        SESSIONS_FILE,
        {
            "schema_version": "1.0",
            "agent_id": config["agent_id"],
            "service_name": config["otel_service_name"],
            "log_group": config["cw_log_group"],
            "generated_at": started_at.isoformat(),
            "sessions": sessions,
        },
    )
    write_json(OUTPUT_DIR / "raw_spans.json", raw_spans)


if __name__ == "__main__":
    main()
