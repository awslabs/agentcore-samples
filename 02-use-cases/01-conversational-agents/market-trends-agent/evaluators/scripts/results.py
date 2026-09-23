"""Pull recent evaluation results for the online eval config.

Reads the `.deploy_output.json` written by deploy.py to learn the
online eval config ID, then tails the corresponding CloudWatch log
group for recent evaluation events. Results are printed grouped by
evaluator.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
DEFAULT_WINDOW_MIN = 60


def _load_log_group() -> str:
    out = Path(__file__).resolve().parent / ".deploy_output.json"
    if out.exists():
        data = json.loads(out.read_text())
        lg = data.get("onlineResultsLogGroup")
        if lg:
            return lg
    raise SystemExit("Cannot find .deploy_output.json — run deploy.py first or pass --log-group.")


def _fetch_events(log_group: str, minutes: int) -> List[Dict]:
    logs = boto3.client("logs", region_name=REGION)
    start_ms = int((time.time() - minutes * 60) * 1000)

    paginator = logs.get_paginator("filter_log_events")
    events: List[Dict] = []
    try:
        for page in paginator.paginate(logGroupName=log_group, startTime=start_ms, limit=1000):
            for ev in page.get("events", []):
                try:
                    events.append(json.loads(ev["message"]))
                except json.JSONDecodeError:
                    events.append({"_raw": ev["message"]})
    except logs.exceptions.ResourceNotFoundException:
        print(f"(log group {log_group} does not exist yet — wait ~5 min after traffic)")
        return []
    return events


def _summarise(events: List[Dict]) -> Dict[str, List[Dict]]:
    by_eval: Dict[str, List[Dict]] = defaultdict(list)
    for ev in events:
        attrs = (ev or {}).get("attributes") or {}
        name = attrs.get("gen_ai.evaluation.name") or ev.get("evaluatorName") or "unknown"
        by_eval[name].append(ev)
    return by_eval


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--log-group", help="Override results log group")
    p.add_argument("--minutes", type=int, default=DEFAULT_WINDOW_MIN, help="Lookback window")
    p.add_argument("--raw", action="store_true", help="Print raw events instead of summary")
    args = p.parse_args()

    log_group = args.log_group or _load_log_group()
    events = _fetch_events(log_group, args.minutes)
    print(f"fetched {len(events)} events from {log_group} (last {args.minutes}m)")

    if args.raw:
        for ev in events:
            print(json.dumps(ev, indent=2))
        return 0

    grouped = _summarise(events)
    for name in sorted(grouped):
        rows = grouped[name]
        print(f"\n=== {name} — {len(rows)} result(s) ===")
        for row in rows[-10:]:
            attrs = (row or {}).get("attributes") or {}
            label = attrs.get("gen_ai.evaluation.score.label") or row.get("label")
            value = attrs.get("gen_ai.evaluation.score.value")
            if value is None:
                value = row.get("value")
            explanation = attrs.get("gen_ai.evaluation.explanation") or row.get("explanation") or ""
            session_id = attrs.get("session.id") or "?"
            trace_id = row.get("traceId") or "?"
            print(
                f"  session={session_id[:40]} trace={trace_id[:16]} "
                f"label={label} value={value}  {str(explanation)[:140]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
