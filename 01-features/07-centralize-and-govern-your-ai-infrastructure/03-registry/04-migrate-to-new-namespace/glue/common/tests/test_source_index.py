"""Tests for how the loader matches a record to an existing target record, and for the cost of it.

Three routes, tried in order: the target recordId a previous run recorded for this source record, then
name(+recordVersion), then -- for a record synchronized from a URL, whose name the service rewrote during
synchronization -- its descriptor sources.

The last of those used to be done with a per-record registry scan, which made a load quadratic:
with the registry filling up as the load progressed, record *k* read back the *k-1* records already
created. These tests pin both the behaviour and the call count, because the behaviour alone looked
fine at three records and fell over at a thousand.

The first route exists for renames. A record renamed in Preview between two runs is matched by
neither its name nor -- unless it is URL-synchronized -- its descriptor source, so without the
recorded id it would be migrated a second time, leaving the record it was migrated to the first time
behind as an orphan. Where that id is stored is a state question; see test_watermark.py.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import registry_api

REGISTRY = "reg-target"


def source(url: str) -> dict:
    return {"fromUrl": {"url": url}}


def desired(url: str, *, name: str | None = None, version: str | None = None) -> dict:
    # Each record carries its own name now (the target name is the source record's name), so the fixture
    # derives one from the URL unless a test is specifically exercising a name mismatch.
    name = name or "svc-" + url.rstrip("/").rsplit("/", 1)[-1]
    record = {
        "name": name,
        "displayName": name,
        "recordType": "MCP",
        "descriptors": {"mcpServer": {"data": "payload", "source": source(url)}},
    }
    if version is not None:
        record["recordVersion"] = version
    return record


def existing(record_id: str, url: str, *, name: str = "renamed-by-sync", version: str | None = None) -> dict:
    record = dict(desired(url, name=name, version=version))
    record["recordId"] = record_id
    record["status"] = "DRAFT"
    return record


def plain(name: str, *, record_id: str | None = None, version: str | None = None) -> dict:
    """A record with no descriptor source, so only the name or the recorded id can match it."""
    record = {
        "name": name,
        "displayName": name,
        "recordType": "CUSTOM",
        "descriptors": {"custom": {"data": "{}"}},
    }
    if version is not None:
        record["recordVersion"] = version
    if record_id is not None:
        record["recordId"] = record_id
        record["status"] = "DRAFT"
    return record


def _unwrap_patch(value):
    """Strip the target registry's `optionalValue` PATCH wrappers, the way the service does when it stores a record."""
    if isinstance(value, dict):
        if set(value) == {"optionalValue"}:
            return _unwrap_patch(value["optionalValue"])
        return {key: _unwrap_patch(child) for key, child in value.items()}
    return value


