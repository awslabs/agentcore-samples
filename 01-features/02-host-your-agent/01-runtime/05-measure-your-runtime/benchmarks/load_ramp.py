#!/usr/bin/env python3
"""Open-loop scale ramp: grow a fleet of cold compute units to a target count.

This is the runner for the scale scenarios in TEST_SCENARIOS.md. It is a
different experiment from simple_test.py, not a variation of it:

  simple_test.py  CLOSED loop. A fixed number of units, requests paced by how
                  fast the units answer. Measures per-request latency.
  load_ramp.py    OPEN loop. Units arrive on a wall-clock schedule regardless of
                  how fast (or whether) earlier ones succeeded, and they
                  ACCUMULATE — nothing is released until the target is reached.
                  Measures how fast a fleet can be built and held.

Four phases, each timed and reported separately:

  1. RAMP      offer `--rate` new units every `--per` seconds until `--target`
               units have been ACCEPTED (option A, see below).
  2. HOLD      keep the full fleet alive for `--hold` seconds, optionally
               probing a sample of it to measure warm latency at full scale.
  3. TEARDOWN  release every live unit, rate-limited to `--teardown-rps`.
  4. (report)  per-second time series + per-phase summaries as JSON.

Option A — what "reaching the ceiling" means under throttling. The offered rate
is held at nominal even while requests are being rejected; the ramp ends when
`--target` units are *accepted*, so a throttled runtime simply takes longer and
the measurement becomes the ACHIEVED rate. A leg that cannot reach the target at
all is stopped by a guard and reports where it stalled. That stall point is the
result, not an error.

Stopping a leg that will never get there (see "Guards" below). Option A means the
offered load does NOT back off, so a leg pinned at its ceiling would keep firing
attempts until `--max-seconds`. Observed in practice: thousands offered for a
few hundred accepted, with a long window where further attempts produced ZERO
new accepted units — the fleet was pinned while the ramp kept paying for
rejections. The informative signal there is not the volume of attempts, and not
the throttle percentage (that can be ~80% from the very first seconds, before
any achieved rate has been established); it is that ACCEPTED STOPPED MOVING. So
`--stall-seconds` is the primary guard and the other two are backstops.

Throttles are counted apart from failures. This matters when a leg is
quota-limited well below the offered rate, so most of the ramp is expected to be
throttled. Counted as failures it would read as a broken run; counted apart it
reads as what it is — the quota under test.

Examples (see TEST_SCENARIOS.md for the full scenarios):

  # Scenario 1: +25 units/s until 5,000 are live, then tear down.
  ./load_ramp.py agentcore-zip --rate 25 --per 1 --target 5000

  # Scenario 2: +400 units/min until 5,000, hold the fleet for 60s.
  ./load_ramp.py agentcore-zip --rate 400 --per 60 --target 5000 --hold 60
"""

# Guards, in the order they are checked (see ramp()):
#
#   --stall-seconds   PRIMARY. Stop when `accepted` has not increased for this
#                     long. Directly measures "this leg is done growing",
#                     whatever the reason (quota, capacity, throttle).
#   --max-offered     Backstop on total attempts, for the case where accepted
#                     creeps up just fast enough to keep resetting the stall
#                     timer while never approaching the target.
#   --max-throttle-pct  Backstop on the reject ratio, evaluated only after
#                     --throttle-pct-after attempts so the noisy early ramp
#                     cannot trip it.
#   --max-units       A known hard ceiling (e.g. an account concurrency quota).
#   --capacity-stop   A run of capacity errors within a sliding window.
#   --max-seconds     Runaway wall-clock guard, the last resort.

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from common import Result, _stats, classify_error


# --------------------------------------------------------------------------- #
# Per-second time series
# --------------------------------------------------------------------------- #
@dataclass
class Tick:
    """One second of the run. `active` is sampled at the end of the second."""
    t: int                      # seconds since the run started
    phase: str
    offered: int = 0            # acquisitions dispatched
    accepted: int = 0           # units that came up and answered
    throttled: int = 0          # rate-limited (retryable)
    capacity: int = 0           # quota/capacity exhausted (not retryable)
    failed: int = 0             # genuine errors
    active: int = 0
    latencies: list[float] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "latencies"}
        # Per-second percentiles are what show degradation as the fleet grows;
        # a run-wide average hides it.
        d["latency"] = _stats(self.latencies)
        return d


class Recorder:
    """Thread-safe collector: per-second ticks + every raw Result."""

    def __init__(self, t0: float, capacity_window: int = 200):
        self.t0 = t0
        self.lock = threading.Lock()
        self.ticks: dict[int, Tick] = {}
        self.results: list[Result] = []
        self.phase = "ramp"
        self.counts = {"offered": 0, "accepted": 0, "throttled": 0,
                       "capacity": 0, "failed": 0}
        # Capacity errors among the last `capacity_window` attempts. A plain
        # CONSECUTIVE counter never fires under heavy throttling: any throttle
        # resets it, and a leg at its ceiling interleaves thousands of throttles
        # among the capacity errors (observed: 1,396 capacity errors spread
        # through 6,423 throttles never produced a run of 25).
        self.recent: deque[str] = deque(maxlen=max(1, capacity_window))
        # When `accepted` last increased — the input to the stall guard. Seeded
        # with t0 so a leg that never accepts anything still stalls out.
        self.last_accept = t0

    def _tick(self, phase: str | None = None) -> Tick:
        """Caller must hold the lock."""
        t = int(time.perf_counter() - self.t0)
        tick = self.ticks.get(t)
        if tick is None:
            tick = Tick(t=t, phase=phase or self.phase)
            self.ticks[t] = tick
        return tick

    def offered(self) -> None:
        with self.lock:
            self._tick().offered += 1
            self.counts["offered"] += 1

    def record(self, res: Result, active: int) -> str:
        """Classify one acquisition attempt and fold it into the series."""
        kind = classify_error(res.error) if not res.ok else "none"
        with self.lock:
            tick = self._tick()
            if res.ok:
                tick.accepted += 1
                self.counts["accepted"] += 1
                tick.latencies.append(res.latency_ms)
                # The fleet grew: the leg is still making progress.
                self.last_accept = time.perf_counter()
            elif kind == "throttle":
                tick.throttled += 1
                self.counts["throttled"] += 1
            elif kind == "capacity":
                tick.capacity += 1
                self.counts["capacity"] += 1
            else:
                tick.failed += 1
                self.counts["failed"] += 1
            # A DENSITY of capacity errors means the ceiling; one in isolation
            # can be a transient placement failure.
            self.recent.append(kind)
            tick.active = active
            self.results.append(res)
        return kind

    def capacity_in_window(self) -> tuple[int, int]:
        """(capacity errors, attempts) over the recent-attempt window."""
        with self.lock:
            return sum(1 for k in self.recent if k == "capacity"), len(self.recent)

    def add_result(self, res: Result) -> None:
        """Record a probe/teardown result without touching the ramp counters."""
        with self.lock:
            self.results.append(res)
            if res.ok:
                self._tick().latencies.append(res.latency_ms)

    def set_phase(self, phase: str) -> None:
        with self.lock:
            self.phase = phase

    def series(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.ticks[t].to_json() for t in sorted(self.ticks)]


