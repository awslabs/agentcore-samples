#!/usr/bin/env python3
"""Analyze X-Ray traces for the Claims Agent to identify performance bottlenecks.

Usage:
    python3 scripts/analyze_traces.py --region us-east-1
    python3 scripts/analyze_traces.py --region us-east-1 --hours 4
"""

import argparse
import json
import time
from datetime import datetime

import boto3


def get_trace_summaries(region: str, hours: int):
    """Get recent trace summaries."""
    xray = boto3.client("xray", region_name=region)
    end = time.time()
    start = end - (hours * 3600)

    response = xray.get_trace_summaries(
        StartTime=start,
        EndTime=end,
    )
    return response.get("TraceSummaries", [])


def get_trace_details(region: str, trace_ids: list[str]):
    """Get full trace details with all segments/spans."""
    xray = boto3.client("xray", region_name=region)

    # X-Ray batch_get_traces accepts up to 5 at a time
    all_traces = []
    for i in range(0, len(trace_ids), 5):
        batch = trace_ids[i : i + 5]
        response = xray.batch_get_traces(TraceIds=batch)
        all_traces.extend(response.get("Traces", []))

    return all_traces


def parse_segments(trace):
    """Parse segments from a trace into a flat list with timing info."""
    spans = []
    for segment in trace.get("Segments", []):
        doc = json.loads(segment["Document"])
        spans.append(doc)
        # Also grab subsegments recursively
        _flatten_subsegments(doc, spans)
    return spans


def _flatten_subsegments(segment, spans, depth=0):
    """Recursively flatten subsegments."""
    for sub in segment.get("subsegments", []):
        sub["_depth"] = depth + 1
        sub["_parent"] = segment.get("name", "?")
        spans.append(sub)
        _flatten_subsegments(sub, spans, depth + 1)


def analyze_trace(trace, trace_id: str):
    """Analyze a single trace and print timing breakdown."""
    spans = parse_segments(trace)
    if not spans:
        print(f"  No segments found for {trace_id}")
        return

    # Sort by start time
    spans.sort(key=lambda s: s.get("start_time", 0))

    trace_start = spans[0].get("start_time", 0)
    trace_end = max(s.get("end_time", 0) for s in spans)
    total_duration = trace_end - trace_start

    print(f"\n{'─' * 90}")
    print(f"Trace: {trace_id}")
    print(f"Total Duration: {total_duration:.1f}s")
    print(f"{'─' * 90}")
    print(f"{'Span Name':<50} {'Duration':>9} {'Start':>8} {'% Total':>8}")
    print(f"{'─' * 50} {'─' * 9} {'─' * 8} {'─' * 8}")

    # Categorize time spent
    categories = {
        "LLM (Bedrock Converse)": 0.0,
        "MCP/Gateway (tool calls)": 0.0,
        "OAuth Token Fetch": 0.0,
        "Memory (retrieve/store)": 0.0,
        "Policy Engine": 0.0,
        "Other": 0.0,
    }

    for span in spans:
        name = span.get("name", "unknown")
        start = span.get("start_time", 0)
        end = span.get("end_time", 0)
        duration = end - start
        offset = start - trace_start
        depth = span.get("_depth", 0)

        if duration < 0.05:  # Skip very short spans
            continue

        pct = (duration / total_duration * 100) if total_duration > 0 else 0
        indent = "  " * depth
        display_name = f"{indent}{name}"[:50]

        print(f"{display_name:<50} {duration:>8.2f}s {offset:>7.1f}s {pct:>7.1f}%")

        # Categorize
        name_lower = name.lower()
        if "converse" in name_lower or "bedrock-runtime" in name_lower or "invoke_model" in name_lower:
            categories["LLM (Bedrock Converse)"] += duration
        elif "gateway" in name_lower or "mcp" in name_lower or "tool" in name_lower:
            categories["MCP/Gateway (tool calls)"] += duration
        elif "oauth" in name_lower or "token" in name_lower or "GetResourceOauth2Token" in name:
            categories["OAuth Token Fetch"] += duration
        elif "memory" in name_lower:
            categories["Memory (retrieve/store)"] += duration
        elif "policy" in name_lower or "cedar" in name_lower:
            categories["Policy Engine"] += duration

    # Print category summary
    print(f"\n{'─' * 50}")
    print("⏱️  Time Breakdown by Category:")
    print(f"{'─' * 50}")
    for cat, dur in sorted(categories.items(), key=lambda x: -x[1]):
        if dur > 0:
            pct = (dur / total_duration * 100) if total_duration > 0 else 0
            bar = "█" * int(pct / 2)
            print(f"  {cat:<30} {dur:>7.2f}s ({pct:>5.1f}%) {bar}")


def main():
    parser = argparse.ArgumentParser(description="Analyze Claims Agent trace performance")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--hours", type=int, default=2, help="Look back N hours")
    parser.add_argument("--trace-id", help="Analyze a specific trace ID")
    args = parser.parse_args()

    print("🔍 Claims Agent — Trace Performance Analysis")
    print(f"   Region: {args.region}")
    print(f"   Window: last {args.hours}h")

    if args.trace_id:
        trace_ids = [args.trace_id]
    else:
        print("\n📡 Fetching trace summaries...")
        summaries = get_trace_summaries(args.region, args.hours)
        print(f"   Found {len(summaries)} traces")

        if not summaries:
            print("   No traces found. Run a test first or increase --hours.")
            return

        # Print summary table
        print(f"\n{'Trace ID':<45} {'Duration':>10} {'Error':>6} {'Fault':>6}")
        print(f"{'─' * 45} {'─' * 10} {'─' * 6} {'─' * 6}")
        for t in summaries:
            print(
                f"{t['Id']:<45} {t.get('Duration', 0):>9.1f}s "
                f"{'❌' if t.get('HasError') else '✓':>6} "
                f"{'💥' if t.get('HasFault') else '✓':>6}"
            )

        trace_ids = [t["Id"] for t in summaries]

    # Get detailed traces
    print(f"\n📊 Fetching detailed spans for {len(trace_ids)} trace(s)...")
    traces = get_trace_details(args.region, trace_ids)

    for trace in traces:
        trace_id = trace["Id"]
        analyze_trace(trace, trace_id)

    # Overall summary
    if len(traces) > 1:
        durations = []
        for trace in traces:
            spans = parse_segments(trace)
            if spans:
                t_start = min(s.get("start_time", float("inf")) for s in spans)
                t_end = max(s.get("end_time", 0) for s in spans)
                durations.append(t_end - t_start)

        if durations:
            print(f"\n{'═' * 90}")
            print("📈 OVERALL STATISTICS")
            print(f"{'═' * 90}")
            print(f"  Traces analyzed: {len(durations)}")
            print(f"  Avg duration:    {sum(durations) / len(durations):.1f}s")
            print(f"  Min duration:    {min(durations):.1f}s")
            print(f"  Max duration:    {max(durations):.1f}s")
            print(f"  P50:             {sorted(durations)[len(durations) // 2]:.1f}s")


if __name__ == "__main__":
    main()