class CountingClient(registry_api.TargetRegistryClient):
    """A target client with the transport replaced by an in-memory registry that counts calls."""

    def __init__(self, records: list[dict], *, registry: str = REGISTRY) -> None:
        self.records = {(registry, record["recordId"]): record for record in records}
        self.calls = {"list": 0, "get": 0, "create": 0, "update": 0}
        self._request_config = {
            "filtersField": "filters",
            "pageTokenField": "nextToken",
            "pageSizeField": "maxResults",
            "pageSize": 2,
        }
        self._response_config = {
            "itemsPath": "registryRecords",
            "nextTokenPath": "nextToken",
            "recordIdPath": "recordId",
            "recordArnPath": "recordArn",
            "recordNamePath": "name",
            "recordVersionPath": "recordVersion",
        }
        self._poll_config = {"maxAttempts": 3, "intervalSeconds": 0}
        # Adopt the real poll budgets instead of hand-copying them, so this double cannot drift
        # from the class it stands in for.
        self._configure_poll_budgets()
        self._in_progress_statuses = {"CREATING", "UPDATING"}
        self._failure_statuses = {"CREATE_FAILED", "UPDATE_FAILED"}
        self._success_statuses = {"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"}
        self._source_index_by_registry = {}
        self._source_index_lock = threading.Lock()
        self._source_index_build_locks = {}
        self._pending_source_identities = {}
        self._claimed_names = {}
        self._claimed_names_lock = threading.Lock()
        self._claimed_targets = {}
        self._claimed_targets_lock = threading.Lock()
        self._created = 0

    def _call(self, *, route_name, registry_id, record_id, body):
        self.calls[route_name] += 1
        if route_name == "list":
            filters = body.get("filters") or []
            if filters:  # name lookup
                wanted = filters[0]["values"][0]
                items = [
                    {"recordId": r["recordId"], "name": r["name"], "recordVersion": r.get("recordVersion")}
                    for (owner, _record_id), r in self.records.items()
                    if owner == registry_id and r["name"] == wanted
                ]
                return {"registryRecords": items}
            # Unfiltered scan, paginated: summaries carry no descriptors, as in the new version.
            page_size = int(body.get("maxResults", 2))
            token = int(body.get("nextToken", 0) or 0)
            ordered = [r for (owner, _rid), r in self.records.items() if owner == registry_id]
            page = ordered[token : token + page_size]
            response = {
                "registryRecords": [
                    {"recordId": r["recordId"], "name": r["name"], "recordVersion": r.get("recordVersion")}
                    for r in page
                ]
            }
            if token + page_size < len(ordered):
                response["nextToken"] = str(token + page_size)
            return response
        if route_name == "create":
            self._created += 1
            new_id = f"rec-new-{self._created}"
            stored = dict(body)
            stored.pop("clientToken", None)
            stored["recordId"] = new_id
            stored["status"] = "DRAFT"
            self.records[(registry_id, new_id)] = stored
            return {
                "recordArn": f"arn:aws:agent-registry:us-east-1:1:registry/{registry_id}/record/{new_id}",
                "status": "CREATING",
            }
        if route_name == "update":
            stored = self.records[(registry_id, record_id)]
            # PATCH semantics: field absent = no change, {} = unset, {"optionalValue": v} = set to v,
            # nested arbitrarily deep. The service stores the unwrapped values, so the fake must too.
            for field, wrapper in body.items():
                if field in {"name", "recordType", "triggerSynchronization"}:
                    stored[field] = wrapper if field != "triggerSynchronization" else stored.get(field)
                    continue
                if wrapper == {}:
                    stored.pop(field, None)
                else:
                    stored[field] = _unwrap_patch(wrapper)
            stored["status"] = "DRAFT"
            return {"recordId": record_id, "status": "UPDATING"}
        raise AssertionError(f"unexpected route {route_name}")

    def _get_record(self, *, registry_id, record_id):
        self.calls["get"] += 1
        try:
            return self.records[(registry_id, record_id)]
        except KeyError:
            # What the service answers for a record that is not there, error code included, because
            # that code is how the client tells "deleted in the target" from "cannot read it".
            raise registry_api.RegistryApiError(
                f"Target API call agent-registry.get failed: ResourceNotFoundException: {record_id}",
                error_code="ResourceNotFoundException",
            ) from None


class SourceIdentityMatching(unittest.TestCase):
    def test_a_renamed_record_is_recognised_by_its_source(self):
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a")])
        result = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a"))
        self.assertEqual(result.action, "existing")
        self.assertEqual(result.new_record_id, "rec-1")
        self.assertEqual(client.calls["create"], 0, "a renamed record must not be duplicated")

    def test_a_different_source_is_a_new_record(self):
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a")])
        result = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/b"))
        self.assertEqual(result.action, "created")

    def test_a_different_record_version_is_a_new_record(self):
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a", version="1.0")])
        result = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a", version="2.0"))
        self.assertEqual(result.action, "created")

    def test_absent_and_empty_record_versions_are_the_same_thing(self):
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a", version="")])
        result = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a"))
        self.assertEqual(result.action, "existing")

    def test_two_existing_records_sharing_an_identity_are_reported_not_guessed(self):
        client = CountingClient(
            [
                existing("rec-1", "https://mcp.example.com/a", name="one"),
                existing("rec-2", "https://mcp.example.com/a", name="two"),
            ]
        )
        with self.assertRaisesRegex(registry_api.RegistryApiError, "multiple records with the same"):
            client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a"))

    def test_a_record_with_no_source_never_triggers_a_scan(self):
        client = CountingClient([])
        client.upsert(
            registry_id=REGISTRY,
            record={
                "name": "plain",
                "displayName": "plain",
                "recordType": "CUSTOM",
                "descriptors": {"custom": {"data": "payload"}},
            },
        )
        # One name lookup, no unfiltered scan, no per-record Gets beyond the post-write poll.
        self.assertEqual(client.calls["list"], 1)


