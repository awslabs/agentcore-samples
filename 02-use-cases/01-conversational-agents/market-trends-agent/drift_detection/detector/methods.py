"""Drift detection methods.

Three statistics over a stream of per-item quality scores. All of them are pure:
no I/O, no AWS calls, no ambient state. State is an explicit dataclass that
round-trips through a plain dict, so a detector can run in a scheduled Lambda
with its state persisted between invocations.

Which method to use depends on the shape of the score stream, not on taste. See
config.py for the mapping and the reasoning behind it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------- #
# Running mean and variance
# --------------------------------------------------------------------------- #


@dataclass
class WelfordState:
    """Welford's online algorithm: single pass, numerically stable, O(1) memory."""

    n: int = 0
    mean: float = 0.0
    m2: float = 0.0


def welford_update(state: WelfordState, x: float) -> WelfordState:
    n = state.n + 1
    delta = x - state.mean
    mean = state.mean + delta / n
    m2 = state.m2 + delta * (x - mean)
    return WelfordState(n=n, mean=mean, m2=m2)


def welford_variance(state: WelfordState) -> float:
    if state.n < 2:
        return 0.0
    return state.m2 / (state.n - 1)


def welford_stddev(state: WelfordState, floor: float = 0.0) -> float:
    """Sample standard deviation, floored.

    The floor matters more than it looks. A healthy agent clustered at the score
    ceiling can produce a genuine standard deviation of zero, which makes any
    deviation infinitely significant and blows up every sigma-scaled statistic.
    """
    return max(math.sqrt(welford_variance(state)), floor)


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


@dataclass
class Verdict:
    """One detector's assessment of one incoming score."""

    alarm: bool
    """The raw statistic crossed its threshold on this sample. Not yet confirmed:
    the persistence gate decides whether this becomes a declared drift."""

    warming_up: bool
    """Not enough baseline yet to make any claim."""

    statistic: float
    threshold: float

    pressure: float = 0.0
    """How close this detector is to alarming, normalized so that 0.0 is far away
    and 1.0 is at the threshold. Each method computes this itself because only it
    knows which direction its threshold faces."""

    detail: str = ""


# --------------------------------------------------------------------------- #
# EWMA control chart
# --------------------------------------------------------------------------- #


@dataclass
class EwmaParams:
    warmup: int = 100
    lam: float = 0.2
    """Smoothing factor. Lower means longer memory: better on gradual drift,
    slower to react to a step change."""
    L: float = 3.0
    """Control limit width, in sigma units."""
    stddev_floor: float = 0.01


@dataclass
class EwmaState:
    baseline: WelfordState = field(default_factory=WelfordState)
    ewma: float = 0.0
    initialized: bool = False
    frozen: bool = False


class Ewma:
    """Exponentially weighted moving average control chart.

        z_t = lam * x_t + (1 - lam) * z_{t-1}
        LCL = mu0 - L * sigma * sqrt(lam / (2 - lam))

    The square-root term is the asymptotic standard deviation of the EWMA
    statistic, which is smaller than sigma because averaging suppresses noise.
    """

    name = "ewma"

    def __init__(self, params: Optional[EwmaParams] = None) -> None:
        self.params = params or EwmaParams()
        self.state = EwmaState()

    def observe(self, score: float) -> Verdict:
        p, st = self.params, self.state

        if st.baseline.n < p.warmup:
            st.baseline = welford_update(st.baseline, score)
            return Verdict(
                alarm=False,
                warming_up=True,
                statistic=0.0,
                threshold=0.0,
                detail=f"warmup {st.baseline.n}/{p.warmup}",
            )

        if not st.initialized:
            st.ewma = st.baseline.mean
            st.initialized = True

        sd = welford_stddev(st.baseline, floor=p.stddev_floor)
        st.ewma = p.lam * score + (1.0 - p.lam) * st.ewma
        spread = sd * math.sqrt(p.lam / (2.0 - p.lam))
        lcl = st.baseline.mean - p.L * spread
        alarm = st.ewma < lcl

        headroom = st.baseline.mean - lcl
        pressure = max(0.0, (st.baseline.mean - st.ewma) / headroom) if headroom > 0 else 0.0

        # Freeze the baseline while alarming so degraded scores cannot quietly
        # become the new normal.
        if not alarm:
            st.frozen = False
            st.baseline = welford_update(st.baseline, score)
        else:
            st.frozen = True

        return Verdict(
            alarm=alarm,
            warming_up=False,
            statistic=st.ewma,
            threshold=lcl,
            pressure=pressure,
            detail=f"mean={st.baseline.mean:.3f} sd={sd:.4f} lam={p.lam} L={p.L}",
        )

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)

    def restore(self, snap: Dict[str, Any]) -> None:
        self.state = EwmaState(
            baseline=WelfordState(**snap.get("baseline", {})),
            ewma=float(snap.get("ewma", 0.0)),
            initialized=bool(snap.get("initialized", False)),
            frozen=bool(snap.get("frozen", False)),
        )


# --------------------------------------------------------------------------- #
# Per-sample z-score
# --------------------------------------------------------------------------- #


@dataclass
class ZScoreParams:
    warmup: int = 100
    z_threshold: float = -2.0
    stddev_floor: float = 0.01


@dataclass
class ZScoreState:
    baseline: WelfordState = field(default_factory=WelfordState)
    frozen: bool = False