# --------------------------------------------------------------------------- #
# Live fleet
# --------------------------------------------------------------------------- #
class Fleet:
    """The set of units that are up and still running."""

    def __init__(self):
        self.lock = threading.Lock()
        self.units: list[Any] = []

    def add(self, unit: Any) -> int:
        with self.lock:
            self.units.append(unit)
            return len(self.units)

    def size(self) -> int:
        with self.lock:
            return len(self.units)

    def sample(self, k: int, rng: random.Random) -> list[Any]:
        with self.lock:
            if k >= len(self.units):
                return list(self.units)
            return rng.sample(self.units, k)

    def snapshot(self) -> list[Any]:
        """A copy of the live units, WITHOUT giving up ownership.

        The throughput phase reuses the whole fleet and must not drain it —
        teardown still has to release every unit afterwards.
        """
        with self.lock:
            return list(self.units)

    def drain(self) -> list[Any]:
        """Take everything, leaving the fleet empty (teardown owns them now)."""
        with self.lock:
            units, self.units = self.units, []
            return units


# --------------------------------------------------------------------------- #
# Phase 1: ramp
# --------------------------------------------------------------------------- #
def ramp(helper, fleet: Fleet, rec: Recorder, args) -> dict[str, Any]:
    """Dispatch acquisitions on a wall-clock schedule until the target is met.

    Arrivals are paced against ABSOLUTE deadlines derived from the start time,
    so a slow dispatch does not push later arrivals late: the schedule is the
    experiment, and drift would quietly turn the offered rate into something
    lower than what is being reported.
    """
    interval = args.per / args.rate      # seconds between two arrivals
    stop: dict[str, Any] = {"reason": None, "detail": None}
    dispatched = 0

    def task(index: int) -> None:
        acq = helper.acquire(index)
        # Track ANY unit the helper handed back, even one whose request failed:
        # a unit that came up is billing and holding a quota slot whether or not
        # it answered, so teardown must own it. Whether it counts as *accepted*
        # is a separate question, decided by result.ok in rec.record().
        active = fleet.add(acq.unit) if acq.unit is not None else fleet.size()
        if acq.provision_ms is not None:
            # For a runtime where launching a unit is a separate call from its
            # first request, that cost is reported apart here. AgentCore has it
            # inside result.latency_ms, so this stays unset on that leg.
            acq.result.provision_ms = acq.provision_ms
        rec.record(acq.result, active)

    t0 = time.perf_counter()
    # Workers only need to cover units in flight (an accepted unit goes idle
    # once its cold request returns), not the whole fleet.
    workers = args.workers or max(64, int(args.rate * 8 / max(args.per, 1)) + 64)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            now_pc = time.perf_counter()
            with rec.lock:
                accepted = rec.counts["accepted"]
                offered = rec.counts["offered"]
                rejected = sum(rec.counts[k] for k in
                               ("throttled", "capacity", "failed"))
                stalled_for = now_pc - rec.last_accept
            elapsed = now_pc - t0

            if accepted >= args.target:
                stop["reason"] = "target-reached"
                break
            # PRIMARY guard: the fleet has stopped growing. Whatever the cause,
            # further attempts buy nothing but rejections. Only armed once the
            # ramp has had time to place its first units, so a leg with a slow
            # cold start is not killed before it accepts anything.
            if (args.stall_seconds and stalled_for >= args.stall_seconds
                    and elapsed >= args.stall_seconds):
                stop["reason"] = "stalled"
                stop["detail"] = (f"no new accepted unit for "
                                  f"{stalled_for:.0f}s (accepted={accepted})")
                break
            if args.max_offered and offered >= args.max_offered:
                stop["reason"] = "max-offered"
                stop["detail"] = (f"{offered} attempts offered for "
                                  f"{accepted} accepted")
                break
            # Reject-ratio backstop. Deliberately NOT evaluated early: on a
            # quota-limited leg the ratio is already ~80% in the first seconds,
            # so an early check would stop the run before it has measured any
            # achieved rate at all.
            if args.max_throttle_pct and offered >= args.throttle_pct_after:
                pct = 100.0 * rejected / offered
                if pct >= args.max_throttle_pct:
                    stop["reason"] = "throttle-ratio"
                    stop["detail"] = (f"{pct:.1f}% of {offered} attempts "
                                      f"rejected (limit {args.max_throttle_pct}%)")
                    break
            if args.max_units and fleet.size() >= args.max_units:
                # A known hard ceiling (e.g. an account concurrency quota)
                # reached before the target. Expected, not an error.
                stop["reason"] = "max-units"
                break
            if args.capacity_stop:
                cap_recent, window = rec.capacity_in_window()
                if window >= args.capacity_stop and cap_recent >= args.capacity_stop:
                    stop["reason"] = "capacity-ceiling"
                    stop["detail"] = (f"{cap_recent} capacity errors in the "
                                      f"last {window} attempts")
                    break
            if elapsed >= args.max_seconds:
                stop["reason"] = "max-seconds"
                break

            # Absolute deadline for arrival number `dispatched`.
            due = t0 + dispatched * interval
            now = time.perf_counter()
            if now < due:
                time.sleep(min(due - now, 0.25))   # wake to re-check the guards
                continue

            pool.submit(task, dispatched)
            rec.offered()
            dispatched += 1

            if args.progress and dispatched % args.progress == 0:
                with rec.lock:
                    c = dict(rec.counts)
                print(f"\r  [ramp {elapsed:6.1f}s] offered={c['offered']} "
                      f"accepted={c['accepted']} active={fleet.size()} "
                      f"throttled={c['throttled']} capacity={c['capacity']} "
                      f"failed={c['failed']}", end="", flush=True)
        # Leaving the `with` block waits for in-flight acquisitions: units
        # already dispatched must be counted (and tracked, so they get torn
        # down) even though the stop condition has fired.
        with rec.lock:
            settled = sum(rec.counts[k] for k in
                          ("accepted", "throttled", "capacity", "failed"))
        why = stop["reason"] + (f" ({stop['detail']})" if stop["detail"] else "")
        print(f"\n  [ramp] {why}, draining {dispatched - settled} "
              f"in-flight acquisition(s)...")
    wall = time.perf_counter() - t0

    with rec.lock:
        counts = dict(rec.counts)
    return {
        "phase": "ramp",
        "stop_reason": stop["reason"],
        "stop_detail": stop["detail"],
        "offered_rate_per_s": round(args.rate / args.per, 3),
        "nominal_seconds": round(args.target * interval, 1),
        "wall_seconds": round(wall, 2),
        "achieved_rate_per_s": round(counts["accepted"] / wall, 3) if wall else 0.0,
        "dispatched": dispatched,
        **counts,
        "active_at_end": fleet.size(),
        "target": args.target,
        "target_reached": counts["accepted"] >= args.target,
    }


