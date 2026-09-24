"""Scheduled drift detector.

Runs on a timer. Each invocation reads whatever evaluation results have appeared
since the last run, feeds them through the per-evaluator detector, and publishes a
drift signal to CloudWatch.

The detector is deliberately not in the request path. Drift is a property of a
distribution over many responses, so there is nothing useful to decide about the
response in front of you, and putting a statistical check inline would add latency
to every call in exchange for a verdict it does not have the evidence to make.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

import boto3

from . import config as cfg
from . import methods, scores
from .state import EvaluatorState, StateStore, now_iso

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("STATE_TABLE", "market-trends-drift-detector-state")

# How far back each run looks. Longer than the schedule interval on purpose:
# evaluation results arrive minutes after the session they describe, and a window
# that only covered the last interval would drop late arrivals. Re-reading is safe
# because every score carries a stable key and is deduplicated per evaluator.
LOOKBACK_SECONDS = int(os.environ.get("DRIFT_LOOKBACK_SECONDS", str(6 * 3600)))


def _clients():
    region = os.environ.get("AWS_REGION", "us-east-1")
    return (
        boto3.client("logs", region_name=region),
        boto3.client("dynamodb", region_name=region),
        boto3.client("cloudwatch", region_name=region),
    )


def _restore(detector, gate, state: EvaluatorState) -> None:
    if state.detector:
        detector.restore(state.detector)
    if state.gate:
        gate.restore(state.gate)


def _metric(name: str, evaluator: str, value: float, unit: str = "None") -> Dict[str, Any]:
    return {
        "MetricName": name,
        "Dimensions": [
            {"Name": "Evaluator", "Value": evaluator},
            {"Name": "ServiceName", "Value": cfg.SERVICE_NAME},
        ],
        "Value": float(value),
        "Unit": unit,
    }


def _publish(cw, metric_data: List[Dict[str, Any]]) -> None:
    if not metric_data:
        return
    for i in range(0, len(metric_data), 20):  # PutMetricData caps at 20 per call
        cw.put_metric_data(Namespace=cfg.DRIFT_NAMESPACE, MetricData=metric_data[i : i + 20])


def process_evaluator(
    ev_cfg: cfg.EvaluatorConfig,
    ev_scores: List[scores.Score],
    store: StateStore,
) -> Dict[str, Any]:
    """Feed one evaluator's new scores through its detector and persist the result."""
    state = store.load(ev_cfg.evaluator) or EvaluatorState(
        evaluator=ev_cfg.evaluator, method=ev_cfg.method, shape=ev_cfg.shape
    )
    state.method = ev_cfg.method
    state.shape = ev_cfg.shape

    detector = methods.build(ev_cfg.method, ev_cfg.resolved_params())
    gate = methods.PersistenceGate(consecutive=cfg.CONSECUTIVE)
    _restore(detector, gate, state)

    seen = set(state.seen_keys)
    fresh = [s for s in ev_scores if s.key not in seen and s.timestamp_ms >= state.watermark_ms]

    verdict = None
    consumed: List[str] = []
    for score in fresh:
        verdict = detector.observe(score.value)
        drifting = gate.observe(verdict.alarm, now_iso())

        consumed.append(score.key)
        state.samples_seen += 1
        state.watermark_ms = max(state.watermark_ms, score.timestamp_ms)
        state.last_score = score.value
        state.last_statistic = verdict.statistic
        state.last_threshold = verdict.threshold
        state.last_pressure = verdict.pressure
        state.last_detail = verdict.detail
        state.warming_up = verdict.warming_up
        state.drifting = drifting

        if drifting and gate.state.run_length == cfg.CONSECUTIVE:
            LOG.warning(
                "DRIFT CONFIRMED evaluator=%s method=%s score=%.3f statistic=%.4f "
                "threshold=%.4f session=%s explanation=%s",
                ev_cfg.evaluator,
                ev_cfg.method,
                score.value,
                verdict.statistic,
                verdict.threshold,
                score.session_id,
                score.explanation[:200],
            )

    state.remember(consumed)
    state.detector = detector.snapshot()
    state.gate = gate.snapshot()
    store.save(state)

    return {
        "evaluator": ev_cfg.evaluator,
        "method": ev_cfg.method,
        "shape": ev_cfg.shape,
        "new_samples": len(fresh),
        "samples_seen": state.samples_seen,
        "warming_up": state.warming_up,
        "drifting": state.drifting,
        "run_length": gate.state.run_length,
        "pressure": round(state.last_pressure, 4),
        "statistic": state.last_statistic,
        "threshold": state.last_threshold,
        "baseline_mean": detector.snapshot().get("baseline", {}).get("mean"),
        "latched_at": gate.state.latched_at,
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    online_cfg_id = os.environ.get("ONLINE_EVAL_CONFIG_ID") or cfg.ONLINE_EVAL_CONFIG_ID
    if not online_cfg_id:
        raise RuntimeError("ONLINE_EVAL_CONFIG_ID is not set; nothing to read scores from")

    logs_client, ddb, cw = _clients()
    store = StateStore(TABLE_NAME, ddb)

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - LOOKBACK_SECONDS * 1000

    all_scores = scores.fetch_scores(logs_client, online_cfg_id, start_ms, end_ms)
    grouped = scores.group_by_evaluator(all_scores)
    LOG.info(
        "read %d evaluation results across %d evaluators in the last %ds",
        len(all_scores),
        len(grouped),
        LOOKBACK_SECONDS,
    )

    results: List[Dict[str, Any]] = []
    metric_data: List[Dict[str, Any]] = []

    for ev_cfg in cfg.ALL_EVALUATORS:
        ev_scores = grouped.get(ev_cfg.evaluator, [])
        # An evaluator with no history and no new scores is simply not deployed in
        # this account. Skip it rather than creating an empty baseline that would
        # later look like a warmed-up detector.
        if not ev_scores and store.load(ev_cfg.evaluator) is None:
            LOG.info("no scores for %s; skipping", ev_cfg.evaluator)
            continue

        result = process_evaluator(ev_cfg, ev_scores, store)
        results.append(result)

        # DriftDetected is the metric the alarm watches. It is emitted on every
        # run, including 0, so the alarm has continuous data and does not sit in
        # INSUFFICIENT_DATA between sparse evaluations.
        metric_data.append(_metric("DriftDetected", ev_cfg.evaluator, 1.0 if result["drifting"] else 0.0))
        metric_data.append(_metric("DriftPressure", ev_cfg.evaluator, result["pressure"]))
        metric_data.append(_metric("SamplesSeen", ev_cfg.evaluator, result["samples_seen"], "Count"))
        metric_data.append(_metric("WarmingUp", ev_cfg.evaluator, 1.0 if result["warming_up"] else 0.0))
        if result["baseline_mean"] is not None:
            metric_data.append(_metric("BaselineMean", ev_cfg.evaluator, result["baseline_mean"]))

    _publish(cw, metric_data)

    drifting = [r["evaluator"] for r in results if r["drifting"]]
    warming = [r["evaluator"] for r in results if r["warming_up"]]
    LOG.info("detectors=%d drifting=%s warming_up=%s", len(results), drifting or "none", warming or "none")

    return {
        "scores_read": len(all_scores),
        "detectors": results,
        "drifting": drifting,
        "warming_up": warming,
    }
