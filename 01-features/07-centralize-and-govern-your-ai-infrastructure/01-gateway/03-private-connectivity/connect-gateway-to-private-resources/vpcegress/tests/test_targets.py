"""Offline contracts: real pinned botocore model + Stubber, no AWS connections."""
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                  AWS_DEFAULT_REGION="us-west-2", AWS_EC2_METADATA_DISABLED="true")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import boto3
from botocore.stub import Stubber, ANY
from botocore.validate import validate_parameters
import create_target
import delete_target
import delete_credential
import create_credential
import lifecycle

GATEWAY = "gateway-1234567890"
PROVIDER = "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/test-key"
MANAGED = ["--vpc-id", "vpc-123abc", "--subnet-ids", "subnet-aaa,subnet-bbb"]
BASE = ["--name", "test-target", "--endpoint", "https://internal.example.com/mcp"]


def response(status="READY"):
    return {"gatewayArn": f"arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/{GATEWAY}",
            "targetId": "target1234", "name": "test-target", "status": status,
            "createdAt": datetime.now(timezone.utc), "updatedAt": datetime.now(timezone.utc),
            "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": "https://internal.example.com/mcp"}}},
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]}


class TargetContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.session = Mock(region_name="us-west-2")
        self.identity = Mock()
        self.identity.get_caller_identity.return_value = {'Account': '123456789012'}
        self.session.client.side_effect = lambda name: self.identity if name == 'sts' else self.client
        self.patches = [patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")),
                        patch.object(lifecycle, "STATE_DIR", self.root),
                        patch.object(delete_credential, "STATE_DIR", self.root),
                        patch.object(boto3, "Session", return_value=self.session),
                        patch.dict(os.environ, {"GATEWAY_ID": GATEWAY}),
                        patch.object(lifecycle.time, "sleep")]
        for p in self.patches:
            p.start()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        for p in reversed(self.patches):
            p.stop()
        self.stub.deactivate()
        self.temp.cleanup()

    def create(self, extra=MANAGED, status="READY"):
        self.stub.add_response("create_gateway_target", response("CREATING"))
        self.stub.add_response("get_gateway_target", response(status))
        create_target.main(BASE + extra)
        self.stub.assert_no_pending_responses()
        return lifecycle.load(lifecycle.record_path("target", "test-target"))

    def test_managed_sdk_shape(self):
        record = self.create(MANAGED + ["--security-group-ids", "sg-aaa", "--listing-mode", "DYNAMIC"])
        request = record["request"]
        validate_parameters(request, self.client.meta.service_model.operation_model("CreateGatewayTarget").input_shape)
        self.assertEqual(request["privateEndpoint"]["managedVpcResource"]["securityGroupIds"], ["sg-aaa"])
        self.assertEqual(request["targetConfiguration"]["mcp"]["mcpServer"]["listingMode"], "DYNAMIC")
        self.assertEqual(record["status"], "READY")

    def test_self_managed_sdk_shape(self):
        arn = "arn:aws:vpc-lattice:us-west-2:222222222222:resourceconfiguration/rcfg-0123456789abcdef0"
        record = self.create(["--resource-configuration-identifier", arn])
        self.assertEqual(record["request"]["privateEndpoint"], {"selfManagedLatticeResource": {"resourceConfigurationIdentifier": arn}})

    def test_invalid_modes_before_clients(self):
        bad = [[], ["--vpc-id", "vpc-123"], ["--subnet-ids", "subnet-aaa"],
               ["--security-group-ids", "sg-aaa"], MANAGED + ["--security-group-ids", ""],
               ["--vpc-id", "vpc-123", "--subnet-ids", "subnet-aaa,"],
               MANAGED + ["--resource-configuration-identifier", "rc-123"],
               ["--resource-configuration-identifier", "arn:aws:vpc-lattice:us-west-2:123456789012:resourcegateway/rgw-123"],
               ["--resource-configuration-identifier", ""], ["--routing-domain", "alb.example.com"]]
        for flags in bad:
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                create_target.main(BASE + flags)
        boto3.Session.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_openapi_api_key_routing(self):
        schema = self.root / "api.json"
        schema.write_text(json.dumps({"openapi": "3.0.0", "paths": {"/health": {}}, "servers": [{"url": "https://placeholder"}]}))
        record = self.create(MANAGED + ["--openapi-schema-file", str(schema), "--credential-provider-arn", PROVIDER,
                                      "--routing-domain", "internal-alb.example.com"])
        request = record["request"]
        self.assertEqual(request["credentialProviderConfigurations"][0]["credentialProviderType"], "API_KEY")
        self.assertEqual(json.loads(request["targetConfiguration"]["mcp"]["openApiSchema"]["inlinePayload"])["servers"],
                         [{"url": BASE[3]}])
        self.assertNotIn("internal.example.com", schema.read_text())

    def test_failed_create_preserves_id_and_fails(self):
        with self.assertRaises(RuntimeError):
            self.create(status="FAILED")
        record = lifecycle.load(lifecycle.record_path("target", "test-target"))
        self.assertEqual(record["targetId"], "target1234")
        self.assertEqual(record["status"], "FAILED")

    def test_update_unsuccessful_fails(self):
        with self.assertRaises(RuntimeError):
            self.create(status="UPDATE_UNSUCCESSFUL")

    def test_conflict_never_adopts(self):
        self.stub.add_client_error("create_gateway_target", service_error_code="ConflictException")
        with self.assertRaises(Exception):
            create_target.main(BASE + MANAGED)
        record = lifecycle.load(lifecycle.record_path("target", "test-target"))
        self.assertNotIn("targetId", record)
        self.assertIn("clientToken", record["request"])
        self.stub.assert_no_pending_responses()

    def test_timeout_preserves_state(self):
        path = lifecycle.record_path("target", "test-target")
        record = {"gatewayId": GATEWAY, "targetId": "target1234"}
        for _ in range(2):
            self.stub.add_response("get_gateway_target", response("CREATING"))
        with self.assertRaises(TimeoutError):
            lifecycle.wait_target(self.client, record, path, attempts=2, delay=0)
        self.assertEqual(lifecycle.load(path)["status"], "CREATING")

    def owned_target(self):
        record = {"name": "test-target", "gatewayId": GATEWAY, "targetId": "target1234", "region": "us-west-2", "accountId": "123456789012", "status": "READY"}
        path = lifecycle.record_path("target", "test-target")
        lifecycle.save(path, record)
        return path

    def test_delete_waits_for_absence(self):
        path = self.owned_target()
        deleted = {key: response("DELETING")[key] for key in ("gatewayArn", "targetId", "status")}
        self.stub.add_response("delete_gateway_target", deleted, {"gatewayIdentifier": GATEWAY, "targetId": "target1234"})
        self.stub.add_response("get_gateway_target", response("DELETING"))
        self.stub.add_client_error("get_gateway_target", service_error_code="ResourceNotFoundException")
        delete_target.main(["--name", "test-target"])
        self.assertEqual(lifecycle.load(path)["status"], "DELETED")
        self.stub.assert_no_pending_responses()

    def test_delete_denied_preserves_record(self):
        path = self.owned_target()
        self.stub.add_client_error("delete_gateway_target", service_error_code="AccessDeniedException")
        with self.assertRaises(Exception):
            delete_target.main(["--name", "test-target"])
        self.assertEqual(lifecycle.load(path)["status"], "READY")

    def test_wrong_account_does_not_treat_not_found_as_cleanup(self):
        path = self.owned_target()
        self.identity.get_caller_identity.return_value = {'Account': '222222222222'}
        with self.assertRaisesRegex(RuntimeError, 'Caller account differs'):
            delete_target.main(['--name', 'test-target'])
        self.assertEqual(lifecycle.load(path)['status'], 'READY')
        self.assertNotIn('bedrock-agentcore-control', [call.args[0] for call in self.session.client.call_args_list])

    def test_record_without_account_requires_reconciliation(self):
        path = self.owned_target()
        record = lifecycle.load(path)
        del record['accountId']
        lifecycle.save(path, record)
        with self.assertRaisesRegex(ValueError, 'lacks accountId'):
            delete_target.main(['--name', 'test-target'])
        boto3.Session.assert_not_called()

    def test_delete_without_ownership_fails_before_client(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            delete_target.main(["--name", "unowned"])
        boto3.Session.assert_not_called()

    def test_credentials_blocked_by_failed_target(self):
        lifecycle.save(lifecycle.record_path("credential", "test-key"), {"type": "api-key", "arn": PROVIDER,
            "secretArn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:test-123456", "createdTime": "2026-11-18T00:00:00+00:00"})
        lifecycle.save(lifecycle.record_path("target", "test-target"), {"status": "FAILED", "request": {"providerArn": PROVIDER}})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            delete_credential.main(["--name", "test-key", "--type", "api-key"])
        boto3.Session.assert_not_called()

    def test_provider_references_on_later_gateway_page_block_delete(self):
        lifecycle.save(lifecycle.record_path('credential', 'test-key'), {'name': 'test-key', 'type': 'api-key',
            'arn': PROVIDER, 'region': 'us-west-2', 'accountId': '123456789012', 'status': 'CREATED',
            'secretArn': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-123456', 'createdTime': '2026-11-18T00:00:00+00:00'})
        now = datetime.now(timezone.utc)
        self.stub.add_response('list_gateways', {'items': [], 'nextToken': 'next'})
        self.stub.add_response('list_gateways', {'items': [{'gatewayId': GATEWAY, 'name': 'other-gateway', 'authorizerType': 'AWS_IAM',
            'status': 'READY', 'createdAt': now, 'updatedAt': now}]})
        self.stub.add_response('list_gateway_targets', {'items': [{'targetId': 'target1234', 'name': 'external-target',
            'status': 'READY', 'createdAt': now, 'updatedAt': now}]})
        detail = response()
        detail['credentialProviderConfigurations'] = [{'credentialProviderType': 'API_KEY', 'credentialProvider': {
            'apiKeyCredentialProvider': {'providerArn': PROVIDER, 'credentialLocation': 'HEADER', 'credentialParameterName': 'x-api-key'}}}]
        self.stub.add_response('get_gateway_target', detail)
        with self.assertRaisesRegex(RuntimeError, 'still referenced'):
            delete_credential.main(['--name', 'test-key', '--type', 'api-key'])
        self.stub.assert_no_pending_responses()

    def test_deleted_provider_name_is_not_reused_for_cleanup(self):
        lifecycle.save(lifecycle.record_path('credential', 'test-key'), {'name': 'test-key', 'type': 'api-key',
            'arn': PROVIDER, 'region': 'us-west-2', 'status': 'DELETED'})
        delete_credential.main(['--name', 'test-key', '--type', 'api-key'])
        boto3.Session.assert_not_called()

    def test_inventory_pagination(self):
        self.stub.add_response("list_gateways", {"items": [], "nextToken": "next"}, {"maxResults": 100})
        self.stub.add_response("list_gateways", {"items": []}, {"maxResults": 100, "nextToken": "next"})
        self.assertEqual(list(delete_credential.pages(self.client, "list_gateways", maxResults=100)), [])
        self.stub.assert_no_pending_responses()

    def test_resume_reuses_token_after_lost_create_response(self):
        self.stub.add_client_error('create_gateway_target', service_error_code='ThrottlingException')
        with self.assertRaises(Exception):
            create_target.main(BASE + MANAGED)
        path = lifecycle.record_path('target', 'test-target')
        request = lifecycle.load(path)['request']
        self.stub.add_response('create_gateway_target', response('CREATING'), request)
        self.stub.add_response('get_gateway_target', response())
        create_target.main(['--name', 'test-target', '--resume'])
        self.assertEqual(lifecycle.load(path)['request']['clientToken'], request['clientToken'])
        self.stub.assert_no_pending_responses()

    def test_oauth_scopes_preserved(self):
        arn = PROVIDER.replace('apikeycredentialprovider', 'oauth2credentialprovider')
        record = self.create(MANAGED + ['--credential-provider-arn', arn, '--scopes', 'api/mcp,tools/read'])
        self.assertEqual(record['request']['credentialProviderConfigurations'][0]['credentialProvider']['oauthCredentialProvider']['scopes'], ['api/mcp', 'tools/read'])

    def test_lambda_iam_contract(self):
        tools = self.root / 'tools.json'
        tools.write_text(json.dumps([{'name': 'echo', 'description': 'Echo tool', 'inputSchema': {'type': 'object', 'properties': {}}}]))
        self.stub.add_response('create_gateway_target', response('CREATING'))
        self.stub.add_response('get_gateway_target', response())
        create_target.main(['--name', 'lambda', '--lambda-arn', 'arn:aws:lambda:us-west-2:123456789012:function:sample', '--tool-schema-file', str(tools)] + MANAGED)
        record = lifecycle.load(lifecycle.record_path('target', 'lambda'))
        self.assertEqual(record['request']['credentialProviderConfigurations'], [{'credentialProviderType': 'GATEWAY_IAM_ROLE'}])

    def test_credential_create_no_secret_in_ledger(self):
        self.stub.add_response("create_api_key_credential_provider", {"name": "test-key", "credentialProviderArn": PROVIDER,
                              "apiKeySecretArn": {"secretArn": "arn:aws:secretsmanager:us-west-2:123456789012:secret:test-123456"}})
        self.stub.add_response('get_api_key_credential_provider', {'name': 'test-key', 'credentialProviderArn': PROVIDER,
            'apiKeySecretArn': {'secretArn': 'arn:aws:secretsmanager:us-west-2:123456789012:secret:test-123456'},
            'createdTime': datetime(2026,11,18,tzinfo=timezone.utc), 'lastUpdatedTime': datetime(2026,11,18,tzinfo=timezone.utc)}, {'name':'test-key'})
        create_credential.main(["--name", "test-key", "--type", "api-key", "--api-key-value", "fake-value-not-to-persist"])
        self.assertNotIn("fake-value-not-to-persist", lifecycle.record_path("credential", "test-key").read_text())


if __name__ == "__main__":
    unittest.main()
