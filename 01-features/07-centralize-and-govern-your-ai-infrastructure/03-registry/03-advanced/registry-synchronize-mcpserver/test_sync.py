"""Real Registry service model; only responses are fixtures. No endpoint is contacted."""

import json
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import boto3
import registry_synchronize_mcpserver as sync
from botocore.stub import ANY, Stubber

REGION = "us-west-2"
ACCOUNT = "111122223333"
REGISTRY = "ab1234567890"
RID = "cd1234567890"
BASE = f"arn:aws:agent-registry:{REGION}:{ACCOUNT}:registry/{REGISTRY}"
ARN = BASE + "/record/" + RID
RUNTIME = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/demo-1234567890"
PROVIDER = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:token-vault/default/oauth2credentialprovider/fixture"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/fixture"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
REG = {
    "name": "example",
    "registryId": REGISTRY,
    "registryArn": BASE,
    "createdAt": NOW,
    "updatedAt": NOW,
    "status": "READY",
    "approvalConfiguration": {"autoApprovalRules": []},
}
RECORD = {
    "name": "server",
    "registryArn": BASE,
    "recordArn": ARN,
    "recordId": RID,
    "createdAt": NOW,
    "updatedAt": NOW,
    "status": "DRAFT",
    "recordType": "MCP",
    "descriptors": {"mcpServer": {"data": '{"name":"server"}'}},
}


class SyncTests(unittest.TestCase):
    def setUp(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "receipt.json"
        self.enterContext(patch.object(sync, "RECEIPT", path))
        session = boto3.Session(aws_access_key_id="fixture", aws_secret_access_key="fixture", region_name=REGION)
        self.client = self.enterContext(closing(session.client("agent-registry-control")))
        self.stub = self.enterContext(Stubber(self.client))

    def create(self, source):
        self.stub.add_response("get_registry", REG, {"registryId": REGISTRY})
        self.stub.add_response(
            "create_registry_record",
            {"status": "CREATING", "recordArn": ARN},
            {
                "registryId": REGISTRY,
                "name": ANY,
                "clientToken": ANY,
                "recordType": "MCP",
                "descriptors": {"mcpServer": {"source": {"fromUrl": source}}},
            },
        )
        result = sync.create(self.client, REGISTRY, ACCOUNT, REGION, source)
        self.assertEqual(json.loads(sync.RECEIPT.read_text())["record_arn"], ARN)
        return result

    def test_public_sync_waits_for_metadata_then_deletes_only_the_record(self):
        source = sync.source("public", REGION, ACCOUNT)
        self.assertEqual(source, {"url": "https://knowledge-mcp.global.api.aws"})
        state = self.create(source)
        params = {"registryId": REGISTRY, "recordId": RID}
        self.stub.add_response("get_registry_record", dict(RECORD, status="CREATING"), params)
        self.stub.add_response("get_registry_record", RECORD, params)
        with patch.object(sync.time, "sleep"):
            sync.describe(self.client, state)
        self.stub.add_response("get_registry_record", RECORD, params)
        self.stub.add_response("delete_registry_record", {}, params)
        self.stub.add_client_error("get_registry_record", "ResourceNotFoundException", expected_params=params)
        sync.cleanup(self.client, state)
        self.assertFalse(sync.RECEIPT.exists())
        self.stub.assert_no_pending_responses()

    def test_protected_source_shapes(self):
        for mode, args, auth in [
            (
                "iam",
                {"role_arn": ROLE},
                {
                    "credentialProviderType": "IAM",
                    "credentialProvider": {
                        "iamCredentialProvider": {"roleArn": ROLE, "service": "bedrock-agentcore", "region": REGION}
                    },
                },
            ),
            (
                "oauth",
                {"provider_arn": PROVIDER, "scopes": ["mcp-server/invoke"]},
                {
                    "credentialProviderType": "OAUTH",
                    "credentialProvider": {
                        "oauthCredentialProvider": {
                            "providerArn": PROVIDER,
                            "grantType": "CLIENT_CREDENTIALS",
                            "scopes": ["mcp-server/invoke"],
                        }
                    },
                },
            ),
        ]:
            with self.subTest(mode=mode):
                source = sync.source(mode, REGION, ACCOUNT, RUNTIME, **args)
                self.assertEqual(
                    source,
                    {
                        "url": f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(RUNTIME, safe='')}/invocations?qualifier=DEFAULT",
                        "credentialProviderConfigurations": [auth],
                    },
                )
                self.create(source)
                sync.RECEIPT.unlink()
        self.stub.assert_no_pending_responses()

    def test_inline_drafts_do_not_fetch_or_approve(self):
        for kind, key in [("AGENT", "a2aAgentCard"), ("MCP", "mcpServer")]:
            with self.subTest(kind=kind):
                data = {"name": "reviewed fixture"}
                args = {
                    "registryId": REGISTRY,
                    "name": ANY,
                    "clientToken": ANY,
                    "recordType": kind,
                    "descriptors": {key: {"data": json.dumps(data)}},
                }
                self.stub.add_response("get_registry", REG)
                self.stub.add_response("create_registry_record", {"recordArn": ARN, "status": "DRAFT"}, args)
                state = sync.create(self.client, REGISTRY, ACCOUNT, REGION, inline=(kind, key, data))
                self.stub.add_response(
                    "get_registry_record", dict(RECORD, recordType=kind, descriptors=args["descriptors"])
                )
                sync.describe(self.client, state)
                sync.RECEIPT.unlink()
        self.stub.assert_no_pending_responses()

    def test_failed_or_empty_sync_never_reports_success(self):
        state = self.create(sync.source("public", REGION, ACCOUNT))
        for data in [dict(RECORD, status="CREATE_FAILED"), dict(RECORD, descriptors={})]:
            self.stub.add_response("get_registry_record", data)
            with self.assertRaises(RuntimeError):
                sync.describe(self.client, state)
        self.assertTrue(sync.RECEIPT.exists())
        self.stub.assert_no_pending_responses()

    def test_no_deletion_of_promoted_foreign_or_unconfirmed_records(self):
        state = self.create(sync.source("public", REGION, ACCOUNT))
        for data in [dict(RECORD, status="APPROVED"), dict(RECORD, recordArn=BASE + "/record/xx1234567890")]:
            self.stub.add_response("get_registry_record", data)
            with self.assertRaises(RuntimeError):
                sync.cleanup(self.client, state)
        with self.assertRaisesRegex(RuntimeError, "No confirmed"):
            sync.cleanup(self.client, {})
        self.stub.add_response("get_registry", REG)
        with self.assertRaises(FileExistsError):
            sync.create(self.client, REGISTRY, ACCOUNT, REGION, {"url": sync.PUBLIC_URL})
        self.assertEqual(state, json.loads(sync.RECEIPT.read_text()))
        self.stub.assert_no_pending_responses()

    def test_reject_destination_or_provider_in_other_account_or_region(self):
        for mode, runtime, args in [
            ("iam", RUNTIME.replace(ACCOUNT, "333344445555"), {"role_arn": ROLE}),
            ("iam", RUNTIME, {"role_arn": ROLE.replace(ACCOUNT, "333344445555")}),
            ("oauth", RUNTIME, {"provider_arn": PROVIDER.replace(REGION, "us-east-1"), "scopes": ["invoke"]}),
            ("oauth", "https://other.invalid", {"provider_arn": PROVIDER, "scopes": ["invoke"]}),
        ]:
            with self.assertRaises(ValueError):
                sync.source(mode, REGION, ACCOUNT, runtime, **args)
