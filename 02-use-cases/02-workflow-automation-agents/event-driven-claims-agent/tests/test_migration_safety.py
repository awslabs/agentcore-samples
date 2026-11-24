"""Local config, ownership, exact SDK request contracts and partial-cleanup tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import configure_target as config
import lifecycle


def missing(operation="Get"):
    return ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "absent"}}, operation)


class FreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "agentcore").mkdir()
        shutil.copyfile(ROOT / "agentcore/agentcore.json", self.root / "agentcore/agentcore.json")

    def configure(self):
        return config.configure_fresh(self.root, "111122223333", "us-west-2", "offline01")

    def test_unique_notification_free_configuration(self):
        state = self.configure()
        self.assertFalse(state["notifications"])
        self.assertEqual(state["provider_name"], "Claimsoffline01_dev_cognito-gateway-m2m")
        self.assertEqual(lifecycle.load(self.root), state)
        original = (self.root / ".claims-v1.json").read_bytes()
        with self.assertRaises(ValueError):
            self.configure()
        self.assertEqual((self.root / ".claims-v1.json").read_bytes(), original)

    def test_config_cannot_select_bare_provider_or_add_unowned_credentials(self):
        self.configure()
        path = self.root / "agentcore/agentcore.json"
        original = json.loads(path.read_text())
        variants = []
        migrated = json.loads(json.dumps(original))
        migrated["migratedFrom"] = "v1"
        variants.append(migrated)
        extra = json.loads(json.dumps(original))
        extra["credentials"].append(dict(extra["credentials"][0], name="unowned"))
        variants.append(extra)
        secret = json.loads(json.dumps(original))
        secret["credentials"][0]["clientSecretRef"] = {"secretId": "unowned"}
        variants.append(secret)
        for value in variants:
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                lifecycle.load(self.root)

    def test_all_old_state_refused_without_overwrite(self):
        for name in (
            "agentcore/.cli",
            "agentcore/aws-targets.json",
            ".cognito-state.json",
            ".env",
            "agentcore/.env.local",
        ):
            path = self.root / name
            path.write_text("old malformed state")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.configure()
            self.assertEqual(path.read_text(), "old malformed state")
            path.unlink()
        self.assertFalse((self.root / ".claims-v1.json").exists())

    def test_invalid_identity_never_writes(self):
        for account, region, suffix in [
            ("bad", "us-west-2", "offline01"),
            ("111122223333", "bad", "offline01"),
            ("111122223333", "us-west-2", "bad-name"),
        ]:
            with self.assertRaises(ValueError):
                config.configure_fresh(self.root, account, region, suffix)
            self.assertFalse((self.root / ".claims-v1.json").exists())

    def test_setup_rejects_pool_collision_on_second_page(self):
        state = self.configure()
        cognito = MagicMock()
        cognito.list_user_pools.side_effect = [
            {"UserPools": [], "NextToken": "next"},
            {"UserPools": [{"Name": state["pool_name"]}]},
        ]
        with self.assertRaisesRegex(ValueError, "collision"):
            lifecycle.setup(self.root, state, lambda _: cognito)
        cognito.create_user_pool.assert_not_called()
        self.assertEqual(state["phase"], "configured")

    def test_setup_rejects_provider_collision_without_mutation(self):
        state = self.configure()
        cognito, control = MagicMock(), MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": []}
        cognito.describe_user_pool_domain.return_value = {}
        control.get_oauth2_credential_provider.return_value = {"credentialProviderArn": "unowned"}
        with self.assertRaisesRegex(ValueError, "provider collision"):
            lifecycle.setup(self.root, state, lambda service: cognito if service == "cognito-idp" else control)
        cognito.create_user_pool.assert_not_called()
        control.create_oauth2_credential_provider.assert_not_called()

    def test_setup_sdk_request_contract_and_secret_never_persisted(self):
        state = self.configure()
        cognito, control = MagicMock(), MagicMock()
        cognito.list_user_pools.return_value = {"UserPools": []}
        cognito.describe_user_pool_domain.return_value = {}
        cognito.create_user_pool.return_value = {
            "UserPool": {
                "Id": "us-west-2_offline",
                "Arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_offline",
            }
        }
        cognito.create_user_pool_client.return_value = {
            "UserPoolClient": {"ClientId": "offlineclient", "ClientSecret": "DO-NOT-PERSIST"}
        }
        provider = {
            "credentialProviderArn": "arn:aws:bedrock-agentcore:us-west-2:111122223333:token-vault/default/oauth2credentialprovider/"
            + state["provider_name"],
            "clientSecretArn": {"secretArn": "arn:aws:secretsmanager:us-west-2:111122223333:secret:offline-abcdef"},
            "status": "READY",
            "oauth2ProviderConfigOutput": {
                "customOauth2ProviderConfig": {
                    "clientId": "offlineclient",
                    "oauthDiscovery": {
                        "discoveryUrl": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_offline/.well-known/openid-configuration"
                    },
                }
            },
        }
        control.get_oauth2_credential_provider.side_effect = [missing(), provider]
        control.create_oauth2_credential_provider.return_value = provider
        control.list_tags_for_resource.return_value = {"tags": {"claims:owner": state["owner"]}}
        lifecycle.setup(self.root, state, lambda service: cognito if service == "cognito-idp" else control)
        self.assertEqual(state["phase"], "auth-ready")
        for service, client in [("cognito-idp", cognito), ("bedrock-agentcore-control", control)]:
            real = boto3.client(service, region_name="us-west-2")  # fake credentials; no API call
            for call in client.mock_calls:
                name, _args, kwargs = call
                operation = real.meta.method_to_api_mapping[name]
                validate_parameters(kwargs, real.meta.service_model.operation_model(operation).input_shape)
        for file in self.root.rglob("*"):
            if file.is_file():
                self.assertNotIn("DO-NOT-PERSIST", file.read_text())
        self.assertFalse((self.root / ".env").exists())

    def test_uncertain_setup_stays_journaled_and_cannot_retry_or_sweep(self):
        state = self.configure()
        state["phase"] = "creating-provider"
        lifecycle.save(self.root, state)
        clients = MagicMock()
        for operation in (lifecycle.setup, lifecycle.cleanup):
            with self.assertRaises(ValueError):
                operation(self.root, state, clients)
        clients.assert_not_called()
        self.assertEqual(json.loads((self.root / ".claims-v1.json").read_text())["phase"], "creating-provider")

    def test_unowned_stack_and_access_denied_are_not_absence(self):
        state = self.configure()
        state["phase"] = "auth-ready"
        clients = MagicMock()
        clients.return_value.describe_stacks.return_value = {"Stacks": [{"StackId": "unowned"}]}
        with patch.object(lifecycle, "verify_auth"), self.assertRaisesRegex(ValueError, "Unowned stack"):
            lifecycle.deploy(self.root, state, clients, "agentcore")
        clients.return_value.delete_stack.assert_not_called()
        denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Get")
        with self.assertRaises(ClientError):
            lifecycle.absent(MagicMock(side_effect=denied))

    def test_partial_stack_cleanup_never_deletes_auth_or_local_state(self):
        state = self.configure()
        state.update(phase="deployed", stack_arn="arn:aws:cloudformation:us-west-2:111122223333:stack/Claims/id")
        cfn = MagicMock()
        cfn.describe_stacks.return_value = {
            "Stacks": [
                {"StackStatus": "CREATE_COMPLETE", "Tags": [{"Key": lifecycle.OWNER_TAG, "Value": state["owner"]}]}
            ]
        }
        cfn.list_stack_resources.return_value = {
            "StackResourceSummaries": [
                {
                    "LogicalResourceId": "Memory",
                    "PhysicalResourceId": "owned-memory",
                    "ResourceType": "AWS::BedrockAgentCore::Memory",
                    "ResourceStatus": "DELETE_SKIPPED",
                }
            ]
        }
        clients = MagicMock(return_value=cfn)
        with self.assertRaisesRegex(ValueError, "Retained/partial"):
            lifecycle.cleanup(self.root, state, clients)
        self.assertEqual(clients.call_args_list[0].args, ("cloudformation",))
        self.assertEqual(clients.call_count, 1)
        self.assertTrue((self.root / "agentcore/aws-targets.json").exists())
        self.assertEqual(json.loads((self.root / ".claims-v1.json").read_text())["phase"], "deleting-stack")

    def test_optional_identity_delivery_sdk_contract_and_ownership(self):
        import identity_tracing

        state = self.configure()
        state.update(phase="deployed", stack_arn="arn:aws:cloudformation:us-west-2:111122223333:stack/Claims/id")
        cfn, control, logs = MagicMock(), MagicMock(), MagicMock()
        cfn.describe_stacks.return_value = {"Stacks": [{"Tags": [{"Key": "claims:owner", "Value": state["owner"]}]}]}
        cfn.list_stack_resources.return_value = {
            "StackResourceSummaries": [
                {"ResourceType": "AWS::BedrockAgentCore::Runtime", "PhysicalResourceId": "Runtime-abcdefghij"},
                {"ResourceType": "AWS::BedrockAgentCore::Gateway", "PhysicalResourceId": "Gateway-abcdefghij"},
            ]
        }
        for kind, method in [("runtime", "get_agent_runtime"), ("gateway", "get_gateway")]:
            getattr(control, method).return_value = {
                "workloadIdentityDetails": {
                    "workloadIdentityArn": "arn:aws:bedrock-agentcore:us-west-2:111122223333:workload-identity-directory/default/workload-identity/"
                    + kind
                }
            }
        logs.describe_log_groups.return_value = {"logGroups": []}
        logs.get_delivery_source.side_effect = missing()
        logs.get_delivery_destination.side_effect = missing()
        arn_prefix = "arn:aws:logs:us-west-2:111122223333:"
        logs.put_delivery_source.side_effect = lambda **kw: {
            "deliverySource": {"arn": arn_prefix + "delivery-source:" + kw["name"]}
        }
        logs.put_delivery_destination.side_effect = lambda **kw: {
            "deliveryDestination": {"arn": arn_prefix + "delivery-destination:" + kw["name"]}
        }
        logs.create_delivery.side_effect = lambda **kw: {
            "delivery": {"id": kw["deliverySourceName"], "arn": arn_prefix + "delivery:" + kw["deliverySourceName"]}
        }
        clients = lambda name: {"logs": logs, "cloudformation": cfn, "bedrock-agentcore-control": control}[name]
        identity_tracing.setup(self.root, state, clients)
        self.assertEqual(state["phase"], "deployed")
        self.assertEqual(len(state["identity_tracing"]), 4)
        real = boto3.client("logs", region_name="us-west-2")
        for name, args, kwargs in logs.mock_calls:
            validate_parameters(
                kwargs, real.meta.service_model.operation_model(real.meta.method_to_api_mapping[name]).input_shape
            )
        entry = state["identity_tracing"][0]
        logs.get_delivery.return_value = {
            "delivery": {"arn": entry["delivery_arn"], "deliveryDestinationArn": entry["destination_arn"]}
        }
        logs.list_tags_for_resource.return_value = {"tags": {"claims:owner": "unowned"}}
        with self.assertRaisesRegex(ValueError, "ownership tag"):
            identity_tracing.cleanup(self.root, state, clients)
        logs.delete_delivery.assert_not_called()

    def test_external_cleanup_requires_owner_tag(self):
        state = self.configure()
        state.update(
            phase="auth-ready",
            provider_arn="owned-provider-arn",
            pool_id="us-west-2_owned",
            secret_arn="owned-secret-arn",
        )
        control = MagicMock()
        control.get_oauth2_credential_provider.return_value = {"credentialProviderArn": "owned-provider-arn"}
        control.list_tags_for_resource.return_value = {"tags": {"claims:owner": "different-owner"}}
        with self.assertRaisesRegex(ValueError, "ownership tag"):
            lifecycle.cleanup(self.root, state, lambda _: control)
        control.delete_oauth2_credential_provider.assert_not_called()
        control.delete_user_pool.assert_not_called()
        control.list_tags_for_resource.return_value = {"tags": {"claims:owner": state["owner"]}}
        with self.assertRaisesRegex(ValueError, "secret ARN drift"):
            lifecycle.cleanup(self.root, state, lambda _: control)
        control.delete_oauth2_credential_provider.assert_not_called()
        self.assertTrue((self.root / ".claims-v1.json").exists())

    def test_verified_cleanup_preserves_all_local_evidence(self):
        state = self.configure()
        state.update(
            phase="auth-ready",
            provider_arn="owned-provider-arn",
            pool_id="us-west-2_owned",
            secret_arn="owned-secret-arn",
        )
        clients = MagicMock()
        for method in ("get_oauth2_credential_provider", "describe_user_pool", "describe_secret"):
            getattr(clients.return_value, method).side_effect = missing()
        directory = self.root / "agentcore/.cli"
        directory.mkdir()
        (directory / "deployed-state.json").write_text('{"targets":{"dev":{"resources":{}}}}')
        lifecycle.cleanup(self.root, state, clients)
        self.assertEqual(state["phase"], "deleted")
        self.assertTrue((directory / "deployed-state.json").exists())
        self.assertTrue((self.root / "agentcore/aws-targets.json").exists())
        self.assertTrue((self.root / ".claims-v1.json").exists())

    def test_scheduled_key_or_identity_is_not_cleaned_up(self):
        state = self.configure()
        state["resources"] = [{"ResourceType": "AWS::KMS::Key", "PhysicalResourceId": "owned-key"}]
        clients = MagicMock()
        clients.return_value.describe_key.return_value = {"KeyMetadata": {"KeyState": "PendingDeletion"}}
        with self.assertRaisesRegex(ValueError, "KMS key still exists"):
            lifecycle.verify_residue(state, clients)
        state["resources"] = []
        state["workload_identity_arns"] = [
            "arn:aws:bedrock-agentcore:us-west-2:111122223333:workload-identity-directory/default/workload-identity/owned"
        ]
        with self.assertRaisesRegex(ValueError, "workload identity remains"):
            lifecycle.verify_residue(state, clients)

    def test_cloud_checks_refuse_stale_local_notification_flag(self):
        import cloud_checks

        state = self.configure()
        state.update(phase="deployed", stack_arn="owned-stack")
        session = MagicMock()
        session.client.return_value.get_caller_identity.return_value = {"Account": state["account"]}
        session.client.return_value.describe_stacks.return_value = {
            "Stacks": [{"Tags": [{"Key": "claims:owner", "Value": state["owner"]}], "Outputs": []}]
        }
        session.client.return_value.get_function_configuration.return_value = {
            "Environment": {"Variables": {"NOTIFICATIONS_ENABLED": "true"}}
        }
        with (
            patch.object(cloud_checks, "load", return_value=state),
            patch.object(cloud_checks, "verify_auth"),
            patch.object(cloud_checks.boto3, "Session", return_value=session),
            self.assertRaisesRegex(ValueError, "Deployed notification mode"),
        ):
            cloud_checks.context()

    def test_cloud_opt_in_precedes_config_and_sdk(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/lifecycle.py"), "deploy"], capture_output=True, text=True, check=False
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--allow-cloud", result.stderr)


class ResourceContractTests(unittest.TestCase):
    def test_config_preserves_memory_auth_policy_and_eval(self):
        project = json.loads((ROOT / "agentcore/agentcore.json").read_text())
        self.assertEqual(project["version"], 2)
        runtime = project["runtimes"][0]
        self.assertEqual((runtime["name"], runtime["build"], runtime["protocol"]), ("claimsagent", "Container", "HTTP"))
        self.assertNotIn("authorizerConfiguration", runtime)
        gateway = project["agentCoreGateways"][0]
        self.assertEqual(gateway["authorizerType"], "CUSTOM_JWT")
        self.assertEqual(gateway["policyEngineConfiguration"]["mode"], "ENFORCE")
        self.assertEqual(len(gateway["targets"]), 6)
        memory = project["memories"][0]
        self.assertEqual(memory["eventExpiryDuration"], 90)
        self.assertEqual(
            [(s["type"], s["namespaces"]) for s in memory["strategies"]],
            [("SEMANTIC", ["claims/{actorId}/facts"]), ("SUMMARIZATION", ["claims/{actorId}/{sessionId}"])],
        )
        policies = project["policyEngines"][0]["policies"]
        self.assertIn("create-claim___create_claim", policies[1]["statement"])
        self.assertTrue(all(p["validationMode"] == "IGNORE_ALL_FINDINGS" for p in policies))
        self.assertEqual(project["credentials"][0]["name"], "cognito-gateway-m2m")
        self.assertEqual(project["onlineEvalConfigs"][0]["samplingRate"], 100)
        self.assertEqual(project["evaluators"][0]["name"], "ClaimsQualityEvaluator")
