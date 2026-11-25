"""Run deterministic quality checks over captured HR Assistant sessions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils.quality_checks import quality_gate_failures, summarize_records


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--minimum-rate", type=float, default=0.95)
    parser.add_argument(
        "--scenario",
        action="append",
        help="Only evaluate records with this managed-dataset scenario_id",
    )
    args = parser.parse_args()
    if not 0 <= args.minimum_rate <= 1:
        parser.error("--minimum-rate must be between 0 and 1")
    return args


def main() -> int:
    """Evaluate selected JSONL records and print a compact summary."""
    args = parse_args()
    records = []
    for path in args.files:
        with path.open(encoding="utf-8") as source:
            records.extend(json.loads(line) for line in source if line.strip())
    if args.scenario:
        selected = set(args.scenario)
        records = [item for item in records if item.get("scenario") in selected]
    if not records:
        print("No session records matched the selected scenarios.")
        return 1

    summary = summarize_records(records)
    print(f"Records evaluated: {len(records)}")
    for name, result in summary.items():
        print(f"{name:<28} {result['passed']:>4}/{result['total']:<4} ({float(result['rate']):.1%})")
    failures = quality_gate_failures(summary, args.minimum_rate)
    if failures:
        print(f"\nQUALITY GATE FAILED: {', '.join(failures)}")
        return 1
    print("\nQUALITY GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
