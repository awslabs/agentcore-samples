"""Offline application contracts; real imports, no inference or tool connections."""

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from botocore.stub import Stubber

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/ITIncidentAgent"))


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = importlib.import_module("main")

    def test_real_sdk_hooks_enforce_scope_and_record_cancelled_results(self):
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

        m = self.main
        evidence = {"tools": [], "trace_id": ""}
        m._run_evidence.set(evidence)
        m._tool_scope.set({"requester_id": "U-1", "ticket_id": "T-1"})
        tool = {
            "name": "change___create_change_request",
            "toolUseId": "tool1",
            "input": {"ticket_id": "T-2", "user_id": "U-1"},
        }
        before = BeforeToolCallEvent(agent=MagicMock(), selected_tool=None, tool_use=tool, invocation_state={})
        m._on_before_tool_call(before)
        self.assertIsInstance(before.cancel_tool, str)
        after = AfterToolCallEvent(
            agent=MagicMock(),
            selected_tool=None,
            tool_use=tool,
            invocation_state={},
            result={"status": "error", "content": [{"text": "cancelled"}]},
            cancel_message=before.cancel_tool,
        )
        with patch.object(m, "_apply_guardrail", side_effect=lambda text, **kw: text):
            m._on_after_tool_call(after)
        self.assertEqual(evidence["tools"][0]["status"], "error")

    def test_guardrail_failure_missing_and_blocked_are_closed(self):
        m = self.main
        with patch.object(m, "GUARDRAIL_ID", "guardrail"), patch.object(m, "_bedrock_runtime") as sdk:
            sdk.apply_guardrail.side_effect = RuntimeError("offline denial")
            with self.assertRaises(RuntimeError):
                m._apply_guardrail("sensitive-input")
            sdk.apply_guardrail.side_effect = None
            sdk.apply_guardrail.return_value = {"action": "GUARDRAIL_INTERVENED", "outputs": []}
            with self.assertRaises(RuntimeError):
                m._apply_guardrail("sensitive-input")
            sdk.apply_guardrail.return_value = {
                "action": "GUARDRAIL_INTERVENED",
                "outputs": [{"text": "blocked"}],
                "assessments": [{"contentPolicy": {"filters": [{"action": "BLOCKED"}]}}],
            }
            with self.assertRaises(RuntimeError):
                m._apply_guardrail("sensitive-input")
            sdk.apply_guardrail.return_value = {"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "redacted"}]}
            self.assertEqual(m._apply_guardrail("sensitive-input"), "redacted")
        with patch.object(m, "GUARDRAIL_ID", ""), self.assertRaises(RuntimeError):
            m._apply_guardrail("input")

    def test_actual_tool_results_not_start_events_and_json_error_fails(self):
        m = self.main
        evidence = {"tools": [], "trace_id": ""}
        m._run_evidence.set(evidence)
        event = types.SimpleNamespace(
            tool_use={"name": "lookup-user___lookup_user", "toolUseId": "tool1", "input": {"user_id": "U-1"}},
            exception=None,
            cancel_message=None,
            result={"status": "success", "content": [{"text": '{"error":"denied"}'}]},
        )
        m._tool_scope.set({"requester_id": "U-1", "ticket_id": "T-1"})
        m._on_before_tool_call(event)
        self.assertEqual(evidence["tools"], [])
        with patch.object(m, "_apply_guardrail", side_effect=lambda text, **kw: text):
            m._on_after_tool_call(event)
        self.assertEqual(evidence["tools"][0]["status"], "error")
        self.assertNotIn("denied", json.dumps(evidence))

    def test_change_request_cannot_target_another_incident(self):
        m = self.main
        m._tool_scope.set({"requester_id": "U-1", "ticket_id": "T-1"})
        event = types.SimpleNamespace(
            tool_use={
                "name": "change___create_change_request",
                "toolUseId": "tool1",
                "input": {"ticket_id": "T-2", "user_id": "U-1"},
            },
            cancel_tool=False,
        )
        m._on_before_tool_call(event)
        self.assertTrue(event.cancel_tool)

    def test_eventbridge_partial_failure_does_not_resolve(self):
        m = self.main
        with patch.object(m, "_events") as sdk:
            sdk.put_events.return_value = {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "Denied"}]}
            with self.assertRaises(RuntimeError):
                m._emit_resolution_event("T-1", "synthetic", "U-1")

    def test_actual_memory_parent_request_uses_sdk_model(self):
        from bedrock_agentcore.memory import MemoryClient
        from memory import enrichment

        memory = MemoryClient(
            boto3_session=boto3.Session(
                aws_access_key_id="offline", aws_secret_access_key="offline", region_name="us-west-2"
            )
        )
        with (
            Stubber(memory.gmdp_client) as stub,
            patch.object(enrichment, "MEMORY_ID", "TestMemory-1234567890"),
            patch.object(enrichment, "_memory_client", memory),
        ):
            request = {
                "memoryId": "TestMemory-1234567890",
                "namespacePath": "incidents/U-1002",
                "searchCriteria": {"searchQuery": "prior", "topK": 5},
            }
            stub.add_response("retrieve_memory_records", {"memoryRecordSummaries": []}, request)
            self.assertEqual(enrichment.retrieve_past_incidents("U-1002", "prior"), [])
            stub.assert_no_pending_responses()
            for actor in ("../other", "U-1/session", "name@example.com", ""):
                with self.assertRaises(ValueError):
                    enrichment.retrieve_past_incidents(actor, "prior")
            stub.add_client_error("retrieve_memory_records", "AccessDeniedException", expected_params=request)
            with self.assertRaises(RuntimeError):
                enrichment.retrieve_past_incidents("U-1002", "prior")

    def test_jira_is_explicit_and_cannot_silently_fall_back(self):
        from mcp_client import client, jira

        with (
            patch.object(client, "get_streamable_http_mcp_client", return_value=object()),
            patch.object(jira, "get_jira_mcp_client_sync", return_value=None) as jira_client,
        ):
            clients, warnings = client.get_all_mcp_clients_safe()
            self.assertEqual(len(clients), 1)
            self.assertEqual(warnings, [])
            jira_client.assert_not_called()
            clients, warnings = client.get_all_mcp_clients_safe(include_jira=True)
            self.assertTrue(warnings)
            jira_client.assert_called_once()

    def test_actual_jwt_decorator_receives_explicit_scopes(self):
        from mcp_client import client

        seen = {}

        def decorator(**kwargs):
            seen.update(kwargs)
            return lambda fn: lambda: fn(access_token="OFFLINE-NOT-A-TOKEN")

        with (
            patch("bedrock_agentcore.identity.auth.requires_access_token", decorator),
            patch.object(client, "GATEWAY_OAUTH_SCOPES", ["resolve:tickets"]),
        ):
            client._create_custom_jwt_client()
        self.assertEqual(seen["scopes"], ["resolve:tickets"])
        self.assertEqual(seen["auth_flow"], "M2M")


if __name__ == "__main__":
    unittest.main()
