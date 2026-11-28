"""SDK/LlamaIndex contracts with modeled or controlled responses; no service calls."""

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
from botocore.stub import ANY, Stubber
from llama_index.memory.bedrock_agentcore import AgentCoreMemory

import research_memory as demo

REGION = "us-east-1"
ACCOUNT = "111122223333"
ID = "ResearchMemory-0123456789"
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        p = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(demo, "RECEIPT", p / "receipt.json"))
        self.session = boto3.Session(aws_access_key_id="fixture", aws_secret_access_key="fixture", region_name=REGION)
        self.client = self.session.client("bedrock-agentcore")
        self.addCleanup(self.client.close)

    def test_create_only_refuses_existing_receipt_and_keeps_failed_id(self):
        manager = Mock()
        manager.create_memory.return_value = {"memoryId": ID}
        manager.get_memory_status.return_value = "FAILED"
        with self.assertRaisesRegex(RuntimeError, "failed"):
            demo.initialize(manager, ACCOUNT, REGION)
        receipt = json.loads(demo.RECEIPT.read_text())
        self.assertEqual(demo.owned_id(receipt, ACCOUNT), ID)
        with self.assertRaisesRegex(ValueError, "Foreign"):
            demo.owned_id(receipt, "333344445555")
        with self.assertRaises(FileExistsError):
            demo.initialize(manager, ACCOUNT, REGION)
        manager.create_memory.assert_called_once()
        kwargs = manager.create_memory.call_args.kwargs
        self.assertEqual(
            kwargs["strategies"],
            [{"semanticMemoryStrategy": {"name": "ResearchFacts", "namespaceTemplates": ["/research/{actorId}/"]}}],
        )
        self.assertEqual(kwargs["event_expiry_days"], 30)

    def test_supported_sdk_create_waits_for_active_before_returning(self):
        client = demo.MemoryClient(region_name=REGION)
        obj = {
            "arn": f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:memory/{ID}",
            "id": ID,
            "name": "ResearchMemory_example",
            "eventExpiryDuration": 30,
            "status": "CREATING",
            "createdAt": NOW,
            "updatedAt": NOW,
        }
        with Stubber(client.gmcp_client) as control, patch.object(demo.time, "sleep") as sleep:
            control.add_response(
                "create_memory",
                {"memory": obj},
                {
                    "name": ANY,
                    "clientToken": ANY,
                    "eventExpiryDuration": 30,
                    "memoryStrategies": [
                        {
                            "semanticMemoryStrategy": {
                                "name": "ResearchFacts",
                                "namespaceTemplates": [demo.NAMESPACE_TEMPLATE],
                            }
                        }
                    ],
                },
            )
            control.add_response("get_memory", {"memory": obj}, {"memoryId": ID})
            control.add_response("get_memory", {"memory": dict(obj, status="ACTIVE")}, {"memoryId": ID})
            demo.initialize(client, ACCOUNT, REGION)
            control.assert_no_pending_responses()
            sleep.assert_called_once()
            self.assertEqual(json.loads(demo.RECEIPT.read_text())["memory_id"], ID)

    def test_real_llamaindex_seed_writes_conversational_event(self):
        context = demo.memory_context(ID, "seed-session")
        plugin = AgentCoreMemory(context=context, region_name=REGION, client=self.client)
        event = {
            "eventId": "00000000000000000000000000000000000000000000",
            "memoryId": ID,
            "actorId": demo.ACTOR,
            "sessionId": "seed-session",
            "eventTimestamp": NOW,
            "payload": [{"conversational": {"role": "USER", "content": {"text": "fixture"}}}],
        }
        with Stubber(self.client) as stub, patch.object(demo, "AgentCoreMemory", return_value=plugin) as factory:
            stub.add_response(
                "create_event",
                {"event": event},
                {
                    "memoryId": ID,
                    "actorId": demo.ACTOR,
                    "sessionId": "seed-session",
                    "eventTimestamp": ANY,
                    "payload": [
                        {"blob": "{}"},
                        {
                            "conversational": {
                                "role": "USER",
                                "content": {
                                    "text": "For our fictional orchard study, compare the Fuji and Gala varieties. Measure weekly rainfall in millimeters."
                                },
                            }
                        },
                        {"blob": "{}"},
                        {
                            "conversational": {
                                "role": "ASSISTANT",
                                "content": {
                                    "text": "Noted the two varieties and the rainfall measure for our orchard study."
                                },
                            }
                        },
                    ],
                },
            )
            demo.seed(ID, REGION)
            stub.assert_no_pending_responses()
            actual = factory.call_args.kwargs["context"]
            self.assertEqual(actual.namespace, "/research/demo-researcher/")
            self.assertTrue(actual.session_id.startswith("planning-"))

    def test_real_function_agent_routes_to_actor_bound_memory_tool(self):
        manager = demo.MemorySessionManager(memory_id=ID, boto3_session=self.session)
        responses = []

        async def converse(**kwargs):
            messages = kwargs["messages"]
            responses.append(messages)
            if len(responses) == 1:
                block = {
                    "toolUse": {"toolUseId": "call1", "name": "search_research_memory", "input": {"query": "orchard"}}
                }
                reason = "tool_use"
            else:
                self.assertIn("Fuji and Gala", json.dumps(messages))
                block = {"text": "We chose Fuji and Gala."}
                reason = "end_turn"
            return {
                "output": {"message": {"role": "assistant", "content": [block]}},
                "stopReason": reason,
                "usage": {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5},
                "metrics": {"latencyMs": 1},
            }

        record = {
            "memoryRecordId": "mem-" + "a" * 40,
            "memoryStrategyId": "ResearchFacts-1234567890",
            "namespaces": [demo.NAMESPACE],
            "content": {"text": "Fuji and Gala"},
            "createdAt": NOW,
        }
        with (
            Stubber(manager._data_plane_client) as stub,
            patch.object(demo, "MemorySessionManager", return_value=manager),
            patch("llama_index.llms.bedrock_converse.base.converse_with_retry_async", converse),
        ):
            stub.add_response(
                "retrieve_memory_records",
                {"memoryRecordSummaries": [record]},
                {
                    "memoryId": ID,
                    "namespace": demo.NAMESPACE,
                    "searchCriteria": {"searchQuery": "orchard", "topK": 3},
                    "maxResults": 3,
                },
            )
            answer = asyncio.run(demo.ask(ID, REGION, "What did we choose?"))
            self.assertIn("Fuji and Gala", answer)
            self.assertEqual(len(responses), 2)
            stub.assert_no_pending_responses()

    def test_supplied_id_cannot_be_deleted_or_adopted(self):
        with (
            patch.object(sys, "argv", ["research_memory.py", "cleanup", "--memory-id", ID]),
            patch.object(demo, "MemoryClient") as client,
            self.assertRaises(SystemExit),
        ):
            try:
                demo.main()
            finally:
                client.assert_not_called()
        self.assertFalse(demo.RECEIPT.exists())

    def test_delete_failure_retains_receipt_then_removes_only_returned_id(self):
        state = {"memory_id": ID, "account": ACCOUNT, "region": REGION, "name": "fixture"}
        demo.save(state)
        fake_session = Mock(region_name=REGION)
        fake_session.client.return_value.get_caller_identity.return_value = {"Account": ACCOUNT}
        manager = Mock()
        manager.delete_memory_and_wait.side_effect = [RuntimeError("fixture denial"), {}]
        with (
            patch.object(sys, "argv", ["research_memory.py", "cleanup"]),
            patch.object(demo.boto3, "Session", return_value=fake_session),
            patch.object(demo, "MemoryClient", return_value=manager),
        ):
            with self.assertRaises(RuntimeError):
                demo.main()
            self.assertTrue(demo.RECEIPT.exists())
            demo.main()
            self.assertFalse(demo.RECEIPT.exists())
            manager.delete_memory_and_wait.assert_called_with(memory_id=ID, max_wait=300, poll_interval=10)
