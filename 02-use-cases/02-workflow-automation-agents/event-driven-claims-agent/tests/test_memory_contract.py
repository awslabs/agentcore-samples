"""Use the real locked Memory/Strands integration, stub only remote clients."""

import re
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/claimsagent"))
import botocore
import botocore.session
from bedrock_agentcore.memory.integrations.strands import session_manager as sdk
from botocore.client import BaseClient
from memory import session
from memory.identifiers import claimant_memory_ids
from wire_contract import parsed_response

# Explicitly pin and apply regex/length metadata: ParamValidator skips regex.
CONSTRAINTS = {
    ("CreateEvent", "actorId"): (1, 255, r"[a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*[a-zA-Z0-9-_/]*"),
    ("CreateEvent", "sessionId"): (1, 100, r"[a-zA-Z0-9][a-zA-Z0-9-_]*"),
    ("RetrieveMemoryRecords", "namespace"): (
        1,
        1024,
        r"[a-zA-Z0-9/*][a-zA-Z0-9-_/*]*(?::[a-zA-Z0-9-_/*]+)*[a-zA-Z0-9-_/*]*",
    ),
}


class MemoryContractTests(unittest.TestCase):
    def test_email_identifiers_and_real_sdk_forwarding(self):
        self.assertEqual(botocore.__version__, "1.44.10")
        model = botocore.session.get_session().get_service_model("bedrock-agentcore")
        for (operation, member), expected in CONSTRAINTS.items():
            metadata = model.operation_model(operation).input_shape.members[member].metadata
            self.assertEqual((metadata["min"], metadata["max"], metadata["pattern"]), expected)

        def check(operation, member, value):
            minimum, maximum, pattern = CONSTRAINTS[operation, member]
            self.assertTrue(minimum <= len(value) <= maximum, (member, len(value)))
            self.assertIsNotNone(re.fullmatch(pattern, value), (member, value))

        # Includes a 254-character mailbox and a larger defensive input. Neither
        # email length nor its punctuation may leak into service identifiers.
        emails = [
            "claimant@example.test",
            "first.last+claims@example.test",
            "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 61,
            "long.claimant+" * 200 + "@example.test",
        ]
        actors = set()
        for email in emails:
            with self.subTest(email_length=len(email)):
                actor, session_id = claimant_memory_ids(email)
                same_actor, next_session = claimant_memory_ids(email)
                self.assertEqual(actor, same_actor)
                self.assertNotEqual(session_id, next_session)
                actors.add(actor)
                self.assertEqual((len(actor), len(session_id)), (73, 38))
                check("CreateEvent", "actorId", actor)
                check("CreateEvent", "sessionId", session_id)
                calls = []

                def wire(client, operation, params, recorded_calls=calls):
                    recorded_calls.append((operation, params))
                    for member in ("actorId", "sessionId"):
                        if member in params:
                            check("CreateEvent", member, params[member])
                    if operation == "ListEvents":
                        body = {"events": []}
                    elif operation == "CreateEvent":
                        body = {
                            "event": {
                                "eventId": "offline-event",
                                "memoryId": params["memoryId"],
                                "actorId": params["actorId"],
                                "sessionId": params["sessionId"],
                                "eventTimestamp": 1795000000,
                                "payload": params["payload"],
                            }
                        }
                    elif operation == "RetrieveMemoryRecords":
                        check(operation, "namespace", params["namespace"])
                        body = {"memoryRecordSummaries": []}
                    else:
                        raise AssertionError("Unstubbed Memory operation: " + operation)
                    return parsed_response(client.meta.service_model, operation, body, params)

                with (
                    patch.object(session, "MEMORY_ID", "ClaimsAgentMemory-offline"),
                    patch.object(BaseClient, "_make_api_call", wire),
                ):
                    manager = session.get_memory_session_manager(session_id, actor)
                    self.assertIsInstance(manager, sdk.AgentCoreMemorySessionManager)
                    config = manager.config
                    self.assertEqual((config.actor_id, config.session_id), (actor, session_id))
                    expected_namespaces = {f"claims/{actor}/facts", f"claims/{actor}/{session_id}"}
                    self.assertEqual(set(config.retrieval_config), expected_namespaces)
                    for namespace in expected_namespaces:
                        check("RetrieveMemoryRecords", "namespace", namespace)
                        manager.memory_client.retrieve_memories(
                            memory_id=config.memory_id, namespace=namespace, query="offline claim facts", top_k=5
                        )
                writes = [p for op, p in calls if op == "CreateEvent"]
                self.assertTrue(writes, "real SDK session creation must reach its serializer")
                self.assertTrue(all(p["actorId"] == actor and p["sessionId"] == session_id for p in writes))
                retrievals = [p["namespace"] for op, p in calls if op == "RetrieveMemoryRecords"]
                self.assertEqual(set(retrievals), expected_namespaces)
        self.assertEqual(len(actors), len(emails))
        with patch("memory.identifiers.uuid.uuid4", return_value=uuid.UUID(int=1)):
            self.assertEqual(claimant_memory_ids(emails[0]), claimant_memory_ids(emails[0]))

    def test_absent_memory_is_noop(self):
        with patch.object(session, "MEMORY_ID", ""):
            self.assertIsNone(session.get_memory_session_manager("session", "actor"))

    def test_real_sdk_actor_session_namespaces_and_retrieval_settings(self):
        memory = MagicMock()
        memory.list_events.return_value = []
        with (
            patch.object(session, "MEMORY_ID", "ClaimsAgentMemory-offline"),
            patch.object(session, "MEMORY_RETRIEVAL_TOP_K", 5),
            patch.object(session, "MEMORY_RETRIEVAL_RELEVANCE", 0.5),
            patch.object(sdk, "MemoryClient", return_value=memory),
            patch.object(sdk.boto3, "Session") as boto_session,
        ):
            manager = session.get_memory_session_manager("session", "actor")
        self.assertIsInstance(manager, sdk.AgentCoreMemorySessionManager)
        config = manager.config
        self.assertEqual(
            (config.memory_id, config.actor_id, config.session_id), ("ClaimsAgentMemory-offline", "actor", "session")
        )
        self.assertEqual(set(config.retrieval_config), {"claims/actor/facts", "claims/actor/session"})
        self.assertEqual(config.retrieval_config["claims/actor/facts"].top_k, 5)
        self.assertEqual(config.retrieval_config["claims/actor/session"].top_k, 3)
        self.assertEqual(config.retrieval_config["claims/actor/facts"].relevance_score, 0.5)
        boto_session.assert_called_once_with(region_name="us-west-2")
        self.assertTrue(memory.list_events.called)
