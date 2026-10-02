"""Shared types and helpers for the runtime benchmarks.

The runtime helper (``agentcore.py``) exposes this shape:

    helper.label            -> str, for display
    helper.setup(n)         -> provision n isolated compute units
    helper.units            -> list of opaque unit handles (len == n)
    helper.invoke(unit, i)  -> Result (fires one request at that unit)
    helper.teardown()       -> release provisioned resources (best-effort)

The runner drives them in batches: each batch fires one request per unit (so
the first batch is cold — first hit on each unit — and later batches are warm,
reusing the same units). See ``simple_test.py``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


# --------------------------------------------------------------------------- #
# Per-request result
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    index: int
    ok: bool
    latency_ms: float
    status: int | None = None
    cold: bool | None = None  # None = unknown (server did not report)
    unit: str | None = None   # which compute unit served it (session id)
    batch: int | None = None  # which batch it belonged to
    error: str | None = None
    # For a runtime where launching a unit is a separate API call from its
    # first request. AgentCore provisions inside the invoke, so the cost is
    # already part of latency_ms and this stays None.
    provision_ms: float | None = None


# --------------------------------------------------------------------------- #
# Ramp interface (load_ramp.py)
# --------------------------------------------------------------------------- #
# The batch runner above provisions everything up front and tears it all down at
# the end. The scale scenarios (TEST_SCENARIOS.md) need the opposite: units are
# created one at a time at a paced rate, and they ACCUMULATE — nothing is
# released until the target count is reached. Both helpers therefore also expose:
#
#     helper.acquire(index) -> Acquired   # create ONE unit, leave it running
#     helper.release(unit)   -> None      # destroy ONE unit (raises on failure)
#
# release() deliberately does not swallow errors — during a 5,000-unit teardown
# the caller needs to know what failed to shut down, because anything left
# running keeps billing and keeps holding a slot against the quota.
@dataclass
class Acquired:
    """One provisioned-and-still-running compute unit.

    ``unit`` is the opaque handle to pass back to ``release()``.
    ``result`` is the cold request that provisioned it (ok=False if it failed).
    ``provision_ms`` is for a runtime where the launch cost is a separate API
    call; AgentCore provisions inside the invoke, so it is already included in
    ``result.latency_ms`` and this stays None.
    """
    unit: Any
    result: Result
    provision_ms: float | None = None


# Substrings that mark a rate/capacity rejection rather than a real failure.
# A quota-limited leg can expect most of a ramp to be throttles, so these must
# be counted separately or the run reads as catastrophically broken when it is
# in fact just quota-bound.
THROTTLE_MARKERS = (
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "ServiceQuotaExceededException",
    "LimitExceededException",
    "Rate exceeded",
    "SlowDown",
    # AgentCore's 200/s per-agent invoke quota can come back as a bare HTTP
    # 403 with an HTML body instead of a typed exception (observed on both
    # invoke and StopRuntimeSession once a leg is over quota) -- botocore
    # can't parse a non-JSON error body into Code/Message, so the raw HTML
    # lands in the exception's own text. "<html" is distinctive enough that a
    # genuine application error should never contain it.
    "<html",
)

# Substrings that mark the ceiling itself: capacity is exhausted, so retrying at
# a lower rate will not help. Distinguished from a throttle because a throttle
# means "slow down" while these mean "stop".
CAPACITY_MARKERS = (
    "ServiceQuotaExceededException",
    "LimitExceededException",
    "InsufficientCapacity",
    "capacity",
)


def classify_error(error: str | None) -> str:
    """Bucket a Result.error into 'none' | 'throttle' | 'capacity' | 'failure'.

    'capacity' is checked first: ServiceQuotaExceededException appears in both
    marker lists, and the ceiling reading is the more informative one.
    """
    if not error:
        return "none"
    if any(m in error for m in CAPACITY_MARKERS):
        return "capacity"
    if any(m in error for m in THROTTLE_MARKERS):
        return "throttle"
    return "failure"


# --------------------------------------------------------------------------- #
# Cold-start detection
# --------------------------------------------------------------------------- #
COLD_KEYS = ("cold_start", "coldStart", "is_cold_start", "isColdStart")


def find_cold(obj: Any, extra_key: str | None = None) -> bool | None:
    """Recursively search a decoded JSON structure for a cold-start flag."""
    keys = COLD_KEYS + ((extra_key,) if extra_key else ())
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and isinstance(obj[k], bool):
                return obj[k]
        for v in obj.values():
            found = find_cold(v, extra_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_cold(v, extra_key)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #
def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {}
    return {
        "count": len(vals),
        "min_ms": round(min(vals), 2),
        "mean_ms": round(statistics.fmean(vals), 2),
        "p50_ms": round(_pct(vals, 50), 2),
        "p75_ms": round(_pct(vals, 75), 2),
        "p90_ms": round(_pct(vals, 90), 2),
        "p99_ms": round(_pct(vals, 99), 2),
        "max_ms": round(max(vals), 2),
    }


def summarize(results: list[Result], wall: float, units: int,
              batches: int, wait: float,
              provision_ms: list[float] | None = None) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    cold = [r for r in ok if r.cold is True]
    warm = [r for r in ok if r.cold is False]
    unknown = [r for r in ok if r.cold is None]
    out = {
        "units": units,
        "batches": batches,
        "wait_s": wait,
        "total": len(results),
        "success": len(ok),
        "errors": len(results) - len(ok),
        "wall_seconds": round(wall, 3),
        "throughput_rps": round(len(ok) / wall, 2) if wall > 0 else 0.0,
        "latency": _stats([r.latency_ms for r in ok]),
        "cold_start": {
            "cold": len(cold),
            "warm": len(warm),
            "unknown": len(unknown),
            "cold_latency": _stats([r.latency_ms for r in cold]),
            "warm_latency": _stats([r.latency_ms for r in warm]),
        },
    }
    # Time to launch each unit, for a runtime where that is a separate call.
    # On AgentCore this cost is inside the first request's cold latency, so
    # there is no separate value there.
    if provision_ms:
        out["provisioning"] = _stats(provision_ms)
    return out


def print_summary(name: str, s: dict[str, Any]) -> None:
    print(f"\n=== {name} ===")
    if s.get("mode"):
        print(f"  mode:        {s['mode']}")
    print(f"  units:       {s['units']}  batches: {s['batches']}  "
          f"wait: {s['wait_s']}s")
    print(f"  requests:    {s['success']}/{s['total']} ok ({s['errors']} errors)")
    print(f"  wall time:   {s['wall_seconds']}s")
    print(f"  throughput:  {s['throughput_rps']} req/s")
    lat = s["latency"]
    if lat:
        print(f"  latency ms:  p50={lat['p50_ms']}  p75={lat['p75_ms']}  "
              f"p90={lat['p90_ms']}  p99={lat['p99_ms']}  min={lat['min_ms']}  "
              f"max={lat['max_ms']}")
    cs = s["cold_start"]
    print(f"  cold starts: cold={cs['cold']}  warm={cs['warm']}  "
          f"unknown={cs['unknown']}")
    if cs["cold_latency"]:
        cl = cs["cold_latency"]
        print(f"    cold ms:   p50={cl['p50_ms']}  mean={cl['mean_ms']}  "
              f"min={cl['min_ms']}  max={cl['max_ms']}")
    if cs["warm_latency"]:
        wl = cs["warm_latency"]
        print(f"    warm ms:   p50={wl['p50_ms']}  mean={wl['mean_ms']}  "
              f"min={wl['min_ms']}  max={wl['max_ms']}")
    pv = s.get("provisioning")
    if pv:
        print("  provisioning (separate unit-launch cost, per unit):")
        print(f"    ms:        p50={pv['p50_ms']}  mean={pv['mean_ms']}  "
              f"min={pv['min_ms']}  max={pv['max_ms']}")
    c = s.get("cost")
    if c:
        sz = c["size"]
        print(f"  cost (est.): ${c['total_usd']:.6f} total  "
              f"(${c['usd_per_1k_requests']:.6f} / 1k req)")
        print(f"    model:     {c['model']}")
        print(f"    basis:     {sz['vcpus']} vCPU / {sz['gb']} GB x {c['units']} "
              f"unit(s) x {c['active_seconds']}s active")
