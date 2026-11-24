"""Run events for the console's live architecture panel.

Each hop the agent really makes (model turn, tool call, tool result) is appended to the events
table keyed by run id; the console polls it. Writing is best-effort: a lost event never fails a
run, and nothing here is used for any authorization decision.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

TTL_SECONDS = 86400
log = logging.getLogger(__name__)


class Events:
    def __init__(self, run_id: str, source: str, table: str | None = None) -> None:
        self.run_id = run_id
        self.source = source
        self.table_name = table if table is not None else os.environ.get("EVENTS_TABLE", "")
        self._n = 0
        self._lock = threading.Lock()
        self._table = None

    def _tbl(self):
        if self._table is None and self.table_name:
            import boto3

            self._table = boto3.resource("dynamodb").Table(self.table_name)
        return self._table

    def emit(
        self,
        kind: str,
        node: str,
        status: str,
        label: str,
        edge: str | None = None,
        at: float | None = None,
        **extra: Any,
    ) -> dict:
        """``at`` (epoch seconds) places an event where it happened when it is learned later,
        e.g. a policy decision the agent only infers from the Gateway's reply."""
        with self._lock:
            self._n += 1
            n = self._n
        now = at if at is not None else time.time()
        item = {
            "run_id": self.run_id,
            "sk": f"{int(now * 1000):013d}#{self.source}#{n:03d}",
            "ts": int(now * 1000),
            "kind": kind,
            "node": node,
            "edge": edge,
            "status": status,
            "label": label[:120],
            "source": self.source,
            "expires": int(now) + TTL_SECONDS,
            **{k: v for k, v in extra.items() if v is not None},
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            tbl = self._tbl()
            if tbl is not None and self.run_id:
                tbl.put_item(Item=item)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break the run
            log.warning("run event %s not written: %s", kind, exc)
        return item
