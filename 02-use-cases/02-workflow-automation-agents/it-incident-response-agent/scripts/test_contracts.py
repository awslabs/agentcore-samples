"""Offline SDK contracts using real boto3 models/Stubber; never AWS or model calls.
Run with the application's exact frozen dependencies available on PYTHONPATH.
"""

import copy
import hashlib
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.parsers import create_parser
from botocore.serialize import create_serializer
from botocore.stub import ANY, Stubber
from verify_evidence import EVALUATORS, TOOLS, verify

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "111111111111"
REGION = "us-west-2"
STACK = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/Test-dev/12345678-1234-1234-1234-123456789012"
SECRET = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:external-jira-abcdef"
VERSION = "a" * 32


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def client(service):
    return boto3.client(service, region_name=REGION, aws_access_key_id="offline", aws_secret_access_key="offline")


class JiraTests(unittest.TestCase):
    def setUp(self):
        self.module = load("jira_provider_test", "lambdas/infra/jira_oauth_provider.py")
        self.control, self.ddb, self.secrets = (
            client("bedrock-agentcore-control"),
            client("dynamodb"),
            client("secretsmanager"),
        )
        self.stubs = {
            "bedrock-agentcore-control": Stubber(self.control),
            "dynamodb": Stubber(self.ddb),
            "secretsmanager": Stubber(self.secrets),
        }
        self.clients = {"bedrock-agentcore-control": self.control, "dynamodb": self.ddb, "secretsmanager": self.secrets}
        for stub in self.stubs.values():
            stub.activate()
            self.addCleanup(stub.deactivate)
        self.addCleanup(patch.stopall)
        patch.object(self.module.boto3, "client", side_effect=lambda service, **kw: self.clients[service]).start()
        patch.dict(os.environ, {"OWNERSHIP_TABLE": "JiraOwnership"}).start()
        self.event = {
            "RequestType": "Create",
            "StackId": STACK,
            "LogicalResourceId": "JiraProvider",
            "ResourceProperties": {
                "ProviderName": "test-jira",
                "ClientId": "test-client",
                "SecretArn": SECRET,
                "SecretVersionId": VERSION,
            },
        }
        self.owner = hashlib.sha256(f"{STACK}/JiraProvider".encode()).hexdigest()
        self.name = "test-jira-" + self.owner[:24]
        self.arn = (
            f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:token-vault/default/oauth2credentialprovider/{self.name}"
        )
        self.current = {
            "clientSecretArn": {"secretArn": SECRET},
            "name": self.name,
            "credentialProviderArn": self.arn,
            "credentialProviderVendor": "AtlassianOauth2",
            "createdTime": 1000.0,
            "lastUpdatedTime": 1000.0,
            "oauth2ProviderConfigOutput": {
                "atlassianOauth2ProviderConfig": {
                    "clientId": "test-client",
                    "oauthDiscovery": {"discoveryUrl": "https://example.invalid/.well-known/openid-configuration"},
                }
            },
            "callbackUrl": "https://example.invalid/callback",
            "status": "READY",
        }
        self.proof = {
            "owner": {"S": self.owner},
            "Status": {"S": "CREATED"},
            "ProviderArn": {"S": self.arn},
            "CreatedTime": {"S": "1000.0"},
        }

    def get_record(self, owned=True):
        self.stubs["dynamodb"].add_response(
            "get_item",
            {"Item": self.proof} if owned else {},
            {"TableName": "JiraOwnership", "Key": {"owner": {"S": self.owner}}, "ConsistentRead": True},
        )

    def get_provider(self, response=None):
        self.stubs["bedrock-agentcore-control"].add_response(
            "get_oauth2_credential_provider", response or self.current, {"name": self.name}
        )

    def absent(self):
        self.stubs["bedrock-agentcore-control"].add_client_error(
            "get_oauth2_credential_provider", "ResourceNotFoundException", expected_params={"name": self.name}
        )

    def test_unrelated_collision_never_reads_secret_or_updates(self):
        self.get_record(False)
        self.get_provider()
        with self.assertRaisesRegex(RuntimeError, "ownership unproven"):
            self.module.handler(self.event, None)
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()

    def test_verified_create_retry_only_reads(self):
        self.get_record()
        self.get_provider()
        self.get_provider()
        result = self.module.handler(self.event, None)
        self.assertEqual(result["Data"]["ProviderArn"], self.arn)

    def test_delete_denial_propagates_and_record_is_not_cleared(self):
        self.event["RequestType"] = "Delete"
        self.get_record()
        self.get_provider()
        self.stubs["bedrock-agentcore-control"].add_client_error(
            "delete_oauth2_credential_provider", "AccessDeniedException", expected_params={"name": self.name}
        )
        with self.assertRaises(self.control.exceptions.AccessDeniedException):
            self.module.handler(self.event, None)

    def test_delete_not_found_is_success(self):
        self.event["RequestType"] = "Delete"
        self.get_record(False)
        self.absent()
        self.assertEqual(self.module.handler(self.event, None)["PhysicalResourceId"], self.name)

    def test_delete_wrong_fingerprint_and_read_denial_fail(self):
        self.event["RequestType"] = "Delete"
        self.get_record()
        self.get_provider({**self.current, "createdTime": 2000.0})
        with self.assertRaises(RuntimeError):
            self.module.handler(self.event, None)

    def test_unproven_create_conflict_never_updates(self):
        self.get_record(False)
        self.absent()
        self.stubs["dynamodb"].add_response(
            "put_item",
            {},
            {
                "TableName": "JiraOwnership",
                "Item": ANY,
                "ConditionExpression": "attribute_not_exists(#o)",
                "ExpressionAttributeNames": {"#o": "owner"},
            },
        )
        self.stubs["secretsmanager"].add_response(
            "get_secret_value",
            {"VersionId": VERSION, "SecretString": '{"client_secret":"offline"}'},
            {"SecretId": SECRET, "VersionId": VERSION},
        )
        self.stubs["bedrock-agentcore-control"].add_client_error(
            "create_oauth2_credential_provider",
            "ConflictException",
            expected_params={
                "name": self.name,
                "credentialProviderVendor": "AtlassianOauth2",
                "oauth2ProviderConfigInput": {
                    "atlassianOauth2ProviderConfig": {"clientId": "test-client", "clientSecret": "offline"}
                },
            },
        )
        with self.assertRaises(self.control.exceptions.ConflictException):
            self.module.handler(self.event, None)
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()

    def test_readiness_cannot_switch_provider_identity(self):
        self.get_record()
        self.get_provider()
        self.get_provider({**self.current, "createdTime": 2000.0})
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.module.handler(self.event, None)

    def test_delete_transport_timeout_is_not_success(self):
        from botocore.exceptions import ReadTimeoutError

        self.get_record()
        self.get_provider()
        self.event["RequestType"] = "Delete"
        with (
            patch.object(
                self.control,
                "delete_oauth2_credential_provider",
                side_effect=ReadTimeoutError(endpoint_url="https://offline.invalid"),
            ),
            self.assertRaises(ReadTimeoutError),
        ):
            self.module.handler(self.event, None)

    def test_secret_only_rotation_uses_version_and_actual_sdk_shape(self):
        self.event.update(
            RequestType="Update",
            PhysicalResourceId=self.name,
            OldResourceProperties=copy.deepcopy(self.event["ResourceProperties"]),
        )
        self.event["ResourceProperties"]["SecretVersionId"] = "b" * 32
        self.get_record()
        self.get_provider()
        self.stubs["secretsmanager"].add_response(
            "get_secret_value",
            {
                "ARN": SECRET,
                "Name": "external-jira",
                "VersionId": "b" * 32,
                "SecretString": json.dumps({"client_secret": "OFFLINE-ROTATED-SENTINEL"}),
            },
            {"SecretId": SECRET, "VersionId": "b" * 32},
        )
        args = {
            "name": self.name,
            "credentialProviderVendor": "AtlassianOauth2",
            "oauth2ProviderConfigInput": {
                "atlassianOauth2ProviderConfig": {"clientId": "test-client", "clientSecret": "OFFLINE-ROTATED-SENTINEL"}
            },
        }
        # Exercise real serializer and protocol response parser, not just shape fixtures.
        op = self.control.meta.service_model.operation_model("UpdateOauth2CredentialProvider")
        encoded = create_serializer(self.control.meta.service_model.protocol).serialize_to_request(args, op)
        self.assertIn(b"clientSecret", encoded["body"])
        parsed = create_parser(self.control.meta.service_model.protocol).parse(
            {"headers": {}, "status_code": 200, "body": json.dumps(self.current).encode()}, op.output_shape
        )
        self.assertEqual(parsed["credentialProviderArn"], self.arn)
        self.stubs["bedrock-agentcore-control"].add_response("update_oauth2_credential_provider", self.current, args)
        self.stubs["dynamodb"].add_response(
            "update_item",
            {},
            {
                "TableName": "JiraOwnership",
                "Key": {"owner": {"S": self.owner}},
                "UpdateExpression": "SET SecretVersionId = :v",
                "ExpressionAttributeValues": {":v": {"S": "b" * 32}},
            },
        )
        self.get_provider()
        result = self.module.handler(self.event, None)
        self.assertNotIn("SENTINEL", json.dumps(result))
        for stub in self.stubs.values():
            stub.assert_no_pending_responses()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.module = load("transaction_test", "lambdas/infra/transaction_search.py")
        self.xray = client("xray")
        self.stub = Stubber(self.xray)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        patcher = patch.object(self.module.boto3, "client", return_value=self.xray)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.event = {
            "RequestType": "Create",
            "StackId": STACK,
            "ResourceProperties": {
                "Account": ACCOUNT,
                "Region": REGION,
                "IndexingPercentage": "100",
                "AllowMutation": "false",
            },
        }
        self.context = types.SimpleNamespace(
            invoked_function_arn=f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:test",
            get_remaining_time_in_millis=lambda: 120000,
        )

    def observe(self, status="ACTIVE", desired=100, actual=100, destination="CloudWatchLogs"):
        self.stub.add_response("get_trace_segment_destination", {"Destination": destination, "Status": status}, {})
        self.stub.add_response(
            "get_indexing_rules",
            {
                "IndexingRules": [
                    {
                        "Name": "Default",
                        "Rule": {
                            "Probabilistic": {"DesiredSamplingPercentage": desired, "ActualSamplingPercentage": actual}
                        },
                    }
                ]
            },
            {},
        )

    def test_observed_ready_only(self):
        self.observe()
        self.observe()
        result = self.module.handler(self.event, self.context)
        self.assertEqual(result["Data"]["ActualIndexingPercentage"], "100")

    def test_pending_then_ready_and_timeout(self):
        self.observe("PENDING")
        self.observe("PENDING")
        self.observe()
        with patch.object(self.module.time, "sleep"):
            self.module.handler(self.event, self.context)
        self.observe("PENDING")
        self.observe("PENDING")
        with patch.object(self.module, "MAX_POLLS", 1), self.assertRaises(TimeoutError):
            self.module.handler(self.event, self.context)

    def test_actual_sampling_mismatch_is_not_success(self):
        self.observe(actual=1)
        self.observe(actual=1)
        with patch.object(self.module, "MAX_POLLS", 1), self.assertRaises(TimeoutError):
            self.module.handler(self.event, self.context)

    def test_index_write_denial_and_unapproved_mutation(self):
        self.event["ResourceProperties"]["AllowMutation"] = "true"
        with self.assertRaises(ValueError):
            self.module.handler(self.event, self.context)
        self.event["ResourceProperties"]["AccountOwnerApproval"] = ACCOUNT + "/" + REGION
        self.observe(desired=1, actual=1)
        self.stub.add_client_error(
            "update_indexing_rule",
            "InvalidRequestException",
            expected_params={"Name": "Default", "Rule": {"Probabilistic": {"DesiredSamplingPercentage": 100.0}}},
        )
        with self.assertRaises(self.xray.exceptions.InvalidRequestException):
            self.module.handler(self.event, self.context)

    def test_wrong_account_and_read_denial_fail(self):
        other = types.SimpleNamespace(invoked_function_arn=f"arn:aws:lambda:{REGION}:222222222222:function:test")
        with self.assertRaises(ValueError):
            self.module.handler(self.event, other)
        self.stub.add_client_error("get_trace_segment_destination", "AccessDeniedException", expected_params={})
        from botocore.exceptions import ClientError

        with self.assertRaises(ClientError):
            self.module.handler(self.event, self.context)

    def test_delete_never_touches_shared_settings(self):
        self.event["RequestType"] = "Delete"
        self.module.handler(self.event, self.context)
        self.stub.assert_no_pending_responses()


