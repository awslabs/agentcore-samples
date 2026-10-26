"""Offline wire-equivalence tests for the SDK-based Preview/target registry clients.

No network: a botocore ``before-send`` hook captures each signed request (proving the modeled
boto3 operations serialize to the exact REST method/URI/body the control planes expect, and
that SigV4 uses the model-derived signing name) and returns canned responses (proving the
clients parse SDK responses and drive iter_records/upsert unchanged). Also covers the
assumed-role refreshable-credentials path with a stubbed STS.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import ClassVar

# Assigned, not setdefault. With setdefault, a developer's own AWS_PROFILE / AWS_CONFIG_FILE / SSO
# session stayed in play and botocore took a different credential-resolution path while building
# clients -- including an IMDS attempt on a machine that has one. The assertions here are about
# request shape and credential scope, so the credentials must be the fixed, fake ones every time.
os.environ["AWS_ACCESS_KEY_ID"] = "AKIDEXAMPLE"
os.environ["AWS_SECRET_ACCESS_KEY"] = "secretkeyexample"  # nosec B105 # pragma: allowlist secret
os.environ["AWS_SESSION_TOKEN"] = "sessiontokenexample"  # nosec B105 -- fake, fixed test value
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
for _ambient in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
    os.environ.pop(_ambient, None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import datetime as dt

from botocore.awsrequest import AWSResponse
from botocore.credentials import RefreshableCredentials
from migration_common import aws_auth, registry_api

REGION = "us-east-1"
PREVIEW_API = {
    "transport": "sigv4RestJson",
    "serviceName": "bedrock-agentcore-control",
    "signingName": "bedrock-agentcore",
    "endpointUrlTemplate": "https://bedrock-agentcore-control.{region}.amazonaws.com",
    "listOperation": "ListRegistryRecords",
    "getOperation": "GetRegistryRecord",
    "routes": {
        "list": {"method": "GET", "path": "/registries/{registryId}/records", "expectedStatus": 200},
        "get": {"method": "GET", "path": "/registries/{registryId}/records/{recordId}", "expectedStatus": 200},
    },
    "request": {
        "registryIdField": "registryId",
        "recordIdField": "recordId",
        "pageTokenField": "nextToken",
        "pageSizeField": "maxResults",
        "pageSize": 100,
        "changedAfterField": None,
    },
    "response": {
        "itemsPath": "registryRecords",
        "nextTokenPath": "nextToken",
        "recordPath": None,
        "recordIdPath": "recordId",
        "updatedAtPath": "updatedAt",
        "recordTypePath": "descriptorType",
    },
}
TARGET_API = {
    "transport": "sigv4RestJson",
    "serviceName": "agent-registry-control",
    "signingName": "agent-registry",
    "endpointUrlTemplate": "https://agent-registry-control.{region}.api.aws",
    "routes": {
        "list": {"method": "POST", "path": "/registries/{registryId}/records-list", "expectedStatus": 200},
        "create": {"method": "POST", "path": "/registries/{registryId}/records", "expectedStatus": 202},
        "get": {"method": "GET", "path": "/registries/{registryId}/records/{recordId}", "expectedStatus": 200},
        "update": {"method": "PATCH", "path": "/registries/{registryId}/records/{recordId}", "expectedStatus": 202},
    },
    "request": {
        "filtersField": "filters",
        "pageTokenField": "nextToken",
        "pageSizeField": "maxResults",
        "pageSize": 100,
    },
    "response": {
        "itemsPath": "registryRecords",
        "nextTokenPath": "nextToken",
        "recordIdPath": "recordId",
        "recordArnPath": "recordArn",
        "recordNamePath": "name",
        "recordVersionPath": "recordVersion",
    },
    "poll": {
        "maxAttempts": 5,
        "intervalSeconds": 0,
        "inProgressStatuses": ["CREATING", "UPDATING"],
        # Mirrors the shipped default: every settled state, not just the one a new record lands in.
        "successStatuses": ["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"],
        "failureStatuses": ["CREATE_FAILED", "UPDATE_FAILED"],
    },
}


class _FakeRaw:
    def __init__(self, data: bytes):
        self._data = data

    def stream(self, *a, **k):
        yield self._data

    def read(self, *a, **k):
        return self._data


def _resp(status: int, body: dict) -> AWSResponse:
    return AWSResponse(
        "https://stub", status, {"Content-Type": "application/json"}, _FakeRaw(json.dumps(body).encode())
    )


def _conflict() -> AWSResponse:
    """The concurrent-update conflict the service returns for a status call that arrives mid-transition.

    Carries the error code in both the header and the body, which is where botocore's rest-json
    parser looks for it, so the client sees a real ``ConflictException`` rather than a bare 409.
    """
    body = {
        "__type": "ConflictException",
        "message": "Concurrent update detected. Please retry.",
    }
    return AWSResponse(
        "https://stub",
        409,
        {"Content-Type": "application/json", "x-amzn-errortype": "ConflictException"},
        _FakeRaw(json.dumps(body).encode()),
    )


def _req_body(request) -> dict:
    body = request.body
    if body is None or body == b"":
        return {}
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body) if body else {}


def _auth(request) -> str:
    value = request.headers.get("Authorization", "")
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)


def _capture(client, responder):
    captured = []

    def before_send(request, **kwargs):
        captured.append(request)
        return responder(request)

    client.meta.events.register("before-send", before_send)
    return captured


def _invoker():
    return aws_auth.AwsApiInvoker(role_arn=None, external_id=None, session_name="t")


class PollBudgets(unittest.TestCase):
    """``target.poll`` decides how long a write and a status transition are waited on.

    Status waits used to be hard-coded to ``min(maxAttempts, 15)``, so raising ``maxAttempts`` for a
    slowly-settling registry changed the record poll and silently did nothing here. The cap is now a
    named, overridable setting bounded by the record budget.
    """

    def _client(self, poll: dict) -> registry_api.TargetRegistryClient:
        return registry_api.TargetRegistryClient(_invoker(), {**TARGET_API, "poll": poll}, REGION)

    def test_defaults_come_from_the_module_constants(self):
        client = self._client({})
        self.assertEqual(client._poll_attempts, registry_api.DEFAULT_POLL_ATTEMPTS)
        self.assertEqual(client._status_poll_attempts, registry_api.DEFAULT_STATUS_POLL_ATTEMPTS)

    def test_a_raised_status_budget_is_honoured(self):
        """The point of the fix: this used to be pinned at 15 whatever the configuration said."""
        client = self._client({"maxAttempts": 90, "statusMaxAttempts": 40})
        self.assertEqual(client._status_poll_attempts, 40)

    def test_the_record_budget_still_caps_the_status_budget(self):
        """Shortening overall polling has to shorten status polling with it."""
        client = self._client({"maxAttempts": 3, "statusMaxAttempts": 40})
        self.assertEqual(client._status_poll_attempts, 3)

    def test_a_lower_status_budget_than_the_default_is_respected(self):
        client = self._client({"maxAttempts": 90, "statusMaxAttempts": 2})
        self.assertEqual(client._status_poll_attempts, 2)

    def test_bad_poll_settings_fail_at_construction_not_mid_load(self):
        """A malformed adapter must be refused before any record is written."""
        for poll in ({"maxAttempts": 0}, {"intervalSeconds": -1}):
            with self.assertRaises(registry_api.RegistryApiError) as raised:
                self._client(poll)
            self.assertIn("maxAttempts", str(raised.exception))


class PreviewDescribeRegistryWire(unittest.TestCase):
    """``describe_registry``, the registry-level read behind ``target-config``.

    Untested until now, while every record-level call was pinned. It is the sole input to
    ``target_registry.derive_create_registry_inputs``, so what it returns becomes the target registry an
    operator creates by hand.
    """

    def test_it_calls_get_registry_and_strips_response_metadata(self):
        client = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)
        captured = _capture(
            client._client,
            lambda request: _resp(
                200,
                {
                    "registryId": "reg-a",
                    "name": "src",
                    "status": "READY",
                    "authorizerType": "CUSTOM_JWT",
                },
            ),
        )

        described = client.describe_registry(registry_id="reg-a")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].method, "GET")
        self.assertIn("reg-a", captured[0].url)
        # Signed with the model-derived signing name, like every other call.
        self.assertIn("bedrock-agentcore", _auth(captured[0]))
        # ResponseMetadata is botocore bookkeeping; it must not leak into a derived payload.
        self.assertNotIn("ResponseMetadata", described)
        self.assertEqual(described["name"], "src")
        self.assertEqual(described["authorizerType"], "CUSTOM_JWT")

    def test_a_service_error_surfaces_as_a_registry_api_error(self):
        client = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)
        _capture(
            client._client,
            lambda request: _resp(404, {"__type": "ResourceNotFoundException", "message": "no such registry"}),
        )
        with self.assertRaises(registry_api.RegistryApiError) as raised:
            client.describe_registry(registry_id="missing")
        # target_registry reports this per mapping, so the message has to carry the service's own.
        self.assertIn("no such registry", str(raised.exception))
        self.assertEqual(raised.exception.error_code, "ResourceNotFoundException")


class PreviewClientWire(unittest.TestCase):
    def test_list_get_wire_and_iteration(self):
        client = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)
        boto = client._client
        self.assertEqual(boto.meta.service_model.metadata.get("signingName"), "bedrock-agentcore")

        page1 = {
            "registryRecords": [{"recordId": "r1", "descriptorType": "MCP", "updatedAt": "2026-01-01T00:00:00Z"}],
            "nextToken": "TOK2",
        }
        page2 = {
            "registryRecords": [{"recordId": "r2", "descriptorType": "CUSTOM", "updatedAt": "2026-01-02T00:00:00Z"}]
        }
        full = {
            "r1": {
                "recordId": "r1",
                "name": "alpha",
                "descriptorType": "MCP",
                "descriptors": {"mcp": {"server": {"inlineContent": "x"}}},
                "updatedAt": "2026-01-01T00:00:00Z",
                "status": "ENABLED",
            },
            "r2": {
                "recordId": "r2",
                "name": "beta",
                "descriptorType": "CUSTOM",
                "descriptors": {"custom": {"inlineContent": "y"}},
                "updatedAt": "2026-01-02T00:00:00Z",
                "status": "ENABLED",
            },
        }

        def responder(request):
            if request.method == "GET" and "/records/" in request.url:
                rid = request.url.split("/records/")[1].split("?")[0]
                return _resp(200, full[rid])
            if "nextToken=TOK2" in request.url:
                return _resp(200, page2)
            return _resp(200, page1)

        cap = _capture(boto, responder)
        records = list(client.iter_records(registry_id="reg-1", load_mode="FULL", changed_after=None))

        self.assertEqual([r.old_record_id for r in records], ["r1", "r2"])
        self.assertEqual(records[0].record.get("name"), "alpha")
        self.assertTrue(all("ResponseMetadata" not in r.record for r in records))

        list_reqs = [r for r in cap if r.method == "GET" and "/records?" in r.url + "?" and "/records/" not in r.url]
        self.assertTrue(any("maxResults=100" in r.url for r in list_reqs))
        self.assertTrue(any("nextToken=TOK2" in r.url for r in cap))
        self.assertTrue(any(r.method == "GET" and r.url.endswith("/records/r1") for r in cap))
        self.assertIn("/bedrock-agentcore/aws4_request", _auth(cap[0]))


class PreviewIncrementalFiltering(unittest.TestCase):
    """INCREMENTAL extraction filters on the source updatedAt against the cutoff."""

    def _records(self, cutoff):
        client = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)
        boto = client._client
        summaries = [
            {"recordId": "old", "descriptorType": "CUSTOM", "updatedAt": "2026-06-01T00:00:00Z"},
            {"recordId": "new", "descriptorType": "CUSTOM", "updatedAt": "2026-07-10T00:00:00Z"},
            {"recordId": "notime", "descriptorType": "CUSTOM"},
        ]
        full = {
            "old": {"recordId": "old", "name": "old", "descriptorType": "CUSTOM", "updatedAt": "2026-06-01T00:00:00Z"},
            "new": {"recordId": "new", "name": "new", "descriptorType": "CUSTOM", "updatedAt": "2026-07-10T00:00:00Z"},
            "notime": {"recordId": "notime", "name": "notime", "descriptorType": "CUSTOM"},
        }

        def responder(request):
            if "/records/" in request.url:
                rid = request.url.split("/records/")[1].split("?")[0]
                return _resp(200, full[rid])
            return _resp(200, {"registryRecords": summaries})

        _capture(boto, responder)
        extracted = list(client.iter_records(registry_id="reg-1", load_mode="INCREMENTAL", changed_after=cutoff))
        return [r.old_record_id for r in extracted], client.warnings

    def test_records_older_than_the_cutoff_are_skipped(self):
        ids, _ = self._records("2026-07-01T00:00:00Z")
        self.assertNotIn("old", ids)
        self.assertIn("new", ids)

    def test_records_without_a_timestamp_are_kept_with_a_warning(self):
        ids, warnings = self._records("2026-07-01T00:00:00Z")
        # Including them is deliberate: a missing timestamp must never silently lose data.
        self.assertIn("notime", ids)
        self.assertTrue(any("no updated timestamp" in w for w in warnings), warnings)

    def test_full_load_keeps_everything(self):
        client = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)
        boto = client._client
        summaries = [
            {"recordId": "old", "descriptorType": "CUSTOM", "updatedAt": "2026-06-01T00:00:00Z"},
            {"recordId": "new", "descriptorType": "CUSTOM", "updatedAt": "2026-07-10T00:00:00Z"},
        ]
        full = {rid: {"recordId": rid, "name": rid, "descriptorType": "CUSTOM"} for rid in ("old", "new")}

        def responder(request):
            if "/records/" in request.url:
                rid = request.url.split("/records/")[1].split("?")[0]
                return _resp(200, full[rid])
            return _resp(200, {"registryRecords": summaries})

        _capture(boto, responder)
        ids = [r.old_record_id for r in client.iter_records(registry_id="reg-1", load_mode="FULL", changed_after=None)]
        self.assertEqual(sorted(ids), ["new", "old"])


class TargetClientWire(unittest.TestCase):
    def test_create_path_wire_and_parse(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        self.assertEqual(boto.meta.service_model.metadata.get("signingName"), "agent-registry")

        desired = {
            "name": "svc-alpha",
            "displayName": "Svc Alpha",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "payload"}},
            "recordVersion": "1",
        }
        new_arn = "arn:aws:agent-registry:us-east-1:123456789012:registry/reg-1/record/rec-new"

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": []})
            if m == "POST" and url.endswith("/records"):
                return _resp(202, {"recordArn": new_arn, "status": "CREATING"})
            if m == "GET" and url.endswith("/records/rec-new"):
                return _resp(
                    200,
                    {
                        "recordId": "rec-new",
                        "status": "DRAFT",
                        "name": "svc-alpha",
                        "displayName": "Svc Alpha",
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": "payload"}},
                        "recordVersion": "1",
                    },
                )
            raise AssertionError(f"unexpected target request {m} {url}")

        cap = _capture(boto, responder)
        result = client.upsert(registry_id="reg-1", record=desired)

        self.assertEqual(result.action, "created")
        self.assertEqual(result.new_record_id, "rec-new")

        list_req = next(r for r in cap if r.method == "POST" and r.url.endswith("/records-list"))
        create_req = next(r for r in cap if r.method == "POST" and r.url.endswith("/records"))
        self.assertTrue(list_req.url.endswith("/registries/reg-1/records-list"))
        self.assertEqual(_req_body(list_req).get("filters"), [{"name": "name", "values": ["svc-alpha"]}])
        self.assertNotIn("registryId", _req_body(list_req))
        cb = _req_body(create_req)
        self.assertEqual(cb.get("name"), "svc-alpha")
        self.assertEqual(cb.get("recordType"), "CUSTOM")
        self.assertEqual(cb.get("descriptors"), {"custom": {"data": "payload"}})
        self.assertIn("clientToken", cb)
        self.assertNotIn("registryId", cb)
        self.assertIn("/agent-registry/aws4_request", _auth(create_req))

    def test_update_path_uses_patch_and_optional_value_wrappers(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        desired = {
            "name": "svc-beta",
            "displayName": "New Label",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "new-payload"}},
            "recordVersion": "1",
        }
        state = {"updated": False}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(
                    200, {"registryRecords": [{"recordId": "rec-x", "name": "svc-beta", "recordVersion": "1"}]}
                )
            if m == "GET" and url.endswith("/records/rec-x"):
                data = "new-payload" if state["updated"] else "old-payload"
                label = "New Label" if state["updated"] else "Old Label"
                return _resp(
                    200,
                    {
                        "recordId": "rec-x",
                        "status": "DRAFT",
                        "name": "svc-beta",
                        "displayName": label,
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": data}},
                        "recordVersion": "1",
                    },
                )
            if m == "PATCH" and url.endswith("/records/rec-x"):
                state["updated"] = True
                return _resp(202, {"recordId": "rec-x", "status": "UPDATING"})
            raise AssertionError(f"unexpected target request {m} {url}")

        cap = _capture(boto, responder)
        result = client.upsert(registry_id="reg-1", record=desired)

        self.assertEqual(result.action, "updated")
        patch_req = next(r for r in cap if r.method == "PATCH")
        self.assertTrue(patch_req.url.endswith("/registries/reg-1/records/rec-x"))
        pb = _req_body(patch_req)
        self.assertIn("optionalValue", pb.get("descriptors", {}))
        self.assertEqual(pb.get("displayName"), {"optionalValue": "New Label"})
        self.assertEqual(pb.get("name"), "svc-beta")


class NameCollisionGuard(unittest.TestCase):
    """The load-time backstop for two source records sharing one target name.

    Extraction no longer detects this up front, so this guard is now the only thing standing
    between two colliding source records and one of them silently overwriting the other: the
    first upsert to ask for a name succeeds, and the second is refused rather than being allowed
    to update -- and so overwrite -- the first one's content.
    """

    def test_a_second_source_record_claiming_the_same_name_is_refused(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        new_arn = "arn:aws:agent-registry:us-east-1:123456789012:registry/reg-1/record/rec-new"

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": []})
            if m == "POST" and url.endswith("/records"):
                return _resp(202, {"recordArn": new_arn, "status": "CREATING"})
            if m == "GET" and url.endswith("/records/rec-new"):
                return _resp(
                    200,
                    {
                        "recordId": "rec-new",
                        "status": "DRAFT",
                        "name": "server-1",
                        "displayName": "server-1",
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": "payload"}},
                    },
                )
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)
        record = {
            "name": "server-1",
            "displayName": "server-1",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "payload"}},
        }
        first = client.upsert(registry_id="reg-1", record=record, source_record_id="rec-1")
        self.assertEqual(first.action, "created")

        with self.assertRaisesRegex(registry_api.RegistryApiError, "rec-1.*rec-2|rec-2.*rec-1"):
            client.upsert(registry_id="reg-1", record=record, source_record_id="rec-2")

    def test_re_processing_the_same_source_record_is_not_a_collision(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        new_arn = "arn:aws:agent-registry:us-east-1:123456789012:registry/reg-1/record/rec-new"
        calls = {"list": 0}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                calls["list"] += 1
                if calls["list"] == 1:
                    return _resp(200, {"registryRecords": []})
                return _resp(
                    200,
                    {"registryRecords": [{"recordId": "rec-new", "name": "server-1"}]},
                )
            if m == "POST" and url.endswith("/records"):
                return _resp(202, {"recordArn": new_arn, "status": "CREATING"})
            if m == "GET" and url.endswith("/records/rec-new"):
                return _resp(
                    200,
                    {
                        "recordId": "rec-new",
                        "status": "DRAFT",
                        "name": "server-1",
                        "displayName": "server-1",
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": "payload"}},
                    },
                )
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)
        record = {
            "name": "server-1",
            "displayName": "server-1",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "payload"}},
        }
        first = client.upsert(registry_id="reg-1", record=record, source_record_id="rec-1")
        second = client.upsert(registry_id="reg-1", record=record, source_record_id="rec-1")
        self.assertEqual(first.new_record_id, "rec-new")
        self.assertEqual(second.action, "existing")


class StatusParityWire(unittest.TestCase):
    """The target record has to end up in the status its Preview record held.

    target creates every record in DRAFT, and DRAFT records are not returned by data-plane search or the
    browsing APIs, so these two operations are what keeps an approved record approved.
    """

    def test_approved_is_submit_then_set_status_when_the_registry_does_not_auto_approve(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        state = {"status": "DRAFT"}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/submit-for-approval"):
                state["status"] = "PENDING_APPROVAL"
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            if m == "PATCH" and url.endswith("/status"):
                state["status"] = _req_body(request)["status"]
                return _resp(202, {"recordId": "rec-1", "status": state["status"]})
            if m == "GET" and url.endswith("/records/rec-1"):
                return _resp(200, {"recordId": "rec-1", "status": state["status"]})
            raise AssertionError(f"unexpected target request {m} {url}")

        cap = _capture(boto, responder)
        result = client.apply_status(
            registry_id="reg-1",
            record_id="rec-1",
            desired_status="APPROVED",
            current_status="DRAFT",
            reason="Migrated from Preview record rec-old in status APPROVED",
        )

        self.assertEqual(result.achieved, "APPROVED")
        self.assertTrue(result.matched)
        self.assertEqual(result.actions, ["submitForApproval", "updateStatus=APPROVED"])

        submit = next(r for r in cap if r.method == "POST")
        self.assertTrue(submit.url.endswith("/registries/reg-1/records/rec-1/submit-for-approval"), submit.url)
        self.assertEqual(_req_body(submit), {})
        self.assertIn("/agent-registry/aws4_request", _auth(submit))

        patch = next(r for r in cap if r.method == "PATCH")
        self.assertTrue(patch.url.endswith("/registries/reg-1/records/rec-1/status"), patch.url)
        body = _req_body(patch)
        self.assertEqual(body.get("status"), "APPROVED")
        self.assertIn("Migrated from Preview record rec-old", body.get("statusReason", ""))
        self.assertNotIn("registryId", body)

    def test_a_registry_that_auto_approves_needs_only_the_submit(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/submit-for-approval"):
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            if m == "GET" and url.endswith("/records/rec-1"):
                # APPROVE_ALL: the service promotes the record without a second call.
                return _resp(200, {"recordId": "rec-1", "status": "APPROVED"})
            raise AssertionError(f"unexpected target request {m} {url}")

        cap = _capture(boto, responder)
        result = client.apply_status(
            registry_id="reg-1", record_id="rec-1", desired_status="APPROVED", current_status="DRAFT"
        )

        self.assertEqual(result.actions, ["submitForApproval"])
        self.assertEqual(result.achieved, "APPROVED")
        self.assertEqual([r.method for r in cap if r.method == "PATCH"], [])

    def test_a_source_status_that_no_new_record_can_hold_is_reported_not_attempted(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        cap = _capture(client._client, lambda request: _resp(500, {}))

        result = client.apply_status(
            registry_id="reg-1",
            record_id="rec-1",
            desired_status="CREATE_FAILED",
            current_status="DRAFT",
        )

        self.assertFalse(result.reproducible)
        self.assertEqual(result.actions, [])
        self.assertEqual(cap, [], "no call should be made for a status the service cannot be put into")

    def test_a_draft_source_record_costs_no_calls(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        cap = _capture(client._client, lambda request: _resp(500, {}))

        result = client.apply_status(
            registry_id="reg-1", record_id="rec-1", desired_status="DRAFT", current_status="DRAFT"
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.actions, [])
        self.assertEqual(cap, [])

    def test_a_refused_transition_is_returned_not_raised(self):
        """The record is loaded and correct; the caller decides what a status failure means."""
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)

        def responder(request):
            if request.method == "POST":
                return _resp(400, {"message": "ValidationException: not submittable"})
            return _resp(200, {"recordId": "rec-1", "status": "DRAFT"})

        _capture(client._client, responder)
        result = client.apply_status(
            registry_id="reg-1", record_id="rec-1", desired_status="APPROVED", current_status="DRAFT"
        )

        self.assertIsNotNone(result.error)
        self.assertEqual(result.achieved, "DRAFT")
        self.assertFalse(result.matched)

    def test_deprecated_is_set_directly(self):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        state = {"status": "DRAFT"}

        def responder(request):
            m, url = request.method, request.url
            if m == "PATCH" and url.endswith("/status"):
                state["status"] = _req_body(request)["status"]
                return _resp(202, {"recordId": "rec-1", "status": state["status"]})
            if m == "GET":
                return _resp(200, {"recordId": "rec-1", "status": state["status"]})
            raise AssertionError(f"unexpected target request {m} {url}")

        cap = _capture(boto, responder)
        result = client.apply_status(
            registry_id="reg-1", record_id="rec-1", desired_status="DEPRECATED", current_status="DRAFT"
        )

        self.assertEqual(result.actions, ["updateStatus=DEPRECATED"])
        self.assertEqual(result.achieved, "DEPRECATED")
        self.assertEqual([r.method for r in cap if r.method == "POST"], [])


class ConcurrentUpdateConflictsAreRetried(unittest.TestCase):
    """The service answers a status call that arrives before the previous write settled with a conflict.

    "ConflictException ... Concurrent update detected. Please retry." is exactly what a migration
    provokes: it creates a record and immediately drives it to its source status. Observed live on
    2 of 6 records in an incremental run, where simply running the migration again fixed both --
    which is the definition of something that should have been retried. botocore does not retry it
    (a 409 is not throttling or 5xx), so the client has to.
    """

    # Same target settings, minus the waiting: the delay is real in production and pointless in a test.
    API: ClassVar[dict] = {**TARGET_API, "poll": {**TARGET_API["poll"], "conflictRetryDelaySeconds": 0}}

    def _client(self):
        return registry_api.TargetRegistryClient(_invoker(), self.API, REGION)

    def test_a_conflict_on_update_status_is_retried_until_it_succeeds(self):
        client = self._client()
        state = {"status": "PENDING_APPROVAL", "patches": 0}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/submit-for-approval"):
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            if m == "PATCH" and url.endswith("/status"):
                state["patches"] += 1
                if state["patches"] < 3:
                    return _conflict()
                state["status"] = _req_body(request)["status"]
                return _resp(202, {"recordId": "rec-1", "status": state["status"]})
            if m == "GET":
                return _resp(200, {"recordId": "rec-1", "status": state["status"]})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(client._client, responder)
        result = client.apply_status(
            registry_id="reg-1", record_id="rec-1", desired_status="APPROVED", current_status="DRAFT"
        )

        self.assertEqual(state["patches"], 3, "the conflicting call should have been retried")
        self.assertEqual(result.achieved, "APPROVED")
        self.assertTrue(result.matched)
        # The retry is invisible in the outcome: this is a record that simply worked.
        self.assertIsNone(result.error)
        self.assertEqual(result.actions, ["submitForApproval", "updateStatus=APPROVED"])

    def test_a_conflict_on_submit_for_approval_is_retried(self):
        client = self._client()
        state = {"submits": 0}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/submit-for-approval"):
                state["submits"] += 1
                if state["submits"] < 2:
                    return _conflict()
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            if m == "GET":
                return _resp(200, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(client._client, responder)
        result = client.apply_status(
            registry_id="reg-1",
            record_id="rec-1",
            desired_status="PENDING_APPROVAL",
            current_status="DRAFT",
        )

        self.assertEqual(state["submits"], 2)
        self.assertEqual(result.achieved, "PENDING_APPROVAL")
        self.assertIsNone(result.error)

    def test_retries_are_bounded_and_a_persistent_conflict_is_reported(self):
        """A conflict that never clears is not a race; it is reported rather than retried forever."""
        client = self._client()
        state = {"patches": 0}

        def responder(request):
            m, url = request.method, request.url
            if m == "PATCH" and url.endswith("/status"):
                state["patches"] += 1
                return _conflict()
            if m == "GET":
                return _resp(200, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            if m == "POST":
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(client._client, responder)
        result = client.apply_status(
            registry_id="reg-1",
            record_id="rec-1",
            desired_status="DEPRECATED",
            current_status="DRAFT",
        )

        self.assertEqual(state["patches"], registry_api.DEFAULT_CONFLICT_RETRY_ATTEMPTS * 2)
        self.assertIsNotNone(result.error)
        self.assertIn("Conflict", result.error)
        self.assertFalse(result.matched)

    def test_an_error_that_is_not_a_conflict_is_not_retried(self):
        """Retrying a refused transition would only turn one clear failure into a slow one."""
        client = self._client()
        state = {"patches": 0}

        def responder(request):
            m, url = request.method, request.url
            if m == "PATCH" and url.endswith("/status"):
                state["patches"] += 1
                return _resp(400, {"__type": "ValidationException", "message": "not allowed"})
            if m == "GET":
                return _resp(200, {"recordId": "rec-1", "status": "DRAFT"})
            if m == "POST":
                return _resp(202, {"recordId": "rec-1", "status": "PENDING_APPROVAL"})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(client._client, responder)
        result = client.apply_status(
            registry_id="reg-1",
            record_id="rec-1",
            desired_status="DEPRECATED",
            current_status="DRAFT",
        )

        # Once for the direct attempt, once after the submit-then-retry fallback: no retry budget.
        self.assertEqual(state["patches"], 2)
        self.assertIsNotNone(result.error)


class SettledStatusHandling(unittest.TestCase):
    """A record the customer already submitted or approved is settled, not unknown.

    The documented cutover is a full live load followed by an incremental run at cutover. Records
    approved in between must still be updatable, or the final run fails on exactly the records that
    were put into service.
    """

    def _update_against_existing_status(self, existing_status: str):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        desired = {
            "name": "svc-beta",
            "displayName": "New Label",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "new-payload"}},
        }
        state = {"updated": False}

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": [{"recordId": "rec-x", "name": "svc-beta"}]})
            if m == "GET" and url.endswith("/records/rec-x"):
                data = "new-payload" if state["updated"] else "old-payload"
                label = "New Label" if state["updated"] else "Old Label"
                return _resp(
                    200,
                    {
                        "recordId": "rec-x",
                        "status": existing_status,
                        "name": "svc-beta",
                        "displayName": label,
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": data}},
                    },
                )
            if m == "PATCH" and url.endswith("/records/rec-x"):
                state["updated"] = True
                return _resp(202, {"recordId": "rec-x", "status": "UPDATING"})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)
        return client.upsert(registry_id="reg-1", record=desired)

    def test_every_settled_status_can_be_updated(self):
        for status in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"):
            with self.subTest(status=status):
                result = self._update_against_existing_status(status)
                self.assertEqual(result.action, "updated")

    def test_an_unknown_status_is_still_rejected(self):
        with self.assertRaisesRegex(registry_api.RegistryApiError, "unknown lifecycle status"):
            self._update_against_existing_status("SOMETHING_NEW")

    def test_a_failure_status_is_still_a_failure(self):
        with self.assertRaisesRegex(registry_api.RegistryApiError, "failure status"):
            self._update_against_existing_status("UPDATE_FAILED")


class ACreatedRecordThatFailsToSettleIsStillNamed(unittest.TestCase):
    """A create can return an id and then the record fails. That record exists in the registry.

    Reporting the failure without the id leaves an orphan nobody can find from the crosswalk, so
    the error carries the recordId and the load stage copies it onto the failed row.
    """

    def _create_then(self, poll_response):
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        desired = {
            "name": "svc-sync",
            "displayName": "svc-sync",
            "recordType": "AGENT",
            "descriptors": {"a2aAgentCard": {"data": "card", "source": {"fromUrl": {"url": "https://nope.example"}}}},
        }
        new_arn = "arn:aws:agent-registry:us-east-1:123456789012:registry/reg-1/record/rec-orphan"

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": []})
            if m == "POST" and url.endswith("/records"):
                return _resp(202, {"recordArn": new_arn, "status": "CREATING"})
            if m == "GET" and url.endswith("/records/rec-orphan"):
                return poll_response()
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)
        with self.assertRaises(registry_api.RegistryApiError) as caught:
            client.upsert(registry_id="reg-1", record=desired)
        return caught.exception

    def test_a_create_failed_status_carries_the_record_id(self):
        error = self._create_then(
            lambda: _resp(
                200,
                {
                    "recordId": "rec-orphan",
                    "status": "CREATE_FAILED",
                    "statusReason": "Failed to fetch agent card from URL",
                },
            )
        )
        self.assertEqual(error.record_id, "rec-orphan")
        self.assertIn("CREATE_FAILED", str(error))
        self.assertIn("Failed to fetch agent card", str(error))

    def test_an_unknown_status_after_create_also_carries_the_record_id(self):
        error = self._create_then(lambda: _resp(200, {"recordId": "rec-orphan", "status": "SOMETHING_NEW"}))
        self.assertEqual(error.record_id, "rec-orphan")

    def test_a_failed_status_read_after_create_still_carries_the_record_id(self):
        # The poll request itself fails, which is what a transport error looks like once _call has
        # wrapped it. The record was still created, so the id has to survive that path too.
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client
        new_arn = "arn:aws:agent-registry:us-east-1:123456789012:registry/reg-1/record/rec-orphan"

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": []})
            if m == "POST" and url.endswith("/records"):
                return _resp(202, {"recordArn": new_arn, "status": "CREATING"})
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)

        def failing_get(**_kwargs):
            raise registry_api.RegistryApiError("Target API call agent-registry-control.get failed")

        client._get_record = failing_get
        with self.assertRaises(registry_api.RegistryApiError) as caught:
            client.upsert(
                registry_id="reg-1",
                record={
                    "name": "svc-sync",
                    "displayName": "svc-sync",
                    "recordType": "CUSTOM",
                    "descriptors": {"custom": {"data": "payload"}},
                },
            )
        self.assertEqual(caught.exception.record_id, "rec-orphan")

    def test_an_error_before_any_write_has_no_record_id(self):
        error = registry_api.RegistryApiError("nothing was written")
        self.assertIsNone(error.record_id)

    def test_an_existing_record_already_in_a_failure_status_carries_its_id(self):
        """The second run over a record that failed the first time.

        A re-run finds the record by name, sees CREATE_FAILED, and refuses it. That record is the
        one the reader has to go and fix or delete, so the row must name it.
        """
        client = registry_api.TargetRegistryClient(_invoker(), TARGET_API, REGION)
        boto = client._client

        def responder(request):
            m, url = request.method, request.url
            if m == "POST" and url.endswith("/records-list"):
                return _resp(200, {"registryRecords": [{"recordId": "rec-stuck", "name": "svc-sync"}]})
            if m == "GET" and url.endswith("/records/rec-stuck"):
                return _resp(
                    200,
                    {
                        "recordId": "rec-stuck",
                        "status": "CREATE_FAILED",
                        "statusReason": "Failed to fetch agent card from URL",
                        "name": "svc-sync",
                        "recordType": "CUSTOM",
                        "descriptors": {"custom": {"data": "payload"}},
                    },
                )
            raise AssertionError(f"unexpected target request {m} {url}")

        _capture(boto, responder)
        with self.assertRaises(registry_api.RegistryApiError) as caught:
            client.upsert(
                registry_id="reg-1",
                record={
                    "name": "svc-sync",
                    "displayName": "svc-sync",
                    "recordType": "CUSTOM",
                    "descriptors": {"custom": {"data": "payload"}},
                },
            )
        self.assertEqual(caught.exception.record_id, "rec-stuck")
        self.assertIn("Existing target record rec-stuck", str(caught.exception))


class LengthBoundsMatchWhatPreviewAccepts(unittest.TestCase):
    """The hand-maintained length bounds must not be tighter than the Preview model's own.

    This tool copies Preview records into the target registry, so a bound below what Preview accepts rejects records
    that demonstrably exist -- before the service is ever asked. That is not a theoretical case: the
    bounds were briefly set to description 1024 and recordVersion 64, and the seed fixtures
    `D2-description-at-max` and `D3-record-version-at-max` create records at exactly the Preview
    maxima (4096 and 255), so the tool refused its own test data on records that had migrated
    successfully the day before.

    The maxima are asserted against the Preview model the SDK carries rather than restated, so the
    day the Preview traits change this test says so instead of silently agreeing with a stale copy.
    """

    #: Preview shape -> the target registry field the migration carries it into.
    SHAPE_FOR_FIELD: ClassVar[dict[str, str]] = {
        "name": "RegistryRecordName",
        "description": "Description",
        "recordVersion": "RegistryRecordVersion",
    }

    def _preview_maxima(self) -> dict[str, int]:
        """The Preview shape maxima, read off the same client the migration itself builds.

        Read through ``boto3`` rather than botocore's loader: where the model comes from is the
        SDK's business, and a test that opens model files asserts something the tool does not do.
        Skips when the SDK cannot model the Preview service -- that is the condition the tool
        reports at run time, not something to fail this bound check on.
        """
        try:
            model = registry_api.PreviewRegistryClient(_invoker(), PREVIEW_API, REGION)._client.meta.service_model
        except Exception as error:  # noqa: BLE001 - an absent model is the skip condition
            self.skipTest(f"SDK cannot model bedrock-agentcore-control: {error}")
        return {field: int(model.shape_for(shape).metadata["max"]) for field, shape in self.SHAPE_FOR_FIELD.items()}

    def test_no_bound_is_tighter_than_preview(self):
        for field, preview_max in self._preview_maxima().items():
            with self.subTest(field=field):
                self.assertGreaterEqual(
                    registry_api._TARGET_FIELD_MAX_LENGTHS[field],
                    preview_max,
                    f"{field} is capped below the {preview_max} Preview accepts, so a legal "
                    "Preview record cannot be migrated",
                )

    def _record(self, **overrides):
        record = {
            "name": "svc",
            "displayName": "svc",
            "recordType": "CUSTOM",
            "descriptors": {"custom": {"data": "payload"}},
        }
        record.update(overrides)
        return record

    def test_a_record_at_every_preview_maximum_is_accepted(self):
        maxima = self._preview_maxima()
        registry_api.validate_target_request(
            self._record(
                name="n" * maxima["name"],
                displayName="d" * 255,
                description="D" * maxima["description"],
                # The Preview pattern for recordVersion excludes underscore, so build it from digits.
                recordVersion="1" + "9" * (maxima["recordVersion"] - 1),
            )
        )

    def test_one_character_over_a_bound_is_still_refused(self):
        maxima = self._preview_maxima()
        for field, value in (
            ("description", "D" * (maxima["description"] + 1)),
            ("recordVersion", "1" + "9" * maxima["recordVersion"]),
            ("name", "n" * (maxima["name"] + 1)),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(registry_api.RegistryApiError, "at most"),
            ):
                registry_api.validate_target_request(self._record(**{field: value}))


class DryRunAppliesTheServiceContract(unittest.TestCase):
    """validate_target_request is what the live load enforces, so a dry run must run it too."""

    def test_a_valid_record_passes(self):
        registry_api.validate_target_request(
            {
                "name": "svc",
                "displayName": "svc",
                "recordType": "CUSTOM",
                "descriptors": {"custom": {"data": "payload"}},
            }
        )

    def test_a_primary_invalid_for_the_record_type_is_rejected(self):
        with self.assertRaisesRegex(registry_api.RegistryApiError, "incompatible with recordType"):
            registry_api.validate_target_request(
                {
                    "name": "svc",
                    "displayName": "svc",
                    "recordType": "MCP",
                    "descriptors": {"a2aAgentCard": {"data": "card"}},
                }
            )

    def test_more_than_one_primary_descriptor_is_rejected(self):
        with self.assertRaisesRegex(registry_api.RegistryApiError, "exactly one primary descriptor"):
            registry_api.validate_target_request(
                {
                    "name": "svc",
                    "displayName": "svc",
                    "recordType": "MCP",
                    "descriptors": {"mcpServer": {"data": "s"}, "custom": {"data": "c"}},
                }
            )

    def test_a_markdown_only_skill_may_omit_data_on_the_definition(self):
        # The live service accepts this exact shape for a markdown-only skill and rejects every
        # alternative (see test_transform for the recorded responses), so the validator has to let a
        # missing ``data`` through when additionalData.skillMd carries the content.
        registry_api.validate_target_request(
            {
                "name": "md-skill",
                "displayName": "md-skill",
                "recordType": "SKILL",
                "descriptors": {"agentSkillsDefinition": {"additionalData": {"skillMd": {"data": "# HELLO"}}}},
            }
        )

    def test_agent_skills_md_is_refused_as_a_target_primary_descriptor(self):
        # The new version has no agentSkillsMd primary. The live service answers one with "Exactly one valid
        # descriptor is allowed for record type SKILL. Valid descriptors: [agentSkillsDefinition,
        # custom]", so the validator must refuse it locally rather than let a dry run PASS a body
        # the service then rejects. Nothing emits this shape any more -- the transform normalizes it
        # -- which is exactly why it needs a guard: a regression there would otherwise reach the target registry.
        with self.assertRaises(registry_api.RegistryApiError):
            registry_api.validate_target_request(
                {
                    "name": "md-skill",
                    "displayName": "md-skill",
                    "recordType": "SKILL",
                    "descriptors": {"agentSkillsMd": {"data": "# HELLO"}},
                }
            )

    def test_the_missing_data_exemption_does_not_extend_to_an_empty_descriptor(self):
        # The exemption is narrow: no data AND no content-bearing child is still invalid, so a
        # genuinely empty descriptor cannot slip through on the back of the skill mapping.
        for descriptors in (
            {"agentSkillsDefinition": {}},
            {"agentSkillsDefinition": {"additionalData": {"skillMd": {"data": ""}}}},
            {"custom": {"additionalData": {"skillMd": {"data": "# HELLO"}}}},
        ):
            with (
                self.subTest(descriptors=descriptors),
                self.assertRaises(registry_api.RegistryApiError),
            ):
                registry_api.validate_target_request(
                    {
                        "name": "svc",
                        "displayName": "svc",
                        "recordType": "SKILL",
                        "descriptors": descriptors,
                    }
                )

    def test_data_is_still_required_on_the_markdown_child_itself(self):
        with self.assertRaisesRegex(registry_api.RegistryApiError, "requires non-empty string data"):
            registry_api.validate_target_request(
                {
                    "name": "md-skill",
                    "displayName": "md-skill",
                    "recordType": "SKILL",
                    "descriptors": {
                        "agentSkillsDefinition": {
                            "data": "{}",
                            "additionalData": {"skillMd": {"dataSchemaVersion": "1.0"}},
                        }
                    },
                }
            )


class RefreshableCredentialsPath(unittest.TestCase):
    """What reaches ``sts:AssumeRole``, and how the resulting session behaves."""

    @staticmethod
    def _fake_sts(recorded: list[dict]):
        """An STS stand-in that records every AssumeRole request it is given."""

        class FakeSts:
            def assume_role(self, **kwargs):
                recorded.append(kwargs)
                return {
                    "Credentials": {
                        "AccessKeyId": "AKIA_ASSUMED",
                        "SecretAccessKey": "sekret",  # pragma: allowlist secret
                        "SessionToken": "tok",
                        "Expiration": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
                    }
                }

        return FakeSts()

    @contextmanager
    def _sts(self, recorded: list[dict]):
        original_client = aws_auth.boto3.client
        fake = self._fake_sts(recorded)
        aws_auth.boto3.client = lambda name, *a, **k: fake if name == "sts" else original_client(name, *a, **k)
        try:
            yield
        finally:
            aws_auth.boto3.client = original_client

    def test_assumed_role_yields_refreshable_credentials(self):
        recorded: list[dict] = []
        with self._sts(recorded):
            invoker = aws_auth.invoker_for_endpoint({"roleArn": "arn:aws:iam::123:role/x"}, "run-1", "load")
            session = invoker.session()
            creds = session.get_credentials()
            self.assertIsInstance(creds, RefreshableCredentials)
            self.assertEqual(creds.get_frozen_credentials().access_key, "AKIA_ASSUMED")
            self.assertIs(invoker.session(), session)  # cached
        self.assertEqual(len(recorded), 1)  # seeded once at build
        self.assertEqual(recorded[0]["RoleArn"], "arn:aws:iam::123:role/x")
        self.assertTrue(recorded[0]["RoleSessionName"].startswith("registry-migration-load-"))

    def test_external_id_reaches_assume_role(self):
        """A cross-account endpoint's externalId must be sent, or every assume-role fails.

        The external id is the condition the generated access role's trust policy checks
        (``sts:ExternalId`` in RegistryAccessStack), so dropping it here breaks every cross-account
        mapping -- or, against a role that does not require it, succeeds while silently discarding
        the control. Nothing asserted this before: the only test here checked RoleArn and
        RoleSessionName, and no test passed an endpoint that carried an externalId at all.
        """
        recorded: list[dict] = []
        with self._sts(recorded):
            aws_auth.invoker_for_endpoint(
                {"roleArn": "arn:aws:iam::123:role/x", "externalId": "shared-secret"},
                "run-1",
                "load",
            ).session()
        self.assertEqual(recorded[0].get("ExternalId"), "shared-secret")

    def test_no_external_id_is_omitted_rather_than_sent_empty(self):
        """A same-account role is assumed without the parameter, not with an empty one."""
        recorded: list[dict] = []
        with self._sts(recorded):
            aws_auth.invoker_for_endpoint({"roleArn": "arn:aws:iam::123:role/x"}, "run-1", "load").session()
        self.assertNotIn("ExternalId", recorded[0])

    def test_a_run_id_is_optional_in_the_session_name(self):
        """Pre-flight and target-config have no run, and must not fabricate one."""
        recorded: list[dict] = []
        with self._sts(recorded):
            aws_auth.invoker_for_endpoint({"roleArn": "arn:aws:iam::123:role/x"}, None, "target-config").session()
        self.assertEqual(recorded[0]["RoleSessionName"], "registry-migration-target-config")

    def test_concurrent_first_callers_assume_the_role_once(self):
        """The load stage shares one invoker across worker threads.

        Without a lock around the lazy build, two threads arriving together each performed an
        AssumeRole and one of the two sessions was then thrown away.
        """
        recorded: list[dict] = []
        with self._sts(recorded):
            invoker = aws_auth.invoker_for_endpoint({"roleArn": "arn:aws:iam::123:role/x"}, "run-1", "load")
            barrier = threading.Barrier(8)

            def build():
                barrier.wait()
                return invoker.session()

            with ThreadPoolExecutor(max_workers=8) as pool:
                sessions = list(pool.map(lambda _: build(), range(8)))
        self.assertEqual(len(recorded), 1)
        self.assertEqual(len({id(session) for session in sessions}), 1)


if __name__ == "__main__":
    unittest.main()
