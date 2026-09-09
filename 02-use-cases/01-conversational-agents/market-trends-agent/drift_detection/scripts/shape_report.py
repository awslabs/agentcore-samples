"""Report the measured shape of every evaluator score stream.

This is the script that decides whether the detector configuration is right, and
it exists because the configuration table in config.py is a starting point rather
than a fact. Score shape is a property of a deployment: it depends on what the
evaluator measures, what traffic the agent actually sees, and how the evaluator
quantises its verdict. Guessing it wrong has a specific and repeatable
consequence, which is a detector that alarms on noise.

Read the DISAGREES column first. Anything listed there is a stream whose observed
shape does not match what config.py assumes, which means the wrong method is
watching it.

Usage:
  shape_report.py
  shape_report.py --hours 6
  shape_report.py --json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent


def resolve_config_id() -> str:
    from_env = os.environ.get("ONLINE_EVAL_CONFIG_ID", "")
    if from_env:
        return from_env
    out = AGENT_ROOT / "evaluators" / "scripts" / ".deploy_output.json"
    if out.exists():
        cid = json.loads(out.read_text()).get("onlineEvaluationConfigId", "")
        if cid:
            return cid
    raise SystemExit("Set ONLINE_EVAL_CONFIG_ID or run evaluators/scripts/deploy.py first.")


def analyse(values: List[float]) -> Dict[str, Any]:
    n = len(values)
    dist = collections.Counter(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    top_fraction = max(dist.values()) / n
    return {
        "n": n,
        "mean": mean,
        "stddev": var**0.5,
        "distinct": sorted(dist),
        "distribution": {str(k): v for k, v in sorted(dist.items())},
        "top_value": max(dist, key=lambda k: dist[k]),
        "top_fraction": top_fraction,
        "at_floor": mean == 0.0,
        "at_ceiling": mean == 1.0 and len(dist) == 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=6.0, help="how far back to read scores")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    sys.path.insert(0, str(AGENT_ROOT))
    from drift_detection.detector import config as cfg
    from drift_detection.detector import scores as score_reader

    logs = boto3.client("logs", region_name=REGION)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(args.hours * 3600 * 1000)

    all_scores = score_reader.fetch_scores(logs, resolve_config_id(), start_ms, end_ms)
    grouped = score_reader.group_by_evaluator(all_scores)

    rows: List[Dict[str, Any]] = []
    for ev in cfg.ALL_EVALUATORS:
        items = grouped.get(ev.evaluator, [])
        if not items:
            rows.append({"evaluator": ev.evaluator, "status": "no data", "configured": ev.shape})
            continue

        stats = analyse([s.value for s in items])
        value_counts = {float(k): v for k, v in stats["distribution"].items()}
        observed = cfg.classify(value_counts, stats["n"])
        recommended = cfg.SHAPE_DEFAULTS.get(observed, {})
        agrees = observed == ev.shape and recommended.get("method") == ev.method

        rows.append(
            {
                "evaluator": ev.evaluator,
                "status": "ok",
                "level": ev.level,
                "configured": ev.shape,
                "configuredMethod": ev.method,
                "observed": observed,
                "recommendedMethod": recommended.get("method"),
                "recommendedParams": recommended.get("params"),
                "agrees": agrees,
                "labels": sorted({s.label for s in items if s.label}),
                **stats,
            }
        )

    if args.json:
        print(json.dumps({"hours": args.hours, "streams": rows}, indent=2, default=str))
        return 0

    print(f"\nObserved score shape over the last {args.hours:g}h ({len(all_scores)} evaluation records)\n")
    print(
        f"{'evaluator':<30} {'n':>4} {'mean':>7} {'sd':>7} {'top':>5} {'observed':<16} "
        f"{'configured':<16} {'method':<8} {'agrees':<7} distribution"
    )
    print("-" * 150)

    disagree = []
    nodata = []
    for r in rows:
        if r["status"] != "ok":
            nodata.append(r["evaluator"])
            print(
                f"{r['evaluator']:<30} {'-':>4} {'-':>7} {'-':>7} {'-':>5} "
                f"{'no data':<16} {r['configured']:<16} {'-':<8} {'-':<7}"
            )
            continue
        if not r["agrees"]:
            disagree.append(r)
        print(
            f"{r['evaluator']:<30} {r['n']:>4} {r['mean']:>7.3f} {r['stddev']:>7.4f} "
            f"{r['top_fraction']:>5.2f} {r['observed']:<16} {r['configured']:<16} "
            f"{r['configuredMethod']:<8} {('yes' if r['agrees'] else 'NO'):<7} "
            f"{json.dumps(r['distribution'])}"
        )

    if disagree:
        print("\nDISAGREES: observed shape does not match the configuration\n")
        for r in disagree:
            print(f"  {r['evaluator']}")
            print(
                f"    observed {r['observed']} (top value {r['top_value']} is "
                f"{r['top_fraction'] * 100:.0f}% of the stream)"
            )
            print(
                f"    configured as {r['configured']} using {r['configuredMethod']}, "
                f"recommended {r['recommendedMethod']} {json.dumps(r['recommendedParams'])}"
            )
            if r["observed"] == "near_degenerate" and r["configuredMethod"] != "zscore":
                print("    a smoothing method on this stream will carry a single isolated dip")
                print("    forward for many samples and satisfy the persistence rule, which")
                print("    is a false alarm rather than drift")
            print()

    degenerate = [r for r in rows if r["status"] == "ok" and (r.get("at_floor") or r.get("at_ceiling"))]
    if degenerate:
        print("PINNED STREAMS: no headroom to detect degradation\n")
        for r in degenerate:
            where = "floor" if r["at_floor"] else "ceiling"
            print(f"  {r['evaluator']} sits at its {where} ({r['mean']:.2f}) across all {r['n']} samples.")
        print("  A stream pinned at the floor cannot fall, so nothing can be detected on it.")
        print("  A stream pinned at the ceiling with zero variance relies entirely on the")
        print("  standard deviation floor, which makes its limit an assumption rather than a")
        print("  measurement. Either way the evaluator, not the detector, is what needs a look.\n")

    if nodata:
        print(f"NO DATA: {', '.join(nodata)}")
        print("  Either not registered, or registered but not attached to the online")
        print("  evaluation config. Run scripts/attach_evaluators.py.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
