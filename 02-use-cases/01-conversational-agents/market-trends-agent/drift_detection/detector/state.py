"""Persisted detector state.

A drift detector is only useful if it remembers. Baselines, warm-up progress, the
consecutive-alarm run length, and the drift latch all have to survive between
scheduled invocations, so they live in DynamoDB keyed by evaluator name.

The whole state is stored as one JSON string rather than as native DynamoDB
attributes. That is deliberate: the state is full of floats, DynamoDB has no float
type, and threading Decimal conversions through a numerical detector is a good way
to introduce a subtle bug in the arithmetic that matters most. State is always
read and written whole, so there is nothing to gain from field-level attributes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

# How many recent score keys to remember for deduplication. Scheduled runs use
# overlapping windows so late-arriving evaluations are not missed, which means the
# same record is seen more than once and must be recognised.
SEEN_KEYS_LIMIT = 500


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EvaluatorState:
    """Everything the detector for one evaluator needs to resume."""

    evaluator: str
    method: str = ""
    shape: str = ""

    detector: Dict[str, Any] = field(default_factory=dict)
    """Method state snapshot: baseline, ewma value, cusum accumulator."""

    gate: Dict[str, Any] = field(default_factory=dict)
    """Persistence and latch state."""

    watermark_ms: int = 0
    """Event timestamp of the newest score consumed."""

    seen_keys: List[str] = field(default_factory=list)
    """Recently consumed score keys, most recent last."""

    samples_seen: int = 0

    last_score: Optional[float] = None
    last_statistic: Optional[float] = None
    last_threshold: Optional[float] = None
    last_pressure: float = 0.0
    last_detail: str = ""
    warming_up: bool = True
    drifting: bool = False
    updated_at: str = ""

    def remember(self, keys: List[str]) -> None:
        self.seen_keys = (self.seen_keys + keys)[-SEEN_KEYS_LIMIT:]


class StateStore:
    """DynamoDB-backed store for per-evaluator detector state."""

    def __init__(self, table_name: str, dynamodb_client) -> None:
        self.table_name = table_name
        self.client = dynamodb_client

    def load(self, evaluator: str) -> Optional[EvaluatorState]:
        resp = self.client.get_item(
            TableName=self.table_name,
            Key={"evaluator": {"S": evaluator}},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item:
            return None

        raw = item.get("state", {}).get("S")
        if not raw:
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("state for %s is not valid JSON; starting fresh", evaluator)
            return None

        data.pop("evaluator", None)
        known = set(EvaluatorState.__dataclass_fields__)
        return EvaluatorState(evaluator=evaluator, **{k: v for k, v in data.items() if k in known})

    def save(self, state: EvaluatorState) -> None:
        state.updated_at = now_iso()
        self.client.put_item(
            TableName=self.table_name,
            Item={
                "evaluator": {"S": state.evaluator},
                "state": {"S": json.dumps(asdict(state))},
                "updated_at": {"S": state.updated_at},
                "drifting": {"BOOL": bool(state.drifting)},
            },
        )

    def load_all(self) -> Dict[str, EvaluatorState]:
        out: Dict[str, EvaluatorState] = {}
        kwargs: Dict[str, Any] = {"TableName": self.table_name}
        while True:
            resp = self.client.scan(**kwargs)
            for item in resp.get("Items", []):
                name = item.get("evaluator", {}).get("S")
                if not name:
                    continue
                loaded = self.load(name)
                if loaded:
                    out[name] = loaded
            key = resp.get("LastEvaluatedKey")
            if not key:
                break
            kwargs["ExclusiveStartKey"] = key
        return out

    def clear(self, evaluator: str) -> None:
        """Remove state entirely, so the detector rebuilds its baseline.

        This is the reset used after a demo: clearing the latch alone would leave
        a baseline that absorbed part of the degraded traffic.
        """
        self.client.delete_item(TableName=self.table_name, Key={"evaluator": {"S": evaluator}})

    def clear_latch(self, evaluator: str) -> bool:
        """Release the drift latch but keep the baseline.

        Used when an operator has acknowledged and fixed the cause and wants the
        alarm to go quiet without discarding warm-up progress.
        """
        state = self.load(evaluator)
        if state is None:
            return False
        state.gate = {"run_length": 0, "latched": False, "latched_at": ""}
        state.drifting = False
        self.save(state)
        return True