class NameCollisionCannotOverwrite(unittest.TestCase):
    """Two source records sharing a name must not silently become one target record.

    The target name is the source record's own name, and Preview never required names to be unique, so
    this is reachable with real data. Without the claim check the second record matches the first by
    name and *updates* it -- the run reports created: 1, updated: 1 and the first record's content is
    gone.
    """

    def _record(self, payload: str, *, name: str = "payments-mcp") -> dict:
        return {
            "name": name,
            "displayName": name,
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": payload}},
        }

    def test_a_second_source_record_with_the_same_name_is_refused(self):
        client = CountingClient([])
        first = client.upsert(registry_id=REGISTRY, record=self._record('{"v":"A"}'), source_record_id="rec-A")
        self.assertEqual(first.action, "created")

        with self.assertRaises(registry_api.RegistryApiError) as ctx:
            client.upsert(registry_id=REGISTRY, record=self._record('{"v":"B"}'), source_record_id="rec-B")
        message = str(ctx.exception)
        self.assertIn("rec-A", message)
        self.assertIn("rec-B", message)
        self.assertIn("overwrite", message)
        self.assertIn("re-extract", message)

        # The first record's content survives, and no update was issued against it.
        self.assertEqual(client.calls["update"], 0)
        self.assertEqual(client.records[(REGISTRY, "rec-new-1")]["descriptors"]["custom"]["data"], '{"v":"A"}')

    def test_the_same_source_record_can_be_processed_twice(self):
        # Idempotent replay must still work: it is the same record, not a collision.
        client = CountingClient([])
        first = client.upsert(registry_id=REGISTRY, record=self._record('{"v":"A"}'), source_record_id="rec-A")
        second = client.upsert(registry_id=REGISTRY, record=self._record('{"v":"A"}'), source_record_id="rec-A")
        self.assertEqual(first.action, "created")
        self.assertEqual(second.action, "existing")
        self.assertEqual(client.calls["create"], 1)

    def test_the_same_name_with_a_distinct_record_version_is_allowed(self):
        client = CountingClient([])
        client.upsert(
            registry_id=REGISTRY,
            record=dict(self._record('{"v":"A"}'), recordVersion="1.0"),
            source_record_id="rec-A",
        )
        result = client.upsert(
            registry_id=REGISTRY,
            record=dict(self._record('{"v":"B"}'), recordVersion="2.0"),
            source_record_id="rec-B",
        )
        self.assertEqual(result.action, "created")
        self.assertEqual(client.calls["create"], 2)

    def test_the_same_name_in_a_different_registry_is_allowed(self):
        client = CountingClient([])
        client.upsert(registry_id="reg-one", record=self._record('{"v":"A"}'), source_record_id="rec-A")
        result = client.upsert(registry_id="reg-two", record=self._record('{"v":"B"}'), source_record_id="rec-B")
        self.assertEqual(result.action, "created")

    def test_a_caller_that_supplies_no_source_id_is_unaffected(self):
        client = CountingClient([])
        client.upsert(registry_id=REGISTRY, record=self._record('{"v":"A"}'))
        result = client.upsert(registry_id=REGISTRY, record=self._record('{"v":"A"}'))
        self.assertEqual(result.action, "existing")

    def test_concurrent_claims_on_one_name_leave_exactly_one_winner(self):
        client = CountingClient([])
        outcomes: list[str] = []
        lock = threading.Lock()

        def claim(index: int) -> None:
            try:
                client.upsert(
                    registry_id=REGISTRY,
                    record=self._record(f'{{"v":"{index}"}}'),
                    source_record_id=f"rec-{index}",
                )
                with lock:
                    outcomes.append("ok")
            except registry_api.RegistryApiError:
                with lock:
                    outcomes.append("refused")

        threads = [threading.Thread(target=claim, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(outcomes.count("refused"), 5, outcomes)
        self.assertEqual(client.calls["create"], 1)


class SharedSyncUrlCannotCollapseRecords(unittest.TestCase):
    """Source records syncing from one URL must not silently become one target record.

    This is the name-collision case's harder sibling, and it was found live rather than reasoned
    about. Four Preview records pointing at one MCP endpoint -- distinct names, distinct content --
    all landed on a single target record: one created it and the rest *updated* it, so the run reported
    three successes and the id-crosswalk mapped four old ids to one new id.

    The name claim cannot catch it. These records ask for different names, so they pass that check.
    The service then overwrites the name and recordVersion of each one with the values from the fetched
    document, the name lookup misses (the service renamed what we created), and source-identity
    matching -- which deliberately excludes the name -- resolves every one of them to the first
    record. Only a claim on the *resolved target record* closes it.
    """

    SHARED = "https://mcp.example.com/shared-upstream"

    def test_a_second_record_resolving_to_the_same_target_record_is_refused(self):
        # "renamed-by-sync" is what the service left behind, so neither incoming name matches it and
        # both fall through to source-identity matching -- exactly the live sequence.
        client = CountingClient([existing("rec-1", self.SHARED)])

        # The first one must succeed by matching rec-1 on source identity, not by name -- otherwise
        # this test would pass without ever reaching the path it is meant to cover.
        first = client.upsert(
            registry_id=REGISTRY,
            record=desired(self.SHARED, name="team-a-gateway"),
            source_record_id="rec-A",
        )
        self.assertEqual(first.new_record_id, "rec-1")
        self.assertEqual(client.calls["create"], 0)

        with self.assertRaises(registry_api.RegistryApiError) as ctx:
            client.upsert(
                registry_id=REGISTRY,
                record=desired(self.SHARED, name="team-b-gateway"),
                source_record_id="rec-B",
            )
        message = str(ctx.exception)
        self.assertIn("rec-A", message)
        self.assertIn("rec-B", message)
        self.assertIn("rec-1", message)
        self.assertIn("overwrite", message)
        # The refusal has to explain the cause, because the fix is in the source registry.
        self.assertIn("synchronize", message)

    def test_the_first_record_is_not_overwritten_by_the_refused_one(self):
        client = CountingClient([existing("rec-1", self.SHARED)])
        client.upsert(
            registry_id=REGISTRY,
            record=dict(desired(self.SHARED, name="team-a-gateway"), description="team A"),
            source_record_id="rec-A",
        )
        updates_after_first = client.calls["update"]

        with self.assertRaises(registry_api.RegistryApiError):
            client.upsert(
                registry_id=REGISTRY,
                record=dict(desired(self.SHARED, name="team-b-gateway"), description="team B"),
                source_record_id="rec-B",
            )
        # Refused before any write: team A's description survives untouched.
        self.assertEqual(client.calls["update"], updates_after_first)
        self.assertEqual(client.records[(REGISTRY, "rec-1")]["description"], "team A")

    def test_replaying_the_same_source_record_is_still_idempotent(self):
        # The guard keys on the source record, so a re-run of the same one is not a collision.
        client = CountingClient([existing("rec-1", self.SHARED)])
        for _ in range(3):
            result = client.upsert(
                registry_id=REGISTRY,
                record=desired(self.SHARED, name="team-a-gateway"),
                source_record_id="rec-A",
            )
        self.assertEqual(result.action, "existing")
        self.assertEqual(client.calls["create"], 0)

    def test_records_on_distinct_urls_are_unaffected(self):
        # The remedy the error message recommends has to actually work.
        client = CountingClient([])
        first = client.upsert(
            registry_id=REGISTRY,
            record=desired("https://mcp.example.com/a", name="team-a-gateway"),
            source_record_id="rec-A",
        )
        second = client.upsert(
            registry_id=REGISTRY,
            record=desired("https://mcp.example.com/b", name="team-b-gateway"),
            source_record_id="rec-B",
        )
        self.assertEqual([first.action, second.action], ["created", "created"])
        self.assertNotEqual(first.new_record_id, second.new_record_id)

    def test_concurrent_claims_on_one_target_record_leave_exactly_one_winner(self):
        # The load stage runs this client from several threads, so the check has to hold under a race.
        client = CountingClient([existing("rec-1", self.SHARED)])
        outcomes: list[str] = []
        lock = threading.Lock()

        def claim(index: int) -> None:
            try:
                client.upsert(
                    registry_id=REGISTRY,
                    record=dict(
                        desired(self.SHARED, name=f"team-{index}-gateway"),
                        description=f"team {index}",
                    ),
                    source_record_id=f"rec-{index}",
                )
                with lock:
                    outcomes.append("ok")
            except registry_api.RegistryApiError:
                with lock:
                    outcomes.append("refused")

        threads = [threading.Thread(target=claim, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(outcomes.count("refused"), 5, outcomes)


class IndexingCost(unittest.TestCase):
    """The whole point: the scan happens once per registry, not once per record."""

    def test_the_registry_is_indexed_once_for_a_whole_load(self):
        pre_existing = [existing(f"rec-{i}", f"https://mcp.example.com/{i}") for i in range(10)]
        client = CountingClient(pre_existing)

        for index in range(10, 40):
            client.upsert(registry_id=REGISTRY, record=desired(f"https://mcp.example.com/{index}"))

        # 30 name lookups (one per record) + exactly one paginated scan of the 10 pre-existing
        # records at pageSize 2 (5 pages).
        self.assertEqual(client.calls["create"], 30)
        self.assertEqual(client.calls["list"], 30 + 5)
        # One Get per pre-existing record while indexing, plus one poll per record written.
        self.assertEqual(client.calls["get"], 10 + 30)

    def test_call_count_grows_linearly_not_quadratically(self):
        def gets_for(record_count: int) -> int:
            client = CountingClient([])
            for index in range(record_count):
                client.upsert(registry_id=REGISTRY, record=desired(f"https://mcp.example.com/{index}"))
            return client.calls["get"]

        small, large = gets_for(10), gets_for(40)
        # Linear: 4x the records costs ~4x the calls. Quadratic would be ~16x.
        self.assertEqual(small, 10)
        self.assertEqual(large, 40)

    def test_a_record_written_during_the_run_is_not_created_twice(self):
        client = CountingClient([])
        first = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a"))
        # Same source arriving again (duplicated staged data) must resolve to the same record.
        second = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a", name="other-name"))
        self.assertEqual(first.action, "created")
        self.assertEqual(second.action, "existing")
        self.assertEqual(second.new_record_id, first.new_record_id)
        self.assertEqual(client.calls["create"], 1)

    def test_concurrent_loaders_share_one_index_build(self):
        client = CountingClient([existing(f"rec-{i}", f"https://mcp.example.com/{i}") for i in range(6)])
        errors: list[Exception] = []

        def load(index: int) -> None:
            try:
                client.upsert(registry_id=REGISTRY, record=desired(f"https://mcp.example.com/new-{index}"))
            except Exception as error:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(error)

        threads = [threading.Thread(target=load, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        # 8 name lookups + one shared scan (6 records at pageSize 2 = 3 pages), not 8 scans.
        self.assertEqual(client.calls["list"], 8 + 3)


class SourceIdentityFunction(unittest.TestCase):
    def test_records_without_a_source_have_no_identity(self):
        self.assertIsNone(
            registry_api._source_identity({"recordType": "CUSTOM", "descriptors": {"custom": {"data": "x"}}})
        )

    def test_identity_is_stable_and_order_independent(self):
        first = registry_api._source_identity(desired("https://a"))
        second = registry_api._source_identity(desired("https://a", name="different-name"))
        self.assertIsNotNone(first)
        self.assertEqual(first, second, "the name must not take part in source identity")

    def test_a_changed_description_does_not_break_the_match(self):
        # description is mutable metadata: including it in the identity meant editing a description
        # made the fallback miss and create a duplicate instead of updating the record.
        original = desired("https://a")
        edited = dict(desired("https://a"), description="now documented")
        self.assertEqual(registry_api._source_identity(original), registry_api._source_identity(edited))

    def test_a_changed_description_is_still_written_through(self):
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a")])
        result = client.upsert(
            registry_id=REGISTRY,
            record=dict(desired("https://mcp.example.com/a"), description="now documented"),
            source_record_id="rec-A",
        )
        # Matched the existing record rather than duplicating it, and updated it.
        self.assertEqual(result.action, "updated")
        self.assertEqual(client.calls["create"], 0)

    def test_identity_covers_additional_data_sources(self):
        base = desired("https://a")
        with_child = desired("https://a")
        with_child["descriptors"]["mcpServer"]["additionalData"] = {
            "tools": {"data": "t", "source": source("https://tools")}
        }
        self.assertNotEqual(registry_api._source_identity(base), registry_api._source_identity(with_child))


class RecordedIdTakesPrecedence(unittest.TestCase):
    """What the loader does with the target recordId an earlier run recorded for this source record."""

    def test_a_renamed_record_updates_the_record_it_was_migrated_to(self):
        # The whole point. The target record is still called what Preview called it last time; the
        # source record has since been renamed, so nothing but the recorded id can find it.
        client = CountingClient([plain("old-name", record_id="rec-1")])
        result = client.upsert(
            registry_id=REGISTRY,
            record=plain("new-name"),
            source_record_id="prev-1",
            known_record_id="rec-1",
        )
        self.assertEqual(result.action, "updated")
        self.assertEqual(result.new_record_id, "rec-1")
        self.assertEqual(client.calls["create"], 0, "a rename must not create a second target record")
        self.assertEqual(client.records[(REGISTRY, "rec-1")]["name"], "new-name")

    def test_without_the_recorded_id_the_same_rename_duplicates(self):
        # The behaviour before the id map existed, pinned so the fix cannot quietly regress.
        client = CountingClient([plain("old-name", record_id="rec-1")])
        result = client.upsert(registry_id=REGISTRY, record=plain("new-name"), source_record_id="prev-1")
        self.assertEqual(result.action, "created")
        self.assertEqual(client.calls["create"], 1)

    def test_the_recorded_id_is_checked_before_the_name(self):
        # Two target records: the one this source record was migrated to, and an unrelated one that
        # happens to hold the name this record now wants. The recorded id has to win, otherwise a
        # rename would start updating somebody else's record.
        client = CountingClient([plain("old-name", record_id="rec-1"), plain("new-name", record_id="rec-other")])
        result = client.upsert(
            registry_id=REGISTRY,
            record=plain("new-name"),
            source_record_id="prev-1",
            known_record_id="rec-1",
        )
        self.assertEqual(result.new_record_id, "rec-1")
        self.assertEqual(client.records[(REGISTRY, "rec-other")]["name"], "new-name")

    def test_an_unchanged_record_is_still_recognised_as_unchanged(self):
        # Matching by id must not turn a no-op into an update: the id resolves, the content matches,
        # and the record is left alone.
        client = CountingClient([plain("same-name", record_id="rec-1")])
        result = client.upsert(
            registry_id=REGISTRY,
            record=plain("same-name"),
            source_record_id="prev-1",
            known_record_id="rec-1",
        )
        self.assertEqual(result.action, "existing")
        self.assertEqual(client.calls["update"], 0)

    def test_a_recorded_record_deleted_in_the_target_falls_back_to_the_name(self):
        # Somebody deleted the migrated record in the target registry. The recorded id is stale, but
        # a record with that name is there, so it is the one to update -- not a third copy.
        client = CountingClient([plain("the-name", record_id="rec-live")])
        result = client.upsert(
            registry_id=REGISTRY,
            record=plain("the-name"),
            source_record_id="prev-1",
            known_record_id="rec-deleted",
        )
        self.assertEqual(result.new_record_id, "rec-live")
        self.assertEqual(client.calls["create"], 0)

    def test_a_recorded_record_deleted_in_the_target_is_reported_not_silently_replaced(self):
        client = CountingClient([])
        result = client.upsert(
            registry_id=REGISTRY,
            record=plain("the-name"),
            source_record_id="prev-1",
            known_record_id="rec-deleted",
        )
        self.assertEqual(result.action, "created")
        self.assertTrue(
            any("rec-deleted" in warning for warning in result.warnings),
            f"a stale recorded id must be reported: {result.warnings}",
        )

    def test_a_get_that_fails_for_any_other_reason_stops_the_record(self):
        # Only "it is not there" may be downgraded to a fallback. A permissions or throttling error
        # must not be read as "create a duplicate".
        class Denied(CountingClient):
            def _get_record(self, *, registry_id, record_id):
                raise registry_api.RegistryApiError(
                    "Target API call agent-registry.get failed: AccessDeniedException",
                    error_code="AccessDeniedException",
                )

        client = Denied([])
        with self.assertRaises(registry_api.RegistryApiError) as caught:
            client.upsert(
                registry_id=REGISTRY,
                record=plain("the-name"),
                source_record_id="prev-1",
                known_record_id="rec-1",
            )
        self.assertIn("AccessDenied", str(caught.exception))
        self.assertEqual(client.calls["create"], 0)

    def test_source_identity_matching_still_works_without_a_recorded_id(self):
        # The recorded id is an addition, not a replacement: a URL-synchronized record renamed by
        # the service is still recognised by its descriptor source on a first-ever run.
        client = CountingClient([existing("rec-1", "https://mcp.example.com/a")])
        result = client.upsert(registry_id=REGISTRY, record=desired("https://mcp.example.com/a"))
        self.assertEqual(result.new_record_id, "rec-1")


if __name__ == "__main__":
    unittest.main()
