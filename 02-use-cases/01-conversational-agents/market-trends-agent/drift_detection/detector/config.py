"""Per-evaluator detector configuration.

The central claim of this feature is that the right detection method depends on
the shape of the score stream, so there is one detector per evaluator rather than
one per agent, and the method is chosen from measured shape.

Why shape decides the method
----------------------------
An offline comparison over synthetic streams of known shape, running every method
against the same stream with the same persistence rule, produced three regimes:

  Five or more levels        EWMA wins. Smoothing suppresses quantisation noise
  (or continuous)            and the drift signal survives it.

  Binary                     EWMA still wins but needs retuning. A binary score
                             has maximal Bernoulli variance, so the same latent
                             quality drop is a much smaller multiple of sigma.
                             Longer memory and a wider limit recover it.

  Near-degenerate            No memory-based method works at any tuning. Healthy
  (almost always one value,  is one value and dips are rare and isolated, so a
  isolated dips)             smoothing method carries a single dip forward for
                             many samples, which then satisfies the persistence
                             rule and produces a false alarm. A memoryless check
                             alarms once, the next sample is healthy, the run
                             breaks, and nothing is declared. Memory is a
                             liability in this regime.

That last row is the counterintuitive one and it is the reason shape has to be
measured rather than assumed. Run scripts/shape_report.py against a real
deployment before trusting anything here.

A note on the evaluators themselves
-----------------------------------
mt_stock_price_drift computes a continuous percentage deviation internally and
then throws the magnitude away, returning a verdict against a fixed threshold.
That conversion to binary is what forces the retuned configuration below. An
evaluator that returned the deviation itself would be materially easier to
monitor. Their code is left alone here, but it is a design lesson worth naming.

A note on the shapes below
---------------------------
mt_market_data_accuracy, mt_broker_personalization, and mt_financial_
professionalism were previously classified near_degenerate or ternary based on
measurements taken through a bug in scores.py's deduplication key, which
silently discarded two of every three TRACE-level records per session (all
three turns in a session share one timestamp, and the old key deduplicated on
timestamp alone). That made these three streams look far more pinned than they
are. See RESEARCH_NOTES.md for the full investigation. The shapes below reflect
a corrected, verified 90-record baseline taken after the fix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

# The agent under observation. Both values are dimensions on the score metrics
# published by AgentCore Evaluations.
SERVICE_NAME = os.environ.get("SERVICE_NAME", "markettrends_market_trends_agent.DEFAULT")
ONLINE_EVAL_CONFIG_ID = os.environ.get("ONLINE_EVAL_CONFIG_ID", "")

# Namespace AgentCore Evaluations publishes evaluator scores to, in Embedded
# Metric Format. Verified by inspection, not assumed.
METRIC_NAMESPACE = "Bedrock-AgentCore/Evaluations"

# Namespace this feature publishes its own drift signal to.
DRIFT_NAMESPACE = os.environ.get("DRIFT_NAMESPACE", "MarketTrends/DriftDetection")

# How many consecutive raw alarms confirm drift. Applied identically to every
# method, which is what makes the offline comparison a fair one.
CONSECUTIVE = int(os.environ.get("DRIFT_CONSECUTIVE", "5"))

# Warm-up length, in samples, before any drift claim is allowed.
#
# 100 is the value the offline comparison was run at and is the honest default. A
# demo on low traffic can lower it with DRIFT_WARMUP, at the cost of a weaker
# variance estimate and therefore a twitchier detector. A detector that is silent
# because it is still warming up is not evidence of health.
WARMUP = int(os.environ.get("DRIFT_WARMUP", "100"))


@dataclass
class EvaluatorConfig:
    """How to watch one evaluator's score stream."""

    evaluator: str
    """Evaluator name. Also the CloudWatch metric name."""

    level: str
    """TRACE or SESSION. TRACE-level evaluators score once per turn, SESSION-level
    once per completed session, so their streams arrive at different rates."""

    shape: str
    """Measured shape: binary, ternary, near_degenerate, five_level, continuous."""

    method: str
    """ewma, zscore, or cusum."""

    params: Dict[str, Any] = field(default_factory=dict)
    """Method parameter overrides. warmup is injected if absent."""

    note: str = ""
    """Why this method, for the operator reading a dashboard at 2am."""

    def resolved_params(self) -> Dict[str, Any]:
        params = dict(self.params)
        params.setdefault("warmup", WARMUP)
        return params


