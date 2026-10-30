#!/usr/bin/env python3
"""analyze_results.py

Parse one or more results-scenario*.json files from load_ramp.py and print a
plain-text summary table for each: Test A (scenario 1, account ceiling / cold
start) or Test B (scenario 2, warm-throughput staircase). Detected from the
JSON's own scenario.target_units, not the filename, so renamed files still
parse correctly.

Nothing here reads from AWS; it only reads the JSON files load_ramp.py wrote.

Usage:
    python3 analyze_results.py results-scenario1-agentcore.json
    python3 analyze_results.py results-scenario*-*.json
    python3 analyze_results.py --json results-scenario2-agentcore.json > out.json

Methodology notes (why this isn't a one-liner):
  - Test A stats (avg/min/max/p50/p75/p90/p95) are computed here from the raw
    per-request records, filtered to ok=true, matching the convention used
    throughout this benchmark series. The ramp phase itself carries no
    percentile summary.
  - Test B's per-step mean/min/max/p50/p75/p90/p99 come straight from the
    harness's own precomputed `throughput.steps[].latency` (computed by
    load_ramp.py over that step's successful requests only, via
    common.py::_stats) — authoritative, not re-derived. One percentile set is
    computed once, in the harness, and read the same way everywhere (live
    progress output, the end-of-run table, and here).
  - The AWS account ID, when a request failed against a quota, is pulled
    directly out of that error message's own text ("...for account
    123456789012...") rather than assumed from configuration, since legs in
    this series have previously landed on different accounts than expected.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    n = len(vals)
    k = (n - 1) * p
    f = int(k)
    c = min(f + 1, n - 1)
    if f == c:
        return vals[f]
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def accounts_in_errors(records: list[dict]) -> set[str]:
    found = set()
    for r in records:
        err = r.get("error")
        if err:
            m = re.search(r"account (\d+)", err)
            if m:
                found.add(m.group(1))
    return found


def error_message_counts(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        if not r.get("ok") and r.get("error"):
            key = r["error"].splitlines()[0][:100]
            # Some quota rejections come back as an HTML error page instead of
            # a typed exception (see common.py's THROTTLE_MARKERS) -- cut the
            # markup so the count key stays a readable one-liner instead of a
            # <head><title>...</title></head> fragment.
            html_at = key.find("<html")
            if html_at != -1:
                key = key[:html_at].rstrip() + " <html error body>"
            counts[key] = counts.get(key, 0) + 1
    return counts


def analyze_test_a(data: dict) -> dict:
    ramp = data["phases"][0]
    results = data["results"]
    oks = [r["latency_ms"] for r in results if r.get("ok")]
    errors = ramp.get("capacity", 0) + ramp.get("failed", 0) + ramp.get("throttled", 0)
    out = {
        "kind": "test_a",
        "target": ramp.get("target"),
        "built": ramp.get("accepted"),
        "errors": errors,
        "stop_reason": ramp.get("stop_reason"),
        "stop_detail": ramp.get("stop_detail"),
        "accounts_in_errors": sorted(accounts_in_errors(results)),
        "error_messages": error_message_counts(results),
    }
    if oks:
        out.update(
            avg=round(statistics.mean(oks), 1),
            min=round(min(oks), 1),
            max=round(max(oks), 1),
            p50=round(pct(oks, 0.50), 1),
            p75=round(pct(oks, 0.75), 1),
            p90=round(pct(oks, 0.90), 1),
            p95=round(pct(oks, 0.95), 1),
        )
    return out


def analyze_test_b(data: dict) -> dict:
    ramp = data["phases"][0]
    throughput = next(p for p in data["phases"] if p["phase"] == "throughput")
    teardown = next((p for p in data["phases"] if p["phase"] == "teardown"), None)
    results = data["results"]

    steps_out = []
    for s in throughput["steps"]:
        lat = s.get("latency") or {}
        steps_out.append(
            {
                "offered_rps": s["offered_rps"],
                "sent": s["sent"],
                "ok": s["ok"],
                "failed": sum(s["errors"].values()),
                "mean": lat.get("mean_ms"),
                "min": lat.get("min_ms"),
                "max": lat.get("max_ms"),
                "p50": lat.get("p50_ms"),
                "p75": lat.get("p75_ms"),
                "p90": lat.get("p90_ms"),
                "p99": lat.get("p99_ms"),
            }
        )

    return {
        "kind": "test_b",
        "target": data["scenario"]["target_units"],
        "built": ramp.get("accepted"),
        "stop_reason": throughput.get("stop_reason"),
        "stop_detail": throughput.get("stop_detail"),
        "steps": steps_out,
        "teardown": (
            {
                "units": teardown.get("units"),
                "released": teardown.get("released"),
                "reaped": teardown.get("reaped", 0),
                "errors": teardown.get("errors"),
            }
            if teardown
            else None
        ),
        "accounts_in_errors": sorted(accounts_in_errors(results)),
        "error_messages": error_message_counts(results),
    }


def analyze(path: Path) -> dict:
    data = json.loads(path.read_text())
    target_units = data.get("scenario", {}).get("target_units", 0)
    # Test A's ramp phase has no `steps`-carrying throughput phase; Test B's
    # does. target_units alone (5000 vs everything else) is what this
    # benchmark's two remaining scenarios actually differ on.
    has_throughput_steps = any(
        p.get("phase") == "throughput" and p.get("steps") for p in data.get("phases", [])
    )
    result = analyze_test_b(data) if has_throughput_steps else analyze_test_a(data)
    result["file"] = str(path)
    result["runtime"] = data.get("runtime")
    result["target_label"] = data.get("target")
    result["target_units"] = target_units
    return result


def print_test_a(r: dict) -> None:
    print(f"== {r['file']} ({r['runtime']}) == Test A: account ceiling")
    print(f"  target: {r['target']}  built: {r['built']}  errors: {r['errors']}")
    print(f"  stop: {r['stop_reason']} | {r['stop_detail']}")
    if "avg" in r:
        print(
            f"  avg={r['avg']} min={r['min']} max={r['max']} "
            f"p50={r['p50']} p75={r['p75']} p90={r['p90']} p95={r['p95']} (ms)"
        )
    if r["accounts_in_errors"]:
        print(f"  accounts in errors: {', '.join(r['accounts_in_errors'])}")
    for msg, count in sorted(r["error_messages"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6}x  {msg}")


def print_test_b(r: dict) -> None:
    print(f"== {r['file']} ({r['runtime']}) == Test B: warm until break")
    print(f"  target: {r['target']}  built: {r['built']}")
    print(f"  stop: {r['stop_reason']} | {r['stop_detail']}")
    header = f"  {'offered':>8} {'sent':>7} {'failed':>7} {'mean':>8} {'min':>8} {'max':>10} {'p50':>8} {'p75':>8} {'p90':>8} {'p99':>8}"
    print(header)
    for s in r["steps"]:
        def f(x):
            return f"{x:.1f}" if isinstance(x, (int, float)) else "-"
        print(
            f"  {s['offered_rps']:>8} {s['sent']:>7} {s['failed']:>7} "
            f"{f(s['mean']):>8} {f(s['min']):>8} {f(s['max']):>10} "
            f"{f(s['p50']):>8} {f(s['p75']):>8} {f(s['p90']):>8} {f(s['p99']):>8}"
        )
    if r["teardown"]:
        t = r["teardown"]
        reaped_note = f", {t['reaped']} already reaped" if t.get("reaped") else ""
        print(f"  teardown: {t['released']} of {t['units']} released"
              f"{reaped_note}, {t['errors']} real errors")
    if r["accounts_in_errors"]:
        print(f"  accounts in errors: {', '.join(r['accounts_in_errors'])}")
    for msg, count in sorted(r["error_messages"].items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6}x  {msg}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="+", help="results-scenario*.json file(s)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON instead of tables")
    args = p.parse_args(argv)

    all_results = []
    for f in args.files:
        path = Path(f)
        if not path.exists():
            print(f"skip (not found): {path}", file=sys.stderr)
            continue
        r = analyze(path)
        all_results.append(r)
        if not args.json:
            (print_test_a if r["kind"] == "test_a" else print_test_b)(r)
            print()

    if args.json:
        print(json.dumps(all_results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
