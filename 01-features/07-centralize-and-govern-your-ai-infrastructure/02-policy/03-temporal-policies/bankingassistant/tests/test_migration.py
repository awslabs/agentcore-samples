"""Offline migration/lifecycle checks. No provisioning or real credentials."""

import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(f"banking_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch("boto3.session.Session.client", side_effect=AssertionError("No SDK on import")), \
     patch("boto3.client", side_effect=AssertionError("No SDK on import")), \
     patch.object(socket.socket, "connect", side_effect=AssertionError("No network on import")):
    setup = load("setup")
    cleanup = load("cleanup")
    access = load("runtime_access")


def error(code="ResourceNotFoundException"):
    return ClientError({"Error": {"Code": code, "Message": "offline test"}}, "Test")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "AWS_PROFILE": "", "AWS_DEFAULT_PROFILE": "", "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull, "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
        })
        self.environment.start()
        os.environ.pop("AWS_PROFILE", None)
        os.environ.pop("AWS_DEFAULT_PROFILE", None)
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("No network"))
        self.network.start()
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / "setup_config.json"
        self.patches = [patch.object(setup, "CONFIG_FILE", self.config),
                        patch.object(cleanup, "CONFIG_FILE", self.config),
                        patch.object(setup.time, "sleep")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.network.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_gateway_complete_update_and_enforce(self):
        params = setup.gateway_params("arn:aws:iam::123456789012:role/banking-gateway-role",
                                      "arn:aws:bedrock-agentcore:us-west-2:123456789012:policy-engine/test",
                                      "https://example.com/.well-known/openid-configuration", "client")
        model = Session().get_service_model("bedrock-agentcore-control")
        validate_parameters(params, model.operation_model("CreateGateway").input_shape)
        self.assertEqual(params["policyEngineConfiguration"]["mode"], "ENFORCE")
        self.assertEqual(params["protocolConfiguration"]["mcp"]["supportedVersions"],
                         ["2025-11-25", "2026-07-28"])
        ctrl = Mock()
        gateway_arn = f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:gateway/gw"
        ctrl.get_gateway.return_value = {"status": "READY", "gatewayArn": gateway_arn, "gatewayUrl": "https://gateway/mcp",
            "workloadIdentityDetails": {"workloadIdentityArn":
                f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:workload-identity-directory/default/workload-identity/gw"}}
        with patch.object(setup.boto3, "client", side_effect=AssertionError("Unexpected SDK client")):
            setup.ensure_gateway(ctrl, params["roleArn"], params["policyEngineConfiguration"]["arn"],
                                 params["authorizerConfiguration"]["customJWTAuthorizer"]["discoveryUrl"],
                                 "client", {"gateway_id": "gw"})
        ctrl.update_gateway.assert_called_once_with(gatewayIdentifier="gw", **params)
        self.assertEqual(json.loads(self.config.read_text())["gateway_arn"], gateway_arn)

    def test_base_permits_exclude_temporally_gated_tools(self):
        tools = {tool for _, tool in setup.BASE_PERMITS}
        self.assertEqual(len(tools), 10)
        self.assertTrue({"transfer_funds", "execute_trade", "rebalance_portfolio", "load_portfolio"}.isdisjoint(tools))
        from policy_wire import PolicyWire, ENGINE
        ctrl, wire = Mock(), PolicyWire()
        ctrl.create_policy.side_effect = wire.create_policy
        ctrl.get_policy.side_effect = wire.get_policy
        setup.ensure_base_permits(ctrl, ENGINE, "arn:gateway", {})
        self.assertEqual(ctrl.create_policy.call_count, 10)
        for call in ctrl.create_policy.call_args_list:
            statement = call.kwargs["definition"]["cedar"]["statement"]
            self.assertIn("banking-assistant-tools___", statement)
            self.assertIn('resource == AgentCore::Gateway::"arn:gateway"', statement)

    def test_create_failure_keeps_engine_id_and_timeout_fails(self):
        ctrl = Mock()
        ctrl.create_policy_engine.return_value = {"policyEngineId": "engine", "policyEngineArn": "arn:engine"}
        ctrl.get_policy_engine.return_value = {"status": "CREATE_FAILED"}
        with self.assertRaises(RuntimeError):
            setup.ensure_policy_engine(ctrl, {})
        self.assertEqual(json.loads(self.config.read_text())["engine_id"], "engine")
        with self.assertRaises(TimeoutError):
            setup.wait_ready(lambda: {"status": "CREATING"}, "READY", "test", attempts=2)

    def test_unrecorded_role_is_not_adopted(self):
        iam = Mock()
        # Exception type used by the function's except clause.
        iam.exceptions.NoSuchEntityException = type("NoSuchEntity", (Exception,), {})
        iam.get_role.return_value = {"Role": {"Arn": "arn:role"}}
        with self.assertRaisesRegex(RuntimeError, "unrecorded"):
            setup.ensure_gateway_role(iam, "123456789012", {})
        iam.put_role_policy.assert_not_called()

    def test_environment_mismatch_rejected(self):
        with self.assertRaises(RuntimeError):
            setup.check_environment({"gateway_id": "legacy"}, "123456789012")
        with self.assertRaises(RuntimeError):
            setup.check_environment({"account_id": "000000000000"}, "123456789012")

    def test_cleanup_access_errors_and_timeouts_are_not_absence(self):
        with self.assertRaises(ClientError):
            cleanup.wait_deleted(Mock(side_effect=error("AccessDeniedException")), "test")
        with self.assertRaises(TimeoutError):
            cleanup.wait_deleted(Mock(return_value={"status": "DELETING"}), "test", attempts=2)
        cleanup.wait_deleted(Mock(side_effect=error()), "test")

    def test_cleanup_paginates_and_removes_gateway_before_engine(self):
        ctrl, iam = Mock(), Mock()
        ctrl.list_policies.side_effect = [{"policies": [{"policyId": "p1"}], "nextToken": "next"},
                                          {"policies": [{"policyId": "p2"}]}]
        ctrl.list_gateway_targets.side_effect = [{"items": [], "nextToken": "next"},
                                                 {"items": [{"targetId": "t1"}]}]
        for method in (ctrl.get_policy, ctrl.get_gateway_target, ctrl.get_gateway, ctrl.get_policy_engine, iam.get_role):
            method.side_effect = error()
        cfg = {"account_id": "123456789012", "region": "us-west-2", "ownership_id": "owner",
               "role_name": "r", "gateway_name": "g", "engine_name": "e",
               "engine_id": "e", "gateway_id": "g",
               "role_arn": "arn:aws:iam::123456789012:role/r",
               "engine_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:policy-engine/e",
               "gateway_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/g"}
        cleanup.cleanup(ctrl, iam, cfg)
        self.assertEqual(ctrl.delete_policy.call_count, 2)
        calls = [c[0] for c in ctrl.mock_calls]
        self.assertLess(calls.index("delete_gateway"), calls.index("delete_policy_engine"))
        self.assertEqual(ctrl.list_gateway_targets.call_args.kwargs["nextToken"], "next")

    def test_cleanup_failure_retains_state_and_checks_account(self):
        cfg = {"account_id": "123456789012", "region": "us-west-2", "ownership_id": "owner",
               "role_name": "r", "gateway_name": "g", "engine_name": "e", "engine_id": "e",
               "engine_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:policy-engine/e"}
        self.config.write_text(json.dumps(cfg))
        session = Mock()
        session.client.return_value.get_caller_identity.return_value = {"Account": cfg["account_id"]}
        session.client.return_value.get_policy_engine.side_effect = error("AccessDeniedException")
        with patch.dict(os.environ, {"REGION": "us-west-2"}), patch.object(cleanup.boto3, "Session", return_value=session), patch("sys.argv", ["cleanup.py"]):
            with self.assertRaises(ClientError):
                cleanup.main()
        self.assertTrue(self.config.exists())

    def test_v1_status_access_and_local_only_negative(self):
        arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/banking-abc"
        status = {"target": "default", "resources": [
            {"resourceType": "runtime", "name": "banking_assistant_tools", "deploymentState": "deployed", "arn": arn},
            {"resourceType": "credential", "name": "banking_assistant_tools-oauth", "deploymentState": "deployed", "arn": "arn:provider"}]}
        self.assertIn("arn%3Aaws%3Abedrock-agentcore", access.access(status, "url"))
        self.assertTrue(access.access(status, "url").endswith("/invocations?qualifier=DEFAULT"))
        self.assertEqual(access.access(status, "credential"), "arn:provider")
        status["resources"][1]["name"] = "fresh-lab-oauth"
        self.assertEqual(access.access(status, "credential", "fresh-lab-oauth"), "arn:provider")
        with self.assertRaises(ValueError):
            access.access(status, "credential")
        status["resources"][0]["deploymentState"] = "local-only"
        with self.assertRaises(ValueError):
            access.access(status, "url")

    def test_readme_shell_syntax_and_policy_json(self):
        text = "\n".join((ROOT / name).read_text() for name in ("README.md", "DEPLOYMENT.md"))
        blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
        policies = []
        for block in blocks:
            block = re.sub(r"^> ?", "", block, flags=re.M)
            result = subprocess.run(["bash", "-n"], input=block, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            if not block.startswith("aws bedrock-agentcore-control create-policy"):
                continue
            # Execute ONLY policy blocks with an inert shell function, never AWS CLI.
            result = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                     "set +H; aws() { printf '%s\\0' \"$@\"; }; " + block],
                                    env={"PATH": os.environ["PATH"], "ENGINE_ID": "fake", "GATEWAY_ARN": "arn:gateway", "REGION": "us-west-2"},
                                    capture_output=True, check=True)
            args = result.stdout.decode().split("\0")
            for i, arg in enumerate(args):
                if arg == "--definition":
                    policies.append(json.loads(args[i + 1])["cedar"]["statement"])
        self.assertEqual(len(policies), 12)
        self.assertTrue(all("when temporal" in p for p in policies))
        self.assertTrue(all("banking-assistant-tools___" in p for p in policies))


if __name__ == "__main__":
    unittest.main()