# --------------------------------------------------------------------------- #
# Phase 2: hold
# --------------------------------------------------------------------------- #
def hold(helper, fleet: Fleet, rec: Recorder, args) -> dict[str, Any]:
    """Keep the fleet alive for `--hold` seconds, sample-probing it.

    Probes are SAMPLED, not a full sweep: one request per unit would be 5,000
    invokes, and at the 200/s invoke quota a single sweep costs ~25s of the
    60s hold. `--hold-probe-rps` keeps that to a small slice of the quota while
    still measuring warm latency with the whole fleet resident.

    Probing is also the only evidence the fleet is genuinely still alive rather
    than merely un-torn-down, so 0 makes the hold unverified — see
    TEST_SCENARIOS.md.
    """
    rec.set_phase("hold")
    rng = random.Random(args.seed)
    probes = 0
    t0 = time.perf_counter()

    if args.hold_probe_rps <= 0:
        print(f"  [hold] {args.hold}s, no probes (unverified hold)")
        time.sleep(args.hold)
        return {"phase": "hold", "seconds": args.hold, "probes": 0,
                "verified": False, "active_at_end": fleet.size()}

    interval = 1.0 / args.hold_probe_rps
    with ThreadPoolExecutor(max_workers=max(16, args.hold_probe_rps * 2)) as pool:
        def probe(unit: Any, index: int) -> None:
            rec.add_result(helper.invoke(unit, index))

        while True:
            elapsed = time.perf_counter() - t0
            if elapsed >= args.hold:
                break
            due = t0 + probes * interval
            now = time.perf_counter()
            if now < due:
                time.sleep(min(due - now, 0.25))
                continue
            picked = fleet.sample(1, rng)
            if picked:
                pool.submit(probe, picked[0], probes)
                probes += 1
            else:
                time.sleep(interval)
            if args.progress and probes % max(args.progress // 10, 1) == 0:
                print(f"\r  [hold {elapsed:6.1f}s] probes={probes} "
                      f"active={fleet.size()}", end="", flush=True)
    print()
    return {
        "phase": "hold",
        "seconds": round(time.perf_counter() - t0, 2),
        "probes": probes,
        "probe_rps": args.hold_probe_rps,
        "verified": True,
        "active_at_end": fleet.size(),
    }


# --------------------------------------------------------------------------- #
# Phase 2b: throughput staircase (scenario 2 in this copy — warm / unit reuse)
# --------------------------------------------------------------------------- #
def throughput(helper, fleet: Fleet, rec: Recorder, args) -> dict[str, Any]:
    """Drive the EXISTING fleet at rising request rates to find its warm ceiling.

    This is scenario 2 in this copy (the original benchmark's scenario 3).
    The ramp measured how fast units can be *built*; this measures how
    much traffic they can *serve* once warm. Units are reused round-robin and
    never released, so every request here is warm — the cold cost was already
    paid and reported by the ramp phase.

    Each step offers `rps` for `--step-seconds`, open-loop against absolute
    deadlines (same reasoning as ramp()). Requests are NOT paced by how fast the
    previous ones answer: if the fleet cannot keep up, that shows up as latency
    and errors, which is the measurement. A closed loop would instead silently
    lower the offered rate and report a throughput that was never attempted.

    AgentCore's ceiling here is administrative, not capacity: `invoke_agent_runtime`
    is quota-limited to 200/s per agent per account, independent of fleet size —
    100 sessions and 5,000 sessions both stop at 200/s. Reaching it means the
    QUOTA was reached, not that the sessions were saturated. `comparable_rps`
    marks that boundary in the result, for reading per-unit throughput and
    latency over the range below it.
    """
    rec.set_phase("throughput")
    units = fleet.snapshot()
    if not units:
        return {"phase": "throughput", "skipped": True,
                "reason": "no live units"}

    # The per-unit staircase (--per-unit-rps-steps; not wired into
    # run_scenario.sh in this copy, see benchmarks/README.md) expresses the
    # staircase PER UNIT and resolves it here, because
    # the real fleet size is only known now: the ramp overshoots its target (in-
    # flight acquisitions land after it is reached), so multiplying at parse time
    # would aim at the wrong total. Rates are what we set; the per-unit rate is
    # the quantity under study, so it is what the steps are defined in.
    rps_steps = args.rps_steps
    per_unit_steps = getattr(args, "per_unit_rps_steps", None)
    if per_unit_steps:
        rps_steps = [p * len(units) for p in per_unit_steps]
        print(f"  [tput] per-unit staircase across {len(units)} unit(s): "
              f"{', '.join(f'{p:g}' for p in per_unit_steps)} req/s/unit "
              f"= {', '.join(f'{r:g}' for r in rps_steps)} req/s total")

    baseline_p99: float | None = None
    steps: list[dict[str, Any]] = []
    stop_reason = "steps-exhausted"
    stop_detail = None
    # Round-robin cursor over the fleet, so load spreads evenly instead of
    # randomly piling several concurrent requests onto one unit.
    cursor = itertools.count()

    for rps in rps_steps:
        # Enough workers to keep `rps` in flight at the latency seen so far;
        # too few would pace the offered rate by completion, closing the loop.
        expect_ms = baseline_p99 or 250.0
        workers = args.step_workers or min(
            2048, max(64, int(rps * (expect_ms / 1000.0) * 3) + 64))
        lat: list[float] = []
        errs: dict[str, int] = {"throttle": 0, "capacity": 0, "failure": 0}
        lock = threading.Lock()
        sent = 0
        interval = 1.0 / rps
        t0 = time.perf_counter()

        # lock/lat/errs bound as defaults (evaluated once, at def time, each
        # loop iteration) rather than captured from the enclosing scope, so
        # each step's closure is pinned to that step's own objects regardless
        # of what the next iteration rebinds those names to.
        def one(index: int, lock: threading.Lock = lock,
                lat: list[float] = lat, errs: dict[str, int] = errs) -> None:
            unit = units[next(cursor) % len(units)]
            res = helper.invoke(unit, index)
            rec.add_result(res)
            with lock:
                if res.ok:
                    lat.append(res.latency_ms)
                else:
                    errs[classify_error(res.error) or "failure"] += 1

        with ThreadPoolExecutor(max_workers=workers) as pool:
            while True:
                elapsed = time.perf_counter() - t0
                if elapsed >= args.step_seconds:
                    break
                due = t0 + sent * interval
                now = time.perf_counter()
                if now < due:
                    time.sleep(min(due - now, 0.05))
                    continue
                pool.submit(one, sent)
                sent += 1
                if args.progress and sent % max(int(rps), 1) == 0:
                    with lock:
                        done, bad = len(lat), sum(errs.values())
                    print(f"\r  [tput {rps:>6.0f}/s {elapsed:5.1f}s] "
                          f"sent={sent} ok={done} err={bad}",
                          end="", flush=True)
            # Leaving the pool waits for in-flight requests, so `wall` covers
            # them and the achieved rate is not inflated by unfinished work.
        wall = time.perf_counter() - t0

        with lock:
            st = _stats(lat)
            rejected = sum(errs.values())
            ok_count = len(lat)
        achieved = ok_count / wall if wall else 0.0
        step = {
            "offered_rps": rps,
            "achieved_rps": round(achieved, 2),
            "sent": sent,
            "ok": ok_count,
            "errors": dict(errs),
            "error_rate": round(rejected / sent, 4) if sent else 0.0,
            "wall_seconds": round(wall, 2),
            "units": len(units),
            "per_unit_rps": round(achieved / len(units), 3),
            "latency": st,
        }
        steps.append(step)
        print(f"\r  [tput {rps:>6.0f}/s] achieved={achieved:8.1f}/s  "
              f"p50={st.get('p50_ms', 0):7.1f}ms p75={st.get('p75_ms', 0):7.1f}ms "
              f"p99={st.get('p99_ms', 0):8.1f}ms  "
              f"err={rejected}/{sent}")

        if baseline_p99 is None and st.get("p99_ms"):
            baseline_p99 = st["p99_ms"]

        # ---- Should the staircase stop climbing? ----
        # Errors first: a rejected request is unambiguous, whereas latency needs
        # a threshold. Both are reported so the knee is visible either way.
        if step["error_rate"] >= args.max_error_rate:
            kinds = ", ".join(f"{k}={v}" for k, v in errs.items() if v)
            stop_reason = "error-rate"
            stop_detail = (f"{100 * step['error_rate']:.1f}% of {sent} requests "
                           f"rejected at {rps}/s ({kinds})")
            break
        if (baseline_p99 and st.get("p99_ms")
                and st["p99_ms"] >= baseline_p99 * args.latency_factor):
            stop_reason = "latency-degraded"
            stop_detail = (f"p99 {st['p99_ms']:.0f}ms at {rps}/s is "
                           f"{st['p99_ms'] / baseline_p99:.1f}x the baseline "
                           f"{baseline_p99:.0f}ms")
            break
        # A step that cannot even be OFFERED at its nominal rate means the
        # generator, not the runtime, is the limit — stop rather than report a
        # ceiling that is really a client-side one.
        if achieved < rps * args.min_achieved_ratio and not rejected:
            stop_reason = "generator-limited"
            stop_detail = (f"only achieved {achieved:.0f}/s of {rps}/s offered "
                           f"with no errors — the client, not the runtime, is "
                           f"the limit (raise --step-workers)")
            break

    best = max(steps, key=lambda s: s["achieved_rps"]) if steps else {}
    # Steps below AgentCore's 200/s invoke quota, the binding constraint on this
    # leg.
    comparable = [s for s in steps if s["offered_rps"] <= args.comparable_rps]
    # The last step that was actually CLEAN — no meaningful errors and no latency
    # knee. `best` is the highest achieved rate, which can be a degraded step
    # (e.g. a step that peaked and then "achieved" a lower rate at a high error
    # percentage). Per-unit capacity has to be read off a healthy step or it
    # reports a rate nobody would run at.
    clean = [s for s in steps
             if s["error_rate"] < args.max_error_rate
             and not (baseline_p99 and s["latency"].get("p99_ms")
                      and s["latency"]["p99_ms"] >= baseline_p99 * args.latency_factor)]
    top = max(clean, key=lambda s: s["achieved_rps"]) if clean else {}
    out = {
        "phase": "throughput",
        "stop_reason": stop_reason,
        "stop_detail": stop_detail,
        "units": len(units),
        "step_seconds": args.step_seconds,
        "steps": steps,
        "max_achieved_rps": best.get("achieved_rps", 0.0),
        "max_achieved_per_unit_rps": best.get("per_unit_rps", 0.0),
        "at_offered_rps": best.get("offered_rps"),
        # The per-unit staircase's headline: the highest per-unit rate served CLEANLY.
        "max_clean_per_unit_rps": top.get("per_unit_rps", 0.0),
        "max_clean_rps": top.get("achieved_rps", 0.0),
        "clean_at_offered_rps": top.get("offered_rps"),
        "comparable_rps": args.comparable_rps,
        "comparable_steps": comparable,
    }
    if per_unit_steps:
        out["per_unit_rps_steps"] = per_unit_steps
        out["saturated"] = stop_reason in ("error-rate", "latency-degraded")
        # Whether the run actually FOUND the ceiling matters as much as the
        # number: a staircase that ran out of steps only proves the unit serves
        # at least the top step, which is a lower bound, not a capacity.
        if not out["saturated"]:
            out["per_unit_note"] = (
                f"unit not saturated — {out['max_clean_per_unit_rps']:g} "
                f"req/s/unit is a LOWER BOUND (stopped: {stop_reason}); "
                f"extend --per-unit-rps-steps to find the ceiling")
    return out


# --------------------------------------------------------------------------- #
# Phase 3: teardown
# --------------------------------------------------------------------------- #
def teardown(helper, fleet: Fleet, args) -> dict[str, Any]:
    """Release every live unit, paced at `--teardown-rps`.

    This is a MEASURED phase, not cleanup: how long it takes to shut 5,000 units
    down is part of the comparison, and the release APIs have their own quotas
    (unpaced, teardown would just throttle itself).

    Failures are reported rather than swallowed — a unit that will not release
    keeps billing and keeps holding a slot against the quota, which would
    silently poison the next leg. A unit AgentCore already reaped on its own
    (AGENTCORE_IDLE_TIMEOUT expired before teardown got to it) is counted
    separately as `reaped`, not as an error: it is not billing anymore and
    there is nothing to retry, so it should not read like a teardown failure.
    """
    units = fleet.drain()
    if not units:
        return {"phase": "teardown", "units": 0}

    # 0 means "use the target's own release quota". Pacing above it just makes
    # teardown throttle itself, and every throttled unit keeps billing.
    rps = args.teardown_rps or getattr(helper, "release_rps", 100)
    print(f"  [teardown] releasing {len(units)} unit(s) at {rps}/s"
          f"{'' if args.teardown_rps else ' (target quota)'}...")
    errors: list[str] = []
    lock = threading.Lock()
    released = 0
    reaped = 0
    interval = 1.0 / rps
    t0 = time.perf_counter()

    def release(unit: Any) -> None:
        nonlocal released, reaped
        # Retry a THROTTLED release. Unlike an invoke, a release that gives up
        # leaves a unit running and billing, so the throttle must not be final.
        # Backoff is bounded; a genuine error (not a throttle) is reported on
        # the first try without wasting attempts.
        for attempt in range(args.release_attempts):
            try:
                helper.release(unit)
                with lock:
                    released += 1
                return
            except Exception as exc:  # noqa: BLE001 - reported, see docstring
                msg = f"{type(exc).__name__}: {exc}"
                if "ResourceNotFoundException" in msg:
                    # Already gone -- most likely AGENTCORE_IDLE_TIMEOUT reaped
                    # it before teardown got here (see common.sh's comment on
                    # that var). Not a release failure: nothing is billing
                    # anymore and there is nothing to retry.
                    with lock:
                        reaped += 1
                    return
                last = f"{unit}: {msg}"
                if classify_error(msg) != "throttle":
                    break
                if attempt + 1 < args.release_attempts:
                    time.sleep(min(2.0 ** attempt * 0.5, 8.0))
        with lock:
            errors.append(last)

    # Retries hold a worker while they back off, so the pool must exceed the
    # pacing rate or the retries would themselves slow the release stream.
    with ThreadPoolExecutor(max_workers=max(32, rps * 2)) as pool:
        for i, unit in enumerate(units):
            due = t0 + i * interval
            now = time.perf_counter()
            if now < due:
                time.sleep(due - now)
            pool.submit(release, unit)
    wall = time.perf_counter() - t0

    if reaped:
        # Expected, not an error -- see the ResourceNotFoundException handling
        # above and common.sh's AGENTCORE_IDLE_TIMEOUT comment.
        print(f"  [teardown] {reaped} unit(s) already reaped by "
              f"AGENTCORE_IDLE_TIMEOUT before teardown got to them "
              f"(not a failure).")
    if errors:
        print(f"  [teardown] {len(errors)} unit(s) FAILED to release — they keep "
              f"billing until their idle timeout:", file=sys.stderr)
        for e in errors[:10]:
            print(f"    {e}", file=sys.stderr)
        if len(errors) > 10:
            print(f"    ... and {len(errors) - 10} more", file=sys.stderr)
    return {
        "phase": "teardown",
        "units": len(units),
        "released": released,
        "reaped": reaped,
        "errors": len(errors),
        "error_sample": errors[:10],
        "wall_seconds": round(wall, 2),
        "released_per_s": round(released / wall, 2) if wall else 0.0,
        "paced_at_rps": rps,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def build_helper(args):
    """Same construction as simple_test.py, minus the batch-only options."""
    from agentcore import AgentCoreHelper
    arn = (args.agentcore_zip_arn if args.target_runtime == "agentcore-zip"
           else args.agentcore_arn)
    if not arn:
        var = ("AGENTCORE_ZIP_ARN" if args.target_runtime == "agentcore-zip"
               else "AGENTCORE_ARN")
        sys.exit(f"error: {var} is required for target {args.target_runtime}")
    return AgentCoreHelper(
        arn=arn, region=args.region, qualifier=args.qualifier,
        prompt=args.prompt, concurrency=args.workers or 256,
        cold_field=args.cold_field,
        endpoint_url=args.agentcore_endpoint)


def run(args) -> int:
    helper = build_helper(args)
    print(f"\nTarget:  {helper.label}")
    print(f"Ramp:    +{args.rate} unit(s) every {args.per}s "
          f"(= {args.rate / args.per:.2f}/s) until {args.target} accepted")
    print(f"Hold:    {args.hold}s"
          + (f", probing {args.hold_probe_rps}/s" if args.hold_probe_rps > 0 else ""))
    if args.per_unit_rps_steps:
        print(f"Tput:    {args.step_seconds}s per step at "
              f"{', '.join(f'{r:g}' for r in args.per_unit_rps_steps)} "
              f"req/s PER UNIT (x fleet size, resolved at run time)")
        print(f"         stop at p99 >= {args.latency_factor}x baseline or "
              f"error rate >= {100 * args.max_error_rate:.0f}%")
    elif args.rps_steps:
        print(f"Tput:    {args.step_seconds}s per step at "
              f"{', '.join(f'{r:g}' for r in args.rps_steps)} req/s")
        print(f"         stop at p99 >= {args.latency_factor}x baseline or "
              f"error rate >= {100 * args.max_error_rate:.0f}%")
    print(f"Guards:  max_seconds={args.max_seconds}"
          + (f" stall={args.stall_seconds}s" if args.stall_seconds else "")
          + (f" max_offered={args.max_offered}" if args.max_offered else "")
          + (f" max_throttle={args.max_throttle_pct}%"
             f"/after {args.throttle_pct_after}" if args.max_throttle_pct else "")
          + (f" max_units={args.max_units}" if args.max_units else "")
          + (f" capacity_stop={args.capacity_stop}/{args.capacity_window}"
             if args.capacity_stop else ""))

    fleet = Fleet()
    rec = Recorder(time.perf_counter(), capacity_window=args.capacity_window)
    phases: list[dict[str, Any]] = []
    try:
        phases.append(ramp(helper, fleet, rec, args))
        if args.hold > 0 and fleet.size() > 0:
            phases.append(hold(helper, fleet, rec, args))
        # Scenario 2: drive the fleet the ramp just built. It runs in the SAME
        # process on purpose — a unit handle is only usable within the context
        # (endpoint, session, auth) that created it, which --no-teardown does
        # not persist, so a staircase resumed from a JSON file could not invoke
        # anything.
        if (args.rps_steps or args.per_unit_rps_steps) and fleet.size() > 0:
            phases.append(throughput(helper, fleet, rec, args))
    except KeyboardInterrupt:
        print("\n[ramp] interrupted — tearing the fleet down before exiting.",
              file=sys.stderr)
    finally:
        # Teardown runs even on interrupt or mid-ramp failure: leaving thousands
        # of units running would bill continuously and block the next leg.
        rec.set_phase("teardown")
        if args.no_teardown:
            print(f"[ramp] --no-teardown set: leaving {fleet.size()} unit(s) "
                  f"running", file=sys.stderr)
            phases.append({"phase": "teardown", "skipped": True,
                           "left_running": fleet.size(),
                           "units": [str(u) for u in fleet.units]})
        else:
            phases.append(teardown(helper, fleet, args))

    report = {
        "target": helper.label,
        "runtime": args.target_runtime,
        "scenario": {
            "rate": args.rate, "per": args.per, "target_units": args.target,
            "hold_s": args.hold, "hold_probe_rps": args.hold_probe_rps,
            "max_seconds": args.max_seconds, "max_units": args.max_units,
            "stall_seconds": args.stall_seconds,
            "max_offered": args.max_offered,
            "max_throttle_pct": args.max_throttle_pct,
            "throttle_pct_after": args.throttle_pct_after,
            "capacity_stop": args.capacity_stop,
            "capacity_window": args.capacity_window,
        },
        "phases": phases,
        "timeseries": rec.series(),
    }
    print_report(report)

    if args.out:
        with open(args.out, "w") as f:
            json.dump({**report,
                       "results": [asdict(r) for r in rec.results]}, f, indent=2)
        print(f"\nWrote results to {args.out}")

    ramp_phase = phases[0] if phases else {}
    # Exit 0 iff the fleet's FINAL accepted count actually reached the target
    # (ramp()'s own "target_reached", computed after the thread pool drains
    # every in-flight acquisition) or stopped at a ceiling we asked it to
    # respect (--max-units). Deliberately NOT keyed off stop_reason alone: the
    # loop can latch a reason like "capacity-ceiling" while accepted is still
    # just under target, and then in-flight acquisitions that were already
    # dispatched push accepted to (or past) target before the phase dict is
    # built — a real, observed case where the final numbers show a full
    # success (e.g. 5,000/5,000 accepted) but stop_reason still names the
    # rejection that happened moments earlier. Every other case is non-zero so
    # the sequential wrapper flags the leg: the numbers are still valid and
    # the stall point IS the result, but "did not reach the target" should not
    # look like a clean run to a caller that only checks the status.
    return 0 if (ramp_phase.get("target_reached")
                 or ramp_phase.get("stop_reason") == "max-units") else 1


def print_report(report: dict[str, Any]) -> None:
    print(f"\n=== {report['target']} — scale ramp ===")
    for p in report["phases"]:
        name = p.get("phase")
        if name == "ramp":
            print(f"  ramp:      {p['accepted']}/{p['target']} accepted in "
                  f"{p['wall_seconds']}s  ({p['stop_reason']})")
            if p.get("stop_detail"):
                print(f"    stopped: {p['stop_detail']}")
            print(f"    rate:    offered {p['offered_rate_per_s']}/s -> achieved "
                  f"{p['achieved_rate_per_s']}/s "
                  f"(nominal {p['nominal_seconds']}s)")
            print(f"    outcome: offered={p['offered']} accepted={p['accepted']} "
                  f"throttled={p['throttled']} capacity={p['capacity']} "
                  f"failed={p['failed']}")
            # The cost of the rejected attempts, which is what the guards bound.
            if p["offered"]:
                rej = p["throttled"] + p["capacity"] + p["failed"]
                print(f"    rejected: {rej}/{p['offered']} attempts "
                      f"({100.0 * rej / p['offered']:.1f}%)")
        elif name == "hold":
            v = "verified by probes" if p.get("verified") else "UNVERIFIED"
            print(f"  hold:      {p['seconds']}s at {p['active_at_end']} active, "
                  f"{p['probes']} probes ({v})")
        elif name == "throughput":
            if p.get("skipped"):
                print(f"  throughput: SKIPPED ({p.get('reason')})")
                continue
            print(f"  throughput: {p['units']} warm unit(s), "
                  f"{p['step_seconds']}s per step  ({p['stop_reason']})")
            if p.get("stop_detail"):
                print(f"    stopped: {p['stop_detail']}")
            print(f"    {'offered':>9} {'achieved':>9} {'per-unit':>9} "
                  f"{'p50':>8} {'p75':>8} {'p99':>9}  errors")
            for s in p["steps"]:
                lat = s["latency"]
                print(f"    {s['offered_rps']:>8.0f}/s {s['achieved_rps']:>8.1f}/s "
                      f"{s['per_unit_rps']:>8.2f}/s "
                      f"{lat.get('p50_ms', 0):>7.1f}ms {lat.get('p75_ms', 0):>7.1f}ms "
                      f"{lat.get('p99_ms', 0):>8.1f}ms"
                      f"  {sum(s['errors'].values())}/{s['sent']}")
            print(f"    peak:    {p['max_achieved_rps']}/s "
                  f"({p['max_achieved_per_unit_rps']}/s per unit) "
                  f"at {p['at_offered_rps']}/s offered")
            if p.get("max_clean_rps") != p.get("max_achieved_rps"):
                # The peak step was degraded, so the peak is not a rate anyone
                # would run at. Show the best HEALTHY step next to it.
                print(f"    best clean step: {p['max_clean_rps']}/s "
                      f"({p['max_clean_per_unit_rps']}/s per unit) "
                      f"at {p['clean_at_offered_rps']}/s offered")
            if p.get("per_unit_rps_steps"):
                # The per-unit staircase's headline number.
                verdict = ("SATURATED" if p.get("saturated")
                           else "NOT saturated (lower bound)")
                print(f"    per-unit capacity: "
                      f"{p['max_clean_per_unit_rps']}/s per unit  [{verdict}]")
                if p.get("per_unit_note"):
                    print(f"    note: {p['per_unit_note']}")
            # Above AgentCore's 200/s invoke quota the legs stop being
            # comparable, so say where the comparable range ends.
            print(f"    comparable range: <= {p['comparable_rps']}/s "
                  f"({len(p['comparable_steps'])} step(s))")
        elif name == "teardown":
            if p.get("skipped"):
                print(f"  teardown:  SKIPPED, {p['left_running']} unit(s) left "
                      f"running")
            elif p.get("units"):
                reaped_note = (f", {p['reaped']} already reaped"
                                if p.get("reaped") else "")
                print(f"  teardown:  {p['released']}/{p['units']} released in "
                      f"{p['wall_seconds']}s ({p['released_per_s']}/s){reaped_note}, "
                      f"{p['errors']} real error(s)")

    # Cold latency across the whole ramp, and how it moved as the fleet grew:
    # the tail is where saturation shows up.
    ramp_ticks = [t for t in report["timeseries"] if t["phase"] == "ramp"]
    lat = [t["latency"] for t in ramp_ticks if t["latency"]]
    if lat:
        first, last = lat[0], lat[-1]
        print(f"  cold latency (per-second p50): first={first['p50_ms']}ms "
              f"last={last['p50_ms']}ms")
    print(f"  timeseries: {len(report['timeseries'])} second(s) recorded")


def main() -> None:
    env = os.environ.get
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target_runtime", nargs="?", default=env("TARGET"),
                   choices=["agentcore", "agentcore-zip"],
                   metavar="TARGET", help="which runtime to ramp (env: TARGET)")
    # -- the scenario --
    p.add_argument("--rate", type=float, default=float(env("RAMP_RATE", "25")),
                   help="new units offered per --per seconds (env: RAMP_RATE)")
    p.add_argument("--per", type=float, default=float(env("RAMP_PER", "1")),
                   help="the rate's interval in seconds: --rate 400 --per 60 is "
                        "400/min (env: RAMP_PER)")
    p.add_argument("--target", type=int, default=int(env("RAMP_TARGET", "5000")),
                   help="stop the ramp once this many units are ACCEPTED "
                        "(env: RAMP_TARGET)")
    p.add_argument("--hold", type=float, default=float(env("RAMP_HOLD", "0")),
                   help="seconds to hold the full fleet after the ramp "
                        "(env: RAMP_HOLD)")
    p.add_argument("--hold-probe-rps", type=int,
                   default=int(env("RAMP_HOLD_PROBE_RPS", "10")),
                   help="probe requests/s during the hold, sampled across the "
                        "fleet; 0 = hold without verifying "
                        "(env: RAMP_HOLD_PROBE_RPS)")
    # -- throughput staircase (scenario 2 in this copy) --
    p.add_argument("--rps-steps", default=env("RAMP_RPS_STEPS", ""),
                   help="comma-separated request rates to drive the warm fleet "
                        "at, e.g. '25,50,100,200,400,800'. Empty = skip the "
                        "throughput phase (env: RAMP_RPS_STEPS)")
    p.add_argument("--step-seconds", type=float,
                   default=float(env("RAMP_STEP_SECONDS", "30")),
                   help="seconds per staircase step (env: RAMP_STEP_SECONDS)")
    # -- per-unit staircase (not wired into run_scenario.sh in this copy) --
    p.add_argument("--per-unit-rps-steps",
                   default=env("RAMP_PER_UNIT_RPS_STEPS", ""),
                   help="staircase expressed PER UNIT, e.g. '5,10,20,40,80'. "
                        "Multiplied by the actual fleet size at run time. Use "
                        "instead of --rps-steps to saturate individual units "
                        "and measure per-unit capacity. Fully implemented but "
                        "not exposed by run_scenario.sh in this copy "
                        "(env: RAMP_PER_UNIT_RPS_STEPS)")
    p.add_argument("--latency-factor", type=float,
                   default=float(env("RAMP_LATENCY_FACTOR", "3.0")),
                   help="stop climbing once a step's p99 reaches this multiple "
                        "of the FIRST step's p99 — the knee of the curve "
                        "(env: RAMP_LATENCY_FACTOR)")
    p.add_argument("--max-error-rate", type=float,
                   default=float(env("RAMP_MAX_ERROR_RATE", "0.05")),
                   help="stop climbing once this fraction of a step's requests "
                        "is rejected (env: RAMP_MAX_ERROR_RATE)")
    p.add_argument("--comparable-rps", type=float,
                   default=float(env("RAMP_COMPARABLE_RPS", "200")),
                   help="offered rate up to which a step is below AgentCore's "
                        "200/s per-agent invoke quota (env: RAMP_COMPARABLE_RPS)")
    p.add_argument("--step-workers", type=int,
                   default=int(env("RAMP_STEP_WORKERS", "0")),
                   help="threads driving each step; 0 = derive from the rate "
                        "and observed latency (env: RAMP_STEP_WORKERS)")
    p.add_argument("--min-achieved-ratio", type=float,
                   default=float(env("RAMP_MIN_ACHIEVED_RATIO", "0.7")),
                   help="if a step achieves less than this fraction of its "
                        "offered rate with NO errors, the client is the "
                        "bottleneck, not the runtime, so stop "
                        "(env: RAMP_MIN_ACHIEVED_RATIO)")
    # -- guards --
    p.add_argument("--max-seconds", type=float,
                   default=float(env("RAMP_MAX_SECONDS", "1800")),
                   help="runaway guard: give up on the ramp after this long "
                        "(env: RAMP_MAX_SECONDS)")
    p.add_argument("--max-units", type=int, default=int(env("RAMP_MAX_UNITS", "0")),
                   help="stop at this many live units — a known hard ceiling, "
                        "e.g. an account concurrency quota; 0 = no cap "
                        "(env: RAMP_MAX_UNITS)")
    p.add_argument("--stall-seconds", type=float,
                   default=float(env("RAMP_STALL_SECONDS", "60")),
                   help="PRIMARY guard: stop when no new unit has been ACCEPTED "
                        "for this long — the fleet has stopped growing, so "
                        "further attempts only buy rejections; 0 = never "
                        "(env: RAMP_STALL_SECONDS)")
    p.add_argument("--max-offered", type=int,
                   default=int(env("RAMP_MAX_OFFERED", "0")),
                   help="backstop: stop after this many acquisition attempts, "
                        "however many were accepted; 0 = no cap "
                        "(env: RAMP_MAX_OFFERED)")
    p.add_argument("--max-throttle-pct", type=float,
                   default=float(env("RAMP_MAX_THROTTLE_PCT", "0")),
                   help="backstop: stop once this %% of all attempts have been "
                        "rejected (throttle+capacity+failure). Only checked "
                        "after --throttle-pct-after attempts, since a "
                        "quota-limited leg is already ~80%% rejected in its "
                        "first seconds; 0 = never (env: RAMP_MAX_THROTTLE_PCT)")
    p.add_argument("--throttle-pct-after", type=int,
                   default=int(env("RAMP_THROTTLE_PCT_AFTER", "2000")),
                   help="arm --max-throttle-pct only after this many attempts "
                        "(env: RAMP_THROTTLE_PCT_AFTER)")
    p.add_argument("--capacity-stop", type=int,
                   default=int(env("RAMP_CAPACITY_STOP", "25")),
                   help="declare the ceiling after this many capacity errors "
                        "within the last --capacity-window attempts; "
                        "0 = never (env: RAMP_CAPACITY_STOP)")
    p.add_argument("--capacity-window", type=int,
                   default=int(env("RAMP_CAPACITY_WINDOW", "200")),
                   help="the sliding window --capacity-stop counts over. A "
                        "consecutive-run rule never fires under heavy "
                        "throttling (env: RAMP_CAPACITY_WINDOW)")
    p.add_argument("--teardown-rps", type=int,
                   default=int(env("RAMP_TEARDOWN_RPS", "0")),
                   help="unit releases per second during teardown; 0 = use the "
                        "target's release quota (AgentCore StopRuntimeSession "
                        "200/s) (env: RAMP_TEARDOWN_RPS)")
    p.add_argument("--release-attempts", type=int,
                   default=int(env("RAMP_RELEASE_ATTEMPTS", "5")),
                   help="tries per unit when a release is THROTTLED. A release "
                        "that gives up leaves the unit running and billing "
                        "(env: RAMP_RELEASE_ATTEMPTS)")
    p.add_argument("--workers", type=int, default=int(env("RAMP_WORKERS", "0")),
                   help="acquisition thread pool size; 0 = derive from --rate "
                        "(env: RAMP_WORKERS)")
    p.add_argument("--no-teardown", action="store_true",
                   default=env("NO_TEARDOWN", "").lower() in ("1", "true", "yes"),
                   help="leave the fleet running (for the warm/reuse scenario); "
                        "the unit handles are written to --out (env: NO_TEARDOWN)")
    p.add_argument("--seed", type=int, default=int(env("RAMP_SEED", "1")),
                   help="RNG seed for hold-phase sampling (env: RAMP_SEED)")
    p.add_argument("--progress", type=int, default=int(env("RAMP_PROGRESS", "25")),
                   help="print progress every N arrivals; 0 = quiet "
                        "(env: RAMP_PROGRESS)")
    # -- shared with simple_test.py --
    p.add_argument("--region", default=env("AWS_REGION", "us-east-1"))
    p.add_argument("--prompt", default=env("PROMPT", "ping"))
    p.add_argument("--cold-field", default=env("COLD_FIELD"))
    p.add_argument("--out", default=env("OUT"),
                   help="write the report + per-request JSON here (env: OUT)")
    p.add_argument("--agentcore-arn", default=env("AGENTCORE_ARN"))
    p.add_argument("--agentcore-zip-arn", default=env("AGENTCORE_ZIP_ARN"))
    p.add_argument("--qualifier", default=env("QUALIFIER", "DEFAULT"))
    p.add_argument("--agentcore-endpoint", default=env("AGENTCORE_ENDPOINT_URL"))
    args = p.parse_args()

    if not args.target_runtime:
        p.error("target is required (agentcore|agentcore-zip or env TARGET)")
    if args.rate <= 0 or args.per <= 0:
        p.error("--rate and --per must be > 0")
    if args.target < 1:
        p.error("--target must be >= 1")
    # A ramp that cannot finish inside the guard would report a stall that is an
    # artefact of the guard, not of the runtime. Catch it before spending money.
    nominal = args.target * args.per / args.rate
    if nominal > args.max_seconds:
        p.error(f"--target {args.target} at {args.rate}/{args.per}s needs "
                f"{nominal:.0f}s, more than --max-seconds {args.max_seconds:.0f}")
    # Same reasoning for --max-offered: a cap below the attempts a healthy ramp
    # needs would report a stall that is an artefact of the cap.
    if args.max_offered and args.max_offered < args.target:
        p.error(f"--max-offered {args.max_offered} is below --target "
                f"{args.target}: even a leg accepting every attempt could not "
                f"finish")
    if args.max_throttle_pct and not 0 < args.max_throttle_pct <= 100:
        p.error("--max-throttle-pct must be in (0, 100]")
    if args.capacity_stop and args.capacity_stop > args.capacity_window:
        p.error(f"--capacity-stop {args.capacity_stop} exceeds "
                f"--capacity-window {args.capacity_window}: it could never fire")

    # --rps-steps / --per-unit-rps-steps arrive as strings (CLI or env); parse
    # into sorted floats. Ascending order is required, not cosmetic: the first
    # step establishes the p99 baseline every later step is compared against.
    def parse_steps(value: Any, flag: str) -> list[float]:
        if not isinstance(value, str):
            return value
        raw = [s.strip() for s in value.split(",") if s.strip()]
        try:
            out = [float(s) for s in raw]
        except ValueError:
            p.error(f"{flag} must be comma-separated numbers, got '{value}'")
        if any(s <= 0 for s in out):
            p.error(f"{flag} values must all be > 0")
        return sorted(out)

    args.rps_steps = parse_steps(args.rps_steps, "--rps-steps")
    args.per_unit_rps_steps = parse_steps(args.per_unit_rps_steps,
                                          "--per-unit-rps-steps")
    # The two are alternative ways to express ONE staircase, so allowing both
    # would silently discard the absolute one inside throughput().
    if args.rps_steps and args.per_unit_rps_steps:
        p.error("--rps-steps and --per-unit-rps-steps both set: they are two "
                "ways to describe the same staircase; keep one")
    if args.per_unit_rps_steps and not args.target:
        p.error("--per-unit-rps-steps needs --target > 0 (there must be a fleet "
                "to divide the load across)")
    if args.per_unit_rps_steps or args.rps_steps:
        if args.step_seconds <= 0:
            p.error("--step-seconds must be > 0")
        if not 1.0 < args.latency_factor:
            p.error("--latency-factor must be > 1.0 (it multiplies the baseline)")
        if not 0 < args.max_error_rate <= 1:
            p.error("--max-error-rate is a fraction in (0, 1]")
        if args.no_teardown:
            p.error("--no-teardown with --rps-steps would leave the fleet "
                    "running AFTER the staircase already used it; drop one")

    sys.exit(run(args))


if __name__ == "__main__":
    main()
