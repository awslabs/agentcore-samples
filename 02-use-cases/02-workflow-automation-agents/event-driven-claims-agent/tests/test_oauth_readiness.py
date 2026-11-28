"""EVENT-2: genuine pinned parsed-wire status, never a live service response."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import botocore
import botocore.session
from botocore.exceptions import ClientError
from wire_contract import parsed_response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import configure_target
import lifecycle

MISSING = object()


class OAuthReadinessTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(botocore.__version__, "1.44.10")
        self.model = botocore.session.get_session().get_service_model("bedrock-agentcore-control")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "agentcore").mkdir()
        (self.root / "agentcore/agentcore.json").write_bytes((ROOT / "agentcore/agentcore.json").read_bytes())
        self.state = configure_target.configure_fresh(self.root, "111122223333", "us-west-2", "readiness1")
        self.provider_arn = (
            "arn:aws:bedrock-agentcore:us-west-2:111122223333:token-vault/default/"
            "oauth2credentialprovider/" + self.state["provider_name"]
        )
        self.secret_arn = "arn:aws:secretsmanager:us-west-2:111122223333:secret:offline-abcdef"
        self.discovery = (
            "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_offline/.well-known/openid-configuration"
        )
        self.cognito, self.control = MagicMock(), MagicMock()
        self.cognito.list_user_pools.return_value = {"UserPools": []}
        self.cognito.describe_user_pool_domain.return_value = {}
        self.cognito.create_user_pool.return_value = {
            "UserPool": {
                "Id": "us-west-2_offline",
                "Arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_offline",
            }
        }
        self.cognito.create_user_pool_client.return_value = {
            "UserPoolClient": {"ClientId": "offlineclient", "ClientSecret": "DO-NOT-PERSIST"}
        }
        self.cognito.describe_user_pool.return_value = {
            "UserPool": {"UserPoolTags": {"claims:owner": self.state["owner"]}}
        }
        self.control.list_tags_for_resource.return_value = {"tags": {"claims:owner": self.state["owner"]}}
        self.clients = lambda service: {"cognito-idp": self.cognito, "bedrock-agentcore-control": self.control}[service]

    def response(self, operation, status=MISSING):
        body = {
            "name": self.state["provider_name"],
            "credentialProviderArn": self.provider_arn,
            "clientSecretArn": {"secretArn": self.secret_arn},
        }
        if operation == "GetOauth2CredentialProvider":
            body.update(
                credentialProviderVendor="CustomOauth2",
                createdTime=1795000000,
                lastUpdatedTime=1795000001,
                oauth2ProviderConfigOutput={
                    "customOauth2ProviderConfig": {
                        "clientId": "offlineclient",
                        "oauthDiscovery": {"discoveryUrl": self.discovery},
                    }
                },
            )
        if status is not MISSING:
            body["status"] = status
        return parsed_response(self.model, operation, body)

    def setup_sequence(self, statuses, create_status="CREATING"):
        self.control.create_oauth2_credential_provider.return_value = self.response(
            "CreateOauth2CredentialProvider", create_status
        )
        responses = iter(statuses)
        first = True

        def get_provider(**kwargs):
            nonlocal first
            if first:
                first = False
                raise ClientError(
                    {"Error": {"Code": "ResourceNotFoundException", "Message": "absent"}}, "GetOauth2CredentialProvider"
                )
            saved = json.loads((self.root / ".claims-v1.json").read_text())
            self.assertEqual(
                (saved["provider_arn"], saved["secret_arn"], saved["phase"]),
                (self.provider_arn, self.secret_arn, "waiting-provider"),
            )
            self.assertFalse((self.root / "agentcore/.cli/deployed-state.json").exists())
            return self.response("GetOauth2CredentialProvider", next(responses))

        self.control.get_oauth2_credential_provider.side_effect = get_provider

    def assert_journal_retained(self):
        saved = json.loads((self.root / ".claims-v1.json").read_text())
        self.assertEqual(saved["provider_arn"], self.provider_arn)
        self.assertEqual(saved["secret_arn"], self.secret_arn)
        self.assertEqual(saved["phase"], "waiting-provider")
        self.assertFalse((self.root / "agentcore/.cli/deployed-state.json").exists())
        self.assertNotIn("DO-NOT-PERSIST", json.dumps(saved))
        self.control.delete_oauth2_credential_provider.assert_not_called()
        self.control.update_oauth2_credential_provider.assert_not_called()

    def test_malformed_credential_reference_never_reaches_journal_or_poll(self):
        invalid = [
            "RAW-VALUE-MUST-NOT-BE-JOURNALED",
            self.secret_arn.replace("111122223333", "999988887777"),
            self.secret_arn.replace("us-west-2", "us-east-1"),
            self.secret_arn.replace(":secretsmanager:", ":s3:"),
            self.secret_arn + "\n",
            self.secret_arn.removesuffix("-abcdef"),
        ]
        for reference in invalid:
            with self.subTest(reference_kind=invalid.index(reference)):
                self.state["phase"] = "configured"
                self.state.pop("secret_arn", None)
                self.setup_sequence(["READY"])
                reply = self.control.create_oauth2_credential_provider.return_value
                reply["clientSecretArn"] = {"secretArn": reference}
                self.control.get_oauth2_credential_provider.side_effect = [
                    ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "absent"}}, "Get"),
                    self.response("GetOauth2CredentialProvider", "READY"),
                ]
                with self.assertRaises(ValueError):
                    lifecycle.setup(self.root, self.state, self.clients)
                saved = json.loads((self.root / ".claims-v1.json").read_text())
                self.assertEqual(saved["provider_arn"], self.provider_arn)
                self.assertEqual(saved["phase"], "waiting-provider")
                self.assertNotIn("secret_arn", saved)
                self.assertNotIn(reference, json.dumps(saved))
                self.assertEqual(self.control.get_oauth2_credential_provider.call_count, 1)
                self.assertFalse((self.root / "agentcore/.cli").exists())
                self.control.delete_oauth2_credential_provider.assert_not_called()
                self.control.update_oauth2_credential_provider.assert_not_called()
                self.control.reset_mock()

    def test_invalid_descriptor_keeps_provider_identity_without_serializing_reply(self):
        for descriptor in (None, [], {"secretArn": 123}, {}):
            with self.subTest(descriptor_type=type(descriptor).__name__):
                self.state["phase"] = "configured"
                self.state.pop("secret_arn", None)
                self.setup_sequence([])
                self.control.create_oauth2_credential_provider.return_value["clientSecretArn"] = descriptor
                with self.assertRaises(ValueError):
                    lifecycle.setup(self.root, self.state, self.clients)
                saved = json.loads((self.root / ".claims-v1.json").read_text())
                self.assertEqual(saved["provider_arn"], self.provider_arn)
                self.assertEqual(saved["phase"], "waiting-provider")
                self.assertNotIn("secret_arn", saved)
                self.assertFalse((self.root / "agentcore/.cli").exists())
                self.control.reset_mock()

    def test_managed_storage_arn_is_retained_as_a_reference(self):
        self.secret_arn = (
            "arn:aws:secretsmanager:us-west-2:111122223333:secret:bedrock-agentcore-identity!fixture/a_+=.@-ABC123"
        )
        self.setup_sequence(["READY"])
        lifecycle.setup(self.root, self.state, self.clients)
        self.assertEqual(lifecycle.load(self.root)["secret_arn"], self.secret_arn)
        files = [p.read_text() for p in self.root.rglob("*") if p.is_file()]
        self.assertNotIn("DO-NOT-PERSIST", "\n".join(files))

    def test_status_is_genuinely_modeled_but_optional(self):
        expected = ["CREATING", "CREATE_FAILED", "UPDATING", "UPDATE_FAILED", "READY", "DELETING", "DELETE_FAILED"]
        for operation in ("CreateOauth2CredentialProvider", "GetOauth2CredentialProvider"):
            shape = self.model.operation_model(operation).output_shape
            self.assertNotIn("status", shape.required_members)
            self.assertEqual(shape.members["status"].enum, expected)
            self.assertNotIn("status", self.response(operation))

    def test_creating_to_ready_journals_before_poll_and_advances_only_after_ready(self):
        self.setup_sequence(["CREATING", "READY"])
        with patch.object(lifecycle.time, "sleep") as sleep:
            lifecycle.setup(self.root, self.state, self.clients)
        sleep.assert_called_once_with(lifecycle.PROVIDER_READY_INTERVAL_SECONDS)
        self.assertEqual(lifecycle.load(self.root)["phase"], "auth-ready")
        self.assertEqual(self.state["provider_status"], "READY")
        deployed = json.loads((self.root / "agentcore/.cli/deployed-state.json").read_text())
        self.assertEqual(
            deployed["targets"]["dev"]["resources"]["credentials"][lifecycle.CREDENTIAL]["credentialProviderArn"],
            self.provider_arn,
        )

    def test_missing_create_status_requires_explicit_get_ready(self):
        self.setup_sequence(["READY"], create_status=MISSING)
        lifecycle.setup(self.root, self.state, self.clients)
        self.assertEqual(self.state["phase"], "auth-ready")
        self.assertEqual(self.control.get_oauth2_credential_provider.call_count, 2)

    def test_failed_missing_unknown_or_updating_get_never_advances(self):
        for status in ("CREATE_FAILED", MISSING, "UNKNOWN_NEW_STATUS", "UPDATING"):
            with self.subTest(status=str(status)):
                self.state["phase"] = "configured"
                self.setup_sequence([status])
                with self.assertRaisesRegex(ValueError, "readiness failed or is unknown"):
                    lifecycle.setup(self.root, self.state, self.clients)
                self.assert_journal_retained()

    def test_known_create_failure_still_journals_returned_ids(self):
        self.setup_sequence([], create_status="CREATE_FAILED")
        with self.assertRaises(ValueError):
            lifecycle.setup(self.root, self.state, self.clients)
        self.assert_journal_retained()
        self.assertEqual(self.control.get_oauth2_credential_provider.call_count, 1)

    def test_creating_timeout_and_late_ready_keep_ids(self):
        self.setup_sequence(["CREATING"] * 3)
        with (
            patch.object(lifecycle, "PROVIDER_READY_ATTEMPTS", 3),
            patch.object(lifecycle.time, "sleep"),
            self.assertRaises(TimeoutError),
        ):
            lifecycle.setup(self.root, self.state, self.clients)
        self.assertEqual(self.control.get_oauth2_credential_provider.call_count, 4)
        self.assert_journal_retained()
        self.state["phase"] = "configured"
        self.setup_sequence(["READY"])
        with (
            patch.object(lifecycle.time, "monotonic", side_effect=[0, 0, 121]),
            self.assertRaises(TimeoutError),
        ):
            lifecycle.setup(self.root, self.state, self.clients)
        self.assert_journal_retained()

    def test_parsed_nonready_deploy_preflight_never_enters_cli(self):
        self.state.update(
            phase="auth-ready",
            provider_arn=self.provider_arn,
            secret_arn=self.secret_arn,
            pool_id="us-west-2_offline",
            client_id="offlineclient",
            discovery_url=self.discovery,
        )
        for status in ("CREATING", "CREATE_FAILED", MISSING, "UNKNOWN_NEW_STATUS", "UPDATING"):
            self.control.get_oauth2_credential_provider.return_value = self.response(
                "GetOauth2CredentialProvider", status
            )
            with self.subTest(status=str(status)), patch.object(lifecycle.subprocess, "run") as cli:
                with self.assertRaisesRegex(ValueError, "not proven READY"):
                    lifecycle.deploy(self.root, self.state, self.clients, "/must-not-run")
                cli.assert_not_called()
        self.control.get_oauth2_credential_provider.return_value = self.response("GetOauth2CredentialProvider", "READY")
        lifecycle.verify_auth(self.state, self.clients)
        self.control.update_oauth2_credential_provider.assert_not_called()

    def test_ready_does_not_override_secret_or_tag_ownership(self):
        self.state.update(
            provider_arn=self.provider_arn,
            secret_arn=self.secret_arn,
            pool_id="us-west-2_offline",
            client_id="offlineclient",
            discovery_url=self.discovery,
        )
        ready = self.response("GetOauth2CredentialProvider", "READY")
        ready["clientSecretArn"]["secretArn"] += "-foreign"
        self.control.get_oauth2_credential_provider.return_value = ready
        with self.assertRaisesRegex(ValueError, "secret ARN drift"):
            lifecycle.verify_auth(self.state, self.clients)
        self.control.get_oauth2_credential_provider.return_value = self.response("GetOauth2CredentialProvider", "READY")
        self.control.list_tags_for_resource.return_value = {"tags": {"claims:owner": "foreign"}}
        with self.assertRaisesRegex(ValueError, "ownership tag"):
            lifecycle.verify_auth(self.state, self.clients)
