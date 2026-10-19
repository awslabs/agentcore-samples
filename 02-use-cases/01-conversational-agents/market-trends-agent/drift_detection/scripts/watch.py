"""Show the current state of every drift detector.

One row per evaluator: which method is watching it, how far through warm-up it is,
what its baseline looks like, how close it is to alarming, and whether drift is
latched.

Pressure is the column to read. It is normalised so 0.0 is quiet and 1.0 is at the
limit, which makes methods with opposite threshold directions comparable in the
same table. A detector that is still warming up shows no pressure because it is
not entitled to an opinion yet.

Usage:
  watch.py
  watch.py --watch            refresh until interrupted
  watch.py --clear-latch mt_pii_comprehend
  watch.py --reset            delete all detector state and rebuild baselines
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent
TABLE_NAME = os.environ.get("STATE_TABLE", "market-trends-drift-detector-state")
FUNCTION_NAME = "market-trends-drift-detector"


def _store():
    sys.path.insert(0, str(AGENT_ROOT))
    from drift_detection.detector.state import StateStore

    return StateStore(TABLE_NAME, boto3.client("dynamodb", region_name=REGION))


def _bar(value: float, width: int = 18) -> str:
    """Pressure as a bar, clipped at the limit so overshoot is visible as full."""
    filled = min(width, max(0, int(round(value * width))))
    return "#" * filled + "." * (width - filled)


def render(store) -> None:
    sys.path.insert(0, str(AGENT_ROOT))
    from drift_detection.detector import config as cfg

    states = store.load_all()
    warmup = cfg.WARMUP

    print(f"\nMarket Trends Agent drift detectors    warmup={warmup}  confirm={cfg.CONSECUTIVE}")
    print(
        f"{'evaluator':<30} {'method':<8} {'shape':<16} {'samples':>9} {'baseline':>9} "
        f"{'stat':>9} {'limit':>9} {'run':>4}  {'pressure':<20} state"
    )
    print("-" * 142)

    if not states:
        print("  no detector state yet. Run the detector Lambda, or generate traffic first.")
        return

    for ev in cfg.ALL_EVALUATORS:
        st = states.get(ev.evaluator)
        if st is None:
            print(
                f"{ev.evaluator:<30} {ev.method:<8} {ev.shape:<16} {'-':>9} {'-':>9} "
                f"{'-':>9} {'-':>9} {'-':>4}  {'':<20} not deployed"
            )
            continue

        baseline = (st.detector or {}).get("baseline", {}) or {}
        mean = baseline.get("mean")
        run_length = (st.gate or {}).get("run_length", 0)

        if st.drifting:
            state = f"DRIFT since {(st.gate or {}).get('latched_at', '')[:19]}"
        elif st.warming_up:
            state = f"warming up {st.samples_seen}/{warmup}"
        else:
            state = "healthy"

        pressure = 0.0 if st.warming_up else st.last_pressure

        print(
            f"{ev.evaluator:<30} {ev.method:<8} {ev.shape:<16} "
            f"{st.samples_seen:>9} "
            f"{(f'{mean:.4f}' if isinstance(mean, (int, float)) else '-'):>9} "
            f"{(f'{st.last_statistic:.4f}' if st.last_statistic is not None else '-'):>9} "
            f"{(f'{st.last_threshold:.4f}' if st.last_threshold is not None else '-'):>9} "
            f"{run_length:>4}  {_bar(pressure):<20} {state}"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="refresh every 30s until interrupted")
    parser.add_argument("--clear-latch", metavar="EVALUATOR", help="release drift latch, keep baseline")
    parser.add_argument("--reset", action="store_true", help="delete all state and rebuild baselines")
    parser.add_argument("--run", action="store_true", help="invoke the detector now, then show state")
    args = parser.parse_args()

    store = _store()

    if args.clear_latch:
        ok = store.clear_latch(args.clear_latch)
        print(json.dumps({"clearedLatch": args.clear_latch, "found": ok}))
        return 0

    if args.reset:
        sys.path.insert(0, str(AGENT_ROOT))
        from drift_detection.detector import config as cfg

        for ev in cfg.ALL_EVALUATORS:
            store.clear(ev.evaluator)
        print(json.dumps({"reset": True, "note": "baselines will rebuild from new traffic"}))
        return 0

    if args.run:
        lam = boto3.client("lambda", region_name=REGION)
        resp = lam.invoke(FunctionName=FUNCTION_NAME, Payload=b"{}")
        payload = json.loads(resp["Payload"].read() or b"{}")
        print(f"detector run: scores_read={payload.get('scores_read')} drifting={payload.get('drifting')}")

    if not args.watch:
        render(store)
        return 0

    try:
        while True:
            render(store)
            time.sleep(30)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