class ZScore:
    """Memoryless per-sample z-score against a real running variance.

    Carries no memory of previous samples beyond the baseline itself, which is
    exactly why it wins on near-degenerate streams: an isolated dip alarms once
    and the next healthy sample breaks the run, so the persistence gate is never
    satisfied.
    """

    name = "zscore"

    def __init__(self, params: Optional[ZScoreParams] = None) -> None:
        self.params = params or ZScoreParams()
        self.state = ZScoreState()

    def observe(self, score: float) -> Verdict:
        p, st = self.params, self.state

        if st.baseline.n < p.warmup:
            st.baseline = welford_update(st.baseline, score)
            return Verdict(
                alarm=False,
                warming_up=True,
                statistic=0.0,
                threshold=p.z_threshold,
                detail=f"warmup {st.baseline.n}/{p.warmup}",
            )

        sd = welford_stddev(st.baseline, floor=p.stddev_floor)
        z = (score - st.baseline.mean) / sd
        alarm = z < p.z_threshold

        if not alarm:
            st.frozen = False
            st.baseline = welford_update(st.baseline, score)
        else:
            st.frozen = True

        return Verdict(
            alarm=alarm,
            warming_up=False,
            statistic=z,
            threshold=p.z_threshold,
            pressure=max(0.0, z / p.z_threshold),
            detail=f"mean={st.baseline.mean:.3f} sd={sd:.4f}",
        )

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)

    def restore(self, snap: Dict[str, Any]) -> None:
        self.state = ZScoreState(
            baseline=WelfordState(**snap.get("baseline", {})),
            frozen=bool(snap.get("frozen", False)),
        )


# --------------------------------------------------------------------------- #
# One-sided lower CUSUM
# --------------------------------------------------------------------------- #


@dataclass
class CusumParams:
    warmup: int = 100
    k: float = 0.5
    """Slack in sigma units. Shifts smaller than k decay out as noise."""
    h: float = 4.0
    """Decision threshold in accumulated sigma units. The primary dial for mean
    time between false alarms."""
    stddev_floor: float = 0.01


@dataclass
class CusumState:
    baseline: WelfordState = field(default_factory=WelfordState)
    s: float = 0.0
    frozen: bool = False


class Cusum:
    """Accumulates standardized shortfalls below the baseline mean.

        S_t = max(0, S_{t-1} + ((mu0 - x_t) / sigma - k))

    A single unlucky sample cannot fire it, while a small persistent decline
    eventually will.
    """

    name = "cusum"

    def __init__(self, params: Optional[CusumParams] = None) -> None:
        self.params = params or CusumParams()
        self.state = CusumState()

    def observe(self, score: float) -> Verdict:
        p, st = self.params, self.state

        if st.baseline.n < p.warmup:
            st.baseline = welford_update(st.baseline, score)
            return Verdict(
                alarm=False,
                warming_up=True,
                statistic=0.0,
                threshold=p.h,
                detail=f"warmup {st.baseline.n}/{p.warmup}",
            )

        sd = welford_stddev(st.baseline, floor=p.stddev_floor)
        shortfall = (st.baseline.mean - score) / sd
        st.s = max(0.0, st.s + shortfall - p.k)
        alarm = st.s > p.h

        # Only absorb into the baseline while the accumulator is quiet. Once
        # evidence is building, the reference must hold still.
        if st.s == 0.0:
            st.frozen = False
            st.baseline = welford_update(st.baseline, score)
        else:
            st.frozen = True

        return Verdict(
            alarm=alarm,
            warming_up=False,
            statistic=st.s,
            threshold=p.h,
            pressure=st.s / p.h if p.h > 0 else 0.0,
            detail=f"mean={st.baseline.mean:.3f} sd={sd:.4f} k={p.k} h={p.h}",
        )

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)

    def restore(self, snap: Dict[str, Any]) -> None:
        self.state = CusumState(
            baseline=WelfordState(**snap.get("baseline", {})),
            s=float(snap.get("s", 0.0)),
            frozen=bool(snap.get("frozen", False)),
        )


# --------------------------------------------------------------------------- #
# Persistence and latching
# --------------------------------------------------------------------------- #


@dataclass
class GateState:
    run_length: int = 0
    """Consecutive raw alarms seen so far."""
    latched: bool = False
    """Drift has been confirmed and stays declared until an operator clears it."""
    latched_at: str = ""
    """ISO timestamp of confirmation, for the alarm annotation."""


class PersistenceGate:
    """Turns raw per-sample alarms into a confirmed, latched drift declaration.

    A single sample crossing a threshold is not drift, it is noise. Requiring a
    run of consecutive alarms is what separates the two, and it is also what
    penalises methods that carry an isolated dip forward in memory.

    Latching exists so that one underlying condition produces one alarm rather
    than a flapping series while scores hover near the limit.
    """

    def __init__(self, consecutive: int = 5) -> None:
        self.consecutive = consecutive
        self.state = GateState()

    def observe(self, raw_alarm: bool, now_iso: str) -> bool:
        """Fold one raw alarm in. Returns True while drift is declared."""
        st = self.state

        if raw_alarm:
            st.run_length += 1
        else:
            st.run_length = 0

        if not st.latched and st.run_length >= self.consecutive:
            st.latched = True
            st.latched_at = now_iso

        return st.latched

    def clear(self) -> None:
        self.state = GateState()

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)

    def restore(self, snap: Dict[str, Any]) -> None:
        self.state = GateState(
            run_length=int(snap.get("run_length", 0)),
            latched=bool(snap.get("latched", False)),
            latched_at=str(snap.get("latched_at", "")),
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

_PARAM_TYPES = {"ewma": EwmaParams, "zscore": ZScoreParams, "cusum": CusumParams}
_METHODS = {"ewma": Ewma, "zscore": ZScore, "cusum": Cusum}


def build(method: str, params: Optional[Dict[str, Any]] = None):
    """Construct a detector by name with a plain-dict parameter override."""
    if method not in _METHODS:
        raise ValueError(f"unknown method {method!r}; expected one of {sorted(_METHODS)}")
    param_obj = _PARAM_TYPES[method](**(params or {}))
    return _METHODS[method](param_obj)
