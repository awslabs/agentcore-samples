"""The target registry configuration derived from each Preview registry.

``target_registry`` is what ``agent-registry-migration target-config`` (and ``init``) drives: it
reads a source registry read-only and returns the ``CreateRegistry`` input an operator applies by
hand. It had no test coverage at all, which mattered most for two of its decisions:

* **which region** the target registry belongs in -- ``target.region or source.region``. Get that wrong
  and the operator creates the registry in the wrong place, then cannot load into it.
* **per-mapping failure isolation** -- one unreachable registry must not hide the others, because
  the whole point of the command is to answer for every mapping at once.

``transform_registry_configuration`` itself is covered thoroughly in test_transform.py; this is
about the module that drives it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import (
    registry_api,
    target_registry,
)

PREVIEW_API = {
    "serviceName": "bedrock-agentcore-control",
    "transport": "sigv4RestJson",
    "signingName": "bedrock-agentcore",
    "endpointUrlTemplate": "https://bedrock-agentcore-control.{region}.amazonaws.com",
    "allowedEndpointHosts": [],
    "endpointUrl": None,
}

SETTINGS = {"api": {"preview": PREVIEW_API}}

TARGET_API = {
    "serviceName": "agent-registry-control",
    "transport": "sigv4RestJson",
    "signingName": "agent-registry",
    "endpointUrlTemplate": "https://agent-registry-control.{region}.api.aws",
    "allowedEndpointHosts": [],
    "endpointUrl": None,
}

TARGET_SETTINGS = {"api": {"preview": PREVIEW_API, "target": TARGET_API}}


def _mapping(mapping_id: str, *, source_region: str, target_region: str | None) -> dict:
    target: dict = {"accountId": "111122223333", "registryId": "reg-new"}
    if target_region is not None:
        target["region"] = target_region
    return {
        "id": mapping_id,
        "source": {
            "accountId": "111122223333",
            "region": source_region,
            "registryId": f"reg-{mapping_id}",
        },
        "target": target,
    }


class _FakePreviewClient:
    """Returns a canned registry, or raises, per source registryId."""

    registries: ClassVar[dict[str, dict]] = {}
    errors: ClassVar[dict[str, Exception]] = {}
    describe_calls: ClassVar[list[str]] = []

    @classmethod
    def reset(cls) -> None:
        cls.registries = {}
        cls.errors = {}
        cls.describe_calls = []

    def __init__(self, invoker, api_config, region) -> None:
        self.region = region

    def describe_registry(self, *, registry_id: str) -> dict:
        type(self).describe_calls.append(registry_id)
        if registry_id in type(self).errors:
            raise type(self).errors[registry_id]
        return type(self).registries[registry_id]


class DeriveCreateRegistryInputs(unittest.TestCase):
    def setUp(self) -> None:
        _FakePreviewClient.reset()
        self._original_client = target_registry.PreviewRegistryClient
        self._original_invoker = target_registry.invoker_for_endpoint
        target_registry.PreviewRegistryClient = _FakePreviewClient
        target_registry.invoker_for_endpoint = lambda endpoint, run_id, purpose: "invoker"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        target_registry.PreviewRegistryClient = self._original_client
        target_registry.invoker_for_endpoint = self._original_invoker

    @staticmethod
    def _preview_registry(name: str = "src") -> dict:
        return {
            "name": name,
            "registryId": "reg-a",
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJWTAuthorizer": {
                    "discoveryUrl": "https://example.test/.well-known/openid-configuration",
                    "allowedAudience": ["aud"],
                }
            },
        }

    def test_the_payload_is_derived_from_the_source_registry(self):
        _FakePreviewClient.registries = {"reg-a": self._preview_registry()}
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS, [_mapping("a", source_region="us-east-1", target_region="us-west-2")]
        )
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["payload"]["name"], "src")
        # The preview shape's top-level authorizer is nested under discoveryConfiguration in the new version.
        self.assertEqual(entry["payload"]["discoveryConfiguration"]["authorizerType"], "CUSTOM_JWT")

    def test_the_region_is_the_targets_not_the_sources(self):
        """The target registry has to be created where the mapping loads into."""
        _FakePreviewClient.registries = {"reg-a": self._preview_registry()}
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS, [_mapping("a", source_region="us-east-1", target_region="eu-west-1")]
        )
        self.assertEqual(entries[0]["region"], "eu-west-1")

    def test_the_region_falls_back_to_the_source_when_the_target_has_none(self):
        _FakePreviewClient.registries = {"reg-a": self._preview_registry()}
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS, [_mapping("a", source_region="ap-south-1", target_region=None)]
        )
        self.assertEqual(entries[0]["region"], "ap-south-1")

    def test_one_unreachable_registry_does_not_hide_the_others(self):
        _FakePreviewClient.registries = {
            "reg-a": self._preview_registry("first"),
            "reg-c": self._preview_registry("third"),
        }
        _FakePreviewClient.errors = {"reg-b": registry_api.RegistryApiError("AccessDeniedException: nope")}
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS,
            [
                _mapping("a", source_region="us-east-1", target_region="us-east-1"),
                _mapping("b", source_region="us-east-1", target_region="us-east-1"),
                _mapping("c", source_region="us-east-1", target_region="us-east-1"),
            ],
        )
        by_id = {entry["mappingId"]: entry for entry in entries}
        self.assertEqual(sorted(by_id), ["a", "b", "c"])
        self.assertIsNone(by_id["a"]["error"])
        self.assertIsNone(by_id["c"]["error"])
        self.assertIn("AccessDenied", by_id["b"]["error"])
        self.assertIsNone(by_id["b"]["payload"])

    def test_warnings_reach_the_caller(self):
        """A dropped authorizer field is an access-control decision, so it must not be silent."""
        registry = self._preview_registry()
        registry["authorizerConfiguration"]["customJWTAuthorizer"]["advertisedScopeMapping"] = {"a": "b"}
        _FakePreviewClient.registries = {"reg-a": registry}
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS, [_mapping("a", source_region="us-east-1", target_region="us-east-1")]
        )
        self.assertTrue(entries[0]["warnings"])
        self.assertIn("advertisedScopeMapping", " ".join(entries[0]["warnings"]))

    def test_mapping_ids_filter_which_registries_are_read(self):
        _FakePreviewClient.registries = {
            "reg-a": self._preview_registry("first"),
            "reg-b": self._preview_registry("second"),
        }
        entries = target_registry.derive_create_registry_inputs(
            SETTINGS,
            [
                _mapping("a", source_region="us-east-1", target_region="us-east-1"),
                _mapping("b", source_region="us-east-1", target_region="us-east-1"),
            ],
            mapping_ids=["b"],
        )
        self.assertEqual([entry["mappingId"] for entry in entries], ["b"])
        # Only the selected mapping's registry was contacted.
        self.assertEqual(_FakePreviewClient.describe_calls, ["reg-b"])

    def test_the_source_is_reported_without_its_external_id(self):
        """Reports must never echo the cross-account trust secret."""
        _FakePreviewClient.registries = {"reg-a": self._preview_registry()}
        mapping = _mapping("a", source_region="us-east-1", target_region="us-east-1")
        mapping["source"]["roleArn"] = "arn:aws:iam::444455556666:role/Reader"
        mapping["source"]["externalId"] = "shared-secret"
        entries = target_registry.derive_create_registry_inputs(SETTINGS, [mapping])
        self.assertNotIn("externalId", entries[0]["source"])
        self.assertNotIn("shared-secret", str(entries[0]))


class UnknownMappingIds(unittest.TestCase):
    def test_a_typo_is_named_rather_than_ignored(self):
        mappings = [_mapping("a", source_region="us-east-1", target_region="us-east-1")]
        self.assertEqual(target_registry.unknown_mapping_ids(mappings, ["a", "b"]), ["b"])

    def test_no_selection_means_nothing_is_unknown(self):
        mappings = [_mapping("a", source_region="us-east-1", target_region="us-east-1")]
        self.assertEqual(target_registry.unknown_mapping_ids(mappings, None), [])
        self.assertEqual(target_registry.unknown_mapping_ids(mappings, []), [])


class CreateRegistryCommand(unittest.TestCase):
    def test_the_command_names_the_targets_regional_endpoint(self):
        payload_path = os.path.join(tempfile.gettempdir(), "a.json")
        command = target_registry.create_registry_command({"mappingId": "a", "region": "eu-west-1"}, payload_path)
        self.assertIn("aws agent-registry-control create-registry", command)
        self.assertIn("--endpoint-url https://agent-registry-control.eu-west-1.api.aws", command)
        self.assertIn(f"file://{payload_path}", command)


class TheCreateRegistryCommandCarriesItsPrerequisite(unittest.TestCase):
    """The emitted command depends on the AWS CLI's age, so it has to say what to do about that.

    An AWS CLI older than the target service model answers "Invalid choice:
    'agent-registry-control'". That is no longer a dead end -- ``--create`` makes the same call
    through this tool's own pinned SDK -- so the note names the symptom and points at the flag.
    """

    def test_the_prerequisite_names_the_failure_and_the_fix(self):
        note = target_registry.create_registry_prerequisite()
        # The symptom, so it is recognisable when someone has already hit it.
        self.assertIn("Invalid choice", note)
        # The fix, which is a flag rather than a file to install by hand.
        self.assertIn("target-config --create", note)
        self.assertIn("READY", note)
        # And no instruction to hand-install a model: that is what shadows the SDK's own model and
        # takes CreateRegistry away again (see ShadowedTargetModel in test_preflight).
        self.assertNotIn("~/.aws/models", note)

    def test_the_sdk_really_does_model_create_registry(self):
        """Pins the fact ``--create`` depends on: the target model carries the registry operations.

        Asked of a ``boto3`` client, which is the only way this tool reaches the control plane.
        Skipped -- not failed -- when ``~/.aws/models`` shadows the SDK's own model, because then
        the answer describes that file rather than the SDK, and the shadowing is what
        ``check`` reports. Skipped too where the SDK cannot model the service at all: that is the
        condition the tool itself fails on, and it is reported there rather than here.
        """
        import boto3

        if os.path.isdir(os.path.join(os.path.expanduser("~"), ".aws", "models", "agent-registry-control")):
            self.skipTest("~/.aws/models/agent-registry-control shadows the SDK's own model")
        try:
            client = boto3.client("agent-registry-control", region_name="us-east-1")
        except Exception as error:  # noqa: BLE001 - an unmodelled service is the skip condition
            self.skipTest(f"SDK cannot model agent-registry-control: {error}")
        operations = set(client.meta.service_model.operation_names)
        self.assertIn("CreateRegistry", operations)
        self.assertIn("GetRegistry", operations)


class ClientTokenMakesARetryIdempotent(unittest.TestCase):
    """A retried create must return the first registry, not make a second one.

    ``init`` can be interrupted between the create call and writing the id down -- Ctrl-C, a lost
    connection, a failed wait. Re-running it then sends the same request, and the token is what makes
    the service answer with the registry that already exists.
    """

    def test_the_same_mapping_and_payload_give_the_same_token(self):
        payload = {"name": "reg", "discoveryConfiguration": {"authorizerType": "AWS_IAM"}}
        self.assertEqual(
            target_registry.client_token("a", payload),
            target_registry.client_token("a", dict(reversed(list(payload.items())))),
        )

    def test_a_different_payload_or_mapping_gives_a_different_token(self):
        payload = {"name": "reg"}
        token = target_registry.client_token("a", payload)
        self.assertNotEqual(token, target_registry.client_token("b", payload))
        self.assertNotEqual(token, target_registry.client_token("a", {"name": "other"}))


class _FakeTargetClient:
    """Records CreateRegistry calls and returns a scripted sequence of GetRegistry statuses."""

    def __init__(self, statuses, *, status_reason=None, arn="arn:aws:agent-registry:us-east-1:1:registry/new-1"):
        self.statuses = list(statuses)
        self.status_reason = status_reason
        self.arn = arn
        self.create_calls: list[dict] = []
        self.get_calls = 0

    def create_registry(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"registryArn": self.arn}

    def get_registry(self, *, registryId):
        self.get_calls += 1
        status = self.statuses.pop(0) if self.statuses else "READY"
        registry = {"registryId": registryId, "status": status}
        if self.status_reason:
            registry["statusReason"] = self.status_reason
        return registry


class WaitForRegistry(unittest.TestCase):
    def setUp(self) -> None:
        # Nothing here should ever sleep: the poll interval is the only thing that would make this
        # suite slow, and the loop is what is under test, not the waiting.
        self._original = target_registry.REGISTRY_POLL_INTERVAL_SECONDS
        target_registry.REGISTRY_POLL_INTERVAL_SECONDS = 0
        self.addCleanup(lambda: setattr(target_registry, "REGISTRY_POLL_INTERVAL_SECONDS", self._original))

    def test_it_polls_until_the_registry_is_ready(self):
        client = _FakeTargetClient(["CREATING", "CREATING", "READY"])
        self.assertEqual(target_registry.wait_for_registry(client, "new-1"), "READY")
        self.assertEqual(client.get_calls, 3)

    def test_a_failed_create_raises_with_the_services_own_reason(self):
        client = _FakeTargetClient(
            ["CREATE_FAILED"],
            status_reason="Unable to create workload identity because access was denied.",
        )
        with self.assertRaises(RuntimeError) as raised:
            target_registry.wait_for_registry(client, "new-1")
        # The reason names the missing permission; a generic timeout would not.
        self.assertIn("workload identity", str(raised.exception))
        self.assertIn("CREATE_FAILED", str(raised.exception))

    def test_running_out_of_attempts_says_the_registry_exists(self):
        original = target_registry.REGISTRY_POLL_ATTEMPTS
        target_registry.REGISTRY_POLL_ATTEMPTS = 2
        self.addCleanup(lambda: setattr(target_registry, "REGISTRY_POLL_ATTEMPTS", original))
        client = _FakeTargetClient(["CREATING", "CREATING"])
        with self.assertRaises(RuntimeError) as raised:
            target_registry.wait_for_registry(client, "new-1")
        # It exists, so the id has to be usable rather than presented as a failed creation.
        self.assertIn("target.registryId", str(raised.exception))

    def test_an_unmodelled_status_is_returned_rather_than_waited_out(self):
        client = _FakeTargetClient(["SOMETHING_NEW"])
        self.assertEqual(target_registry.wait_for_registry(client, "new-1"), "SOMETHING_NEW")


class CreateTargetRegistries(unittest.TestCase):
    """The create path: what reaches the API, what comes back, and what a failure leaves behind."""

    def setUp(self) -> None:
        self.client = _FakeTargetClient(["READY"])
        self._original_builder = target_registry.build_control_plane_client
        self._original_invoker = target_registry.invoker_for_endpoint
        target_registry.build_control_plane_client = lambda **kwargs: self.client
        target_registry.invoker_for_endpoint = lambda endpoint, run_id, purpose: _FakeInvoker()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        target_registry.build_control_plane_client = self._original_builder
        target_registry.invoker_for_endpoint = self._original_invoker

    def _entry(self, mapping_id="a", payload=None):
        return {
            "mappingId": mapping_id,
            "region": "us-east-1",
            "payload": payload or {"name": "reg", "discoveryConfiguration": {"authorizerType": "AWS_IAM"}},
            "warnings": [],
            "error": None,
        }

    def test_it_creates_waits_and_reports_the_generated_id(self):
        entries = [self._entry()]
        result = target_registry.create_target_registries(
            TARGET_SETTINGS, [_mapping("a", source_region="us-east-1", target_region="us-east-1")], entries
        )
        self.assertEqual(result[0]["registryId"], "new-1")
        self.assertEqual(result[0]["status"], "READY")
        # The payload reaches the API as derived, plus the idempotency token and nothing else.
        sent = self.client.create_calls[0]
        self.assertEqual(sent["name"], "reg")
        self.assertEqual(sent["clientToken"], target_registry.client_token("a", entries[0]["payload"]))

    def test_a_derive_failure_is_not_created(self):
        entries = [{"mappingId": "a", "region": "us-east-1", "payload": None, "error": "boom"}]
        target_registry.create_target_registries(TARGET_SETTINGS, [], entries)
        self.assertEqual(self.client.create_calls, [])

    def test_a_failed_wait_still_reports_the_registry_that_exists(self):
        self.client = _FakeTargetClient(["CREATE_FAILED"], status_reason="access was denied")
        target_registry.build_control_plane_client = lambda **kwargs: self.client
        original = target_registry.REGISTRY_POLL_INTERVAL_SECONDS
        target_registry.REGISTRY_POLL_INTERVAL_SECONDS = 0
        self.addCleanup(lambda: setattr(target_registry, "REGISTRY_POLL_INTERVAL_SECONDS", original))
        entries = [self._entry()]
        result = target_registry.create_target_registries(
            TARGET_SETTINGS, [_mapping("a", source_region="us-east-1", target_region="us-east-1")], entries
        )
        # Both, and this is the point: the id is what stops a retry creating a second registry.
        self.assertEqual(result[0]["registryId"], "new-1")
        self.assertIn("access was denied", result[0]["createError"])


class _FakeInvoker:
    def session(self):
        return "session"


if __name__ == "__main__":
    unittest.main()