# Code-based evaluators, registered by evaluators/scripts/deploy.py.
#
# Every shape below is what shape_report.py measured on real healthy traffic, not
# what reading the evaluator source suggested. Those two disagreed, and the
# measurement won. Reading the code says these return binary verdicts, which is
# true and beside the point: on healthy traffic they return the same verdict
# almost every time, and a stream that is 98% one value is a constant with
# outliers rather than a distribution over two values.
CODE_EVALUATORS: List[EvaluatorConfig] = [
    EvaluatorConfig(
        evaluator="mt_schema_validator",
        level="TRACE",
        shape="near_degenerate",
        method="zscore",
        params={"z_threshold": -2.0},
        note="Tool-span schema validity. Measured 98% at 1.0 with a single dip in "
        "53 samples. An EWMA on this stream latched a false drift off that one "
        "dip, which is how this configuration got corrected.",
    ),
    EvaluatorConfig(
        evaluator="mt_stock_price_drift",
        level="TRACE",
        shape="near_degenerate",
        method="zscore",
        params={"z_threshold": -2.0},
        note="Quoted price versus reference, thresholded to a verdict. The "
        "underlying measurement is a continuous deviation and the evaluator "
        "discards it. Measured 94% at 1.0 with 5.6% real DRIFT verdicts on "
        "healthy traffic in a corrected 90-record baseline (see "
        "RESEARCH_NOTES.md); close to the ceiling but not fully pinned.",
    ),
    EvaluatorConfig(
        evaluator="mt_pii_regex",
        level="TRACE",
        shape="near_degenerate",
        method="zscore",
        params={"z_threshold": -2.0},
        note="Regex PII scan. Pinned at the ceiling on healthy traffic.",
    ),
    EvaluatorConfig(
        evaluator="mt_pii_comprehend",
        level="SESSION",
        shape="near_degenerate",
        method="zscore",
        params={"z_threshold": -2.0},
        note="Comprehend PII confidence. Measured 94% at 0.5 with three isolated "
        "dips in 53 samples, and the memoryless check stayed quiet through all "
        "of them.",
    ),
    EvaluatorConfig(
        evaluator="mt_workflow_contract_gsr",
        level="SESSION",
        shape="near_degenerate",
        method="zscore",
        params={"z_threshold": -2.0},
        note="Required tool groups satisfied in order. One sample per session, so "
        "this stream is the slowest to warm up.",
    ),
]

# LLM judges from evaluators/custom_evaluators.py, scoring on {0, .25, .5, .75, 1}.
#
# Registered through a separate path from the code-based evaluators and not
# attached to online evaluation by default. Run scripts/attach_evaluators.py, or
# they never score live traffic and the most common cause of drift stays invisible.
JUDGE_EVALUATORS: List[EvaluatorConfig] = [
    EvaluatorConfig(
        evaluator="mt_market_data_accuracy",
        level="TRACE",
        shape="ternary",
        method="ewma",
        params={"lam": 0.1, "L": 4.0},
        note="Judge: are quoted figures supported by retrieved data. Earlier "
        "measurements showed this pinned at the floor; that turned out to be an "
        "artifact of a deduplication bug in scores.py that silently discarded "
        "two of every three TRACE-level records (see RESEARCH_NOTES.md). A "
        "corrected 90-record baseline shows real dispersion: 33% at 0.0, 59% at "
        "1.0, 8% at 0.75. Genuinely dispersed, not degenerate.",
    ),
    EvaluatorConfig(
        evaluator="mt_broker_personalization",
        level="TRACE",
        shape="binary",
        method="ewma",
        params={"lam": 0.1, "L": 4.0},
        note="Judge: is the answer tailored to the stored broker profile. Earlier "
        "measurements showed this pinned at the ceiling; same deduplication bug "
        "as mt_market_data_accuracy. A corrected 90-record baseline shows 67% at "
        "0.25 (the low value, not the ceiling) and 33% at 1.0. Genuinely "
        "dispersed.",
    ),
    EvaluatorConfig(
        evaluator="mt_financial_professionalism",
        level="TRACE",
        shape="binary",
        method="ewma",
        params={"lam": 0.1, "L": 4.0},
        note="Judge: tone and disclaimer discipline. Previously configured as "
        "ternary from a 53-sample window; a corrected 90-record baseline (after "
        "fixing the scores.py deduplication bug, see RESEARCH_NOTES.md) shows "
        "only two values in play, 66% at 0.75 and 34% at 0.5. Reclassified "
        "binary. Still the coarse-but-dispersed regime the wider EWMA limit is "
        "tuned for, params unchanged.",
    ),
]

