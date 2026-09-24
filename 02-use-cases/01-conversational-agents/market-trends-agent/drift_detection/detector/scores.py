"""Reading evaluator scores out of AgentCore Evaluations.

Source of truth is the evaluation results log group, not the CloudWatch metrics.

Both exist. AgentCore Evaluations writes one Embedded Metric Format record per
evaluation into

    /aws/bedrock-agentcore/evaluations/results/<onlineEvaluationConfigId>

and the embedded metric block publishes the same score into the
Bedrock-AgentCore/Evaluations namespace. The metrics are the convenient path and
the wrong one here, for two reasons:

  Aggregation. A metric datapoint is an aggregate over a period. The detection
  methods are defined over individual scores, so reading Average over a period
  silently changes the statistic being monitored, and it changes it by an amount
  that depends on how much traffic happened to land in that period.

  Identity. A scheduled detector must not consume the same score twice, and must
  not miss one that arrived late. Log records carry a session id and a timestamp,
  which gives a stable key to deduplicate on. Metric datapoints do not.

So this module reads records and treats the metrics as the operator-facing view.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

LOG = logging.getLogger(__name__)

RESULTS_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/evaluations/results/"

# Attribute keys on the evaluation result record. Verified against real records.
_ATTR_NAME = "gen_ai.evaluation.name"
_ATTR_SCORE = "gen_ai.evaluation.score.value"
_ATTR_LABEL = "gen_ai.evaluation.score.label"
_ATTR_EXPLANATION = "gen_ai.evaluation.explanation"
_ATTR_SESSION = "session.id"
_ATTR_RESPONSE = "gen_ai.response.id"
_ATTR_LEVEL = "aws.bedrock_agentcore.evaluation_level"


def results_log_group(online_eval_config_id: str) -> str:
    return f"{RESULTS_LOG_GROUP_PREFIX}{online_eval_config_id}"


@dataclass(frozen=True)
class Score:
    """One evaluator's verdict on one trace or session."""

    evaluator: str
    value: float
    label: str
    session_id: str
    timestamp_ms: int
    response_id: str = ""
    level: str = ""
    explanation: str = ""

    @property
    def key(self) -> str:
        """Stable identity for deduplication.

        Session id plus timestamp is not unique: AgentCore stamps every trace-level
        evaluation in a session with that session's start time, so all three turns
        in a three-turn session share one timeUnixNano. Deduplicating on
        evaluator|session_id|timestamp_ms alone collapses those three real, distinct
        scores down to whichever one the API happened to return first, silently
        discarding the other two. response_id is unique per turn and is what
        actually distinguishes them; fall back to timestamp_ms only for the rare
        record that has no response_id (SESSION-level evaluators, which produce one
        record per session and have nothing to disambiguate against anyway).
        """
        disambiguator = self.response_id or str(self.timestamp_ms)
        return f"{self.evaluator}|{self.session_id}|{disambiguator}"


def parse_record(message: str, timestamp_ms: int) -> Optional[Score]:
    """Parse one log record into a Score, or None if it is not a result record."""
    try:
        rec = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return None

    attrs = rec.get("attributes") or {}
    name = attrs.get(_ATTR_NAME)
    if name is None or _ATTR_SCORE not in attrs:
        return None

    try:
        value = float(attrs[_ATTR_SCORE])
    except (TypeError, ValueError):
        return None

    # Prefer the record's own event time over the log ingestion time. Evaluation
    # runs minutes after the session, and ordering by ingestion time would
    # scramble the stream the detectors see.
    ts = timestamp_ms
    if isinstance(rec.get("timeUnixNano"), (int, float)):
        ts = int(rec["timeUnixNano"] // 1_000_000)

    return Score(
        evaluator=str(name),
        value=value,
        label=str(attrs.get(_ATTR_LABEL, "")),
        session_id=str(attrs.get(_ATTR_SESSION) or attrs.get(_ATTR_RESPONSE) or ""),
        timestamp_ms=ts,
        response_id=str(attrs.get(_ATTR_RESPONSE, "")),
        level=str(attrs.get(_ATTR_LEVEL, "")),
        explanation=str(attrs.get(_ATTR_EXPLANATION, "")),
    )


def fetch_scores(
    logs_client,
    online_eval_config_id: str,
    start_time_ms: int,
    end_time_ms: int,
    seen_keys: Optional[Set[str]] = None,
) -> List[Score]:
    """Read evaluation results in a window, oldest first.

    Returns scores sorted by their own event timestamp so the detectors observe
    them in the order they were produced. Anything already in seen_keys is
    dropped, which is what makes the caller safe to run on a schedule with
    overlapping windows.
    """
    log_group = results_log_group(online_eval_config_id)
    seen = seen_keys or set()
    out: List[Score] = []
    token: Optional[str] = None

    while True:
        kwargs = {
            "logGroupName": log_group,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
        }
        if token:
            kwargs["nextToken"] = token

        try:
            resp = logs_client.filter_log_events(**kwargs)
        except logs_client.exceptions.ResourceNotFoundException:
            LOG.warning(
                "results log group %s does not exist yet; no evaluations have run",
                log_group,
            )
            return []

        for event in resp.get("events", []):
            score = parse_record(event.get("message", ""), int(event.get("timestamp", 0)))
            if score is None or score.key in seen:
                continue
            seen.add(score.key)
            out.append(score)

        token = resp.get("nextToken")
        if not token:
            break

    out.sort(key=lambda s: (s.timestamp_ms, s.evaluator))
    return out


def group_by_evaluator(scores: Iterable[Score]) -> Dict[str, List[Score]]:
    grouped: Dict[str, List[Score]] = {}
    for s in scores:
        grouped.setdefault(s.evaluator, []).append(s)
    for items in grouped.values():
        items.sort(key=lambda s: s.timestamp_ms)
    return grouped
