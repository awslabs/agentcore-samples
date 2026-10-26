#!/usr/bin/env python3
"""Measure what `loadConcurrency` buys, using the real load loop and a simulated target latency.

This is a **simulation, not a field measurement**. It drives the actual concurrency machinery the
load stage uses (`_iter_outcomes`, the same batching and ordering), but each record's work is a
sleep standing in for the target create plus status polling. That is a fair model of the shape of the
work -- it is network wait, not compute -- and it is honest about what it is not: a real registry's
throttling, retries and variance.

    python3 tools/benchmark_load_concurrency.py
    python3 tools/benchmark_load_concurrency.py --records 200 --latency-ms 120
    python3 tools/benchmark_load_concurrency.py --concurrency 1,4,8,16,32

It also asserts what the tests assert: the output is identical at every concurrency.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "glue", "common"))

from migration_common.jobs.transform_load import RecordOutcome, _iter_outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=96, help="records to simulate (default 96)")
    parser.add_argument(
        "--latency-ms",
        type=int,
        default=50,
        help="simulated per-record target latency in milliseconds (default 50)",
    )
    parser.add_argument(
        "--concurrency",
        default="1,4,8,16,32",
        help="comma-separated loadConcurrency values to compare (default 1,4,8,16,32)",
    )
    options = parser.parse_args(argv)

    # Validated up front, because every one of these reaches real code: a negative latency becomes
    # time.sleep(-x) (ValueError), a concurrency of 0 reaches _iter_outcomes, and an empty list made
    # the loop body never run while the summary still claimed "output was identical at every
    # concurrency" -- a pass asserting nothing.
    if options.records < 1:
        parser.error("--records must be at least 1")
    if options.latency_ms < 0:
        parser.error("--latency-ms cannot be negative")
    levels: list[int] = []
    for value in str(options.concurrency).split(","):
        text = value.strip()
        if not text:
            continue
        try:
            level = int(text)
        except ValueError:
            parser.error(f"--concurrency takes comma-separated integers, got {text!r}")
        if not 1 <= level <= 32:
            parser.error(f"--concurrency values must be between 1 and 32, got {level}")
        levels.append(level)
    if len(levels) < 2:
        parser.error(
            "--concurrency needs at least two values to compare (for example 1,8); comparing one "
            "level proves nothing about whether output is stable across levels"
        )

    latency = options.latency_ms / 1000.0
    records = [(f"obj-{index // 500:05d}", {"mappingId": "bench", "index": index}) for index in range(options.records)]

    def worker(source_key: str, envelope: dict) -> RecordOutcome:
        time.sleep(latency)
        return RecordOutcome(
            mapping_id="bench",
            source_object=source_key,
            old_record_id=f"old-{envelope['index']}",
            status="SUCCEEDED",
            processed_at="",
            action="created",
            new_record_id=f"new-{envelope['index']}",
        )

    print(
        f"{options.records} records, {options.latency_ms} ms simulated per-record latency "
        f"(serial floor {options.records * latency:.1f}s)\n"
    )
    print(f"{'concurrency':>11}  {'elapsed':>9}  {'speedup':>8}  {'records/s':>9}")
    baseline_time: float | None = None
    baseline_order: list[str] | None = None
    for concurrency in levels:
        started = time.perf_counter()
        outcomes = list(_iter_outcomes(records, worker, concurrency=concurrency))
        elapsed = time.perf_counter() - started
        order = [outcome.old_record_id or "" for outcome in outcomes]
        if baseline_time is None:
            baseline_time, baseline_order = elapsed, order
        elif order != baseline_order:
            print("\nFAILED: output order changed with concurrency", file=sys.stderr)
            return 1
        # Guarded, like the speedup on the line below already was. perf_counter makes a zero
        # improbable rather than impossible, and one of the two being guarded was the tell.
        speedup = (baseline_time / elapsed) if elapsed else float("inf")
        throughput = (len(outcomes) / elapsed) if elapsed else float("inf")
        print(f"{concurrency:>11}  {elapsed:>8.2f}s  {speedup:>7.1f}x  {throughput:>9.1f}")

    print(
        f"\nOutput was identical across all {len(levels)} concurrency levels "
        f"({', '.join(str(level) for level in levels)}), {options.records} records each. Real runs "
        "will fall short of these numbers: the target control plane throttles, and retries cost wall "
        "time this model does not spend."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
