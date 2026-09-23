"""Tests for the concurrent load path.

Concurrency must be invisible in the output: the same records, the same order, the same counts,
whatever the worker count. These tests pin that, plus the thread-safety of the shared target client
cache and the memory bound on batching.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.jobs.transform_load import (
    RecordOutcome,
    TargetClientPool,
    _iter_batches,
    _iter_outcomes,
)


def outcome_for(index: int) -> RecordOutcome:
    return RecordOutcome(
        mapping_id="map-a",
        source_object=f"obj-{index}",
        old_record_id=f"old-{index}",
        status="SUCCEEDED",
        processed_at="2026-07-01T00:00:00Z",
        action="created",
        new_record_id=f"new-{index}",
    )


class Batching(unittest.TestCase):
    def test_splits_into_fixed_size_batches(self):
        self.assertEqual(list(_iter_batches(range(5), 2)), [[0, 1], [2, 3], [4]])

    def test_exact_multiple_has_no_trailing_empty_batch(self):
        self.assertEqual(list(_iter_batches(range(4), 2)), [[0, 1], [2, 3]])

    def test_empty_input_yields_nothing(self):
        self.assertEqual(list(_iter_batches([], 3)), [])

    def test_batching_is_lazy_so_memory_stays_bounded(self):
        consumed = []

        def source():
            for index in range(6):
                consumed.append(index)
                yield index

        batches = _iter_batches(source(), 2)
        next(batches)
        # Only the first batch has been pulled from the (potentially huge) record stream.
        self.assertEqual(consumed, [0, 1])


class OutcomeOrdering(unittest.TestCase):
    """Reports must be deterministic, so results are emitted in input order."""

    def _records(self, count):
        return [(f"obj-{i}", {"mappingId": "map-a", "i": i}) for i in range(count)]

    def _worker_with_jitter(self):
        def worker(source_key, envelope):
            # Later records finish first, so completion order differs from input order.
            time.sleep(0.005 * (5 - envelope["i"] % 6))
            return outcome_for(envelope["i"])

        return worker

    def test_sequential_path_preserves_order(self):
        results = list(_iter_outcomes(self._records(6), self._worker_with_jitter(), concurrency=1))
        self.assertEqual([r.old_record_id for r in results], [f"old-{i}" for i in range(6)])

    def test_concurrent_path_preserves_input_order(self):
        results = list(_iter_outcomes(self._records(6), self._worker_with_jitter(), concurrency=4))
        self.assertEqual([r.old_record_id for r in results], [f"old-{i}" for i in range(6)])

    def test_same_results_at_every_concurrency(self):
        def worker(source_key, envelope):
            return outcome_for(envelope["i"])

        baseline = [r.old_record_id for r in _iter_outcomes(self._records(9), worker, concurrency=1)]
        for concurrency in (2, 4, 8, 16):
            results = [r.old_record_id for r in _iter_outcomes(self._records(9), worker, concurrency=concurrency)]
            self.assertEqual(results, baseline, f"concurrency={concurrency} changed the output")

    def test_work_actually_overlaps(self):
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def worker(source_key, envelope):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.02)
            with lock:
                active["now"] -= 1
            return outcome_for(envelope["i"])

        list(_iter_outcomes(self._records(8), worker, concurrency=4))
        self.assertGreater(active["peak"], 1, "records were not processed concurrently")

    def test_a_failing_record_does_not_stop_the_others(self):
        def worker(source_key, envelope):
            result = outcome_for(envelope["i"])
            if envelope["i"] == 2:
                result.status = "FAILED"
                result.error = "boom"
            return result

        results = list(_iter_outcomes(self._records(5), worker, concurrency=3))
        self.assertEqual(len(results), 5)
        self.assertEqual([r.succeeded for r in results], [True, True, False, True, True])


class ClientPoolSafety(unittest.TestCase):
    """One target client per distinct target, built exactly once even under concurrent use."""

    def setUp(self):
        import migration_common.jobs.transform_load as module

        self.module = module
        self._original_client = module.TargetRegistryClient
        self._original_invoker = module.invoker_for_endpoint
        self.constructions: list[str] = []
        guard = threading.Lock()

        def fake_client(invoker, api_config, region):
            with guard:
                self.constructions.append(region)
            time.sleep(0.01)  # widen the window a racing caller could slip through
            return f"client-{region}"

        module.TargetRegistryClient = fake_client
        module.invoker_for_endpoint = lambda endpoint, run_id, purpose: "invoker"
        self.addCleanup(self._restore)
        self.pool = TargetClientPool({"serviceName": "agent-registry-control"}, "run-1")

    def _restore(self):
        self.module.TargetRegistryClient = self._original_client
        self.module.invoker_for_endpoint = self._original_invoker

    def test_repeated_calls_reuse_one_client(self):
        first = self.pool.for_target({"region": "us-east-1"})
        second = self.pool.for_target({"region": "us-east-1"})
        self.assertIs(first, second)
        self.assertEqual(self.constructions, ["us-east-1"])

    def test_distinct_targets_get_distinct_clients(self):
        self.pool.for_target({"region": "us-east-1"})
        self.pool.for_target({"region": "us-west-2"})
        self.pool.for_target({"region": "us-east-1", "roleArn": "arn:aws:iam::111122223333:role/R"})
        self.assertEqual(self.constructions, ["us-east-1", "us-west-2", "us-east-1"])

    def test_concurrent_callers_build_it_only_once(self):
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(self.pool.for_target({"region": "us-east-1"})))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.constructions, ["us-east-1"], "client was built more than once")
        self.assertEqual(len(set(results)), 1, "threads received different clients")


if __name__ == "__main__":
    unittest.main()