ALL_EVALUATORS: List[EvaluatorConfig] = CODE_EVALUATORS + JUDGE_EVALUATORS


def by_name(name: str) -> EvaluatorConfig:
    for cfg in ALL_EVALUATORS:
        if cfg.evaluator == name:
            return cfg
    raise KeyError(f"no detector configured for evaluator {name!r}")


# Shape to method mapping, kept separate from the per-evaluator table so
# shape_report.py can flag a configuration that disagrees with observation.
SHAPE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "continuous": {"method": "ewma", "params": {"lam": 0.2, "L": 3.0}},
    "six_level": {"method": "ewma", "params": {"lam": 0.2, "L": 3.0}},
    "five_level": {"method": "ewma", "params": {"lam": 0.2, "L": 3.0}},
    # Coarse but genuinely dispersed. Smoothing still helps; the limit has to be
    # wider because quantisation inflates the variance.
    "ternary": {"method": "ewma", "params": {"lam": 0.1, "L": 4.0}},
    "binary": {"method": "ewma", "params": {"lam": 0.1, "L": 4.0}},
    # Concentrated on one value with sparse outliers. Memory is a liability here.
    "near_degenerate": {"method": "zscore", "params": {"z_threshold": -2.0}},
}

# Fraction of the stream sitting on a single value above which the remainder are
# sparse outliers rather than part of a distribution.
#
# 0.90 is set from observation, not taste. On this agent, mt_schema_validator sits
# at 0.98 with one dip in 53 samples, and an EWMA watching it carried that single
# dip forward across eight consecutive samples, satisfied the persistence rule,
# and latched a drift that had not happened. mt_pii_comprehend sits at 0.94 with
# three dips in 53, and a memoryless z-score on the same kind of stream stayed
# quiet throughout. Anything at or above this level belongs on a memoryless check.
NEAR_DEGENERATE_THRESHOLD = 0.90

# Fraction of the stream a value must reach to count as a real level of the
# distribution, rather than an outlier that happens not to be common enough to
# make the stream near_degenerate.
#
# Without this, classify() counted every distinct value it saw, and a single
# sample landing on a value the stream had never produced before was enough to
# change the answer. On a 324-sample measurement of this agent's judge
# evaluators, one sample (0.3% of the stream) turned a measured ternary shape
# into five_level, and one sample (0.3%) turned a measured binary shape into
# ternary. Neither evaluator's real behaviour had changed; the classifier was
# just counting noise as a level. 0.02 is set with headroom above what was
# observed (0.3%-1%) so an isolated stray sample cannot flip the recommendation,
# while a value that is a real minority outcome of the evaluator (occurring in
# a few percent of sessions or more) still counts.
MIN_LEVEL_FRACTION = 0.02


def classify(value_counts: Dict[float, int], n: int) -> str:
    """Infer stream shape from observed scores.

    The number of distinct values is the obvious thing to branch on and the wrong
    one, for two reasons. First, what decides the method is dispersion: how
    concentrated the stream is on a single value. A three-level score spread
    56/35/6 across its levels behaves like a distribution and smoothing helps. A
    three-level score sitting 98% on one value is a constant with occasional
    outliers, and smoothing turns each outlier into a sustained threshold
    crossing. Second, counting every distinct value with no regard for how often
    it occurs makes the answer unstable: a single stray sample on a value the
    stream had never shown before changes the level count and therefore the
    recommendation, even though nothing about the evaluator changed. Values
    below MIN_LEVEL_FRACTION are excluded from the level count for that reason;
    they still show up in the report's distribution column, they just do not
    count as a level on their own.
    """
    if n <= 0 or not value_counts:
        return "near_degenerate"

    top_value_fraction = max(value_counts.values()) / n
    if top_value_fraction >= NEAR_DEGENERATE_THRESHOLD:
        return "near_degenerate"

    significant = [v for v, c in value_counts.items() if (c / n) >= MIN_LEVEL_FRACTION]
    k = len(significant)

    if k <= 1:
        # Nothing else in the stream reached the noise floor either; the
        # remaining mass is scattered outliers around a single dominant value.
        return "near_degenerate"
    if k == 2:
        return "binary"
    if k == 3:
        return "ternary"
    if k <= 6:
        return "five_level"
    return "continuous"