class EvidenceTests(unittest.TestCase):
    def fixture(self):
        tickets, evaluations = [], []
        for i in range(2):
            e = {
                "ticket_id": str(i),
                "session_id": "session" + str(i),
                "trace_id": str(i + 1) * 32,
                "started_at": "2026-01-01T00:00:01+00:00",
                "completed_at": "2026-01-01T00:01:01+00:00",
                "event_id": "event-id",
                "tools": [
                    {"name": tool, "tool_use_id": tool, "status": "success", "result_sha256": "a" * 64}
                    for tool in TOOLS
                ],
                "memory_record_ids": ["record"] if i else [],
                "memory_namespaces": ["incidents/user/session0"] if i else [],
            }
            tickets.append(
                {
                    "ticket_id": str(i),
                    "requester_id": "user",
                    "status": "Resolved",
                    "resolution_comment": "Synthetic resolution",
                    "workflow_evidence": e,
                }
            )
            evaluations.extend(
                {
                    "trace_id": e["trace_id"],
                    "session_id": e["session_id"],
                    "timestamp": "2026-01-01T00:03:01+00:00",
                    "evaluator": name,
                    "score": 0.8,
                }
                for name in EVALUATORS
            )
        return tickets, evaluations, 1767225600

    def test_complete_correlated_fixture(self):
        self.assertEqual(verify(*self.fixture())["tickets"], 2)

    def test_missing_error_old_uncorrelated_results_never_pass(self):
        for change in (
            lambda t, e: e.pop(),
            lambda t, e: e[0].update(score=None),
            lambda t, e: e[0].update(error="denied"),
            lambda t, e: e[0].update(timestamp="2020-01-01T00:00:00Z"),
            lambda t, e: e[0].update(session_id="unrelated"),
            lambda t, e: t[1]["workflow_evidence"].update(memory_record_ids=[]),
            lambda t, e: t[0]["workflow_evidence"].update(tools=[]),
        ):
            t, e, start = self.fixture()
            change(t, e)
            with self.assertRaises(ValueError):
                verify(t, e, start)


if __name__ == "__main__":
    unittest.main()
