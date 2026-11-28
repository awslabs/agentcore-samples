"""Offline malformed/duplicate incident and tool retry safety checks."""

import asyncio
import hashlib
import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from botocore.stub import ANY, Stubber
from test_contracts import ROOT, client, load

sys.path.insert(0, str(ROOT / "app/ITIncidentAgent"))


class IncidentTests(unittest.TestCase):
    def test_validation_rejects_malformed_and_namespace_pii_identifiers(self):
        with patch.dict(
            os.environ,
            {"AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/Test-1234567890"},
        ):
            trigger = load("trigger_validation", "lambdas/trigger/ticket_event_handler.py")
        valid = {
            "ticket_id": "T-1",
            "requester_id": "U-1",
            "title": "Synthetic",
            "description": "Synthetic incident",
            "synthetic": True,
        }
        self.assertIsNone(trigger._validate_ticket(valid))
        for value in (
            {},
            [],
            {**valid, "title": []},
            {**valid, "requester_id": "name@example.com"},
            {**valid, "ticket_id": "other/session"},
            {**valid, "priority": "UNKNOWN"},
            {**valid, "synthetic": False},
        ):
            self.assertIsNotNone(trigger._validate_ticket(value))

    def test_duplicate_claim_does_not_invoke_model_or_downgrade_ticket(self):
        main = importlib.import_module("main")
        payload = {
            "ticket_id": "T-1",
            "requester_id": "U-1",
            "title": "Synthetic",
            "description": "Synthetic",
            "synthetic": True,
        }

        async def collect():
            return [json.loads(row) async for row in main.invoke(payload, types.SimpleNamespace(session_id="s" * 33))]

        with (
            patch.object(main, "TICKETS_TABLE", "OwnedTickets"),
            patch.object(main, "_ddb") as ddb,
            patch.object(main, "load_model") as model,
        ):
            ddb.Table.return_value.update_item.side_effect = RuntimeError("conditional claim denied")
            rows = asyncio.run(collect())
            self.assertEqual(rows[-1]["status"], "Failed")
            model.assert_not_called()
            self.assertEqual(ddb.Table.return_value.update_item.call_count, 1)

    def test_jira_dispatch_sets_real_runtime_user_field_and_rejects_other_users(self):
        arn = "arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/Test-1234567890"
        sdk = client("bedrock-agentcore")
        with (
            patch.dict(os.environ, {"AGENT_RUNTIME_ARN": arn, "JIRA_RUNTIME_USER_ID": "U-1"}),
            patch("boto3.client", return_value=sdk),
        ):
            trigger = load("trigger_user_binding", "lambdas/trigger/ticket_event_handler.py")
            payload = {"issue_key": "INC-1", "requester_id": "U-1", "synthetic": True}
            with Stubber(sdk) as stub:
                stub.add_response(
                    "invoke_agent_runtime",
                    {"response": b"", "contentType": "application/json"},
                    {
                        "agentRuntimeArn": arn,
                        "runtimeSessionId": ANY,
                        "runtimeUserId": "U-1",
                        "payload": json.dumps(payload).encode(),
                    },
                )
                trigger._invoke_runtime(payload, "INC-1")
                stub.assert_no_pending_responses()
                with self.assertRaises(ValueError):
                    trigger._invoke_runtime({**payload, "requester_id": "U-2"}, "INC-1")

    def test_change_retry_reads_matching_record_without_second_user_stamp(self):
        ddb = client("dynamodb")
        with (
            patch.dict(os.environ, {"CHANGES_TABLE": "OwnedChanges", "USERS_TABLE": "OwnedUsers"}),
            patch("boto3.client", return_value=ddb),
        ):
            module = load("change_retry", "lambdas/tools/create_change_request.py")
        event = {"ticket_id": "T-1", "user_id": "U-1", "summary": "Synthetic remediation", "action": "rebuild_index"}
        change_id = "CHG-" + hashlib.sha256(b"T-1/U-1/rebuild_index").hexdigest()[:24]
        requests = []
        ddb.meta.events.register(
            "before-parameter-build.dynamodb.TransactWriteItems", lambda params, **kw: requests.append(params)
        )
        with Stubber(ddb) as stub:
            stub.add_response("transact_write_items", {}, {"TransactItems": ANY})
            first = module.lambda_handler(event, None)
            self.assertEqual(first["change_id"], change_id)
            stub.add_client_error(
                "transact_write_items",
                "TransactionCanceledException",
                expected_params={"TransactItems": ANY},
                modeled_fields={"CancellationReasons": [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}]},
            )
            stub.add_response(
                "get_item",
                {
                    "Item": {
                        k: {"S": v}
                        for k, v in {
                            "ticket_id": "T-1",
                            "user_id": "U-1",
                            "action": "rebuild_index",
                            "status": "applied",
                            "created_at": first["applied_at"],
                        }.items()
                    }
                },
                {"TableName": "OwnedChanges", "Key": {"change_id": {"S": change_id}}, "ConsistentRead": True},
            )
            second = module.lambda_handler(event, None)
            self.assertEqual(second, first)
            stub.assert_no_pending_responses()
        self.assertEqual(
            requests[0]["TransactItems"][0]["Put"]["ConditionExpression"], "attribute_not_exists(change_id)"
        )
        self.assertEqual(requests[0]["TransactItems"][1]["Update"]["ConditionExpression"], "attribute_exists(user_id)")


if __name__ == "__main__":
    unittest.main()
